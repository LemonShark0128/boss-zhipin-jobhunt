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
import jd_page
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


def _brand_blocked(cfg, item, previous):
    """conv-08：命中 filters.brand_block 返回命中的模式，否则空串。公司名优先取列表项，退回记录。"""
    patterns = [p for p in ((cfg.get('filters') or {}).get('brand_block') or []) if isinstance(p, str) and p]
    brand = (item.get('brandName') or '').strip() or (previous.get('brand') or '').strip()
    if not patterns or not brand:
        return ''
    return hunt_filter.any_match(patterns, brand) or ''


# audit-01 U1：「需人工」会话又来新的 HR 消息时提醒一次并重新置顶（只看列表快照，不打开、不回复）
MANUAL_NEW_MAX_PER_HOP = 3


def _manual_seen_ts(row):
    """转人工后已看到的最新消息时间：last_seen.ts，没有就用 updated_ts；已提醒过的快照也算看过；
    用户取消置顶（= 处理完）的时刻之前的消息也算看过（11-06 21:30 真机误报：消息早于取消置顶仍被提醒）。"""
    def num(value):
        return value if isinstance(value, (int, float)) and not isinstance(value, bool) else None
    seen = num((row.get('last_seen') or {}).get('ts'))
    if seen is None:
        seen = num(row.get('updated_ts')) or 0
    pin = row.get('pin') if isinstance(row.get('pin'), dict) else {}
    handled = num(pin.get('done_ts')) if pin.get('done_by') == 'user_unpinned' else None
    return max(seen, num((row.get('manual_new_snap') or {}).get('ts')) or 0, handled or 0)


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


# conv-13c：BOSS 消息里的 <copy>…</copy> 等标记（可能是 &lt;copy&gt; 转义形式）只留里面的字
# 只认小写标签名与小写转义残片，避免误伤「R&D」「融资<A轮」这类正文
MARKUP_TAG = re.compile(r'</?[a-z][a-z0-9-]*(?:\s[^<>]*)?>')
MARKUP_TAIL = re.compile(r'(?:</?[a-z][a-z0-9-]*(?:\s[^<>]*)?|&(?:l|g|a|am|q|qu|quo|n|nb|nbs))$')


def _push_text(text):
    text = MARKUP_TAG.sub('', html.unescape(text or ''))
    lines = [line.strip() for line in text.splitlines()]
    joined = ' '.join(line for line in lines if line and line not in CARD_BUTTON_LINES)
    # 旧版本存的原话可能被截在半个标签处
    return MARKUP_TAIL.sub('', joined).rstrip()


# conv-13：需人工的会话在 BOSS 置顶，用户取消置顶即处理完
PHONE_CARD = re.compile(r'互换电话|交换电话|电话联系TA')
PIN_MAX_FAILURES = 3
PIN_DONE_STATUSES = frozenset(('closed', 'declined', 'complete'))


def _pin_text(text):
    text = _push_text(text)
    return 'HR 请求交换电话' if PHONE_CARD.search(text) else text[:40]


def _want_pin(record, kind, text, msg_ts, now_ts, result=None, line=None):
    """登记「要人看」；未完成的沿用（更新原话），已完成的重新登记。"""
    pin = record.get('pin')
    fields = {'reason': kind, 'text': _pin_text(text), 'msg_ts': msg_ts}
    if isinstance(pin, dict) and not pin.get('done_ts'):
        pin.update(fields)
    else:
        record['pin'] = dict(fields, want_ts=now_ts, pinned_ts=None, done_ts=None,
                             done_by=None, failures=0, pin_migrated_13b=True)
    if result is not None:
        registered = result.setdefault('_pin_reg', {}).setdefault(record['conv_key'], [])
        if line is not None:
            registered.append(line)


def _pin_open(record):
    pin = record.get('pin')
    return isinstance(pin, dict) and not pin.get('done_ts')


def _pin_wanted(record):
    """未完成、未置顶、失败未满 3 次。"""
    return (_pin_open(record) and not record['pin'].get('pinned_ts')
            and record['pin'].get('failures', 0) < PIN_MAX_FAILURES)


def _pin_waited(pin, now_ts):
    since = pin.get('msg_ts') or pin.get('want_ts') or now_ts
    hours = int(max(0, now_ts - since) // 3600)
    return '刚刚' if hours < 1 else '等了 %dh' % hours


def _pin_ref_ts(row):
    """conv-13b：判断是否在 lookback 内，优先看最后一条消息时间，没有再看 updated_ts。"""
    seen = (row.get('last_seen') or {}).get('ts')
    if isinstance(seen, (int, float)) and not isinstance(seen, bool) and seen > 0:
        return seen
    updated = row.get('updated_ts')
    return updated if isinstance(updated, (int, float)) and not isinstance(updated, bool) else None


def _pin_in_window(row, options, now_ts):
    ref = _pin_ref_ts(row)
    return ref is not None and 0 <= now_ts - ref <= options.get('lookback_hours', 72) * 3600


def _pin_migrate_13b(records, options, now_ts):
    """conv-13b 一次性修正（每条 pin 记 pin_migrated_13b）：conv-13 的 setTop(boss, true) 全部被接口拒，
    未置顶、未完成的失败计数清零以便重试；不在 lookback 内的撤掉（pin=None）。已置顶/已完成的只打标记。"""
    changed = False
    for row in records.values():
        pin = row.get('pin')
        if not isinstance(pin, dict) or pin.get('pin_migrated_13b'):
            continue
        changed = True
        if not pin.get('done_ts') and not pin.get('pinned_ts'):
            if not _pin_in_window(row, options, now_ts):
                row['pin'] = None
                continue
            pin['failures'] = 0
        pin['pin_migrated_13b'] = True
    return changed


def _pin_migrate(records, options, now_ts):
    """首次迁移补登：全部记录都没有 pin 字段时做一次，不推送。返回补登的键。"""
    if not records or any('pin' in row for row in records.values()):
        return []
    added = []
    for key, row in sorted(records.items(), key=lambda kv: kv[1].get('updated_ts') or 0):
        updated = row.get('updated_ts')
        # conv-13b：两类都限 lookback 内（按 last_seen.ts，没有再按 updated_ts）
        if not _pin_in_window(row, options, now_ts):
            continue
        recent_manual = row.get('status') == 'manual'
        paperclip = (row.get('job_ok') is False and bool(row.get('resume_asked'))
                     and row.get('status') not in conv_rules.TERMINAL)
        if not (recent_manual or paperclip):
            continue
        seen = row.get('last_seen') or {}
        want = updated if isinstance(updated, (int, float)) else now_ts
        _want_pin(row, 'manual' if recent_manual else 'resume_asked', seen.get('text') or '',
                  seen.get('ts'), want)
        added.append(key)
    return added


def _notify(kind, record, result, out, cfg=None, new_msgs=(), quiet=False):
    if record['notified'].get(kind):
        return
    record['notified'][kind] = True
    if quiet and kind == 'manual':
        # conv-07：同跳另有「📎 不过筛但要简历」推送，这里只记状态与计数，不重复推
        result[kind] += 1
        text = new_msgs[-1]['text'] if new_msgs else record['last_seen']['text']
        _want_pin(record, kind, text, _msg_ts(record, new_msgs), _hop_ts(result), result)
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
        if kind == 'manual':
            _want_pin(record, kind, text, _msg_ts(record, new_msgs), _hop_ts(result), result, line)
    result['notifications'].append(line)
    out.append(line)
    result[kind] += 1


def _msg_ts(record, new_msgs):
    if new_msgs and new_msgs[-1].get('ts'):
        return new_msgs[-1]['ts']
    return (record.get('last_seen') or {}).get('ts')


def _hop_ts(result):
    return result.get('_now_ts') or time.time()


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
    system_pats = conv_parse.system_patterns(cfg)  # audit-01 R3：配置的系统正则每处都生效
    if any(conv_parse.is_system_message(m, system_texts, system_pats) and pattern.search(m.get('text') or '')
           for m in history[indices[-1]+1:]):
        return None
    return card


def _resume_manual(row, result, out, now_ts):
    row['status'] = 'manual'
    if not row['notified']['manual']:
        row['notified']['manual'] = True
        line = '🙋 需人工 %s：对方发了简历请求卡片，请手动点「同意」' % _identity(row)
        _want_pin(row, 'resume_card', '对方发了简历请求卡片，请手动点「同意」',
                  _msg_ts(row, ()), now_ts, result, line)
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
        if gap and int(gap[1]) <= SS.min_gap_seconds(cfg) + 5:
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
                  owner_unknown=0, row_missing=0, out_of_view=0, out_of_view_own=0, revealed=0, revealed_processed=0, reveal_failed=0, declined=0, brand_blocked=0, pending_conversations=0, out_of_view_rejected=0, manual_new=0, pinned=0, pin_failed=0, pin_done=0, pin_backlog=0, notifications=[], plans=[], day_count=0,
                  daily_cap=options.get('daily_cap', 20), pending_verification=False)
    try:
        # safety-01：只有非对话的未核实（投递/旧 inbox/账本损坏）才停整跳；对话挂起只停它自己的会话
        SS.ledger_guard(cfg, scope='outbound')
    except RuntimeError as exc:
        # audit-01 R4：推送只放人话（不带命令/event_id），原始异常留在 runs.log 与 error 字段
        out.append('⚠️ 待核实 ' + str(exc))
        if str(exc).startswith('skip_ledger_corrupt'):
            line = '⛔ 发送流水文件损坏，对话本跳暂停（需要先修复流水）'
        else:
            line = '⛔ 有待核对的投递发送，对话本跳暂停（先确认那个 HR 有没有收到消息）'
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
    system_pats = conv_parse.system_patterns(cfg)  # audit-01 R3：配置的系统正则每处都生效
    resume_sent_pattern = re.compile(options.get(
        'resume_sent_pattern', conv_actions.DEFAULT_RESUME_SENT_PATTERN))
    now_ts = now.timestamp()
    path = os.path.join(cfg['paths']['workdir'], 'conversations.json')
    records = conv_store.load(path)
    result['_now_ts'] = now_ts
    pin_on = bool(options.get('pin_manual', False))
    pin_quota = options.get('pin_max_per_hop', 8)
    # conv-13：首次迁移补登积压（开关关时也补登，只是不置顶；不推送）
    migrated_13b = _pin_migrate_13b(records, options, now_ts)
    if (_pin_migrate(records, options, now_ts) or migrated_13b) and not dry:
        conv_store.save(path, records)
    pending_keys = SS.conv_pending(cfg)
    for key in sorted(pending_keys):
        label = _identity(records[key]) if key in records else key
        line = '⚠️ 待核实 %s（发送结果待核对，只停这个会话）' % label
        result['notifications'].append(line)
        out.append(line)
    result['pending_conversations'] = len(pending_keys)
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
        # conv-13：用户取消置顶 = 处理完；终态也销（不自动取消置顶）
        pin_closed = False
        for key, row in records.items():
            if not _pin_open(row):
                continue
            pin = row['pin']
            if row.get('status') in PIN_DONE_STATUSES:
                pin.update(done_ts=now_ts, done_by='status')
            elif (pin.get('pinned_ts') and key in items
                  and items[key].get('isTop') is not True and items[key].get('isTop') in (0, False)):
                pin.update(done_ts=now_ts, done_by='user_unpinned')
            else:
                continue
            result['pin_done'] += 1
            pin_closed = True
        if pin_closed and not dry:
            conv_store.save(path, records)
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
        manual_new = []
        for key, item in items.items():
            previous = records.get(key, {})
            snap = _snapshot(item)
            if (previous.get('status') == 'manual'
                    and (item.get('lastMessageInfo') or {}).get('fromSelf') is not True
                    and 0 <= now_ts-snap['ts'] <= options.get('lookback_hours', 72)*3600
                    and snap['ts'] > _manual_seen_ts(previous)
                    and key not in pending_keys
                    and not _brand_blocked(cfg, item, previous)):
                manual_new.append((snap['ts'], key, item, snap))
            if ((item.get('lastMessageInfo') or {}).get('fromSelf') is True
                    or not 0 <= now_ts-snap['ts'] <= options.get('lookback_hours', 72)*3600
                    or (snap == previous.get('last_seen') and previous.get('status') != 'ignored')
                    or previous.get('status') in conv_rules.TERMINAL
                    or key in pending_keys):
                continue
            blocked = _brand_blocked(cfg, item, previous)
            if blocked:
                # conv-08：屏蔽公司（与投递同一份 filters.brand_block）不打开、不揭示、不占名额，转人工推一次
                row = copy.deepcopy(previous) if previous else conv_store.new_record(
                    key, 'inbound', item.get('encryptJobId', ''), item.get('brandName', ''),
                    item.get('name', ''), '', now_ts)
                row['status'] = 'manual'
                row['note'] = '屏蔽公司，不自动处理'
                row['updated_ts'] = now_ts
                conv_store.add_action(row, now_ts, 'brand_block', blocked)
                result['brand_blocked'] += 1
                if not row['notified'].get('manual'):
                    row['notified']['manual'] = True
                    said = '' if conv_parse.is_system_message({'text': snap['text']}, system_texts, system_pats) else snap['text'][:30]
                    line = '🔒 屏蔽公司 %s｜%s：%s（不自动处理，需你手动看）' % (row['brand'], row['boss'], said)
                    result['notifications'].append(line)
                    out.append(line)
                if not dry:
                    records[key] = row
                    conv_store.save(path, records)
                continue
            indices = [i for i, row in enumerate(rows) if row.get('conv_key') == key]
            if not indices:
                out_of_view.append((snap['ts'], key, item, snap))
                continue
            if len(indices) == 1 and rows[indices[0]].get('receipt'):
                continue
            candidates.append((snap['ts'], key, item, snap))
        revealed_keys = set()
        initial_out_of_view = {entry[1] for entry in out_of_view}
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

        # conv-11：合并后最旧优先；同时间保留抓包中的顺序，揭示留到处理时。
        candidate_keys = {entry[1] for entry in candidates + out_of_view}
        candidates = [(_snapshot(item)['ts'], key, item, _snapshot(item))
                      for key, item in items.items() if key in candidate_keys]
        candidates.sort(key=lambda c: c[0])
        result['candidates'] = len(candidates)
        result['out_of_view'] = len(out_of_view)
        def normalize_own_text(text):
            return unicodedata.normalize('NFKC', ''.join(html.unescape(text).split()))

        own_texts = []
        if out_of_view or manual_new:
            for entry in SS.ledger_scan(cfg)[0].values():
                text = normalize_own_text(entry.get('text') or '')
                if text and not re.fullmatch(r'<[^<>]+>', text):
                    own_texts.append(text)
        notified_out_of_view = 0
        def notify_out_of_view(key, item, snap):
            nonlocal notified_out_of_view
            if (conv_parse.is_system_message({'text': snap['text']}, system_texts, system_pats)
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
            if conv_rules.classify(snap['text'], cfg) == 'reject':
                # conv-09：40 行外快照就是拒信 → 直接结束，不推 📭、不占 3 条推送名额
                result['out_of_view_rejected'] += 1
                if not dry:
                    row = copy.deepcopy(row)
                    row['status'] = 'closed'
                    row['out_of_view_snap'] = snap
                    row['last_seen'] = {'ts': snap['ts'], 'text': snap['text']}
                    row['updated_ts'] = now_ts
                    conv_store.add_action(row, now_ts, 'out_of_view_reject', 'closed')
                    records[key] = row
                    conv_store.save(path, records)
                return
            if notified_out_of_view >= 3:
                return
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

        def is_own_text(text):
            normalized = normalize_own_text(text)
            return len(normalized) >= 8 and any(own.startswith(normalized) for own in own_texts)

        for _, key, item, snap in sorted(manual_new, key=lambda entry: entry[0]):
            if result['manual_new'] >= MANUAL_NEW_MAX_PER_HOP:
                break
            if (conv_parse.is_system_message({'text': snap['text']}, system_texts, system_pats)
                    or resume_sent_pattern.search(snap['text']) or is_own_text(snap['text'])):
                continue
            row = records[key] if dry else copy.deepcopy(records[key])
            line = '🙋 需人工·新消息 %s：%s（转人工后对方又发了消息，需你手动看）' % (
                _identity(row), _push_text(snap['text'])[:40])
            result['manual_new'] += 1
            result['notifications'].append(line)
            out.append(line)
            if dry:
                continue
            row['manual_new_snap'] = snap
            conv_store.add_action(row, now_ts, 'manual_new', 'notified')
            _want_pin(row, 'manual_new', snap['text'], snap['ts'], now_ts, result, line)
            records[key] = row
            conv_store.save(path, records)

        pin_attempts = 0
        pin_tried = set()
        pinned_now = []
        pin_plan = []

        def pin_item(key, row):
            item = items.get(key) or {}
            source = item if all(item.get(k) for k in ('encryptJobId', 'name', 'brandName')) else {
                'encryptJobId': row.get('job_id'), 'name': row.get('boss'), 'brandName': row.get('brand')}
            return {k: source[k] for k in ('encryptJobId', 'name', 'brandName', 'friendId', 'securityId')
                    if source.get(k) not in (None, '')}

        def pin_failed(key, row, reason):
            pin = row['pin']
            pin['failures'] = pin.get('failures', 0) + 1
            conv_store.add_action(row, now_ts, 'pin', reason)
            result['pin_failed'] += 1
            if pin['failures'] == PIN_MAX_FAILURES:
                line = '⚠️ 置顶失败 %s，需你手动看' % _identity(row)
                result['notifications'].append(line)
                out.append(line)

        def attempt_pin(key, backlog=False):
            nonlocal pin_attempts
            row = records[key]
            if pin_attempts:
                time.sleep(2)
            pin_attempts += 1
            pin_tried.add(key)
            receipt = chat_dom.pin_conversation(br, tab, pin_item(key, row))
            if isinstance(receipt, dict) and receipt.get('ok') is True:
                row['pin']['pinned_ts'] = now_ts
                conv_store.add_action(row, now_ts, 'pin', 'already' if receipt.get('already') else 'pinned')
                result['pinned'] += 1
                if backlog:
                    result['pin_backlog'] += 1
                pinned_now.append(key)
            else:
                reason = receipt.get('reason') if isinstance(receipt, dict) else None
                pin_failed(key, row, reason if isinstance(reason, str) and reason else 'invalid_response')
            conv_store.save(path, records)

        def pin_now(key):
            # 本跳刚登记、仍在视窗的会话当场置顶
            if (dry or not pin_on or key not in result.get('_pin_reg', {}) or key in pin_tried
                    or key not in records or not _pin_wanted(records[key]) or pin_attempts >= pin_quota):
                return
            attempt_pin(key)

        jobs = None
        planned_daily = set(daily)
        occupied = 0
        for _, key, item, snap in candidates:
            if len(planned_daily if dry else daily) >= result['daily_cap']:
                break
            if occupied >= options.get('per_hop', 8):
                break
            rows = chat_dom.conversation_rows(br, tab)
            # Earlier reveals can also displace candidates from the initial window.
            if not any(row.get('conv_key') == key for row in rows):
                rows = reveal_rows(item, key)
                if rows is None:
                    failed_keys.add(key)
                    result['reveal_failed'] = len(failed_keys)
                    notify_out_of_view(key, item, snap)
                    continue
                revealed_keys.add(key)
                result['revealed'] += 1
            indices = [i for i, row in enumerate(rows) if row.get('conv_key') == key]
            if len(indices) == 1 and rows[indices[0]].get('receipt'):
                continue
            occupied += 1
            if len(indices) != 1:
                result['row_missing'] += 1
                continue
            index = indices[0]
            status, _, history = chat_dom.open_by_index(br, tab, index, conv_key=key)
            if status != 'ok' or not history:
                continue
            raw_history = history
            resume_evidence = next((m for m in history
                                    if conv_parse.is_system_message(m, system_texts, system_pats) and
                                    resume_sent_pattern.search(str(m.get('text') or ''))), None)
            history = [m for m in history if not conv_parse.is_system_message(m, system_texts, system_pats)]
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
                    pin_now(key)
                continue
            result['processed'] += 1
            # conv-12：本跳一开始在 40 行外、或本会话触发过揭示的，被处理都算「揭示后处理」
            if key in revealed_keys or key in initial_out_of_view:
                result['revealed_processed'] += 1
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
                        jd = jd_page.read_jd(br, jd_tab, cfg)
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
                        pin_now(key)
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
                        pin_now(key)
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
                'job_city': job.get('cityName') or '',
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
                if pin_on and (resume_asked or any(a['type'] == 'notify' and a.get('kind') == 'manual'
                                                   for a in ordered)):
                    pin_plan.append(key)
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
                    _want_pin(committed, 'resume_asked', resume_msgs[-1].get('text'),
                              resume_msgs[-1].get('ts'), now_ts, result, line)
                    result['notifications'].append(line)
                    out.append(line)
                    result['ignored_resume_asked'] += 1
            records[key] = committed
            conv_store.save(path, records)
            pin_now(key)
            daily = _daily_keys(records, now)
            if result['pending_verification']:
                break
        # conv-13：补置顶积压，按 want_ts 从旧到新；揭示失败不占名额
        backlog = sorted((row['pin'].get('want_ts') or 0, key) for key, row in records.items()
                         if _pin_wanted(row) and key not in pin_tried and key not in pending_keys)
        if pin_on and dry:
            for _, key in backlog:
                if len(pin_plan) >= pin_quota:
                    break
                if key not in pin_plan:
                    pin_plan.append(key)
            result['pin_plan'] = pin_plan[:pin_quota]
            for key in result['pin_plan']:
                out.append('🧪 计划置顶 %s' % key)
        elif pin_on and not result['pending_verification']:
            reveals = 0
            for _, key in backlog:
                if pin_attempts >= pin_quota or reveals >= pin_quota * 2:
                    break
                current = chat_dom.conversation_rows(br, tab)
                if sum(r.get('conv_key') == key for r in current) != 1:
                    reveals += 1
                    if reveal_rows(pin_item(key, records[key]), key) is None:
                        pin_tried.add(key)
                        pin_failed(key, records[key], 'reveal_failed')
                        conv_store.save(path, records)
                        continue
                attempt_pin(key, backlog=True)
        if pinned_now:
            registered = result.get('_pin_reg', {})
            drop = {line for key in pinned_now for line in registered.get(key, [])}
            open_count = sum(_pin_open(r) and bool(r['pin'].get('pinned_ts')) for r in records.values())
            block = ['📌 新置顶 %d 个（置顶待你处理共 %d）' % (len(pinned_now), open_count)]
            for key in pinned_now:
                row, pin = records[key], records[key]['pin']
                said = _push_text(pin.get('text'))
                said = '：' + said if said else ''
                block.append('  · %s%s（%s）' % (_identity(row), said, _pin_waited(pin, now_ts)))
            block = '\n'.join(block)
            notes, placed = [], False
            for line in result['notifications']:
                if line in drop:
                    if not placed:
                        notes.append(block)
                        placed = True
                    continue
                notes.append(line)
            if not placed:
                notes.append(block)
            result['notifications'] = notes
            out.append(block)
        result['day_count'] = len(daily)
        overview = ('💬 对话｜列表 {captured}（抓包 {captures}）｜候选 {candidates}｜处理 {processed}｜发简历 {resume_sent}｜'
                    '问第一轮 {round1}｜问第二轮 {round2}｜问齐 {complete}｜需人工 {manual}｜'
                    '不过筛 {ignored}｜回绝 {declined}｜row_missing {row_missing}'
                    + ('｜40行外 {out_of_view}' if result['out_of_view'] else '')
                    + ('｜屏蔽 {brand_blocked}' if result['brand_blocked'] else '')
                    + ('｜40行外拒信 {out_of_view_rejected}' if result['out_of_view_rejected'] else '')
                    + ('｜需人工新消息 {manual_new}' if result['manual_new'] else '')
                    + ('｜置顶 {pinned}' if result['pinned'] else '')
                    + '｜今日 {day_count}/{daily_cap}').format(**result)
        result['overview'] = overview
        out.append(overview)
        reveal_line = '💬 滚动定位｜revealed {revealed}｜揭示后处理 {revealed_processed}｜reveal_failed {reveal_failed}'.format(**result)
        if result.get('reveal_reasons'):
            reveal_line += '（%s）' % ', '.join('%s %d' % kv for kv in sorted(result['reveal_reasons'].items()))
        if result.get('rows_not_ready'):
            reveal_line += '｜rows_not_ready'
        out.append(reveal_line)
        if hours and now.hour == max(hours):
            counts = {state: sum(r['status'] == state for r in records.values())
                      for state in ('complete', 'manual', 'engaged', 'round2', 'ignored')}
            # conv-10：「需人工」只算近 lookback（默认 72h）内变成需人工的，记录状态不改
            window = options.get('lookback_hours', 72) * 3600
            counts['manual'] = sum(r['status'] == 'manual' and
                                   isinstance(r.get('updated_ts'), (int, float)) and
                                   0 <= now_ts - r['updated_ts'] <= window for r in records.values())
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
            counts['pinned_open'] = sum(_pin_open(r) and bool(r['pin'].get('pinned_ts'))
                                        for r in records.values())
            if pin_on or counts['pinned_open']:
                line += '｜📌 置顶待你处理 %d' % counts['pinned_open']
            result['summary_line'] = line
            out.append(line)
        return result
    finally:
        result.pop('_pin_reg', None)
        result.pop('_now_ts', None)
        if tab is not None and reveal_attempted:
            chat_dom.reset_list_scroll(br, tab)
        result['tabs_closed'] = CM.cleanup_browser(br)
