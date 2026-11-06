"""投递状态、账本同步与缓存管理。"""
import hashlib
import math
import json
import os
import uuid
import send_safety as SS
import hunt_config as HC
import hunt_filter as HF
import hunt_schedule as HQ


def contact_last_sent(st, cfg):
    """Read sent contact timestamps once per command/tick; never persist the cache."""
    path = SS.ledger_path(cfg)
    cached = cfg.get("_contact_last_sent_cache")
    if cached is not None and cached[0] == path:
        return cached[1]
    latest = {}
    available = False
    try:
        with open(path, encoding="utf-8", errors="replace") as rows:
            available = True
            for line in rows:
                try:
                    row = json.loads(line)
                    if (not isinstance(row, dict) or row.get("status") != "sent"
                            or not isinstance(row.get("brand"), str) or not row["brand"]
                            or not isinstance(row.get("boss"), str) or not row["boss"]):
                        continue
                    try:
                        epoch = float(row.get("ts_epoch"))
                        if epoch > 1e11:
                            epoch /= 1000.0
                    except (TypeError, ValueError, OverflowError):
                        epoch = float("nan")
                    if not math.isfinite(epoch):
                        dt = HC.parse_local_ts(row.get("ts"), cfg)
                        if dt is None:
                            continue
                        epoch = dt.timestamp()
                    key = row["brand"] + "|" + row["boss"]
                    latest[key] = max(latest.get(key, epoch), epoch)
                except (ValueError, TypeError, OverflowError, OSError):
                    continue
    except OSError:
        pass
    cfg["_contact_last_sent_cache"] = (path, latest, available)
    return latest


def same_hr_filter(st, cfg, job):
    """Shared candidate/pool/send rule; unknown timestamps fail closed."""
    key = "%s|%s" % (job.get("brandName"), job.get("bossName"))
    if key not in st.get("contacts", []):
        return None
    days = (cfg.get("filters") or {}).get("same_hr_window_days")
    if days is None:
        return "skip_same_hr"
    if days == 0:
        return "same_hr_window_pass"
    last = contact_last_sent(st, cfg).get(key)
    if last is None:
        return "skip_same_hr_unknown_ts"
    if HC.now_local(cfg).timestamp() - last >= days * 86400:
        return "same_hr_window_pass"
    return "skip_same_hr"


def candidates(cfg, jobs=None, st=None):
    if st is None:
        st = load_state(cfg)
    sc = cfg.get("scoring") or {}
    out = []
    for j in (jobs if jobs is not None else HF.load_jobs(cfg)):
        verdict, reasons, flags = HF.judge(j, cfg)
        if verdict == "reject":
            continue
        if j["encryptJobId"] in st.get("sent", {}):
            continue
        contact_result = same_hr_filter(st, cfg, j)
        if contact_result in ("skip_same_hr", "skip_same_hr_unknown_ts"):
            continue
        if contact_result:
            flags = list(flags) + [contact_result]
        s = HF.score(j, cfg)
        if s < int(sc.get("min_score", 0)):
            continue
        gr = HF.grade_job(j, cfg)
        out.append({"grade": gr["grade"], "grade_final": gr["final"],
                    "grade_reason": gr["reason"], "score": s, "reasons": reasons, "flags": flags, "job": j})
    out.sort(key=HF.grade_sort_key)
    return out


def load_state(cfg):
    path = cfg["paths"]["state"]
    st = {"sent": {}, "contacts": [], "day": "", "day_count": 0, "day_by_target": {},
          "inbox_seen": [], "replied": [], "followed": [],
          "day_by_grade": {}, "b_pool": {}, "grade_cache": {}, "conv_state": {}, "checked_jobs": {},
          "retry_jobs": {}, "platform_chatted": {}, "opened_no_greet": {}}
    try:
        with open(path, encoding="utf-8") as f:
            loaded = json.load(f)
    except FileNotFoundError:
        loaded = {}
    except (OSError, ValueError) as exc:
        raise ValueError("state %s: %s" % (path, exc)) from exc
    expected = {k: type(v) for k, v in st.items()}
    expected.update(auto_replies=dict, pending=dict, ticks_done=list, review_pool=dict, manual_grade=dict,
                    ticks_failed=list, missed_reported=list)
    if not isinstance(loaded, dict):
        raise ValueError("state %s: expected object" % path)
    for key, kind in expected.items():
        if key in loaded and type(loaded[key]) is not kind:
            raise ValueError("state %s: invalid %s (expected %s)" % (path, key, kind.__name__))
    # 旧 sent 是 jid -> 时间字符串；统一成 dict，保持下游 .get() 读取兼容。
    for jid, row in loaded.get('sent', {}).items():
        if isinstance(row, str):
            loaded['sent'][jid] = {'ts': row}
    for key in ('sent', 'auto_replies', 'pending', 'grade_cache', 'b_pool', 'conv_state', 'review_pool', 'manual_grade'):
        if any(not isinstance(v, dict) for v in loaded.get(key, {}).values()):
            raise ValueError("state %s: invalid %s entry" % (path, key))
    for key in ('day_by_target', 'day_by_grade'):
        if any(type(v) is not int or v < 0 for v in loaded.get(key, {}).values()):
            raise ValueError("state %s: invalid %s count" % (path, key))
    if any(not isinstance(v, str) for v in loaded.get('checked_jobs', {}).values()):
        raise ValueError("state %s: invalid checked_jobs entry" % path)
    for row in loaded.get('retry_jobs', {}).values():
        if (not isinstance(row, dict) or not isinstance(row.get('fp'), str)
                or type(row.get('n')) is not int or row['n'] < 0):
            raise ValueError("state %s: invalid retry_jobs entry" % path)
    if loaded.get('day_count', 0) < 0:
        raise ValueError("state %s: negative day_count" % path)
    for key in ('contacts', 'inbox_seen', 'replied', 'followed', 'ticks_done'):
        if any(not isinstance(v, str) for v in loaded.get(key, [])):
            raise ValueError("state %s: invalid %s entry" % (path, key))
    for key in ('sent', 'auto_replies'):
        for row in loaded.get(key, {}).values():
            for field in ('ts', 'applied_ts', 'grade', 'text', 'greet_text'):
                if field in row and row[field] is not None and not isinstance(row[field], str):
                    raise ValueError("state %s: invalid %s.%s" % (path, key, field))
    st.update(loaded)
    for k, v in (("sent", {}), ("contacts", []), ("day_by_target", {}),
                 ("inbox_seen", []), ("replied", []), ("followed", []),
                 ("day_by_grade", {}), ("b_pool", {}), ("grade_cache", {})):
        st.setdefault(k, v)
    today = HC.stamp(cfg, "%Y-%m-%d")
    if st.get("day") != today:
        st["day"], st["day_count"], st["day_by_target"], st["day_by_grade"] = today, 0, {}, {}
        st["checked_jobs"] = {}
        st["retry_jobs"] = {}
    # 旧 state 在当天迁移时从已发记录恢复各级已用量，不凭空重置。
    for grade in HQ.grade_limits(cfg):
        known = sum(1 for row in st["sent"].values()
                    if (row.get("applied_ts") or row.get("ts") or "").startswith(today)
                    and row.get("grade") == grade)
        st["day_by_grade"][grade] = max(int(st["day_by_grade"].get(grade, 0)), known)
    refresh_ledger_counts(cfg, st)
    return st


def record_outcome(cfg, st, job, status, phase, window_key):
    """记录当天结论或临时失败；可能发送过的结果只交给 ledger。"""
    if status.startswith(("skip_min_gap", "detail_unavailable:")) or status in (
            "skip_captcha", "skip_network", "skip_not_logged_in", "detail_unavailable",
            "skip_daily_cap", "pending_verification", "unknown"):
        return "halt"
    jid = job["encryptJobId"]
    if phase == "send" and status == "sent":
        st.setdefault("retry_jobs", {}).pop(jid, None)
        return "none"
    retryable = ((phase == "grade" and status in ("skip_missing_jd", "skip_no_button"))
                 or (phase == "send" and status in ("skip_no_button", "error: unknown_conversation", "error: chat_not_opened")))
    opened = phase == "send" and (status.startswith("send_aborted:") or status in (
        "fill_err", "stale_draft", "chat_not_opened"))
    terminal = status in ("skip_already_chatted", "skip_grade", "skip_jd_redline",
                          "err_no_greeting", "err_template_too_long", "skip_need_app") or opened
    if not retryable and not terminal:
        return "none"
    fp = HF.grade_fingerprint(job, cfg)
    retries = st.setdefault("retry_jobs", {})
    if retryable:
        retry_fp = HF.stable_fingerprint(job, cfg)
        old = retries.get(jid) or {}
        n = old.get("n", 0) + 1 if old.get("fp") == retry_fp else 1
        # max=N 表示首次失败后最多重试 N 次；max=0 即失败一次当天终态。
        limit = int((cfg.get("sending") or {}).get("transient_retry_max", 2))
        if n <= limit:
            retries[jid] = {"fp": retry_fp, "n": n, "last": HC.stamp(cfg), "window": window_key}
            return "retry"
    st.setdefault("checked_jobs", {})[jid] = fp
    retries.pop(jid, None)
    if status == "skip_already_chatted":
        st.setdefault("platform_chatted", {})[jid] = HC.stamp(cfg)
    if opened:
        st.setdefault("opened_no_greet", {})[jid] = {"ts": HC.stamp(cfg), "reason": status}
    return "terminal"


def cached_jd(cfg, job, st):
    ttl = (cfg.get("grading") or {}).get("jd_cache_ttl_days", 7)
    if ttl == 0:
        return None
    entry = (st.get("grade_cache") or {}).get(job["encryptJobId"])
    if not entry or not entry.get("jd_text"):
        return None
    if "jd_fp" not in entry:
        if entry.get("fingerprint") != HF.legacy_grade_fingerprint(job, cfg):
            return None
        entry.update(jd_fp=HF.jd_fingerprint(job), jd_ts=HC.stamp(cfg), rule_fp=HF.rule_fingerprint(cfg),
                     job={k: job.get(k) for k in HF.JD_FIELDS}, fingerprint=HF.grade_fingerprint(job, cfg))
    if entry.get("jd_fp") != HF.jd_fingerprint(job):
        return None
    ts = HC.parse_local_ts(entry.get("jd_ts"), cfg)
    if ts is None or not 0 <= (HC.now_local(cfg) - ts).total_seconds() <= ttl * 86400:
        return None
    return entry


def cached_grade(cfg, job, st):
    entry = cached_jd(cfg, job, st)
    if entry is None:
        return None
    if entry.get("rule_fp") != HF.rule_fingerprint(cfg):
        previous = entry.get("grade")
        entry.update(HF.grade_with_jd(job, cfg, entry["jd_text"]))
        entry.update(rule_fp=HF.rule_fingerprint(cfg), fingerprint=HF.grade_fingerprint(job, cfg),
                     cached=True, regraded=True)
        if previous == "B" and entry["grade"] == "C":
            st.get("b_pool", {}).pop(job["encryptJobId"], None)
    return entry


def save_state(cfg, st):
    path = cfg["paths"]["state"]
    tmp = path + ".tmp.%s.%s" % (os.getpid(), uuid.uuid4().hex)
    try:
        with open(tmp, "x", encoding="utf-8") as f:
            json.dump(st, f, ensure_ascii=False, indent=1)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)


def refresh_ledger_counts(cfg, st):
    if not os.path.exists(SS.ledger_path(cfg)):
        return  # legacy state migrates under the command lock before sending
    today = HC.stamp(cfg, "%Y-%m-%d")
    events = [r for r in SS.ledger_latest(cfg) if r['status'] == 'sent']
    for row in events:
        jid = row.get('job_id')
        if row['cmd'] in {'interview_reply', 'followup'} and jid:
            field = 'replied' if row['cmd'] == 'interview_reply' else 'followed'
            if jid not in st.setdefault(field, []):
                st[field].append(jid)
            if field == 'replied':
                st.setdefault('pending', {}).pop(jid, None)
        if row['cmd'] in {'auto_reply', 'reply-to'}:
            st.setdefault('auto_replies', {})[row['event_id']] = dict(row)
    rows = [r for r in events if r['cmd'] == 'send']
    st['day'], st['day_count'], st['day_by_grade'], st['day_by_target'] = today, 0, {}, {}
    for row in rows:
        jid = row.get('job_id')
        if jid:
            st.setdefault('sent', {}).setdefault(jid, dict(row, applied_ts=row['ts']))
        if row.get('brand') and row.get('boss'):
            contact = row['brand'] + '|' + row['boss']
            if contact not in st.setdefault('contacts', []):
                st['contacts'].append(contact)
        if row['ts'].startswith(today):
            st['day_count'] += 1
            for field, source in [('day_by_grade', 'grade'), ('day_by_target', 'target')]:
                key = row.get(source)
                if key:
                    st[field][key] = st[field].get(key, 0) + 1


def migrate_ledger(cfg, st):
    from send_safety import ledger_append
    existing = SS.ledger_latest(cfg)
    event_ids = {r['event_id'] for r in existing}
    job_ids = {r.get('job_id') for r in existing if r['cmd'] == 'send' and r['status'] == 'sent'}
    for jid, row in st.get('sent', {}).items():
        if jid in job_ids:
            continue
        text = row.get('greet_text') or ''
        ledger_append(cfg, dict(row, event_id='legacy-job:' + jid, cmd='send',
            conv_key='', job_id=jid, text=text, text_sha256=hashlib.sha256(text.encode()).hexdigest(),
            ts=row.get('applied_ts') or row.get('ts') or '', status='sent'))
    for key, row in st.get('auto_replies', {}).items():
        if key in event_ids or 'legacy-reply:' + key in event_ids:
            continue
        if any(event['status'] == 'sent' and event['cmd'] in {'auto_reply', 'reply-to'}
               and event.get('conv_key') == key and event.get('text') == row.get('text')
               and event.get('ts') == row.get('ts') for event in existing):
            continue
        text = row.get('text') or ''
        ledger_append(cfg, dict(row, event_id='legacy-reply:' + key, cmd='auto_reply',
            conv_key=key, job_id='', text=text, text_sha256=hashlib.sha256(text.encode()).hexdigest(),
            ts=row.get('ts') or '', status='sent'))


def atomic_json(path, data):
    # Same atomic/fsync implementation as state; caller holds the shared lock.
    save_state({'paths': {'state': path}}, data)
