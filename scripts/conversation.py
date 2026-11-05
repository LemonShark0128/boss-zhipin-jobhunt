"""One bounded conversation hop; delivery receipts, not intentions, commit state."""
import copy
import hashlib
import html
import os
import re
import time
import unicodedata
from datetime import datetime
from urllib.parse import quote

import chat_dom
import chrome_mcp as CM
import conv_actions
import conv_llm
import conv_parse
import conv_rules
import conv_store
import hunt_config as HC
import hunt_filter
import hunt_state
import outbound
import send_safety as SS

LIST_CAPTURE_MAX = 12


def _job_title(cfg, job_id, job, item, state):
    """Resolve a title from existing local sources without changing state."""
    sent = (state.get('sent') or {}).get(job_id)
    pooled = (state.get('b_pool') or {}).get(job_id)
    values = (sent.get('name') if isinstance(sent, dict) else None,
              job.get('jobName'),
              pooled.get('title') if isinstance(pooled, dict) else None,
              item.get('jobName'))
    return next((value.strip() for value in values
                 if isinstance(value, str) and value.strip()), '')


def _identity(record):
    """Format notification identity; the display fallback never enters storage."""
    title = (record.get('title') or '').strip() or '岗位未知'
    return '%s｜%s｜%s' % (record['brand'], title, record['boss'])


def _decline_match(options, job, item, row, jd):
    fields = [source.get(key, '') for source in (job, item, row)
              for key in ('brandIndustry', 'brandName', 'jobName', 'brand', 'title')]
    fields.append(hunt_filter.jd_duty_scope(jd or ''))
    return next((word for word in options.get('decline_industries', [])
                 if isinstance(word, str) and word and
                 any(word in value for value in fields if isinstance(value, str))), '')


def _snapshot(item):
    info = item.get('lastMessageInfo') or {}
    text = re.sub(r'\s+', ' ', str(info.get('showText') or '')).strip()
    try:
        ts = float(info.get('msgTime') or 0) / 1000
    except (ValueError, TypeError):
        ts = 0
    return {'ts': ts, 'text': text}


def _new_messages(history, record, snapshot):
    last_id = record.get('last_msg_id')
    if last_id:
        matches = [i for i, m in enumerate(history) if m.get('id') == last_id]
        if len(matches) != 1:
            return []  # Cursor absent from visible history: do not replay old requests.
        messages = history[matches[0]+1:]
    else:
        start = len(history)
        while start and history[start-1]['who'] == 'friend':
            start -= 1
        messages = history[start:]
    result = []
    for message in messages:
        if message['who'] != 'friend':
            continue
        row = dict(message)
        try:
            ts = float(row.get('ts') or snapshot['ts'])
            row['ts'] = ts / 1000 if ts > 100000000000 else ts
        except (TypeError, ValueError):
            row['ts'] = snapshot['ts']
        result.append(row)
    return result


# 简历/交换请求卡片 innerText 里单独成行的按钮字，推送时去掉（conv-07）
CARD_BUTTON_LINES = frozenset(('拒绝', '同意', '或者发送', '电话号码', '微信号码'))


def _push_text(text):
    lines = [line.strip() for line in (text or '').splitlines()]
    return ' '.join(line for line in lines if line and line not in CARD_BUTTON_LINES)


def _notify(kind, record, result, out, cfg=None, new_msgs=(), quiet=False):
    if record['notified'].get(kind):
        return
    record['notified'][kind] = True
    if quiet and kind == 'manual':
        # conv-07：同跳另有「📎 不过筛但要简历」推送，这里只记状态与计数，不重复推
        result[kind] += 1
        return
    if kind == 'complete':
        card = {key: record[key] for key in ('brand', 'title', 'boss')}
        card.update({key: '未答' if conv_rules._empty(record['answers'].get(key))
                     else record['answers'][key] for key in conv_rules.ANSWER_KEYS})
        line = '📇 问齐 ' + _identity(card) + '｜' + '｜'.join(
            str(card[key]) for key in conv_rules.ANSWER_KEYS)
    else:
        if new_msgs:
            text = new_msgs[-1]['text']
        else:
            text = record['last_seen']['text']
            options = (cfg or {}).get('conversation') or {}
            pattern = options.get(
                'resume_sent_pattern', conv_actions.DEFAULT_RESUME_SENT_PATTERN)
            if (conv_parse.is_system_message(
                    {'text': text}, conv_parse.system_message_texts(cfg),
                    conv_parse.system_patterns(cfg)) or re.search(pattern, text)):
                text = '（系统消息）'
        line = '🙋 需人工 %s：%s' % (_identity(record), _push_text(text)[:40])
    result['notifications'].append(line)
    out.append(line)
    result[kind] += 1


def _daily_keys(records, now):
    today = now.date()
    return {key for key, row in records.items() if any(
        action.get('result') == 'sent' and
        datetime.fromtimestamp(action['ts'], now.tzinfo).date() == today
        for action in row.get('actions', []))}


def _resume_card(cfg, history):
    """Return the configured card text only while its latest request is unresolved."""
    options = cfg.get('conversation') or {}
    card = options.get('resume_request_card', '我想要一份您的附件简历')
    indices = [i for i, m in enumerate(history)
               if card and m.get('who') == 'friend' and card in (m.get('text') or '')]
    if not indices:
        return None
    pattern = re.compile(options.get(
        'resume_sent_pattern', conv_actions.DEFAULT_RESUME_SENT_PATTERN))
    system_texts = conv_parse.system_message_texts(cfg)
    if any(conv_parse.is_system_message(m, system_texts) and pattern.search(m.get('text') or '')
           for m in history[indices[-1]+1:]):
        return None
    return card


def _resume_manual(row, result, out, now_ts):
    row['status'] = 'manual'
    if not row['notified']['manual']:
        row['notified']['manual'] = True
        line = '🙋 需人工 %s：对方发了简历请求卡片，请手动点「同意」' % _identity(row)
        result['notifications'].append(line)
        out.append(line)
        result['manual'] += 1
        conv_store.add_action(row, now_ts, 'notify', 'notified')


def _execute(cfg, br, tab, job, before, actions, planned, result, out, now_ts, history=(), new_msgs=(),
             quiet_manual=False):
    row = copy.deepcopy(planned)
    # Planner action logs are intentions. Keep only actual non-send diagnostics.
    row['actions'] = copy.deepcopy(before['actions'])
    for action in planned['actions']:
        if action not in before['actions'] and action['result'] != 'planned':
            conv_store.add_action(row, action['ts'], action['type'], action['result'])
    row['notified'] = copy.deepcopy(before['notified'])
    sends = [a for kind in ('send_text', 'send_resume') for a in actions if a['type'] == kind]
    for field in ('resume_sent_ts', 'asked_round1_ts', 'asked_round2_ts'):
        row[field] = before[field]
    row['sent_templates'] = list(before.get('sent_templates', []))
    row['experience_answered_ts'] = before.get('experience_answered_ts')
    if sends:
        row['status'] = before['status']
    for action in sends:
        if action['type'] == 'send_text':
            if 'template_part' in action:
                template, opener = action['template_part'], action.get('opener_part', '')
            elif planned['asked_round1_ts'] != before['asked_round1_ts']:
                template = conv_rules.round_questions(before, cfg, 1)
                opener = action['text'][:-len(template)] if template else action['text']
            elif planned['asked_round2_ts'] != before['asked_round2_ts']:
                template, opener = action['text'], ''
            else:
                template, opener = action['text'], ''
            def send():
                return conv_actions.send_text(br, tab, cfg, row['conv_key'], job,
                                               action['text'], template, opener)
        else:
            card = _resume_card(cfg, history)
            if card and not (cfg.get('conversation') or {}).get('auto_accept_resume_request', True):
                _resume_manual(row, result, out, now_ts)
                return row
            def send():
                if card:
                    return conv_actions.accept_resume_request(
                        br, tab, cfg, row['conv_key'], row['job_id'], card)
                return conv_actions.send_resume(br, tab, cfg, row['conv_key'], row['job_id'])
        receipt = send()
        gap = re.fullmatch(r'skip_min_gap\((\d+)\)', receipt)
        if gap and int(gap[1]) <= (cfg.get('sending') or {}).get('min_gap_seconds', 0) + 5:
            (cfg.get('_sleep') or time.sleep)(int(gap[1]) + 1)
            receipt = send()  # Only one retry; all other receipts keep the existing failure path.
        conv_store.add_action(row, now_ts, action['type'], receipt)
        if receipt != 'sent':
            if receipt in ('send_aborted:accept_button', 'send_aborted:resume_requested'):
                _resume_manual(row, result, out, now_ts)
            elif receipt in ('manual_required', 'send_aborted:template_mismatch'):
                row['status'] = 'manual'
                _notify('manual', row, result, out, cfg, new_msgs, quiet=quiet_manual)
            elif receipt == 'pending_verification':
                result['pending_verification'] = True
                line = '⚠️ 待核实 ' + _identity(row)
                result['notifications'].append(line)
                out.append(line)
            else:
                row['last_seen'] = copy.deepcopy(before['last_seen'])
                if before.get('last_msg_id'):
                    row['last_msg_id'] = before['last_msg_id']
                else:
                    row.pop('last_msg_id', None)
            return row
        if action['type'] == 'send_resume':
            if card:
                conv_store.add_action(row, now_ts, 'resume_via', 'accept_card')
            row['resume_sent_ts'] = planned['resume_sent_ts']
            result['resume_sent'] += 1
            bubbles = conv_actions.LAST_RESUME_BUBBLES
            if bubbles >= 2:
                line = '⚠️ 简历重复 %s：出现 %d 条已发送' % (_identity(row), bubbles)
                result['notifications'].append(line)
                out.append(line)
                result['resume_duplicates'] += 1
        else:
            row['sent_templates'] = (row['sent_templates'] + [template])[-5:]
            row['experience_answered_ts'] = planned.get('experience_answered_ts')
            for number in (1, 2):
                field = 'asked_round%d_ts' % number
                row[field] = planned[field]
                if row[field] != before[field]:
                    result['round%d' % number] += 1
    row['status'] = planned['status']
    for action in actions:
        if action['type'] == 'notify':
            _notify(action['kind'], row, result, out, cfg, new_msgs, quiet=quiet_manual)
            conv_store.add_action(row, now_ts, 'notify', 'notified')
    return row


def run(cfg, br, out, now=None, dry=False):
    """Read captured candidates, refresh DOM rows before opening, and close owned tabs."""
    options = cfg.get('conversation') or {}
    dry = bool(dry or options.get('dry', False))
    result = dict(ok=True, dry_run=dry, captured=0, captures=0, candidates=0, processed=0, resume_sent=0, resume_duplicates=0,
                  round1=0, round2=0, complete=0, manual=0, ignored=0, ignored_resume_asked=0,
                  owner_unknown=0, row_missing=0, out_of_view=0, out_of_view_own=0, revealed=0, reveal_failed=0, declined=0, notifications=[], plans=[], day_count=0,
                  daily_cap=options.get('daily_cap', 20), pending_verification=False)
    try:
        SS.ledger_guard(cfg)
    except RuntimeError as exc:
        line = '⚠️ 待核实 ' + str(exc)
        out.append(line)
        result.update(ok=False, pending_verification=True, error=str(exc), notifications=[line])
        return result
    if not options.get('enabled', False):
        result['reason'] = 'disabled'
        return result
    now = now or HC.now_local(cfg)
    now = now.astimezone(HC.cfg_tz(cfg))
    hours = options.get('hours', list(range(9, 22)))
    if now.hour not in hours:
        result['reason'] = 'outside_hours'
        return result
    system_texts = conv_parse.system_message_texts(cfg)
    resume_sent_pattern = re.compile(options.get(
        'resume_sent_pattern', conv_actions.DEFAULT_RESUME_SENT_PATTERN))
    now_ts = now.timestamp()
    path = os.path.join(cfg['paths']['workdir'], 'conversations.json')
    records = conv_store.load(path)
    try:
        state = hunt_state.load_state(cfg)
    except Exception:
        state = {}
    daily = _daily_keys(records, now)
    result['day_count'] = len(daily)
    tab = None
    reveal_attempted = False
    try:
        tab = br.new_tab(chat_dom.CHAT_URL)
        br.nav(tab, chat_dom.CHAT_URL, wait=5)
        items = {}
        saw_empty_list = False
        for attempt in range(2):
            valid = False
            for index in range(LIST_CAPTURE_MAX):
                response = br.network(tab, 'friend/getG', index=index, max_body=400000)
                text = response.get('text') or ''
                if response.get('isError') or text.lstrip().startswith('[NO_MATCH]'):
                    break
                data = CM.parse_network_json(text)
                if (not isinstance(data, dict) or data.get('code') != 0
                        or not isinstance(data.get('zpData'), dict)
                        or not isinstance(data['zpData'].get('result'), list)):
                    continue
                result['captures'] += 1
                if not data['zpData']['result']:
                    saw_empty_list = True
                    continue
                for item in data['zpData']['result']:
                    if isinstance(item, dict):
                        key = chat_dom.source_conversation_key(item)
                        if key:
                            previous = items.get(key)
                            # Missing/equal times favor the more recent capture,
                            # which we visited first (smaller index).
                            if previous is None:
                                items[key] = item
                            else:
                                old_ts, new_ts = _snapshot(previous)['ts'], _snapshot(item)['ts']
                                if old_ts and new_ts and new_ts > old_ts:
                                    items[key] = item
                            valid = True
            if valid:
                break
            if attempt == 1:
                raise RuntimeError('chat_empty_list' if saw_empty_list else 'chat_no_valid_response')
            # Restart capture on the same owned tab without changing tab ownership.
            if hasattr(br, 'reload'):
                br.reload(tab)
            else:
                br.nav(tab, 'about:blank', wait=1)
                br.nav(tab, chat_dom.CHAT_URL, wait=6)
        result['captured'] = len(items)
        rows = chat_dom.conversation_rows(br, tab)
        # conv-06b：列表 DOM 可能晚于抓包渲染。读到空行、或最新的会话不在行里（它本该在最前面），
        # 就等列表渲染再重读一次；页面无法判断（None）时照旧，不空等。
        newest = max(items.items(), key=lambda kv: _snapshot(kv[1])['ts'], default=(None, None))[0]
        if items and (not rows or not any(row.get('conv_key') == newest for row in rows)):
            ready = chat_dom.wait_rows_ready(br, tab)
            if ready is False:
                result['rows_not_ready'] = True
            if ready:
                rows = chat_dom.conversation_rows(br, tab)
        candidates = []
        out_of_view = []
        for key, item in items.items():
            previous = records.get(key, {})
            snap = _snapshot(item)
            if ((item.get('lastMessageInfo') or {}).get('fromSelf') is True
                    or not 0 <= now_ts-snap['ts'] <= options.get('lookback_hours', 72)*3600
                    or (snap == previous.get('last_seen') and previous.get('status') != 'ignored')
                    or previous.get('status') in conv_rules.TERMINAL):
                continue
            indices = [i for i, row in enumerate(rows) if row.get('conv_key') == key]
            if not indices:
                out_of_view.append((snap['ts'], key, item, snap))
                continue
            if len(indices) == 1 and rows[indices[0]].get('receipt'):
                continue
            candidates.append((snap['ts'], key, item, snap))
        revealed_keys = set()
        failed_keys = set()
        reveal_reasons = result['reveal_reasons'] = {}

        def reveal_rows(item, key):
            nonlocal reveal_attempted
            reveal_attempted = True
            receipt = chat_dom.reveal_conversation(br, tab, item)
            if not isinstance(receipt, dict) or receipt.get('ok') is not True:
                reason = receipt.get('reason') if isinstance(receipt, dict) else None
                reason = reason if isinstance(reason, str) and reason else 'invalid_response'
                reveal_reasons[reason] = reveal_reasons.get(reason, 0) + 1
                return None
            current = chat_dom.conversation_rows(br, tab)
            if not current and chat_dom.wait_rows_ready(br, tab, attempts=2):
                current = chat_dom.conversation_rows(br, tab)
            hits = sum(row.get('conv_key') == key for row in current)
            if hits == 1:
                return current
            reason = 'rows_empty' if not current else 'rows_missing' if hits == 0 else 'rows_duplicate'
            reveal_reasons[reason] = reveal_reasons.get(reason, 0) + 1
            return None

        failed = []
        for entry in out_of_view:
            _, key, item, _ = entry
            current = reveal_rows(item, key)
            if current is None:
                failed.append(entry)
                failed_keys.add(key)
            else:
                if any(row.get('conv_key') == key and row.get('receipt') for row in current):
                    continue
                revealed_keys.add(key)
                candidates.append(entry)
        candidates.sort(key=lambda c: c[0], reverse=True)
        result['candidates'] = len(candidates)
        result['out_of_view'] = result['reveal_failed'] = len(failed_keys)
        def normalize_own_text(text):
            return unicodedata.normalize('NFKC', ''.join(html.unescape(text).split()))

        own_texts = []
        if out_of_view:
            for entry in SS.ledger_scan(cfg)[0].values():
                text = normalize_own_text(entry.get('text') or '')
                if text and not re.fullmatch(r'<[^<>]+>', text):
                    own_texts.append(text)
        notified_out_of_view = 0
        def notify_out_of_view(key, item, snap):
            nonlocal notified_out_of_view
            if notified_out_of_view >= 3:
                return
            if (conv_parse.is_system_message({'text': snap['text']}, system_texts)
                    or resume_sent_pattern.search(snap['text'])
                    or records.get(key, {}).get('out_of_view_snap') == snap):
                return
            normalized_snap = normalize_own_text(snap['text'])
            if len(normalized_snap) >= 8 and any(own.startswith(normalized_snap) for own in own_texts):
                result['out_of_view_own'] += 1
                return
            row = records.get(key)
            if row is None:
                row = conv_store.new_record(
                    key, 'inbound', item.get('encryptJobId', ''),
                    item.get('brandName', ''), item.get('name', ''),
                    item.get('jobName', ''), now_ts)
            line = '📭 列表 40 行外有未处理消息 %s｜%s：%s（需你手动看）' % (
                row['brand'], row['boss'], snap['text'][:30])
            if not dry:
                row['out_of_view_snap'] = snap
                row['updated_ts'] = now_ts
                records[key] = row
                conv_store.save(path, records)
            result['notifications'].append(line)
            out.append(line)
            notified_out_of_view += 1

        for _, key, item, snap in sorted(failed, key=lambda c: c[0], reverse=True):
            notify_out_of_view(key, item, snap)
        jobs = None
        planned_daily = set(daily)
        for _, key, item, snap in candidates[:options.get('per_hop', 8)]:
            if len(planned_daily if dry else daily) >= result['daily_cap']:
                break
            rows = chat_dom.conversation_rows(br, tab)
            # Earlier reveals can also displace candidates from the initial window.
            if key in revealed_keys or (revealed_keys and not any(row.get('conv_key') == key for row in rows)):
                rows = reveal_rows(item, key)
                if rows is None:
                    failed_keys.add(key)
                    result['out_of_view'] = result['reveal_failed'] = len(failed_keys)
                    notify_out_of_view(key, item, snap)
                    continue
            indices = [i for i, row in enumerate(rows) if row.get('conv_key') == key]
            if len(indices) != 1:
                result['row_missing'] += 1
                continue
            index = indices[0]
            if rows[index].get('receipt'):
                continue
            status, _, history = chat_dom.open_by_index(br, tab, index, conv_key=key)
            if status != 'ok' or not history:
                continue
            raw_history = history
            resume_evidence = next((m for m in history
                                    if conv_parse.is_system_message(m, system_texts) and
                                    resume_sent_pattern.search(str(m.get('text') or ''))), None)
            history = [m for m in history if not conv_parse.is_system_message(m, system_texts)]
            if any(m.get('who') not in ('mine', 'friend') for m in history):
                result['owner_unknown'] += 1
                continue
            previous = records.get(key)
            job_id = previous['job_id'] if previous is not None else item.get('encryptJobId', '')
            title = previous.get('title') if previous is not None else ''
            # The list item's title is the HR's role, not the job title.
            if (title or '').strip() and (title or '').strip() == (item.get('title') or '').strip():
                title = ''
            if not (title or '').strip():
                if jobs is None:
                    jobs = {j['encryptJobId']: j for j in hunt_filter.load_jobs(cfg)}
                title = _job_title(cfg, job_id, jobs.get(job_id) or {}, item, state)
            row = copy.deepcopy(previous) if previous is not None else conv_store.new_record(
                key, 'reply' if any(m['who'] == 'mine' for m in history) else 'inbound',
                item.get('encryptJobId', ''), item.get('brandName', ''),
                item.get('name', ''), title, now_ts)
            row['title'] = title
            if row['resume_sent_ts'] is None and resume_evidence is not None:
                try:
                    sent_ts = float(resume_evidence.get('ts') or now_ts)
                    sent_ts = sent_ts / 1000 if sent_ts > 100000000000 else sent_ts
                except (TypeError, ValueError):
                    sent_ts = now_ts
                row['resume_sent_ts'] = sent_ts
                conv_store.add_action(row, now_ts, 'resume_seen', 'observed')
            if row['status'] == 'ignored':
                row['status'] = 'new'
                row.pop('last_msg_id', None)
            new_msgs = _new_messages(history, row, snap)
            cursor_lost = (row.get('last_msg_id') and history and history[-1]['who'] == 'friend' and
                           sum(m.get('id') == row['last_msg_id'] for m in history) != 1)
            if cursor_lost or not history or history[-1]['who'] != 'friend' or not new_msgs:
                reason = 'cursor_lost' if cursor_lost else 'no_new_hr_messages'
                if dry:
                    result['plans'].append({'conv_key': key, 'actions': [], 'reason': reason})
                    out.append('🧪 计划 %s：%s' % (key, reason))
                else:
                    row['last_seen'] = snap
                    row['updated_ts'] = now_ts
                    if cursor_lost:
                        row['status'] = 'manual'
                        _notify('manual', row, result, out, cfg, new_msgs)
                    records[key] = row
                    conv_store.save(path, records)
                continue
            result['processed'] += 1
            if key in revealed_keys:
                result['revealed'] += 1
            if jobs is None:
                jobs = {j['encryptJobId']: j for j in hunt_filter.load_jobs(cfg)}
            job = jobs.get(row['job_id']) or dict(item)
            jd = row.get('jd', '')
            if row['origin'] == 'inbound' and row.get('job_ok') is None:
                jd = ''
                if row['job_id']:
                    jd_tab = br.new_tab('about:blank')
                    nav = br.nav(jd_tab, 'https://www.zhipin.com/job_detail/%s.html' % quote(row['job_id'], safe=''), wait=4)
                    if not nav or not nav.get('isError'):
                        jd = outbound.read_jd(br, jd_tab, cfg)
                        try:
                            page_title = br.json(jd_tab, r'''(() => {
                              const heading = document.querySelector('.job-banner h1,.job-name,h1');
                              const title = (heading?.innerText || '').trim();
                              if (title) return title;
                              return (document.title.match(/「(.*?)招聘」/)?.[1] || '').trim();
                            })()''')
                            if (not (row.get('title') or '').strip() and
                                    isinstance(page_title, str) and page_title.strip()):
                                row['title'] = page_title.strip()
                        except Exception:
                            pass  # Missing titles use the retry path below.
                if not jd:
                    row['jd_failures'] = row.get('jd_failures', 0) + 1
                    row['updated_ts'] = now_ts
                    if row['jd_failures'] >= 3:
                        row['status'] = 'manual'
                        row['last_seen'] = snap
                        if not dry:
                            _notify('manual', row, result, out, cfg, new_msgs)
                    if dry:
                        result['plans'].append({'conv_key': key, 'actions': [], 'reason': 'jd_unavailable'})
                        out.append('🧪 计划 %s：JD 读取失败，暂缓' % key)
                    else:
                        records[key] = row
                        conv_store.save(path, records)
                    continue
                title = (row.get('title') or '').strip()
                if not title:
                    row['title_failures'] = row.get('title_failures', 0) + 1
                    row['updated_ts'] = now_ts
                    if row['title_failures'] >= 3:
                        row['status'] = 'manual'
                        row['last_seen'] = snap
                        if not dry:
                            _notify('manual', row, result, out, cfg,
                                    [{'text': '岗位名读不到'}])
                    if dry:
                        result['plans'].append({'conv_key': key, 'actions': [], 'reason': 'title_unavailable'})
                        out.append('🧪 计划 %s：岗位名读不到，暂缓' % key)
                    else:
                        records[key] = row
                        conv_store.save(path, records)
                    continue
                if not (job.get('jobName') or '').strip():
                    job = dict(job, jobName=title)
                row.update(job_ok=(hunt_filter.judge(job, cfg)[0] == 'pass' and
                                   hunt_filter.grade_with_jd(job, cfg, jd)['grade'] in ('A', 'B')),
                           jd=jd, jd_hash=hashlib.sha256(jd.encode()).hexdigest(), jd_failures=0,
                           title_failures=0)
            decline_word = _decline_match(options, job, item, row, jd)
            def opener():
                opener_cfg = cfg
                if dry:
                    # The LLM helper caches successful openers when workdir is set.
                    opener_cfg = copy.deepcopy(cfg)
                    opener_cfg['paths'].pop('workdir', None)
                return conv_llm.opener(opener_cfg, job, jd) or options.get('opener_fallback', '对这个岗位挺感兴趣，')
            if (row['status'] == 'new' and row['asked_round1_ts'] is not None and
                    (row['resume_sent_ts'] is not None or row.get('job_ok') is False)):
                row['status'] = 'engaged'
                conv_store.add_action(row, now_ts, 'status_repair', 'new_to_engaged')
            actions, planned = conv_rules.decide(row, new_msgs, {
                'cfg': cfg, 'now_ts': now_ts, 'job_ok': row.get('job_ok'), 'opener': opener,
                'declined': bool(decline_word),
                'decline_text': options.get('decline_text') or conv_rules.DECLINE_TEXT,
                'lead': lambda kind: conv_llm.lead(
                    cfg, kind, new_msgs[-1]['text'], history, conv_llm.G.facts(cfg)),
                'answer': lambda text: conv_llm.answer(
                    cfg, text, history, conv_llm.G.facts(cfg)),
                'history': history, 'extract': lambda h: conv_llm.extract(cfg, h)})
            planned['last_seen'] = snap
            planned['last_msg_id'] = history[-1].get('id') or None
            planned['updated_ts'] = now_ts
            ordered = [a for kind in ('send_text', 'send_resume', 'notify') for a in actions if a['type'] == kind]
            card_text = options.get('resume_request_card', '我想要一份您的附件简历')
            unqualified = row['origin'] == 'inbound' and row.get('job_ok') is False
            resume_msgs = [m for m in new_msgs if m.get('who') == 'friend' and (
                conv_rules.classify(m.get('text'), cfg) == 'resume' or
                (card_text and card_text in (m.get('text') or '')))]
            resume_asked = unqualified and not decline_word and bool(resume_msgs)
            # conv-07：本跳会推「📎 不过筛但要简历」时，转人工不再另推 🙋（同一会话只推一条）
            paperclip = resume_asked and not row.get('resume_asked')
            if dry:
                plan = {'conv_key': key, 'actions': ordered, 'status': planned['status']}
                result['plans'].append(plan)
                if decline_word:
                    plan['decline'] = decline_word
                    if options.get('decline_not_interested', True):
                        dry_cfg = copy.deepcopy(cfg)
                        dry_cfg.setdefault('conversation', {})['dry'] = True
                        plan['not_interested'] = conv_actions.mark_not_interested_company(
                            br, tab, dry_cfg, key)
                    else:
                        plan['not_interested'] = 'disabled'
                    out.append('🧪 计划 %s decline %s not_interested %s' % (
                        key, decline_word, plan['not_interested']))
                if resume_asked:
                    plan['ignored_resume_asked'] = True
                    out.append('🧪 📎 不过筛但要简历 ' + _identity(row))
                if any(a['type'] == 'send_resume' for a in ordered):
                    card = _resume_card(cfg, raw_history)
                    via = 'accept_card' if card else 'button'
                    if card and not options.get('auto_accept_resume_request', True):
                        locate = 'send_aborted:accept_button'
                    else:
                        dry_cfg = copy.deepcopy(cfg)
                        dry_cfg.setdefault('conversation', {})['dry'] = True
                        if card:
                            locate = conv_actions.accept_resume_request(br, tab, dry_cfg, key, row['job_id'], card)
                        else:
                            locate = conv_actions.send_resume(br, tab, dry_cfg, key, row['job_id'])
                    plan.update(resume_via=via, locate=locate)
                    out.append('🧪 计划 %s 简历方式 %s 定位 %s' % (key, via, locate))
                if any(a['type'] in ('send_text', 'send_resume') for a in ordered):
                    planned_daily.add(key)
                for action in ordered:
                    out.append('🧪 计划 %s %s %s' % (key, action['type'], action.get('text', action.get('kind', ''))))
                if not ordered:
                    out.append('🧪 计划 %s 状态 %s' % (key, planned['status']))
                continue
            committed = _execute(cfg, br, tab, job, row, actions, planned, result, out, now_ts, raw_history, new_msgs,
                                 paperclip)
            if committed['status'] == 'declined':
                result['declined'] += 1
                line = '🚫 回绝 %s（命中：%s）' % (_identity(committed), decline_word)
                result['notifications'].append(line)
                out.append(line)
                if options.get('decline_not_interested', True):
                    receipt = conv_actions.mark_not_interested_company(br, tab, cfg, key)
                    conv_store.add_action(committed, now_ts, 'not_interested', receipt)
                    if receipt != 'done':
                        line = '⚠️ 不感兴趣未点成 %s，请手动' % _identity(committed)
                        result['notifications'].append(line)
                        out.append(line)
            if unqualified and not decline_word:
                result['ignored'] += 1
                if resume_asked and not committed.get('resume_asked'):
                    committed['resume_asked'] = True
                    line = '📎 不过筛但要简历 ' + _identity(row)
                    said = _push_text(resume_msgs[-1].get('text'))[:40]
                    if said:
                        line += '：' + said
                    result['notifications'].append(line)
                    out.append(line)
                    result['ignored_resume_asked'] += 1
            records[key] = committed
            conv_store.save(path, records)
            daily = _daily_keys(records, now)
            if result['pending_verification']:
                break
        result['day_count'] = len(daily)
        overview = ('💬 对话｜列表 {captured}（抓包 {captures}）｜候选 {candidates}｜处理 {processed}｜发简历 {resume_sent}｜'
                    '问第一轮 {round1}｜问第二轮 {round2}｜问齐 {complete}｜需人工 {manual}｜'
                    '不过筛 {ignored}｜回绝 {declined}｜row_missing {row_missing}'
                    + ('｜40行外 {out_of_view}' if result['out_of_view'] else '')
                    + '｜今日 {day_count}/{daily_cap}').format(**result)
        result['overview'] = overview
        out.append(overview)
        reveal_line = '💬 滚动定位｜revealed {revealed}｜reveal_failed {reveal_failed}'.format(**result)
        if result.get('reveal_reasons'):
            reveal_line += '（%s）' % ', '.join('%s %d' % kv for kv in sorted(result['reveal_reasons'].items()))
        if result.get('rows_not_ready'):
            reveal_line += '｜rows_not_ready'
        out.append(reveal_line)
        if hours and now.hour == max(hours):
            counts = {state: sum(r['status'] == state for r in records.values())
                      for state in ('complete', 'manual', 'engaged', 'round2', 'ignored')}
            counts['ignored'] = sum(r['origin'] == 'inbound' and r.get('job_ok') is False
                                    and r['status'] != 'declined' for r in records.values())
            counts['ignored_resume_asked'] = sum(r.get('job_ok') is False and bool(r.get('resume_asked'))
                                                 for r in records.values())
            counts.update(revealed=result['revealed'], reveal_failed=result['reveal_failed'])
            result['summary'] = counts
            line = '📊 对话小结｜问齐 %d｜需人工 %d｜等对方回复 %d｜不过筛 %d（其中要简历 %d）' % (
                counts['complete'], counts['manual'], counts['engaged']+counts['round2'], counts['ignored'],
                counts['ignored_resume_asked'])
            line += '｜revealed {revealed}｜reveal_failed {reveal_failed}'.format(**result)
            result['summary_line'] = line
            out.append(line)
        return result
    finally:
        if tab is not None and reveal_attempted:
            chat_dom.reset_list_scroll(br, tab)
        result['tabs_closed'] = outbound.cleanup_browser(br)
