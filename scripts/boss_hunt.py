#!/usr/bin/env python3
"""BOSS直聘 求职助手（可移植版）：扫描 -> 按用户档案筛选 -> 发招呼 -> 盯收件箱。

所有个人化内容都在 config.json 里，代码不含任何个人经历/城市/关键词。

  python3 boss_hunt.py init                 # 生成 ~/.boss-hunt/config.json
  python3 boss_hunt.py validate             # 校验配置（不联网）
  python3 boss_hunt.py login-check          # 只读：确认浏览器里已登录
  python3 boss_hunt.py preflight            # 登录 + 招呼语开关 + 额度提示
  python3 boss_hunt.py scan                 # 抓岗位列表到 raw/
  python3 boss_hunt.py plan                 # 干跑：只报告会投谁，不发
  python3 boss_hunt.py greet  --job-id X    # 预览该岗位现写的招呼语（本地模型，可离线调）
  python3 boss_hunt.py send 2               # 真发（首跑建议 1-2 个）
  python3 boss_hunt.py inbox                # 盯新招呼 + 分类回复
  python3 boss_hunt.py run --send 5         # 扫描 + 发送 + 收件箱（定时任务用这个）
  python3 boss_hunt.py template --job-id X  # 预览渲染后的招呼语

配置查找顺序：--config PATH > $BOSS_HUNT_CONFIG > ~/.boss-hunt/config.json
"""
import argparse
import glob
import hashlib
import math
import json
import os
import re
import sys
import signal
import uuid
import unicodedata
from functools import wraps
from send_safety import (state_lock, ledger_send, ledger_guard, ledger_count,
                         ledger_latest, ledger_path, ledger_repair, ledger_scan)
from contextlib import contextmanager
import time
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import chat_dom
import chat_send as CS
import conv_actions as CA
import chrome_mcp as CM  # noqa: E402
import greet as G        # noqa: E402  # 按岗位 JD 现写招呼语（greet.py）
import reply as R        # noqa: E402  # Phase B：按 HR 消息回话（reply.py）
from conv_state import update_conv_state
from conv_parse import parse_message_owner, owner_parser_js
from send_safety import reply_operation, terminal_operation, confirms_new_bubble
import human as H        # noqa: E402  # 人工介入记录与话术沉淀（human.py）
import hunt_config as HC
import hunt_filter as HF
import hunt_schedule as HQ
import hunt_state as HS
import outbound as O





# --------------------------------------------------------------------------
# 配置
# --------------------------------------------------------------------------














# --------------------------------------------------------------------------
# 筛选
# --------------------------------------------------------------------------






























# --------------------------------------------------------------------------
# 记账
# --------------------------------------------------------------------------











































def locked_command(fn):
    @wraps(fn)
    def wrapped(args):
        cfg = HC.load_config(HC.config_path(args))
        try:
            with state_lock(cfg['paths']['state']):
                st = HS.load_state(cfg)
                ledger_guard(cfg)
                HS.migrate_ledger(cfg, st)
                return fn(args)
        except (ValueError, RuntimeError, OSError) as exc:
            if want_json(args):
                emit_json({'cmd': args.cmd, 'ok': False, 'error': str(exc)})
            else:
                print(str(exc), file=sys.stderr)
            return 1
    return wrapped


# --------------------------------------------------------------------------
# 招呼语
# --------------------------------------------------------------------------




# --------------------------------------------------------------------------
# 浏览器动作
# --------------------------------------------------------------------------

























INTERVIEW_DEFAULT = ("您好，方便安排线上面试吗？我这边时间需要先确认一下，"
                     "您先给几个可选时段，我和您确认后再回复您，谢谢！")


def classify(msg, cfg):
    pats = (cfg.get("inbox") or {}).get("patterns") or {}
    rej = pats.get("reject") or r"不合适|不匹配|不符合|已招满|招满了|暂不考虑|不太适合|有更合适的人选|已招到"
    itv = pats.get("interview") or r"面试|面谈|复试|初试|约个?时间|什么时候方便|线上|线下|面试官"
    itr = pats.get("interested") or r"感兴趣|有兴趣|简历|方便沟通|聊聊|沟通一下|想了解|加个微信|加微信"
    for label, p in (("reject", rej), ("interview", itv), ("interested", itr)):
        if re.search(p, msg or ""):
            return label
    return "other"


def load_rejects(cfg):
    path = os.path.join(cfg["paths"]["workdir"], "rejects.json")
    if not os.path.exists(path):
        return {}
    with open(path, encoding="utf-8") as f:
        rows = json.load(f)
    if not isinstance(rows, dict) or any(not isinstance(v, dict) for v in rows.values()):
        raise ValueError("rejects.json 必须是对象，拒绝覆盖损坏的数据")
    return rows


def save_rejects(cfg, rows):
    HS.atomic_json(os.path.join(cfg['paths']['workdir'], 'rejects.json'), rows)




def message_data(it):
    info = it.get("lastMessageInfo") or {}
    txt = re.sub(r"\s+", " ", str(info.get("showText") or "")).strip()
    try:
        ts = float(info.get("msgTime") or 0) / 1000
    except (ValueError, TypeError):
        ts = 0
    return txt, ts


def greeting_matches(txt, greeting):
    a, b = re.sub(r"\s+", "", txt or ""), re.sub(r"\s+", "", greeting or "")
    # 列表文本可能截断，至少 14 字相互包含才视作自己的招呼。
    return len(a) >= 14 and len(b) >= 14 and (a[:14] in b or b[:14] in a)


def snapshot_message_owner(it):
    """Adapt explicit API side evidence to the same canonical marker parser."""
    info = it.get('lastMessageInfo') or {}
    classes = info.get('classes') or ''
    if isinstance(info.get('fromSelf'), bool):
        classes += ' ' + ('item-mine' if info['fromSelf'] else 'item-friend')
    return parse_message_owner(classes, info.get('showText'))


def last_is_ours(cfg, row, it, greeting=""):
    return {'mine': True, 'friend': False}.get(snapshot_message_owner(it))


def rejection_reason(txt, cfg):
    rules = (cfg.get("inbox") or {}).get("reject_reasons")
    if rules is None:
        rules = {"已招满": "已招满|招满了|已招到|岗位关闭", "学历": "学历|学位|证书",
                 "薪资": "薪资|薪酬|工资|预算", "行业不符": "行业|跨行",
                 "经验不符": "经验|年限|资历"}
    for reason, pattern in rules.items():
        if re.search(pattern, txt):
            return reason
    return "无理由婉拒"


def collect_rejects(cfg, st, items, jobs=None, now=None):
    with state_lock(os.path.join(cfg['paths']['workdir'], 'rejects.json'), reentrant=True):
        return _collect_rejects(cfg, st, items, jobs, now)


def _collect_rejects(cfg, st, items, jobs=None, now=None):
    """本地采集器；items 来自已加载的会话快照，不发消息。"""
    now = now or HC.now_local(cfg)
    jobs = jobs or {}
    rows = load_rejects(cfg)
    before = json.dumps(rows, sort_keys=True, ensure_ascii=False)
    sent = st.get("sent") or {}
    greetings = {}
    cache_path = os.path.join(cfg["paths"]["workdir"], "greet_cache.json")
    if os.path.exists(cache_path):
        with open(cache_path, encoding="utf-8") as f:
            for key, row in json.load(f).items():
                greetings.setdefault(key.split("|", 1)[0], []).append(row.get("text") or "")

    def record(jid, it, kind):
        key = chat_dom.source_conversation_key(it)
        # A snapshot may use a pair while DOM uses an id. Only exact brand/HR
        # pairs may join these namespaces; no company substring fallback.
        matches = [k for k, v in st.get('conv_state', {}).items()
                   if v.get('job_id') == jid or (chat_dom.conversation_norm(v.get('brand')) == chat_dom.conversation_norm(it.get('brandName'))
                   and chat_dom.conversation_norm(v.get('boss')) == chat_dom.conversation_norm(it.get('name'))
                   and v.get('brand') and v.get('boss'))]
        if len(matches) == 1:
            key = matches[0]
        aliases = [k for k, v in rows.items() if key and v.get('conv_key') == key]
        old_key = jid if jid in rows else (aliases[0] if len(aliases) == 1 else jid)
        old = rows.get(old_key)
        if old and (old.get("kind") == "explicit" or kind != "explicit"):
            return
        applied = sent.get(jid) or {}
        job = jobs.get(jid) or {}
        txt, ts = message_data(it)
        if old_key != jid:
            rows.pop(old_key, None)
        rows[jid] = {"conv_key": key, "brand": applied.get("brand") or job.get("brandName") or it.get("brandName") or "",
                     "title": applied.get("name") or job.get("jobName") or "",
                     "grade": applied.get("grade") or (st.get("grade_cache", {}).get(jid) or {}).get("grade") or "unknown",
                     "applied_ts": applied.get("applied_ts") or applied.get("ts") or "",
                     "detected_ts": (old or {}).get("detected_ts") or now.strftime("%Y-%m-%d %H:%M"),
                     "kind": kind, "raw": txt,
                     "last_out_ts": ts if kind == 'no_reply' and ts else '',
                     "stale_hours": max(0, (now.timestamp() - ts) / 3600) if kind == 'no_reply' and ts else '',
                     "attribution": "our_apply" if jid in sent else ("hr_initiated" if jid in items else "unknown"),
                     "reason": "投递后72h无回应" if kind == "no_reply" else rejection_reason(txt, cfg),
                     "target": applied.get("target") or (HQ.target_of(cfg, job) if job else "unknown")}
        if old and kind == "explicit":
            rows[jid]["explicit_ts"] = now.strftime("%Y-%m-%d %H:%M")

    for jid, it in items.items():
        txt, ts = message_data(it)
        if last_is_ours(cfg, {}, it) is not False or not txt or not ts or classify(txt, cfg) != "reject":
            continue
        own = greeting_matches(txt, (sent.get(jid) or {}).get("greet_text"))
        own = own or any(greeting_matches(txt, t) for t in greetings.get(jid, []))
        if not own:
            record(jid, it, "explicit")
    for jid, row in sent.items():
        applied = HC.parse_local_ts(row.get("applied_ts") or row.get("ts"), cfg)
        if not applied or (now - applied).total_seconds() < 72 * 3600:
            continue
        it = items.get(jid)
        if it is None and row.get("brand"):
            # 换 id 时只允许唯一公司匹配；同公司多岗位/多会话一律无法判断。
            same = [v for v in items.values() if v.get("brandName") == row["brand"]]
            applications = [v for v in sent.values() if v.get("brand") == row["brand"]]
            if len(same) == len(applications) == 1:
                it = same[0]
        if it and any(last_is_ours(cfg, row, it, text) for text in greetings.get(jid, [""])):
            record(jid, it, "no_reply")
    if json.dumps(rows, sort_keys=True, ensure_ascii=False) != before:
        path = os.path.join(cfg["paths"]["workdir"], "rejects.json")
        save_rejects(cfg, rows)
    return rows




def collect_stale_rejects(cfg, st, now=None):
    now = time.time() if now is None else now
    path = os.path.join(cfg['paths']['workdir'], 'rejects.json')
    with state_lock(path, reentrant=True):
        rows = load_rejects(cfg)
        changed = False
        for key, state in st.get('conv_state', {}).items():
            if state.get('status') != 'stale' or state.get('ts_precision_coarse'):
                continue
            jid = state.get('job_id') or ''
            # Deduplicate by stable conversation identity, including old job rows.
            old = rows.get(jid) if jid else None
            aliases = [k for k, r in rows.items() if r.get('conv_key') == key]
            if (old and old.get('kind') in {'explicit', 'stale_no_reply'}) or any(
                    rows[k].get('kind') in {'explicit', 'stale_no_reply'} for k in aliases):
                continue
            for alias in aliases:
                rows.pop(alias)
            applied = st.get('sent', {}).get(jid, {})
            rows[jid or key] = dict(brand=state.get('brand') or applied.get('brand', ''),
                title=state.get('title') or applied.get('name', ''),
                grade=applied.get('grade') or state.get('grade', ''),
                attribution='our_apply' if jid in st.get('sent', {}) else 'hr_initiated',
                target=applied.get('target') or state.get('target', ''),
                applied_ts=applied.get('applied_ts') or applied.get('ts', ''),
                last_out_ts=state.get('last_ts'), stale_hours=state.get('stale_hours', 0),
                detected_ts=datetime.fromtimestamp(now, HC.cfg_tz(cfg)).strftime('%Y-%m-%d %H:%M'),
                reason='我已回复后72h无回应', kind='stale_no_reply', conv_key=key)
            changed = True
        if changed:
            save_rejects(cfg, rows)
        return rows


def unanswered_report(st, now=None, hours=72, limit=5):
    now = time.time() if now is None else now
    items = {k: [] for k in ('waiting_me', 'waiting_them', 'stale', 'unknown', 'pending_verify')}
    for key, state in st.get('conv_state', {}).items():
        row = dict(state, conv_key=key)
        status = row.get('status', 'unknown')
        if status in ('waiting_them', 'stale'):
            row['wait_hours'] = max(0, (now - row.get('last_ts', now)) / 3600)
            status = 'stale' if row['wait_hours'] >= hours and not row.get('ts_precision_coarse') else 'waiting_them'
        row['status'] = status
        items.get(status, items['unknown']).append(row)
    for rows in items.values():
        rows.sort(key=lambda r: (-r.get('last_ts', 0), r['conv_key']))
    return dict(ok=True, counts={k: len(v) for k, v in items.items()},
                items={k: v[:max(0, limit)] for k, v in items.items()})


def cmd_unanswered(args):
    # Read only the state file; no ledger migration, logging or browser setup.
    path = HC.config_path(args)
    with open(path, encoding='utf-8') as f:
        cfg = json.load(f)
    state_path = (cfg.get('paths') or {}).get('state') or os.path.join(
        (cfg.get('paths') or {}).get('workdir') or os.path.join(os.path.dirname(path), 'data'), 'state.json')
    try:
        with open(os.path.expanduser(state_path), encoding='utf-8') as f:
            st = json.load(f)
    except FileNotFoundError:
        st = {}
    if not isinstance(st, dict) or not isinstance(st.get('conv_state', {}), dict):
        raise ValueError('invalid conv_state')
    data = unanswered_report(st, hours=args.hours, limit=args.limit)
    if args.unknown:
        data['items'] = {'unknown': data['items']['unknown']}
    if want_json(args):
        print(json.dumps(data, ensure_ascii=False))
    else:
        c = data['counts']
        print('待我回 %d ｜等对方 %d ｜超时 %d（≥%gh）｜待核对 %d ｜读取待验证 %d' % (c['waiting_me'], c['waiting_them'], c['stale'], args.hours, c['unknown'], c['pending_verify']))
        for status, label in [('waiting_me', '待我回'), ('waiting_them', '等我方之后对方回'), ('stale', '超时未回'), ('unknown', '待核对'), ('pending_verify', '读取待验证')]:
            if status not in data['items']:
                continue
            print(label + '：')
            for r in data['items'][status]:
                print('  · %s｜%s｜%s%s' % (r.get('brand', ''), r.get('title', ''), r.get('last_text', ''),
                      ('｜已等 %.1fh' % r['wait_hours']) if 'wait_hours' in r else ''))
    return 0


def read_status_message(br, tab, row):
    rows = chat_dom.conversation_rows(br, tab)
    target = chat_dom.select_conversation(rows, conv_key=row['conv_key'])
    status, _, _ = chat_dom.open_by_index(br, tab, rows.index(target), conv_key=row['conv_key'])
    if status != 'ok':
        raise ValueError('unknown_conversation')
    data = br.json(tab, """(() => {
      const panel=document.querySelector('.chat-conversation,.chat-content,.chat-box');
      const header=panel && panel.querySelector('.chat-title');
      const es=panel ? [...panel.querySelectorAll('.message-item')] : [];
      const el=es[es.length-1], content=el && el.querySelector('.message-content');
      const raw=el && (el.getAttribute('data-ts') || el.getAttribute('data-time') || el.getAttribute('data-timestamp'));
      const stamp=Number(raw) || Date.parse(raw) / 1000 || 0;
      return {header:header ? header.innerText : '',
        classes:el ? el.className : '',
        text:content ? content.innerText : '', seq:es.length,
        ts_epoch:Math.round(stamp > 1e12 ? stamp : stamp*1000),
        ts:stamp > 1e12 ? stamp/1000 : stamp};
      })()""", default={}) or {}
    data['owner'] = parse_message_owner(data.get('classes'), data.get('text'))
    data['side'] = {'mine':'me', 'friend':'them'}.get(data['owner'])
    header = chat_dom.conversation_norm(data.get('header'))
    names = [chat_dom.conversation_norm(row.get(k)) for k in ('brand', 'boss')]
    if not any(len(n) >= 2 and n[:3] in header for n in names):
        raise ValueError('unknown_conversation: header mismatch')
    # Snapshot time can supply a clock after DOM alone establishes ownership.
    if not data.get('ts') and row.get('last_ts'):
        data['ts'] = row['last_ts']
        data['ts_epoch'] = int(row['last_ts'] * 1000)
    # Without a usable message time we cannot establish a 72h clock.
    if not data.get('ts'):
        data['side'] = None
        data['owner'] = 'unknown'
    return data


def scan_conv_states(cfg, br, tab, st, now=None, items=None):
    now = time.time() if now is None else now
    states = st.setdefault('conv_state', {})
    result = dict(checked=0, failed=0, skipped_failures=0, deadline=False)
    try:
        HQ.check_deadline(cfg)
        rows = chat_dom.conversation_rows(br, tab)
        enriched = []
        for row in rows:
            row = dict(row)
            matches = [(jid, it) for jid, it in (items or {}).items()
                       if (chat_dom.source_conversation_key(it) == row.get('conv_key') or (
                           row.get('brand') and row.get('boss') and
                           chat_dom.conversation_norm(row['brand']) == chat_dom.conversation_norm(it.get('brandName')) and
                           chat_dom.conversation_norm(row['boss']) == chat_dom.conversation_norm(it.get('name'))))]
            if len(matches) == 1:
                jid, item = matches[0]
                ts = (item.get('lastMessageInfo') or {}).get('msgTime') or item.get('lastTS') or 0
                row.update(job_id=jid, last_ts=float(ts)/1000)
            enriched.append(row)
        rows = enriched
        unique = {}
        for row in rows:
            key = row.get('conv_key')
            if key:
                unique.setdefault(key, []).append(row)
        rows = [v[0] for v in unique.values() if len(v) == 1]
        eligible = []
        for row in rows:
            prev = states.get(row['conv_key'], {})
            # Snapshot msgTime advancing past the last check invalidates cooldown.
            if row.get('last_ts', 0) > prev.get('snapshot_msg_ts', prev.get('checked_ts', prev.get('last_ts', 0))):
                prev.update(failures=0, retry_after_epoch=0)
            failures = prev.get('failures', 0)
            if failures >= 3 and not prev.get('retry_after_epoch'):
                delay = 7200 if failures >= 12 else 1800 if failures >= 6 else 600
                prev.update(retry_after_epoch=prev.get('checked_ts', now) + delay, status='pending_verify')
                states[row['conv_key']] = prev
            if now < prev.get('retry_after_epoch', 0):
                prev['status'] = 'pending_verify'
                result['skipped_failures'] += 1
                continue
            eligible.append(row)
        rows = eligible
        def priority(row):
            prev = states.get(row['conv_key'], {})
            group = {'waiting_me': 0, 'waiting_them': 1}.get(prev.get('status'), 2)
            return (group, prev.get('checked_ts', 0) if group < 2 else -max(prev.get('last_ts', 0), row.get('last_ts', 0)))
        rows.sort(key=priority)
        for row in rows[:max(0, int((cfg.get('inbox') or {}).get('status_scan_max', 20)))]:
            HQ.check_deadline(cfg)
            key = row['conv_key']; prev = states.get(key, {})
            try:
                message = read_status_message(br, tab, row)
                current = update_conv_state(prev, message, now, cfg)
                current.update(checked_ts=now, failures=0, retry_after_epoch=0,
                               snapshot_msg_ts=row.get('last_ts', 0))
                current.pop('error', None)
                current.update({k: row[k] for k in ('brand', 'boss', 'title', 'job_id') if row.get(k)})
                states[key] = current
                result['checked'] += 1
            except TimeoutError:
                raise
            except Exception as exc:
                failures = prev.get('failures', 0) + 1
                delay = (7200 if failures >= 12 else 1800 if failures >= 6 else 600) if failures >= 3 else 0
                states[key] = dict(prev, checked_ts=now, failures=failures, error=str(exc),
                    status='pending_verify', retry_after_epoch=now+delay if delay else 0,
                    snapshot_msg_ts=row.get('last_ts', 0))
                states[key].update({k:row[k] for k in ('brand','boss','title','job_id') if row.get(k)})
                result['failed'] += 1
    except TimeoutError:
        result['deadline'] = True
    return result


def reject_report(cfg, days=30, now=None):
    now = now or HC.now_local(cfg)
    cutoff = now - timedelta(days=max(0, days))
    rows = []
    for jid, row in load_rejects(cfg).items():
        dt = HC.parse_local_ts(row.get("detected_ts"), cfg)
        if dt and cutoff <= dt <= now:
            rows.append(dict(row, job_id=jid))
    result = {"total": len(rows), "days": days, "items": rows}
    for field in ("kind", "attribution", "reason", "grade", "target"):
        counts = {}
        for row in rows:
            key = row.get(field) or "unknown"
            counts[key] = counts.get(key, 0) + 1
        result["by_" + field] = counts
    return result


def daily_summary(cfg, st=None):
    """只读取本地状态；不会调用浏览器或推送服务。"""
    st = HS.load_state(cfg) if st is None else st
    today = HC.stamp(cfg, "%Y-%m-%d")
    same_day = st.get("day") == today
    rows = load_rejects(cfg)
    fresh = [r for r in rows.values()
             if (r.get("explicit_ts") or r.get("detected_ts") or "").startswith(today)]
    explicit = [r for r in fresh if r.get("kind") == "explicit"]
    no_reply = sum(1 for r in fresh if r.get("kind") == "no_reply")
    stale_no_reply = sum(1 for r in fresh if r.get("kind") == "stale_no_reply")
    reasons = {}
    for row in explicit:
        reason = row.get("reason") or "无理由婉拒"
        reasons[reason] = reasons.get(reason, 0) + 1
    sent = int(st.get("day_count") or 0) if same_day else 0
    by_grade = dict(st.get("day_by_grade") or {}) if same_day else {}
    by_grade = dict({"A": 0, "B": 0}, **by_grade)
    pending = len(st.get("pending") or {})
    opened = sum(1 for row in (st.get("opened_no_greet") or {}).values()
                 if (row.get("ts") or "").startswith(today))
    return {"day": today, "sent": sent, "daily_cap": int((cfg.get("sending") or {}).get("daily_cap") or 0),
            "by_grade": by_grade, "rejects": len(explicit), "no_reply": no_reply, "stale_no_reply": stale_no_reply,
            "cumulative": len(rows), "reason_breakdown": reasons, "pending": pending,
            "opened_no_greet": opened, "activity": bool(sent or fresh or pending or opened)}


def summary_lines(data):
    if not data["activity"]:
        return ["今天没有活动"]
    grades = " · ".join("%s %d" % (k, v) for k, v in data["by_grade"].items())
    lines = ["📊 今天小结", "投递 %d/%d（%s）" % (data["sent"], data["daily_cap"], grades),
             "被「不合适」%d 个｜72h 无回复 %d 个｜累计 %d" % (data["rejects"], data["no_reply"], data["cumulative"])]
    lines.append("投递无回复 %d｜沟通后无回复 %d" % (data["no_reply"], data.get("stale_no_reply", 0)))
    if data["reason_breakdown"]:
        lines.append("原因：" + "、".join("%s %d" % (k, v) for k, v in data["reason_breakdown"].items()))
    lines.append("待你确认 %d 条" % data["pending"])
    if data.get("opened_no_greet"):
        lines.append("已开会话未发招呼 %d 个" % data["opened_no_greet"])
    return lines


def cmd_summary(args):
    cfg = HC.load_config(HC.config_path(args))
    data = dict(daily_summary(cfg), cmd="summary", ok=True)
    if want_json(args):
        emit_json(data)
    else:
        print("\n".join(summary_lines(data)))
    return 0


def cmd_rejects(args):
    cfg = HC.load_config(HC.config_path(args))
    data = dict(reject_report(cfg, args.days), cmd="rejects", ok=True)
    if want_json(args):
        emit_json(data)
    else:
        print("近 %d 天拒绝/无回复：%d" % (args.days, data["total"]))
        for field in ("kind", "attribution", "reason", "grade", "target"):
            print(field + "：" + json.dumps(data["by_" + field], ensure_ascii=False))
    return 0


def send_in_chat(br, tab, msg, marker, conv_key=None, cfg=None, cmd='auto_reply', job_id='', expected_history=None, new_turn=False, op_id=None, fact_context=None):
    key = conv_key or br.__dict__.get('_boss_sessions', {}).get(tab)
    if not key or not cfg:
        return 'error: unknown_conversation'
    before = chat_dom.active_conversation(br, tab)
    if before.get('conv_key') != key:
        return 'error: unknown_conversation'
    if any(m.get('who') not in {'mine', 'friend'} for m in before.get('messages') or []):
        return 'send_aborted:owner_unknown'
    with state_lock(cfg['paths']['state'], reentrant=True):
        ledger_guard(cfg, verify=lambda row: confirms_new_bubble(row, before))
    trigger, operation = reply_operation(key, before.get('messages') or [], op_id)
    if cmd == 'reply-to' and terminal_operation(cfg, operation):
        return 'duplicate_op'
    if cmd in {'reply-to', 'auto_reply'} and (before.get('messages') or [{}])[-1].get('who') == 'mine' and not new_turn:
        return 'last_turn_is_ours'
    if expected_history is not None and chat_dom.history_fingerprint(before.get('messages') or []) != chat_dom.history_fingerprint(expected_history):
        return 'stale_draft'
    binding_cfg = cfg
    if fact_context is not None:
        if (fact_context.get('conv_key') != key or fact_context.get('op_id') != operation
                or fact_context.get('text_sha256') != hashlib.sha256(msg.encode()).hexdigest()
                or fact_context.get('draft_fingerprint') != chat_dom.history_fingerprint(before.get('messages') or [])):
            return 'stale_draft'
        import copy
        binding_cfg = copy.deepcopy(cfg)
        binding_cfg.setdefault('user', {}).setdefault('greeting', {})['facts'] = list(fact_context['facts'])
    good, reasons = R.check(msg, binding_cfg)
    from message_policy import safe_interview_reply
    if cmd == 'interview_reply':
        if not safe_interview_reply(msg, cfg):
            return 'manual_required'
    elif R.commitment(msg):
        return 'manual_required'
    if cmd in {'auto_reply', 'reply-to'} and not good:
        return 'manual_required'
    send_meta = {}
    def send():
        f = CS.type_into_chat(br, tab, msg)
        if f.get('ok') is not True:
            return 'fill_err'
        clicked = chat_dom.atomic_chat_click(br, tab, key, msg, chat_dom.normalized_history(before.get('messages') or []))
        outcome = CS.click_outcome(clicked)
        if outcome == 'aborted':
            if clicked['reason'] == 'stale_draft':
                return 'stale_draft'
            return 'send_aborted:' + clicked['reason']
        if outcome == 'clicked' and clicked.get('conv_key') != key:
            return 'pending_verification'
        send_meta['via'] = 'confirm_only' if outcome == 'pending' else clicked.get('via')
        try:
            confirmed = 'pending_verification'
            for attempt in range(3 if outcome == 'pending' else 1):
                time.sleep(2.0)
                after = chat_dom.active_conversation(br, tab)
                if after.get('conv_key') != key:
                    break
                confirmed = CS.confirm_new_bubble(
                    [m.get('id') for m in before.get('messages') or []], after.get('messages') or [],
                    hashlib.sha256(msg.encode()).hexdigest())
                if confirmed == 'sent':
                    break
            HC.trace('after snapshot msgs=%d ids=%s' % (
                len(after.get('messages') or []),
                [m.get('id') for m in after.get('messages') or []][-3:]))
            return 'replied' if confirmed == 'sent' else confirmed
        except Exception as e:
            HC.trace('confirm error: %r' % e)
            return 'pending_verification'

    return ledger_send(cfg, cmd, key, job_id, msg, send,
                       {'meta': send_meta, 'trigger': trigger, 'op_id': operation if cmd == 'reply-to' else '',
                        'message_seq': len(before.get('messages') or []) + 1,
                        'msg_count': len(before.get('messages') or []),
                        'last_message_id': (before.get('messages') or [{}])[-1].get('id') or '',
                        'click_ts_epoch': int((cfg.get('_clock') or time.time)() * 1000),
                        'reason': 'explicit_new_turn' if new_turn else 'inbound_reply',
                        'draft_fingerprint': chat_dom.history_fingerprint(expected_history)
                        if expected_history is not None else ''})


def reply_in_chat(br, tab, brand, msg, marker, cfg=None, boss=None, job_id='', cmd='interview_reply', conv_key=None):
    if not conv_key:
        return 'error: unknown_conversation'
    br.nav(tab, chat_dom.CHAT_URL, wait=5)
    rows = chat_dom.conversation_rows(br, tab)
    try:
        target = chat_dom.select_conversation(rows, conv_key=conv_key)
    except ValueError as exc:
        return str(exc)
    status, _, messages = chat_dom.open_by_index(br, tab, rows.index(target), conv_key=target['conv_key'])
    if status != 'ok':
        return status
    if not messages:
        return 'stale_draft'
    last = messages[-1]
    if cmd == 'interview_reply' and (last.get('who') != 'friend' or classify(last.get('text'), cfg) != 'interview'):
        return 'stale_draft'
    if cmd == 'followup' and last.get('who') != 'mine':
        return 'stale_draft'
    return send_in_chat(br, tab, msg, marker, target['conv_key'], cfg, cmd, job_id,
                        expected_history=messages)


def inbox(cfg, br, tab, st, out):
    try:
        ledger_guard(cfg)
    except RuntimeError as exc:
        out.append(str(exc))
        return {'pending_verification': True, 'error': str(exc), 'auto_replied': [],
                'replied_ok': [], 'followed_up': []}
    nav = br.nav(tab, "https://www.zhipin.com/web/geek/chat", wait=5)
    if cfg.get("_deadline") and nav and nav.get("isError"):
        raise RuntimeError("inbox_navigation_failed")
    items, valid_pages = {}, 0
    for i in range(4):
        HQ.check_deadline(cfg)
        r = br.network(tab, "friend/getG", index=i, max_body=400000)
        d = CM.parse_network_json(r.get("text") or "")
        if cfg.get("_deadline") and r.get("isError"):
            raise RuntimeError("inbox_network_failed")
        if not d:
            continue
        if d.get("code") != 0 or not isinstance((d.get("zpData") or {}).get("result"), list):
            if cfg.get("_deadline"):
                raise RuntimeError("inbox_invalid_response")
            continue
        valid_pages += 1
        for it in ((d.get("zpData") or {}).get("result") or []):
            if isinstance(it, dict) and it.get("encryptJobId"):
                items[it["encryptJobId"]] = it
    if cfg.get("_deadline") and not valid_pages:
        raise RuntimeError("inbox_no_valid_response")
    status_scan = scan_conv_states(cfg, br, tab, st, items=items)
    # Only fresh DOM ownership or an explicit API side can resolve a snapshot.
    ownership = {}
    for jid, item in items.items():
        key = chat_dom.source_conversation_key(item)
        txt, ts = message_data(item)
        current = st.setdefault('conv_state', {}).get(key, {})
        side = {'mine':True, 'friend':False}.get(snapshot_message_owner(item))
        if (current.get('last_text') == txt and current.get('last_ts') == ts
                and current.get('status') not in {'unknown','pending_verify'}
                and current.get('last_side') in {'me', 'them'}):
            side = current['last_side'] == 'me'
        ownership[jid] = side
        if side is None:
            st['conv_state'][key] = dict(current, status=('pending_verify' if current.get('status') == 'pending_verify' else 'unknown'), owner='unknown', last_side=None,
                last_ts=ts, last_text=txt, job_id=jid, brand=item.get('brandName', ''),
                boss=item.get('name', ''))
    # Attach known job metadata only through the same stable conversation key.
    for jid, item in items.items():
        state = st.get('conv_state', {}).get(chat_dom.source_conversation_key(item))
        if state is not None:
            state.update(job_id=jid, brand=item.get('brandName', ''), boss=item.get('name', ''))
    collect_stale_rejects(cfg, st)
    if status_scan['failed'] or status_scan['skipped_failures']:
        out.append('会话状态检查 %d ｜失败 %d ｜连续失败跳过 %d' % (status_scan['checked'], status_scan['failed'], status_scan['skipped_failures']))
    HS.save_state(cfg, st)
    report = unanswered_report(st, hours=float((cfg.get('inbox') or {}).get('stale_hours', 72)))
    if status_scan['deadline']:
        counts = report['counts']
        if any(counts.values()):
            out.append('待我回 %d ｜等对方 %d ｜超时 ≥%gh %d（详见 unanswered）' % (
                counts['waiting_me'], counts['waiting_them'], float((cfg.get('inbox') or {}).get('stale_hours', 72)), counts['stale']))
        return dict(status_scan=status_scan, unanswered=report, messages_total=len(items))
    lookback = float((cfg.get("inbox") or {}).get("lookback_hours", 72))
    max_listed = int((cfg.get("inbox") or {}).get("max_listed", 5))
    sys_pat = ((cfg.get("inbox") or {}).get("patterns") or {}).get("system") or \
        r"附件简历.{0,20}已发送|已发送给对方|对方已同意|简历已收到|感谢投递|自动回复|系统消息|投递成功|您的附件简历"
    now_ms = int(time.time() * 1000)
    jobs = {j["encryptJobId"]: j for j in HF.load_jobs(cfg)}
    sent_jids = st.get("sent") or {}
    verified_items = {j: dict(i, lastMessageInfo=dict(i.get('lastMessageInfo') or {},
        fromSelf=ownership[j])) for j,i in items.items() if ownership.get(j) is not None}
    rejects = collect_rejects(cfg, st, verified_items, jobs)
    st.setdefault("pending", {})

    fresh_hits, interest, interviews, reply_cands, manual_reply = [], [], [], [], []
    replied_ok, reply_failed, followed_up = [], [], []
    n_fresh = 0
    owner_unknown = sum(v is None for v in ownership.values())
    for jid, it in items.items():
        HQ.check_deadline(cfg)
        info = it.get("lastMessageInfo") or {}
        ts = info.get("msgTime") or it.get("lastTS") or 0
        if not ts or (now_ms - ts) > lookback * 3600 * 1000:
            continue
        if ownership.get(jid) is True:
            continue
        if ownership.get(jid) is None:
            continue  # Already retained in conv_state as unknown, never dropped.
        n_fresh += 1
        txt = re.sub(r"\s+", " ", info.get("showText") or "").strip()
        if re.search(sys_pat, txt):
            continue
        job = jobs.get(jid) or {}
        verdict = HF.judge(job, cfg)[0] if job else "pass"
        sc = HF.score(job, cfg) if job else 0
        label = classify(txt, cfg)
        who = "%s｜%s｜%s" % (it.get("brandName") or "?", job.get("jobName") or txt[:20],
                              job.get("salaryDesc") or "")
        meta = {"job_id": jid, "job_id_raw": it.get("jobId"), "brand": it.get("brandName"),
                "boss": it.get("name"),
                "title": job.get("jobName") or it.get("title"),
                "salary": job.get("salaryDesc"), "city": job.get("cityName"), "score": sc,
                "verdict": verdict, "label": label, "text": txt}
        if jid not in st["inbox_seen"] and verdict == "pass":
            st["inbox_seen"].append(jid)
            fresh_hits.append((sc, who, meta))
        if label == "interview":
            if jid not in st["replied"]:
                st["pending"][jid] = dict(meta, ts=HC.stamp(cfg))
            interviews.append((jid, it, who, meta))
        elif label == "reject":
            st["pending"].pop(jid, None)
        elif label == "interested" and verdict == "pass":
            interest.append((sc, who, meta))
        if verdict == "pass" and label in ("interested", "other") and (it.get("brandName") or ""):
            reply_cands.append((sc, who, dict(meta, ts=info.get("msgTime"))))

    fresh_hits.sort(key=lambda x: -x[0])
    interest.sort(key=lambda x: -x[0])
    reply_cands.sort(key=lambda x: -(x[2].get("ts") or 0))   # 最新优先：DOM 只渲染前 ~20 个会话
    fresh_meta = [m for _, _, m in fresh_hits]
    interest_meta = [m for _, _, m in interest]
    fresh_hits = [w for _, w, _ in fresh_hits]
    interest = [w for _, w, _ in interest]

    out.append("近 %d 小时新消息 %d 条（新招呼 %d）｜对口 %d｜感兴趣 %d｜约面试 %d"
               % (int(lookback), n_fresh, len(items), len(fresh_hits), len(interest), len(interviews)))
    if fresh_hits:
        out.append("新增对口招呼：")
        out.extend("  · " + h for h in fresh_hits[:max_listed])
        if len(fresh_hits) > max_listed:
            out.append("  · …另有 %d 个" % (len(fresh_hits) - max_listed))
    if interest:
        out.append("对方感兴趣（前 %d）：" % max_listed)
        out.extend("  · " + h for h in interest[:max_listed])
        if len(interest) > max_listed:
            out.append("  · …另有 %d 个" % (len(interest) - max_listed))

    ar = (cfg.get("inbox") or {}).get("auto_reply") or {}
    auto_ok, auto_fail = [], []
    auto_counts = dict(new_greet=0, open_failed=0, already_mine=0, owner_unknown=0)
    if ar.get("enabled"):
        cap = int(ar.get("daily_cap", 3))
        today = HC.stamp(cfg, "%Y-%m-%d")
        hist = st.setdefault("auto_replies", {})
        used = ledger_count(cfg, {"auto_reply", "interview_reply", "followup"})
        max_tries = int(ar.get("max_tries") or 8)
        if not chat_dom.goto_tab(br, tab, chat_dom.NEW_GREET_TAB):
            out.append("自动回话：切不到「新招呼」标签，跳过")
        else:
            texts = chat_dom.conversation_rows(br, tab)
            auto_counts["new_greet"] = len(texts)
            out.append("新招呼 %d 个｜今日已回 %d/%d" % (len(texts), used, cap))
            if texts and not any(tx.get("conv_key") for tx in texts):
                out.append("⚠️ 新招呼 %d 个但一个会话 ID 都读不出（页面结构可能已改版），自动回话跳过" % len(texts))
            processed, tries = set(), 0
            while used < cap and tries < max_tries:
                HQ.check_deadline(cfg)
                target = None
                for i, tx in enumerate(texts):
                    if tx.get("conv_key") and tx["conv_key"] not in processed:
                        target = i
                        break
                if target is None:
                    break
                row = texts[target]
                key = row["conv_key"]
                processed.add(key)
                tries += 1
                stt, item, msgs = chat_dom.open_by_index(br, tab, target, conv_key=key)
                if stt != "ok" or not msgs:
                    HQ.check_deadline(cfg)
                    stt, item, msgs = chat_dom.open_by_index(br, tab, target, conv_key=key)
                if stt != "ok" or not msgs:
                    auto_counts["open_failed"] += 1
                    continue
                if any(m.get('who') not in {'mine', 'friend'} for m in msgs):
                    owner_unknown += 1
                    auto_counts['owner_unknown'] += 1
                    continue
                item = chat_dom.clean_item(item)
                if msgs[-1].get("who") == "mine":
                    auto_counts["already_mine"] += 1
                    continue                      # 最后一条是我发的，不用回
                gr = R.generate(cfg, {"brandName": item}, "", log=out.append, history=msgs)
                # 对方要简历：直接点 BOSS 自带的「发附件简历」，不要嘴上说「整理一下发您」
                if gr.get("intent") == "resume":
                    if ar.get("dry"):
                        out.append("🧪 预演 [%s] → 发 BOSS 附件简历" % item[:16])
                        used += 1
                        continue
                    cst = st.get("conv_state", {}).get(key, {})
                    rres = CA.send_resume(br, tab, cfg, key,
                                          cst.get("job_id") or ("tab:%s" % key))
                    if rres in ("sent", "pending_verification"):
                        used += 1
                        # send_resume 自己写发送流水；这里不能再调 remember_reply
                        # （它要求 ledger 里有 cmd='auto_resume' 的 sent 事件，否则抛 missing_confirmed_reply_event）
                        HS.save_state(cfg, st)
                        out.append("📎 发简历 [%s] → %s" % (item[:16], rres))
                        if not chat_dom.goto_tab(br, tab, chat_dom.NEW_GREET_TAB):
                            break
                        texts = chat_dom.conversation_rows(br, tab)
                        continue
                    # 发简历失败：退回话术 + 待办，不能既没发出去又没吭声
                    out.append("⚠️ 发简历 [%s] 失败(%s)，退回文字" % (item[:16], rres))
                if not gr.get("text"):
                    manual_reply.append({"brand": item[:26], "job_id": "tab:%s" % key,
                                         "why": gr.get("reasons") or [],
                                         "situation": H.categorize(msgs[-1].get("text") or ""),
                                         "last": (msgs[-1].get("text") or "")[:60]})
                    continue
                if ar.get("dry"):
                    auto_ok.append({"brand": item[:26], "reply": gr["text"],
                                    "source": gr.get("source") + ":dry", "intent": gr.get("intent")})
                    out.append("🧪 预演 [%s] → %s" % (item[:16], gr["text"]))
                    used += 1
                    continue
                context = gr.get('fact_context')
                if context:
                    context = dict(context, conv_key=key, op_id=reply_operation(key, msgs)[1],
                                   draft_fingerprint=chat_dom.history_fingerprint(msgs))
                res = send_in_chat(br, tab, gr["text"], gr["text"][:6], key, cfg, expected_history=msgs,
                                   fact_context=context)
                if res == "replied":
                    used += 1
                    HS.remember_reply(cfg, st, key, 'auto_reply', {
                        'source': gr.get('source'), 'item': item[:40],
                        'hr': (msgs[-1].get('text') or '')[:80]})
                    if gr.get('intent') == 'resume':
                        current = st.get('conv_state', {}).get(key, {})
                        job = jobs.get(current.get('job_id')) or {}
                        followup = {
                            'ts': HC.stamp(cfg), 'conv_key': key,
                            'brand': row.get('brand') or current.get('brand') or '',
                            'boss': row.get('boss') or current.get('boss') or '',
                            'job': row.get('title') or current.get('title') or job.get('jobName') or '',
                            'reply': gr['text'],
                        }
                        st['resume_followups'] = ([r for r in st.get('resume_followups', [])
                                                   if r.get('conv_key') != key] + [followup])[-30:]
                    HS.save_state(cfg, st)
                    auto_ok.append({"brand": item[:26], "reply": gr["text"],
                                    "source": gr.get("source"), "intent": gr.get("intent")})
                    out.append("💬 回 [%s] → %s" % (item[:16], gr["text"]))
                    if not chat_dom.goto_tab(br, tab, chat_dom.NEW_GREET_TAB):   # 会话已移出该标签，重读
                        break
                    texts = chat_dom.conversation_rows(br, tab)
                else:
                    auto_fail.append({"brand": item[:26], "error": res})
                    out.append("⚠️ 回 [%s] 失败(%s)" % (item[:16], res))
    if auto_counts["new_greet"] > 0:
        out.append("自动回话：新招呼 %d 个｜回 %d｜需人工 %d｜打开失败 %d｜我最后说过 %d｜归属不明 %d" % (
            auto_counts["new_greet"], len(auto_ok), len(manual_reply),
            auto_counts["open_failed"], auto_counts["already_mine"], auto_counts["owner_unknown"]))
    if manual_reply:
        out.append("需人工回（%d）：" % len(manual_reply))
        for n, m in enumerate(manual_reply[:int((cfg.get("inbox") or {}).get("max_listed", 5))], 1):
            out.append("  %d. %s｜%s｜%s" % (n, m.get("situation") or "?",
                                            (m.get("brand") or "")[:20],
                                            (m.get("last") or m.get("text") or "")[:38]))
    if (cfg.get("inbox") or {}).get("interview_auto_reply", True):
        reply = (cfg.get("user") or {}).get("interview_reply") or INTERVIEW_DEFAULT
        marker = "面试" if "面试" in reply else reply[:6]
        for jid, it, who, meta in interviews:
            if jid in st["replied"]:
                continue
            res = reply_in_chat(br, tab, it.get("brandName") or "", reply, marker, cfg, it.get("name"), jid,
                                conv_key=chat_dom.source_conversation_key(it))
            if res == "replied":
                HS.remember_reply(cfg, st, chat_dom.source_conversation_key(it), 'interview_reply')
                st["replied"].append(jid)
                st["pending"].pop(jid, None)
                out.append("📅 约面试 %s → 已回「先给时段，我确认后再答复」" % who)
                HS.save_state(cfg, st)
                replied_ok.append(meta)
            else:
                out.append("⚠️ 约面试 %s → 回复失败(%s)，请手工回" % (who, res))
                reply_failed.append(dict(meta, error=res))

    fu = (cfg.get("inbox") or {}).get("followup") or {}
    if fu.get("enabled"):
        hours = float(fu.get("after_hours", 48))
        text = (cfg.get("user") or {}).get("followup_template") or \
               "您好，之前给您发过消息，想确认下这个岗位还在招吗？我这边随时可以聊。"
        for jid, it in items.items():
            if jid not in sent_jids or jid in st["followed"]:
                continue
            info = it.get("lastMessageInfo") or {}
            ts = info.get("msgTime") or 0
            if not ts or (now_ms - ts) < hours * 3600 * 1000:
                continue
            if ownership.get(jid) is not True:
                continue  # 对方已回或归属不明，不需要跟进
            res = reply_in_chat(br, tab, it.get("brandName") or "", text, text[:6], cfg, it.get("name"), jid, "followup",
                                conv_key=chat_dom.source_conversation_key(it))
            if res == "replied":
                HS.remember_reply(cfg, st, chat_dom.source_conversation_key(it), 'followup')
                st["followed"].append(jid)
                HS.save_state(cfg, st)
            out.append("🔁 跟进 %s → %s" % (it.get("brandName") or "?", res))
            followed_up.append({"job_id": jid, "brand": it.get("brandName"), "result": res})
    report = unanswered_report(st, hours=float((cfg.get('inbox') or {}).get('stale_hours', 72)))
    counts = report['counts']
    out.append('待核对 %d（unanswered --unknown）' % counts['unknown'])
    if any(counts.values()):
        out.append('待我回 %d ｜等对方 %d ｜超时 ≥%gh %d（详见 unanswered）' % (
            counts['waiting_me'], counts['waiting_them'], float((cfg.get('inbox') or {}).get('stale_hours', 72)), counts['stale']))
    return {
        "status_scan": status_scan,
        "unanswered": report, "owner_unknown": owner_unknown,
        "window_hours": int(lookback),
        "messages_total": len(items),
        "new_messages": n_fresh,
        "new_matches": fresh_meta,
        "interest": interest_meta,
        "interviews": [dict(m, replied=(jid in st["replied"]))
                       for jid, it, who, m in interviews],
        "replied_ok": replied_ok,
        "auto_reply_counts": dict(auto_counts, replied=len(auto_ok), manual=len(manual_reply)),
        "auto_replied": auto_ok,
        "auto_failed": auto_fail,
        "manual_reply": manual_reply,
        "reply_failed": reply_failed,
        "followed_up": followed_up,
        "rejects_total": len(rejects),
        "unread_semantics": "打开消息页会清零未读；本条按招呼语文本/时间比对判定",
    }


# --------------------------------------------------------------------------
# 子命令
# --------------------------------------------------------------------------
def emit(cfg, lines, quiet=False):
    head = "【%s %s】" % (HC.stamp(cfg), HC.cfg_tz(cfg).key.split("/")[-1])
    block = head + "\n" + "\n".join("- " + str(x) for x in lines)
    try:
        with open(cfg["paths"]["log"], "a", encoding="utf-8") as f:
            f.write(block + "\n")
    except Exception:
        pass
    if not quiet:
        print(block)


SCHEMA = 2


def emit_json(payload, cfg=None, lines=None):
    """Machine-readable output for harnesses.

    stdout gets exactly one JSON object (nothing else). The human-readable
    block still lands in runs.log so the daily log stays greppable.
    """
    if cfg is not None and lines:
        try:
            head = "【%s %s】" % (HC.stamp(cfg), HC.cfg_tz(cfg).key.split("/")[-1])
            block = head + "\n" + "\n".join("- " + str(x) for x in lines)
            with open(cfg["paths"]["log"], "a", encoding="utf-8") as f:
                f.write(block + "\n")
        except Exception:
            pass
    out = {"schema": SCHEMA}
    out.update(payload)
    print(json.dumps(out, ensure_ascii=False, indent=2))


def want_json(args):
    return bool(getattr(args, "json", False))


def cmd_init(args):
    target = HC.config_path(args)
    src = os.path.join(HERE, "..", "assets", "config.example.json")
    src = os.path.abspath(src)
    if os.path.exists(target) and not args.force:
        if want_json(args):
            emit_json({"cmd": "init", "ok": False, "error": "exists", "config": target})
        else:
            print("已存在：%s（要覆盖加 --force）" % target)
        return 1
    os.makedirs(os.path.dirname(target), exist_ok=True)
    with open(src, encoding="utf-8") as f:
        data = f.read()
    with open(target, "w", encoding="utf-8") as f:
        f.write(data)
    if want_json(args):
        emit_json({"cmd": "init", "ok": True, "config": target,
                   "next": "编辑 user/targets/filters，然后跑 validate"})
        return 0
    print("已生成 %s\n下一步：把 user/targets/filters 填成你自己的情况，然后 `python3 boss_hunt.py validate`。"
          % target)
    return 0


def cmd_validate(args):
    path = HC.config_path(args)
    try:
        cfg = HC.load_config(path, create_dirs=False)
    except ValueError as exc:
        if want_json(args):
            emit_json({"cmd": "validate", "ok": False, "config": path,
                       "errors": [str(exc)], "warnings": []})
        else:
            print("❌ " + str(exc))
        return 1
    errs, warns = HQ.validate_cfg(cfg)
    if not errs:
        try:
            rows = grades_by_src(cfg, HS.load_state(cfg), HF.load_jobs(cfg))
        except (ValueError, OSError) as exc:
            warns.append("零产出关键词诊断不可用：%s" % exc)
        else:
            totals = keyword_source_totals(cfg, rows)
            for kw in zero_output_keywords(cfg, rows):
                judged, _, no_jd = totals[kw]
                warns.append("零产出关键词「%s」：已判数=%d，no_jd=%d，final_A+final_B+sent+b_pool=0（仅回放已有 JD）"
                             % (kw, judged, no_jd))
    if want_json(args):
        cap = (cfg.get("sending") or {}).get("daily_cap")
        emit_json({"cmd": "validate", "ok": not errs, "config": path,
                   "workdir": cfg["paths"]["workdir"], "errors": errs, "warnings": warns,
                   "targets": [{"name": t.get("name"), "city": t.get("city"),
                                "city_code": t.get("city_code"),
                                "keywords": t.get("keywords") or []}
                               for t in (cfg.get("targets") or []) if t.get("enabled", True)],
                   "daily_cap": cap, "timezone": HC.cfg_tz(cfg).key})
        return 1 if errs else 0
    print("配置：%s" % path)
    print("工作目录：%s" % cfg["paths"]["workdir"])
    for w in warns:
        print("⚠️  " + w)
    for e in errs:
        print("❌ " + e)
    if not errs:
        cap = (cfg.get("sending") or {}).get("daily_cap")
        tgt = ", ".join("%s(%s)" % (t.get("city"), "/".join(t.get("keywords") or []))
                        for t in cfg.get("targets") or [] if t.get("enabled", True))
        print("✅ 配置可用：目标 %s｜日上限 %s｜时区 %s" % (tgt, cap, HC.cfg_tz(cfg).key))
    return 1 if errs else 0


def cmd_grade(args):
    cfg = HC.load_config(HC.config_path(args))
    roles = (cfg.get("grading") or {}).get("roles") or []
    job = {"jobName": args.job_name or (roles[0] if roles else ""),
           "brandName": args.brand or "", "brandIndustry": args.industry or ""}
    jd = ""
    if args.jd_file:
        with open(os.path.expanduser(args.jd_file), encoding="utf-8") as f:
            jd = f.read()
    gr = HF.grade_job(job, cfg, jd)
    if want_json(args):
        emit_json(dict(gr, cmd="grade", ok=True, title=job["jobName"]))
    else:
        print("等级 %s｜%s\n信号计分 %s" % (gr["grade"], gr["reason"], json.dumps(gr["scores"], ensure_ascii=False)))
    return 0


def cmd_grades(args):
    by_src = getattr(args, "by_src", False)
    cfg = HC.load_config(HC.config_path(args), create_dirs=not by_src)
    st = HS.load_state(cfg)
    if by_src:
        jobs = HF.load_jobs(cfg)
        rows = grades_by_src(cfg, st, jobs)
        if want_json(args):
            emit_json({"cmd": "grades", "ok": True, "unique_total": len(jobs),
                       "open_unique": len(open_pool(st, jobs, cfg)),
                       "open_with_bpool": len(open_pool(st, jobs, cfg, include_bpool=True)), "by_src": rows})
        else:
            print("open_unique=%d｜open_with_bpool=%d（行内 open 不去重：多来源岗位每行各计一次）" %
                  (len(open_pool(st, jobs, cfg)), len(open_pool(st, jobs, cfg, include_bpool=True))))
            print("src｜open｜open_with_bpool｜no_jd｜final_A｜final_B｜final_C｜sent｜b_pool｜no_signal｜redline｜sr")
            for row in rows:
                print("%(src)s｜%(open)d｜%(open_with_bpool)d｜%(no_jd)d｜%(final_A)d｜%(final_B)d｜%(final_C)d｜%(sent)d｜%(b_pool)d｜%(no_signal)d｜%(redline)d｜%(sr).2f" % row)
        return 0
    counts = {"A?": 0, "B?": 0, "C": 0}
    applied = {}
    rows = []
    for j in HF.load_jobs(cfg):
        gr = HF.grade_job(j, cfg)
        key = gr["grade"]
        counts[key] = counts.get(key, 0) + 1
        sent = j["encryptJobId"] in st["sent"]
        applied[key] = applied.get(key, 0) + int(sent)
        rows.append(dict(gr, job_id=j["encryptJobId"], title=j.get("jobName"), sent=sent))
    data = {"cmd": "grades", "ok": True, "counts": counts, "applied": applied,
            "total": len(rows), "items": rows[:max(0, args.top)]}
    if want_json(args):
        emit_json(data)
    else:
        print("粗标分布：" + " / ".join("%s %d（已投 %d）" % (k, v, applied.get(k, 0)) for k, v in counts.items()))
        for row in data["items"]:
            print("%s｜%s｜%s" % (row["grade"], row["title"], row["reason"]))
    return 0


def open_pool(st, jobs, cfg, include_bpool=False):
    """默认保持快照口径；include_bpool 补 tick 的池中候选及职能豁免。"""
    pool = st.get("b_pool") or {}
    source = {j["encryptJobId"]: j for j in jobs}
    if include_bpool:
        for jid, row in pool.items():
            if jid not in source and row.get("job"):
                source[jid] = row["job"]
    roles = (cfg.get("grading") or {}).get("roles") or []
    result = []
    for job in source.values():
        jid = job["encryptJobId"]
        if (jid in st.get("sent", {}) or jid in st.get("platform_chatted", {})
                or jid in st.get("opened_no_greet", {})
                or HS.same_hr_filter(st, cfg, job) in ("skip_same_hr", "skip_same_hr_unknown_ts")
                or st.get("checked_jobs", {}).get(jid) == HF.grade_fingerprint(job, cfg)
                or (not (include_bpool and jid in pool) and not HF.any_match(roles, job.get("jobName") or ""))
                or HF.judge(job, cfg)[0] == "reject"
                or HF.score(job, cfg) < int((cfg.get("scoring") or {}).get("min_score", 0))):
            continue
        result.append(job)
    return result


def grades_by_src(cfg, st, jobs):
    """只读诊断：来源按快照 _src → sent.src → b_pool.src → unknown 反解。"""
    opened = {j["encryptJobId"] for j in open_pool(st, jobs, cfg)}
    with_pool = {j["encryptJobId"] for j in open_pool(st, jobs, cfg, include_bpool=True)}
    snapshots = {j["encryptJobId"]: j for j in jobs}
    source = dict(snapshots)
    for state_key in ("b_pool", "sent", "grade_cache"):
        for jid, saved in (st.get(state_key) or {}).items():
            job = saved.get("job") if isinstance(saved, dict) else None
            source.setdefault(jid, job or {})
    groups = {}
    for jid, job in source.items():
        jd = (st.get("grade_cache", {}).get(jid) or {}).get("jd_text") or ""
        gr = HF.grade_with_jd(job, cfg, jd) if jid in opened and jd.strip() else None
        sent = st.get("sent", {}).get(jid)
        pool = st.get("b_pool", {}).get(jid) or {}
        sources = ((snapshots.get(jid) or {}).get("_src")
                   or (sent.get("src") if isinstance(sent, dict) else None)
                   or pool.get("src") or "unknown")
        if isinstance(sources, str):
            sources = [sources]
        for src in dict.fromkeys(sources):
            row = groups.setdefault(src, dict(src=src, open=0, open_with_bpool=0, no_jd=0,
                                             final_A=0, final_B=0, final_C=0,
                                             sent=0, b_pool=0, no_signal=0, redline=0, sr=0))
            row["open"] += int(jid in opened)
            row["open_with_bpool"] += int(jid in with_pool)
            row["no_jd"] += int(jid in opened and not jd.strip())
            row["sent"] += int(jid in st.get("sent", {}))
            row["b_pool"] += int(jid in st.get("b_pool", {}))
            if gr and gr.get("final"):
                key = "final_" + gr["grade"]
                if key in row:
                    row[key] += 1
                if gr.get("cause") in ("no_signal", "redline"):
                    row[gr["cause"]] += 1
    for row in groups.values():
        judged = row["final_A"] + row["final_B"] + row["final_C"]
        row["sr"] = (row["final_A"] + row["final_B"]) / judged if judged else 0
    return sorted(groups.values(), key=lambda row: (-(row["final_A"] + row["final_B"]), row["src"]))


def keyword_source_totals(cfg, rows):
    """跨城市/页段合计关键词产出，反解 scan 已有文件名，不把未扫描当零产出。"""
    totals = {}
    for target in cfg.get("targets") or []:
        if not target.get("enabled", True):
            continue
        for kw in target.get("keywords") or []:
            safe_kw = re.sub(r"[^\w\u4e00-\u9fa5-]+", "_", kw)
            pattern = re.compile(r"^%s_%s_\d+\.json$" %
                                 (re.escape(target.get("city") or "city"), re.escape(safe_kw)))
            judged, output_ab, no_jd = totals.get(kw, (0, 0, 0))
            for row in rows:
                if pattern.fullmatch(row["src"]):
                    judged += row["final_A"] + row["final_B"] + row["final_C"]
                    output_ab += row["final_A"] + row["final_B"] + row.get("sent", 0) + row.get("b_pool", 0)
                    no_jd += row.get("no_jd", 0)
            totals[kw] = judged, output_ab, no_jd
    return totals


def zero_output_keywords(cfg, rows):
    return [kw for kw, (judged, output_ab, no_jd) in keyword_source_totals(cfg, rows).items()
            if judged > 0 and output_ab == 0 and no_jd == 0]


def regrade_report(cfg, st, jobs, alt=None, top=20, near_miss_threshold=5, pool_threshold=45):
    """只用本地快照与缓存正文回放，不调用会迁移缓存的读取函数。"""
    rules = {"current": cfg}
    if alt is not None:
        rules["alt"] = alt
    raw = {j["encryptJobId"]: j for j in jobs}
    cache = st.get("grade_cache") or {}
    funnels = {}
    for label, rule in rules.items():
        opened = {j["encryptJobId"] for j in open_pool(st, jobs, rule)}
        funnel = {"total": len(jobs), "judge_reject": {}, "role_miss": 0,
                  "coarse": {"A?": 0, "B?": 0, "C?": 0}, "sent": 0, "same_hr": 0,
                  "open_role_matched": 0, "fresh_pool": 0}
        for job in jobs:
            jid = job["encryptJobId"]
            gr = HF.grade_job(job, rule)
            key = gr["grade"]
            if key == "C":
                key = "C?"
            funnel["coarse"][key] = funnel["coarse"].get(key, 0) + 1
            role_hit = bool(HF.any_match((rule.get("grading") or {}).get("roles"), job.get("jobName") or ""))
            funnel["role_miss"] += int(not role_hit)
            verdict, reasons, _ = HF.judge(job, rule)
            if verdict == "reject":
                prefix = reasons[0].split(":")[0]
                funnel["judge_reject"][prefix] = funnel["judge_reject"].get(prefix, 0) + 1
            sent = jid in st.get("sent", {})
            same_hr = HS.same_hr_filter(st, rule, job) in ("skip_same_hr", "skip_same_hr_unknown_ts")
            funnel["sent"] += int(sent)
            funnel["same_hr"] += int(same_hr)
            if jid in opened:
                funnel["open_role_matched"] += 1
                jd = (cache.get(jid) or {}).get("jd_text")
                if not jd or HF.grade_with_jd(job, rule, jd)["grade"] != "C":
                    funnel["fresh_pool"] += 1
        with_pool = open_pool(st, jobs, rule, include_bpool=True)
        funnel["open_role_matched_with_bpool"] = len(with_pool)
        funnel["fresh_pool_with_bpool"] = sum(
            1 for job in with_pool
            if not (cache.get(job["encryptJobId"]) or {}).get("jd_text")
            or HF.grade_with_jd(job, rule, cache[job["encryptJobId"]]["jd_text"])["grade"] != "C")
        funnels[label] = funnel

    jd_stats = {label: {"grades": {"A": 0, "B": 0, "C": 0},
                        "causes": {k: 0 for k in ("no_signal", "redline", "tie_unknown", "role_miss", "ok")},
                        "near_miss": {"count": 0, "by_kind": {}}} for label in rules}
    matrix = {"stored_to_current": {}}
    if alt is not None:
        matrix["current_to_alt"] = {}
    orphan, legacy, total = 0, 0, 0
    migrations, near_rows = [], []

    def add_transition(which, before, after):
        key = "%s->%s" % (before, after)
        matrix[which][key] = matrix[which].get(key, 0) + 1

    def item(jid, job, before, gr, nm):
        return {"job_id": jid, "title": job.get("jobName"), "brand": job.get("brandName"),
                "from": before, "to": gr["grade"], "cause": gr["cause"], "hits": gr["hits"],
                "redlines": gr["redlines"], "near_miss": nm}

    for jid, entry in cache.items():
        if not entry.get("jd_text"):
            continue
        total += 1
        legacy += int("jd_fp" not in entry and "fingerprint" in entry)
        # 保留 raw → B 池 → 已投的优先级，缺失时才使用新版缓存岗位字段。
        job = raw.get(jid) or (st.get("b_pool", {}).get(jid) or {}).get("job")
        if not job:
            sent = st.get("sent", {}).get(jid)
            if sent:
                job = dict(sent, encryptJobId=jid, jobName=sent.get("name") or sent.get("jobName"),
                           brandName=sent.get("brand") or sent.get("brandName"))
        if not job:
            job = entry.get("job")
        if not job:
            orphan += 1
            continue
        results, misses = {}, {}
        for label, rule in rules.items():
            gr = HF.grade_with_jd(job, rule, entry["jd_text"])
            nm = HF.near_miss(gr, rule)
            results[label], misses[label] = gr, nm
            stats = jd_stats[label]
            for field, key in (("grades", gr["grade"]), ("causes", gr["cause"])):
                stats[field][key] = stats[field].get(key, 0) + 1
            if nm:
                stats["near_miss"]["count"] += 1
                kinds = stats["near_miss"]["by_kind"]
                kinds[nm["kind"]] = kinds.get(nm["kind"], 0) + 1
        current = results["current"]
        stored = entry.get("grade")
        add_transition("stored_to_current", stored, current["grade"])
        if stored != current["grade"]:
            migrations.append(item(jid, job, stored, current, misses["current"]))
        if alt is not None:
            other = results["alt"]
            add_transition("current_to_alt", current["grade"], other["grade"])
            if current["grade"] != other["grade"]:
                migrations.append(item(jid, job, current["grade"], other, misses["alt"]))
        if misses["current"]:
            near_rows.append(item(jid, job, stored, current, misses["current"]))
    migration_ids = {row["job_id"] for row in migrations}
    rows = migrations + [row for row in near_rows if row["job_id"] not in migration_ids]
    nm_count = jd_stats["current"]["near_miss"]["count"]
    upgrades = sum(matrix.get("current_to_alt", {}).get("C->" + k, 0) for k in ("A", "B"))
    tight = nm_count >= near_miss_threshold or (alt is not None and upgrades >= near_miss_threshold)
    dry = funnels["current"]["fresh_pool"] < pool_threshold
    verdict = "both" if tight and dry else "rules_tight" if tight else "pool_dry" if dry else "inconclusive"
    why = ["near_miss_current=%d（阈值 ≥%d）" % (nm_count, near_miss_threshold),
           "fresh_pool=%d（阈值 <%d）" % (funnels["current"]["fresh_pool"], pool_threshold)]
    if alt is not None:
        why.append("alt C->A/B=%d（阈值 ≥%d）" % (upgrades, near_miss_threshold))
    return {"cmd": "regrade", "ok": True, "list": funnels,
            "jd": dict(jd_stats, total=total, legacy=legacy, orphan=orphan), "matrix": matrix,
            "diagnosis": {"verdict": verdict, "why": why,
                          "thresholds": {"near_miss": near_miss_threshold, "pool": pool_threshold}},
            "items": rows[:max(0, top)]}


def cmd_regrade(args):
    cfg = HC.load_config(HC.config_path(args), create_dirs=False)
    alt = None
    if args.alt_config:
        source = HC.load_config(os.path.abspath(os.path.expanduser(args.alt_config)), create_dirs=False)
        # 比较只改变规则/用户画像，数据目录、额度和调度保持当前配置。
        alt = dict(cfg)
        for key in ("grading", "filters", "scoring", "user"):
            if key in source:
                alt[key] = source[key]
        alt["paths"] = cfg["paths"]
        errors, _ = HQ.validate_cfg(alt)
        if errors:
            payload = {"cmd": "regrade", "ok": False, "errors": errors}
            if want_json(args):
                emit_json(payload)
            else:
                print("配置无效：" + "；".join(errors))
            return 1
    data = regrade_report(cfg, HS.load_state(cfg), HF.load_jobs(cfg), alt, args.top,
                          args.near_miss_threshold, args.pool_threshold)
    if want_json(args):
        emit_json(data)
    else:
        print("列表漏斗：" + json.dumps(data["list"], ensure_ascii=False))
        print("JD 层分布：" + json.dumps(data["jd"], ensure_ascii=False))
        print("诊断：%s｜%s" % (data["diagnosis"]["verdict"], "；".join(data["diagnosis"]["why"])))
    return 0


def cmd_greet(args):
    """离线预览某岗位现写的招呼语（不需要浏览器；JD 可从文件/快照/参数给）。"""
    cfg = HC.load_config(HC.config_path(args))
    jobs = HF.load_jobs(cfg)
    job = None
    jid = args.job_id
    if jid:
        job = next((j for j in jobs if j.get("encryptJobId") == jid), None)
        if job is None:
            print("❌ raw/ 快照里没有这个 encryptJobId：%s（先 scan，或去掉 --job-id 用 --jd 试写）" % jid)
            return 1
    else:
        job = {"jobName": args.job_name or "示例岗位", "brandName": args.brand or "示例公司",
               "cityName": args.city or "", "skills": (args.skills or "").split(",")
               if args.skills else []}
    jd = ""
    if args.jd_file:
        with open(os.path.abspath(os.path.expanduser(args.jd_file)), encoding="utf-8") as f:
            jd = f.read()
    elif args.jd:
        jd = args.jd
    jd_source = "file" if args.jd_file else ("arg" if args.jd else "none")
    up, msg = G.ollama_status(cfg) if G.enabled(cfg) else (True, "mode=template")
    runs = []
    for _ in range(max(1, int(args.times or 1))):
        gr = G.generate(cfg, job, jd, use_cache=not args.no_cache,
                        fallback=lambda j: O.render((cfg.get("user") or {}).get("greeting_template") or "",
                                                  cfg, j))
        ok, reasons = G.check(gr.get("text") or "", cfg, job, jd) \
            if str(gr.get("source") or "").startswith("llm") else (None, ["template 渲染，闸门只对模型输出生效"])
        runs.append({"text": gr.get("text"), "source": gr.get("source"),
                     "chars": len(gr.get("text") or ""), "attempts": gr.get("attempts"),
                     "secs": gr.get("secs"), "gate_ok": ok, "gate_reasons": reasons,
                     "fallback_reason": gr.get("fallback_reason")})
    if want_json(args):
        emit_json({"cmd": "greet", "ok": bool(runs and runs[0]["text"]) and up,
                   "job_id": job.get("encryptJobId"), "title": job.get("jobName"),
                   "brand": job.get("brandName"), "jd_source": jd_source,
                   "jd_chars": len(jd), "ollama": msg, "greet": G.describe(cfg, job),
                   "marker": O.verify_marker(cfg, job, runs[0]["text"] if runs else ""),
                   "runs": runs})
        return 0
    print("岗位：%s｜%s｜%s" % (job.get("jobName"), job.get("brandName"),
                             job.get("salaryDesc") or ""))
    print("招呼语：%s（Ollama %s；JD 来源 %s，%d 字）"
          % (G.describe(cfg, job), msg, jd_source, len(jd)))
    for i, r in enumerate(runs, 1):
        gate = "跳过" if r["gate_ok"] is None else ("通过" if r["gate_ok"] else "未过")
        extra = ""
        if r.get("fallback_reason"):
            extra = "｜回退原因 %s" % r["fallback_reason"]
        elif r["gate_ok"] is False:
            extra = "：" + ",".join(r["gate_reasons"])
        print("\n[%d] %d 字｜来源 %s｜耗时 %ss｜闸门 %s%s"
              % (i, r["chars"], r["source"], r["secs"], gate, extra))
        print(r["text"] or "（空）")
    print("\n--- 校验标记：%r" % O.verify_marker(cfg, job, runs[0]["text"] if runs else ""))
    return 0 if all(r["text"] for r in runs) else 1


def cmd_template(args):
    cfg = HC.load_config(HC.config_path(args))
    job = {"jobName": args.job_name or "示例岗位", "brandName": args.brand or "示例公司",
           "cityName": args.city or "示例城市"}
    text = O.render((cfg.get("user") or {}).get("greeting_template") or "", cfg, job)
    if want_json(args):
        emit_json({"cmd": "template", "ok": 0 < len(text) <= 380, "text": text,
                   "chars": len(text), "marker": O.verify_marker(cfg, job),
                   "recommended_chars": [80, 200], "hard_max": 380})
        return 0
    print(text)
    print("\n--- 校验标记：%r（发送后必须在弹窗里出现）" % O.verify_marker(cfg, job))
    print("字数：%d（建议 80–200 字；>380 会被拦）" % len(text))
    return 0




def cmd_login(args):
    cfg = HC.load_config(HC.config_path(args))
    br = CM.Browser(cfg)
    bridge, ext = "", ""
    lines = ["bridge:%s" % bridge, "extension:%s" % ext]
    logged, url, closed = False, "", 0
    try:
        bridge = br.connect()
        ext = br.wake
        tab, stt = O.check_login(br, cfg)
        url = stt.get("url", "")
        lines.append("页面：%s" % url)
        logged = bool(stt.get("avatar")) and not stt.get("login")
        if logged:
            lines.append("✅ 已登录（读到头像/用户区）")
        elif stt.get("login"):
            lines.append("🔑 未登录：请在浏览器里用手机扫码登录，登录后重跑本命令")
        else:
            lines.append("❓ 无法确认：请让用户在浏览器打开 zhipin.com 自行确认")
    finally:
        closed = O.cleanup_browser(br)
        lines.append("已关自己开的标签页 %d 个" % closed)
    if want_json(args):
        emit_json({"cmd": "login-check", "ok": logged, "logged": logged, "bridge": bridge,
                   "extension": ext, "url": url, "tabs_closed": closed}, cfg, lines)
        return 0
    emit(cfg, lines)
    return 0


def cmd_preflight(args):
    cfg = HC.load_config(HC.config_path(args))
    br = CM.Browser(cfg)
    bridge, ext = "", ""
    lines = ["bridge:%s" % bridge, "extension:%s" % ext]
    logged, gs, closed = False, "", 0
    cap = int((cfg.get("sending") or {}).get("daily_cap") or 0)
    day_count = None
    greet_ok, greet_msg = (G.ollama_status(cfg) if G.enabled(cfg) else (True, "mode=template"))
    lines.append("招呼语：%s｜%s" % (G.describe(cfg, {}), greet_msg))
    try:
        bridge = br.connect()
        ext = br.wake
        tab, stt = O.check_login(br, cfg)
        logged = bool(stt.get("avatar")) and not stt.get("login")
        lines.append("登录：%s" % ("ok" if logged else "未确认/未登录"))
        gs = O.greeting_switch(br, tab, fix=args.fix)
        lines.append("招呼语开关：%s" % gs)
        if gs != "auto_greeting_off":
            lines.append("请手动确认招呼语开关已关闭")
        state = HS.load_state(cfg)
        day_count = state.get("day_count")
        lines.append("今日已发 %s/%s（%s）" % (day_count, cap, HC.cfg_tz(cfg).key))
    finally:
        closed = O.cleanup_browser(br)
        lines.append("已关自己开的标签页 %d 个" % closed)
    if want_json(args):
        emit_json({"cmd": "preflight", "ok": logged and gs == "auto_greeting_off" and greet_ok,
                   "logged": logged, "greeting_switch": gs, "day_count": day_count,
                   "daily_cap": cap, "bridge": bridge, "extension": ext,
                   "greet_mode": G.block(cfg).get("mode"), "greet_llm": greet_msg,
                   "greet_ok": greet_ok,
                   "manual_action": "请手动确认招呼语开关已关闭" if gs != "auto_greeting_off" else "",
                   "tabs_closed": closed}, cfg, lines)
        return 0
    emit(cfg, lines)
    return 0


def cmd_scan(args):
    cfg = HC.load_config(HC.config_path(args))
    br = CM.Browser(cfg)
    bridge, ext = "", ""
    lines = ["bridge:%s" % bridge, "extension:%s" % ext]
    saved, errs, closed = 0, [], 0
    jobs, cs = [], []
    try:
        bridge = br.connect()
        ext = br.wake
        saved, errs = O.scan(cfg, br, targets=args.target, keywords=args.keyword,
                           pages=args.pages)
        lines.append("抓取 %d 页响应" % saved)
        lines.extend("⚠️ " + e for e in errs)
        jobs = HF.load_jobs(cfg)
        cs = HS.candidates(cfg)
        lines.append("库内岗位 %d，其中 A%d/B%d"
                     % (len(jobs), sum(1 for x in cs if x["grade"].rstrip("?") == "A"),
                        sum(1 for x in cs if x["grade"].rstrip("?") == "B")))
    finally:
        closed = O.cleanup_browser(br)
        lines.append("已关自己开的标签页 %d 个" % closed)
    if want_json(args):
        emit_json({"cmd": "scan", "ok": saved > 0, "pages_saved": saved, "errors": errs,
                   "jobs_total": len(jobs), "candidates": len(cs),
                   "grade_a": sum(1 for x in cs if x["grade"].rstrip("?") == "A"),
                   "grade_b": sum(1 for x in cs if x["grade"].rstrip("?") == "B"),
                   "bridge": bridge, "extension": ext, "tabs_closed": closed}, cfg, lines)
        return 0
    emit(cfg, lines)
    return 0


def cmd_plan(args):
    cfg = HC.load_config(HC.config_path(args))
    st = HS.load_state(cfg)
    cs = HS.candidates(cfg)
    todo = [x for x in cs if x["job"]["encryptJobId"] not in (st.get("sent") or {})]
    cap = int((cfg.get("sending") or {}).get("daily_cap") or 0)
    left = max(0, cap - int(st.get("day_count") or 0))
    lines = ["候选 %d（A%d/B%d），未投 %d，今日剩 %d/%d"
             % (len(cs), sum(1 for x in cs if x["grade"].rstrip("?") == "A"),
                sum(1 for x in cs if x["grade"].rstrip("?") == "B"), len(todo), left, cap)]
    for x in todo[:args.top]:
        j = x["job"]
        lines.append("%s%s｜%s｜%s｜%s｜%s·%s｜%s"
                     % (x["grade"], x["score"], j.get("jobName"), j.get("salaryDesc") or "面议",
                        j.get("brandName"), j.get("cityName"), j.get("areaDistrict") or "",
                        " / ".join(x["flags"]) if x["flags"] else "对口"))
    if len(todo) > args.top:
        lines.append("…另有 %d 个未列出" % (len(todo) - args.top))
    if want_json(args):
        emit_json({"cmd": "plan", "ok": True, "ts": HC.stamp(cfg), "timezone": HC.cfg_tz(cfg).key,
                   "summary": {"candidates": len(cs),
                               "grade_a": sum(1 for x in cs if x["grade"].rstrip("?") == "A"),
                               "grade_b": sum(1 for x in cs if x["grade"].rstrip("?") == "B"),
                               "unsent": len(todo), "remaining_today": left,
                               "daily_cap": cap, "day_count": int(st.get("day_count") or 0)},
                   "items": [{"grade": x["grade"], "grade_final": x["grade_final"],
                              "grade_reason": x["grade_reason"], "score": x["score"],
                              "job_id": x["job"]["encryptJobId"],
                              "title": x["job"].get("jobName"),
                              "salary": x["job"].get("salaryDesc"),
                              "brand": x["job"].get("brandName"),
                              "city": x["job"].get("cityName"),
                              "district": x["job"].get("areaDistrict"),
                              "flags": x["flags"],
                              "url": "https://www.zhipin.com/job_detail/%s.html"
                                     % x["job"]["encryptJobId"]}
                             for x in todo[:args.top]],
                   "not_listed": max(0, len(todo) - args.top)}, cfg, lines)
        return 0
    emit(cfg, lines)
    return 0
















def run_tick(cfg, st, now, out):
    key = None
    cfg.pop("_contact_last_sent_cache", None)
    plan = HQ.tick_plan(cfg, now)
    data = dict(plan, cmd="tick", ok=True, tabs_closed=0,
                budget={"same_hr_window_days": (cfg.get("filters") or {}).get("same_hr_window_days")},
                today_sent=HQ.tick_today_sent(st), missed=[])
    lock_context = state_lock(cfg['paths']['state'])
    try:
        lock_context.__enter__()
    except RuntimeError as exc:
        out.append(str(exc))
        return dict(data, ok=False, reason="locked", error=str(exc))
    br = None
    state_loaded = False
    try:
        loaded = HS.load_state(cfg)
        state_loaded = True
        st.clear()
        st.update(loaded)
        data["today_sent"] = HQ.tick_today_sent(st)
        data["missed"] = HQ.missed_windows(cfg, st, now)
        if data["missed"]:
            keys = [now.strftime("%Y-%m-%d") + " " + item["window"] for item in data["missed"]]
            st["missed_reported"] = ((st.get("missed_reported") or []) + keys)[-64:]
            try:
                HS.save_state(cfg, st)
            except Exception as exc:
                out.append("错过窗口播报记账失败:%s" % exc)
        catch_up = None
        if plan["window"] is None and (cfg.get("schedule") or {}).get("catch_up") is True and data["missed"]:
            catch_up = min(data["missed"], key=lambda item: item["delay_min"])["window"]
            plan = dict(plan, actions=["send"])
            data["actions"] = plan["actions"]
        if not plan["actions"]:
            out.append("不在窗口，跳过")
            return data
        key = now.strftime("%Y-%m-%d") + " " + (catch_up or plan["window"])
        ledger_guard(cfg)
        HS.migrate_ledger(cfg, st)
        if key in st.get("ticks_done", []):
            out.append("本窗口已完成，跳过")
            return dict(data, reason="already_done")
        with HQ.tick_deadline(cfg):
            tab = None
            if any(a in plan["actions"] for a in ("scan", "send", "inbox")):
                br = CM.Browser(cfg)
                try:
                    data["bridge"] = br.connect(fast=True)
                except RuntimeError as exc:
                    if sys.platform != "darwin" or str(exc) not in {"bridge_offline", "extension_offline"}:
                        raise
                    CM.wake_extension(cfg)
                    time.sleep(HC.TICK_WAKE_WAIT_SECONDS)
                    data["bridge"] = br.connect(fast=True)
                    data["bridge_recovered"] = True
                    out.append("桥离线已唤醒 Chrome 并恢复")
                tab, login = O.check_login(br, cfg)
                if not tab or login.get("login") or not login.get("avatar"):
                    raise RuntimeError("not_logged_in_or_unconfirmed")
            for action in plan["actions"]:
                HQ.check_deadline(cfg)
                if action == "scan":
                    try:
                        pages, errors = O.scan(cfg, br, pages=int((cfg.get("schedule") or {}).get("scan_pages", 2)))
                        data["scan"] = {"pages_saved": pages, "errors": errors}
                        if errors or not pages:
                            raise RuntimeError("scan_failed:%s" % (errors or "no_pages"))
                    except Exception as exc:
                        reason = str(exc) or type(exc).__name__
                        if isinstance(exc, TimeoutError) or any(
                                marker in reason.lower() for marker in ("not_logged_in", "risk_", "captcha")):
                            raise
                        data["scan_error"] = reason
                        data["scan"] = {"pages_saved": 0, "errors": [reason]}
                        # scan opens its own search tabs; inspect its last page before any send.
                        scan_tab = br.opened[-1] if br.opened else tab
                        verdict = O.page_guard(br, scan_tab)
                        if verdict:
                            raise RuntimeError("scan_" + verdict)
                        out.append("补池失败，使用旧快照继续:%s" % reason)
                elif action == "send":
                    data["send"] = O.do_send(cfg, br, tab, st, None, out, window_key=key)
                    if catch_up:
                        n = data["send"]["sent"]
                        data["catch_up"] = {"window": catch_up, "sent": n}
                        out.append("🔁 补跑 %s 跳：发出 %d" % (catch_up, n))
                elif action == "inbox":
                    data["inbox"] = inbox(cfg, br, tab, st, out)
                    if data['inbox'].get('status_scan', {}).get('deadline'):
                        raise TimeoutError('inbox status scan deadline; partial results saved')
                elif action == "summary":
                    data["summary"] = daily_summary(cfg, st)
                    out.extend(summary_lines(data["summary"]))
            st.setdefault("ticks_done", []).append(key)
            st["ticks_done"] = st["ticks_done"][-64:]
            HS.save_state(cfg, st)
    except Exception as e:
        data.update(ok=False, error=str(e))
        out.append("本跳失败，未用额度留待下跳：%s" % e)
        try:
            st.setdefault("ticks_failed", []).append({"key": key, "error": str(e), "ts": HC.stamp(cfg)})
            st["ticks_failed"] = st["ticks_failed"][-64:]
            if state_loaded:
                HS.save_state(cfg, st)
            else:
                out.append("state 未成功载入，保留原文件，失败审计仅留本次输出")
        except Exception as exc:
            out.append("失败跳审计保存失败:%s" % exc)
    finally:
        data["today_sent"] = HQ.tick_today_sent(st)
        try:
            if br is not None:
                try:
                    data["tabs_closed"] = br.close_own_tabs()
                    out.append("已关自己开的标签页 %d 个" % data["tabs_closed"])
                finally:
                    br.close()
        except Exception as e:
            data.update(ok=False, cleanup_error=str(e))
            out.append("标签清理失败:%s" % e)
        finally:
            lock_context.__exit__(None, None, None)
    return data


def cmd_tick(args):
    cfg = HC.load_config(HC.config_path(args), create_dirs=not args.dry_run)
    now = HC.now_local(cfg)
    plan = HQ.tick_plan(cfg, now)
    out = ["这跳做 %s（%s %s）" % ("/".join(plan["actions"]) or "不在窗口，跳过", plan["now"], plan["timezone"])]
    if args.dry_run:
        st = HS.load_state(cfg)
        sending = cfg.get("sending") or {}
        budget = max(0, int(sending.get("daily_cap") or 0) - st["day_count"])
        modern = HF.grading_enabled(cfg) or "grade_quota" in sending
        quotas = HQ.quotas_for_now(cfg, st, now) if modern else HQ.grade_limits(cfg)
        if modern:
            budget = min(budget, max(0, int(sending.get("max_per_window", 8))))
        if not modern or not sending.get("carry_over", "grade_quota" in sending):
            budget = min(budget, max(0, int((cfg.get("schedule") or {}).get("per_window") or 0)))
        if modern:
            quotas = HQ.shrink_quotas(quotas, budget)
        budget = min(budget, sum(quotas.values())) if "send" in plan["actions"] else 0
        data = dict(plan, cmd="tick", ok=True, dry_run=True, budget={"limit": budget, "same_hr_window_days": (cfg.get("filters") or {}).get("same_hr_window_days")}, quotas=quotas)
        # dry-run 只读 state/ledger，不写日志、不创建浏览器或锁。
        if want_json(args):
            emit_json(data)
        else:
            print(out[0])
            print("本跳预算 %d；各级配额 %s" % (budget, json.dumps(quotas, ensure_ascii=False)))
        return 0
    errs, warns = HQ.validate_cfg(cfg)
    if errs:
        data = dict(plan, cmd="tick", ok=False, errors=errs)
        out.extend(errs)
    else:
        data = run_tick(cfg, {}, now, out)
    if want_json(args):
        emit_json(data, cfg, out)
    else:
        emit(cfg, out)
    return 0 if data["ok"] else 1


@locked_command
def cmd_send(args):
    cfg = HC.load_config(HC.config_path(args))
    st = HS.load_state(cfg)
    br = CM.Browser(cfg)
    bridge, ext = "", ""
    out = ["bridge:%s" % bridge, "extension:%s" % ext]
    tab = None
    closed = 0
    res = {"sent": 0, "day_count": st.get("day_count"),
           "daily_cap": int((cfg.get("sending") or {}).get("daily_cap") or 0),
           "items": [], "skipped": []}
    try:
        bridge = br.connect()
        ext = br.wake
        first = (cfg.get("targets") or [{}])[0]
        tab = br.new_tab("https://www.zhipin.com/web/geek/jobs?city=%s"
                         % str(first.get("city_code") or ""))
        time.sleep(3)
        res = O.do_send(cfg, br, tab, st, args.n, out)
    finally:
        closed = O.cleanup_browser(br)
        out.append("已关自己开的标签页 %d 个" % closed)
        HS.save_state(cfg, st)
    if want_json(args):
        emit_json(dict(res, cmd="send", ts=HC.stamp(cfg), bridge=bridge, extension=ext,
                       tabs_closed=closed), cfg, out)
        return 0
    emit(cfg, out)
    return 0


@locked_command
def cmd_chat(args):
    import conversation
    cfg = HC.load_config(HC.config_path(args))
    out = []
    br = CM.Browser(cfg)
    try:
        # Disabled/out-of-hours hops still use run's guards without connecting.
        options = cfg.get('conversation') or {}
        if (not options.get('enabled', False) or
                HC.now_local(cfg).hour not in options.get('hours', list(range(9, 22)))):
            data = conversation.run(cfg, br, out, dry=args.dry)
        else:
            br.connect()
            _, login = O.check_login(br, cfg)
            if login.get('login') or not login.get('avatar'):
                raise RuntimeError('not_logged_in_or_unconfirmed')
            data = conversation.run(cfg, br, out, dry=args.dry)
    finally:
        # run cleans up its tabs; this also covers connection/login failures.
        if getattr(br, 'opened', None):
            O.cleanup_browser(br)
        else:
            br.close()
    if want_json(args):
        emit_json(dict(data, cmd='chat', ts=HC.stamp(cfg)), cfg, out)
    else:
        emit(cfg, out)
    return 0 if data.get('ok', True) else 1


@locked_command
def cmd_inbox(args):
    cfg = HC.load_config(HC.config_path(args))
    st = HS.load_state(cfg)
    br = CM.Browser(cfg)
    bridge, ext = "", ""
    out = ["bridge:%s" % bridge, "extension:%s" % ext]
    tab = None
    data, closed = {}, 0
    try:
        bridge = br.connect()
        ext = br.wake
        tab = br.new_tab("https://www.zhipin.com/web/geek/chat")
        time.sleep(4)
        data = inbox(cfg, br, tab, st, out)
    finally:
        closed = O.cleanup_browser(br)
        out.append("已关自己开的标签页 %d 个" % closed)
        HS.save_state(cfg, st)
    if want_json(args):
        emit_json(dict(data, cmd="inbox", ts=HC.stamp(cfg), bridge=bridge, extension=ext,
                       tabs_closed=closed, resume_followups=st.get('resume_followups', [])), cfg, out)
        return 0
    emit(cfg, out)
    return 0


@locked_command
def cmd_run(args):
    cfg = HC.load_config(HC.config_path(args))
    st = HS.load_state(cfg)
    br = CM.Browser(cfg)
    bridge, ext = "", ""
    out = ["bridge:%s" % bridge, "extension:%s" % ext]
    tab = None
    cs, closed = [], 0
    scan_res, send_res, inbox_res = {"pages_saved": 0, "errors": []}, None, None
    try:
        bridge = br.connect()
        ext = br.wake
        scan_out = O.scan(cfg, br, pages=args.pages)
        scan_res = {"pages_saved": scan_out[0], "errors": scan_out[1]}
        out.append("scan: +%d 页%s" % (scan_out[0], (" ⚠️ " + ",".join(scan_out[1])) if scan_out[1] else ""))
        cs = HS.candidates(cfg)
        out.append("候选 %d（A%d/B%d）" % (len(cs), sum(1 for x in cs if x["grade"].rstrip("?") == "A"),
                                         sum(1 for x in cs if x["grade"].rstrip("?") == "B")))
        per = args.send if args.send is not None else (None if (cfg.get("sending") or {}).get("carry_over") else int(
            (cfg.get("schedule") or {}).get("per_window") or 0))
        if per is None or per:
            tab = br.new_tab("https://www.zhipin.com/web/geek/jobs?city=%s"
                             % str((cfg.get("targets") or [{}])[0].get("city_code") or ""))
            time.sleep(3)
            send_res = O.do_send(cfg, br, tab, st, per, out)
        if args.inbox:
            if tab is None:
                tab = br.new_tab("https://www.zhipin.com/web/geek/chat")
                time.sleep(4)
            inbox_res = inbox(cfg, br, tab, st, out)
    finally:
        closed = O.cleanup_browser(br)
        out.append("已关自己开的标签页 %d 个" % closed)
        HS.save_state(cfg, st)
    if want_json(args):
        emit_json({"cmd": "run", "ok": True, "ts": HC.stamp(cfg), "bridge": bridge,
                   "extension": ext, "tabs_closed": closed, "scan": scan_res,
                   "candidates": {"total": len(cs),
                                  "grade_a": sum(1 for x in cs if x["grade"].rstrip("?") == "A"),
                                  "grade_b": sum(1 for x in cs if x["grade"].rstrip("?") == "B")},
                   "send": send_res, "inbox": inbox_res}, cfg, out)
        return 0
    emit(cfg, out)
    return 0


def cmd_ledger_ack(args):
    from send_safety import ledger_ack
    cfg = HC.load_config(HC.config_path(args), create_dirs=False)
    try:
        if args.list:
            result = {'items': [r for r in ledger_latest(cfg)
                                if r['status'] in {'pending', 'unknown'}]}
        else:
            with state_lock(cfg['paths']['state']):
                result = ledger_ack(cfg, args.event_id, args.sent, args.not_sent,
                                    args.conv_key, args.text_sha256, args.note)
        result.update(cmd='ledger-ack', ok=True)
        code = 0
    except (ValueError, RuntimeError, OSError) as exc:
        result, code = dict(cmd='ledger-ack', ok=False, error=str(exc)), 1
    if want_json(args):
        emit_json(result)
    else:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    return code


def cmd_ledger_repair(args):
    cfg = HC.load_config(HC.config_path(args), create_dirs=False)
    if args.apply:
        with state_lock(cfg['paths']['state']):
            result = ledger_repair(cfg, apply=True)
    else:
        result = ledger_repair(cfg)
    result.update(cmd='ledger-repair', ok=True, dry_run=not args.apply)
    if want_json(args):
        emit_json(result)
    else:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


def cmd_reset_state(args):
    cfg = HC.load_config(HC.config_path(args))
    path = cfg['paths']['state']
    if args.confirm != os.path.abspath(path):
        print('恢复需二次确认：--confirm ' + os.path.abspath(path), file=sys.stderr)
        return 2
    with state_lock(path):
        if ledger_scan(cfg)[2]:
            print('pending_verification：请先运行 ledger-repair --dry-run，再用 --apply 隔离', file=sys.stderr)
            return 1
        ledger_guard(cfg)
        if os.path.exists(path):
            import shutil
            shutil.copy2(path, path + '.backup.' + uuid.uuid4().hex)
        HS.save_state(cfg, {'sent': {}, 'day': '', 'day_count': 0})
    return 0


def add_json_flag(p):
    """Accept --json after the subcommand too, without clobbering the global value."""
    p.add_argument("--json", action="store_true", default=argparse.SUPPRESS,
                   help="输出单个 JSON 对象（给 harness / 脚本解析）")


def build_parser():
    ap = argparse.ArgumentParser(description="BOSS直聘 求职助手（可移植版）")
    ap.add_argument("--config", help="配置文件路径（默认 ~/.boss-hunt/config.json）")
    ap.add_argument("--json", action="store_true",
                    help="输出单个 JSON 对象（给 harness / 脚本解析；人类文本仍进 runs.log）")
    sub = ap.add_subparsers(dest="cmd")

    p = sub.add_parser("reset-state", help="显式恢复状态，保留备份；必须再次确认绝对路径")
    p.add_argument("--confirm", help="再次确认要恢复的 state 绝对路径")
    p.set_defaults(func=cmd_reset_state)

    p = sub.add_parser("init", help="生成配置模板")
    p.add_argument("--force", action="store_true")
    add_json_flag(p)
    p.set_defaults(func=cmd_init)

    p = sub.add_parser("validate", help="校验配置（不联网）")
    add_json_flag(p)
    p.set_defaults(func=cmd_validate)

    p = sub.add_parser("template", help="预览招呼语")
    p.add_argument("--job-name")
    p.add_argument("--brand")
    p.add_argument("--city")
    add_json_flag(p)
    p.set_defaults(func=cmd_template)

    p = sub.add_parser("grade", help="离线按 JD 终判等级")
    p.add_argument("--jd-file")
    p.add_argument("--job-name")
    p.add_argument("--brand")
    p.add_argument("--industry")
    add_json_flag(p)
    p.set_defaults(func=cmd_grade)

    p = sub.add_parser("grades", help="离线统计 raw 快照粗标分布")
    p.add_argument("--top", type=int, default=20)
    p.add_argument("--by-src", action="store_true", help="严格只读，按来源统计 open 与本地 JD 终判；缓存 JD 不校验 TTL/jd_fp；行内 open 不去重")
    add_json_flag(p)
    p.set_defaults(func=cmd_grades)

    p = sub.add_parser("regrade", help="只读回放缓存 JD，诊断规则门槛与候选池")
    p.add_argument("--alt-config")
    p.add_argument("--top", type=int, default=20)
    p.add_argument("--near-miss-threshold", type=int, default=5)
    p.add_argument("--pool-threshold", type=int, default=45)
    add_json_flag(p)
    p.set_defaults(func=cmd_regrade)

    p = sub.add_parser("greet", help="预览某岗位现写的招呼语（llm 模式，离线）")
    p.add_argument("--job-id", help="raw/ 快照里的 encryptJobId")
    p.add_argument("--job-name", help="没有 job-id 时手工指定岗位名")
    p.add_argument("--brand", help="手工指定公司名")
    p.add_argument("--city")
    p.add_argument("--skills", help="逗号分隔的技能标签（手工指定岗位时用）")
    p.add_argument("--jd", help="直接给 JD 正文")
    p.add_argument("--jd-file", help="从文件读 JD 正文")
    p.add_argument("--times", type=int, default=1, help="连续生成几条看多样性（默认 1）")
    p.add_argument("--no-cache", action="store_true", help="忽略缓存重新生成")
    add_json_flag(p)
    p.set_defaults(func=cmd_greet)

    p = sub.add_parser("reply-to", help="按你给的意思回某个会话（并记录话术）")
    p.add_argument("target", help="新招呼列表里的公司名/HR 名片段")
    p.add_argument("--brief", required=True, help="你想表达的意思（不用写成成品）")
    p.add_argument('--force', action='store_true', help='按指定主题回答（仍检查轮次）')
    p.add_argument('--new-turn', action='store_true', help='明确追加一条新消息（仍按 op-id 去重）')
    p.add_argument('--op-id', help='人工回复操作标识；重试使用相同标识')
    p.add_argument("--dry", action="store_true", help="只预览不发送")
    add_json_flag(p)
    p.set_defaults(func=cmd_reply_to)

    p = sub.add_parser("human-compact", help="合并人工话术（不需要浏览器）")
    p.add_argument("--dry", action="store_true", help="只预览不写文件")
    add_json_flag(p)
    p.set_defaults(func=cmd_human_compact)

    p = sub.add_parser("human-stats", help="看已沉淀的人工话术")
    add_json_flag(p)
    p.set_defaults(func=cmd_human_stats)

    p = sub.add_parser("login-check", help="只读确认登录态")
    add_json_flag(p)
    p.set_defaults(func=cmd_login)

    p = sub.add_parser("preflight", help="登录 + 招呼语开关体检")
    p.add_argument("--fix", action="store_true", help="自动关掉 BOSS 自带的招呼语")
    add_json_flag(p)
    p.set_defaults(func=cmd_preflight)

    p = sub.add_parser("scan", help="抓岗位列表")
    p.add_argument("--target", action="append", help="只跑某个 target.name（可重复）")
    p.add_argument("--keyword", action="append", help="只跑某些关键词（校准用）")
    p.add_argument("--pages", type=int, default=3)
    add_json_flag(p)
    p.set_defaults(func=cmd_scan)

    p = sub.add_parser("plan", help="干跑：只报告不发送")
    p.add_argument("--top", type=int, default=20)
    add_json_flag(p)
    p.set_defaults(func=cmd_plan)

    p = sub.add_parser("summary", help="离线当天总结（不推送）")
    add_json_flag(p)
    p.set_defaults(func=cmd_summary)

    p = sub.add_parser('unanswered', help='离线查看谁在等谁（不打开浏览器）')
    p.add_argument('--hours', type=float, default=72)
    p.add_argument('--limit', type=int, default=5)
    p.add_argument('--unknown', action='store_true', help='只列归属待核对会话')
    add_json_flag(p)
    p.set_defaults(func=cmd_unanswered)

    p = sub.add_parser("rejects", help="离线拒绝/无回复分组统计")
    p.add_argument("--days", type=int, default=30)
    add_json_flag(p)
    p.set_defaults(func=cmd_rejects)

    p = sub.add_parser('ledger-ack', help='核对未知发送，追加终态并保留原始记录')
    p.add_argument('event_id', nargs='?')
    mode = p.add_mutually_exclusive_group(required=True)
    mode.add_argument('--list', action='store_true')
    mode.add_argument('--sent', action='store_true')
    mode.add_argument('--not-sent', action='store_true')
    p.add_argument('--conv-key')
    p.add_argument('--text-sha256')
    p.add_argument('--note', default='')
    add_json_flag(p)
    p.set_defaults(func=cmd_ledger_ack)

    p = sub.add_parser('ledger-repair', help='离线发送流水诊断与损坏隔离（默认只读）')
    repair_mode = p.add_mutually_exclusive_group()
    repair_mode.add_argument('--dry-run', action='store_true')
    repair_mode.add_argument('--apply', action='store_true')
    add_json_flag(p)
    p.set_defaults(func=cmd_ledger_repair)

    p = sub.add_parser("tick", help="按配置时区执行本窗口动作")
    p.add_argument("--dry-run", action="store_true", help="只打印动作计划，不开浏览器")
    add_json_flag(p)
    p.set_defaults(func=cmd_tick)

    p = sub.add_parser("send", help="真发招呼")
    p.add_argument("n", type=int, help="本轮最多发几个（首跑建议 1-2）")
    add_json_flag(p)
    p.set_defaults(func=cmd_send)

    p = sub.add_parser('chat', help='按轮次处理 HR 对话')
    p.add_argument('--dry', action='store_true', help='只列出计划动作，不发送、不写对话存档')
    add_json_flag(p)
    p.set_defaults(func=cmd_chat)

    p = sub.add_parser("inbox", help="盯收件箱")
    add_json_flag(p)
    p.set_defaults(func=cmd_inbox)

    p = sub.add_parser("run", help="扫描 + 发送 + 收件箱")
    p.add_argument("--send", type=int, help="本轮发送数（默认用 schedule.per_window）")
    p.add_argument("--pages", type=int, default=3)
    p.add_argument("--inbox", action="store_true", help="同时处理收件箱")
    add_json_flag(p)
    p.set_defaults(func=cmd_run)
    return ap


@locked_command
def cmd_reply_to(args):
    """按本次人工确认要点生成并发送；要点不持久化为学习事实。

    用法：boss_hunt.py reply-to 示例公司 --brief "简单说某平台BDM，带6人，新签80+家" [--dry]
    """
    cfg = HC.load_config(HC.config_path(args))
    st = HS.load_state(cfg)
    br = CM.Browser(cfg)
    bridge = ""
    out = []
    try:
        bridge = br.connect()
        ext = br.wake
        tab = br.new_tab(chat_dom.CHAT_URL)
        time.sleep(5)
        if not chat_dom.goto_tab(br, tab, chat_dom.NEW_GREET_TAB):
            out.append("切不到「新招呼」标签")
            emit(cfg, out)
            return 2
        rows = chat_dom.conversation_rows(br, tab)
        try:
            target = chat_dom.select_conversation(rows, args.target)
        except ValueError as exc:
            out.append(str(exc))
            emit(cfg, out)
            return 2
        key = target['conv_key']
        stt, item, msgs = chat_dom.open_by_index(br, tab, rows.index(target), conv_key=key)
        item = chat_dom.clean_item(item)
        if stt != "ok" or not msgs:
            out.append("会话打不开(%s)：%s" % (stt, item))
            emit(cfg, out)
            return 2
        hr_text = msgs[-1].get("text") if msgs else ""
        trigger, operation = reply_operation(key, msgs, args.op_id)
        previous = terminal_operation(cfg, operation)
        if previous:
            if previous['status'] == 'sent':
                HS.remember_reply(cfg, st, key, 'reply-to')
                HS.save_state(cfg, st)
            if want_json(args):
                emit_json({'cmd': 'reply-to', 'ok': True, 'result': 'duplicate_op', 'op_id': operation})
            else:
                out.append('duplicate_op: ' + operation)
                emit(cfg, out)
            return 0
        gr = R.generate(cfg, {"brandName": item}, args.brief, log=out.append,
                        history=msgs, force=True, new_turn=args.new_turn)
        draft = gr.get("text") or ""
        if not draft:
            out.append("按要点写不出合格的回复：%s" % "、".join(gr.get("reasons") or []))
            emit(cfg, out)
            return 2
        out.append("对象：%s" % item)
        out.append("HR 说：%s" % (hr_text or "")[:80])
        out.append("你的意思：%s" % args.brief)
        out.append("要发的话：%s" % draft)
        if args.dry:
            out.append("（预演，未发送）")
            emit(cfg, out)
            return 0
        res = send_in_chat(br, tab, draft, draft[:6], key, cfg, "reply-to", expected_history=msgs,
                           new_turn=args.new_turn, op_id=operation,
                           fact_context=dict(gr['fact_context'], conv_key=key, op_id=operation,
                                             draft_fingerprint=chat_dom.history_fingerprint(msgs)) if gr.get('fact_context') else None)
        ok = (res == "replied")
        sit = H.categorize(hr_text)
        if ok:
            HS.remember_reply(cfg, st, key, 'reply-to', {
                'source': 'human:' + sit, 'item': item, 'hr': (hr_text or '')[:80]})
            HS.save_state(cfg, st)
            out.append("✅ 已发出（归类：%s，本次要点仅用于本次操作）" % sit)
        else:
            out.append("⚠️ 发送失败(%s)，请核对结果后再处理" % res)
        emit(cfg, out)
        return 0 if ok else 1
    finally:
        O.cleanup_browser(br)


def cmd_human_compact(args):
    cfg = HC.load_config(HC.config_path(args))
    try:
        result = H.compact(cfg, dry=args.dry)
    except H.HumanStoreError as exc:
        if want_json(args):
            emit_json({'cmd': 'human-compact', 'ok': False, 'error': str(exc)})
        else:
            print(str(exc), file=sys.stderr)
        return 1
    if want_json(args):
        emit_json(dict(result, cmd="human-compact", ok=True))
    else:
        for item in result["items"]:
            print("%s：before=%d → after=%d method=%s" % (
                item["situation"], item["before"], item["after"], item["method"]))
        print("model_calls=%d max_model_calls=%d dry=%s" % (
            result["model_calls"], result["max_model_calls"], args.dry))
    return 0


def cmd_human_stats(args):
    cfg = HC.load_config(HC.config_path(args))
    rows = H.stats(cfg)
    if want_json(args):
        emit_json({"cmd": "human-stats", "ok": True,
                   "hints": [{"situation": s, "count": c, "last": l, "brief": b}
                             for s, c, l, b in rows]})
        return 0
    if not rows:
        print("还没有人工介入记录。用 reply-to 回一次就会积累。")
        return 0
    for s, c, l, b in rows:
        print("%s ×%d（%s） 要点：%s" % (s, c, l, b[:60].replace("\n", " ").replace("\r", " ")))
    return 0


def main():
    ap = build_parser()
    args = ap.parse_args()
    if not getattr(args, "func", None):
        ap.print_help()
        return 2
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
