"""Local, durable send journal. Callers hold state_lock across the transaction."""
import math
import hashlib
import json
import os
import uuid
import threading
from clock import stamp, now_ts
from contextlib import contextmanager
import fcntl

_held_locks = threading.local()


@contextmanager
def state_lock(path, reentrant=False):
    # A persistent inode is essential: unlinking a flock file creates two locks.
    lock = os.path.join(os.path.dirname(os.path.abspath(path)), '.lock')
    owned = getattr(_held_locks, 'owned', {})
    owner = (os.getpid(), lock)
    if reentrant and owner in owned:
        yield
        return
    os.makedirs(os.path.dirname(lock), exist_ok=True)
    try:
        handle = open(lock, 'a+', encoding='utf-8')
    except IsADirectoryError:
        raise RuntimeError('另一个实例在跑(pid unknown)：遗留目录锁 %s' % lock)
    with handle:
        try:
            fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            handle.seek(0)
            raise RuntimeError('另一个实例在跑(pid %s)' % (handle.read().strip() or 'unknown'))
        try:
            handle.seek(0); handle.truncate(); handle.write(str(os.getpid())); handle.flush()
            _held_locks.owned = owned
            owned[owner] = handle
            yield
        finally:
            owned.pop(owner, None)
            fcntl.flock(handle, fcntl.LOCK_UN)


DEFAULT_MIN_GAP_SECONDS = 60  # audit-01 Q4：投递/账本闸门/对话模块统一的缺省发送间隔


def min_gap_seconds(cfg):
    """sending.min_gap_seconds；未写时统一 60 秒（显式写 0 仍按 0）。"""
    value = (cfg.get('sending') or {}).get('min_gap_seconds')
    return DEFAULT_MIN_GAP_SECONDS if value is None else float(value)


def ledger_path(cfg):
    return os.path.join(cfg['paths']['workdir'], 'send_ledger.jsonl')


def ledger_scan(cfg):
    """Read a complete valid prefix; leave every damaged byte untouched."""
    from pathlib import Path
    path = Path(ledger_path(cfg))
    raw = path.read_bytes() if path.exists() else b''
    rows, offset = {}, 0
    for line in raw.splitlines(keepends=True):
        try:
            row = json.loads(line)
            if (not line.endswith(b'\n') or not isinstance(row, dict) or
                    row.get('status') not in {'pending', 'sent', 'unknown', 'failed', 'voided'} or
                    not row.get('event_id')):
                raise ValueError('invalid ledger event')
        except (ValueError, UnicodeError):
            break
        rows[row['event_id']] = row
        offset += len(line)
    return rows, raw[:offset], raw[offset:]


def ledger_latest(cfg):
    from pathlib import Path
    rows, _, tail = ledger_scan(cfg)
    # Quarantine is durable evidence of a potentially lost send event. An
    # explicit conversation-bound reconciliation can supersede this sentinel.
    for path in Path(cfg['paths']['workdir']).glob('send_ledger.corrupt-*'):
        key = 'quarantine:' + path.name
        rows.setdefault(key, dict(event_id=key, status='unknown', cmd='ledger-repair',
                                  ts='', pending_verification=True))
    if tail:
        key = 'corrupt:' + hashlib.sha256(tail).hexdigest()
        rows[key] = dict(event_id=key, status='unknown', cmd='ledger-repair',
                         ts='', pending_verification=True)
    return list(rows.values())


def ledger_repair(cfg, apply=False):
    """Dry-run by default. Caller holds state_lock for apply."""
    import tempfile
    rows, prefix, tail = ledger_scan(cfg)
    result = dict(corrupt_bytes=len(tail), quarantined_bytes=0, quarantine='',
                  pending_verification=bool(tail), pending_events=[])
    if apply and tail:
        path = ledger_path(cfg)
        quarantine = os.path.join(cfg['paths']['workdir'], 'send_ledger.corrupt-' + stamp(cfg, '%Y%m%d%H%M%S') + '-' + uuid.uuid4().hex)
        with open(quarantine, 'xb') as f:
            f.write(tail); f.flush(); os.fsync(f.fileno())
        fd, temp = tempfile.mkstemp(prefix='.ledger-repair-', dir=os.path.dirname(path))
        try:
            with os.fdopen(fd, 'wb') as f:
                f.write(prefix); f.flush(); os.fsync(f.fileno())
            os.replace(temp, path)
        finally:
            if os.path.exists(temp): os.unlink(temp)
        result.update(quarantined_bytes=len(tail), quarantine=quarantine)
    result['pending_events'] = [r['event_id'] for r in ledger_latest(cfg)
                                if r['status'] in {'pending', 'unknown'}]
    result['pending_verification'] = bool(result['pending_events'])
    return result


def ledger_append(cfg, row):
    path = ledger_path(cfg)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, 'a', encoding='utf-8') as f:
        f.write(json.dumps(row, ensure_ascii=False) + '\n')
        f.flush()
        os.fsync(f.fileno())


def confirms_new_bubble(row, conversation):
    """Require count, chronological and content evidence after a saved baseline."""
    if (conversation.get('conv_key') != row.get('conv_key') or
            any(k not in row for k in ('msg_count','last_message_id','click_ts_epoch'))):
        return False
    messages = conversation.get('messages') or []
    count = row['msg_count']
    if not isinstance(count, int) or count < 0 or len(messages) <= count:
        return False
    if count and row['last_message_id'] and messages[count-1].get('id') != row['last_message_id']:
        return False
    from datetime import datetime
    for message in messages[count:]:
        if message.get('who') != 'mine' or hashlib.sha256(
                (message.get('text') or '').encode()).hexdigest() != row.get('text_sha256'):
            continue
        try:
            raw = message.get('ts_epoch')
            if raw is not None:
                epoch = float(raw)
            else:
                raw = message.get('ts')
                try:
                    epoch = float(raw)
                    if epoch < 1e12:
                        epoch *= 1000
                except (ValueError, TypeError):
                    epoch = datetime.fromisoformat(raw.replace('Z','+00:00')).timestamp()*1000
            if math.isfinite(epoch) and epoch >= row['click_ts_epoch']:
                return True
        except (ValueError, TypeError, AttributeError):
            continue
    return False


CONV_CMDS = frozenset({'conv_text', 'conv_resume'})
GUARD_SCOPES = frozenset({'all', 'outbound', 'conv'})


def _conv_row(row):
    """带会话键的对话模块发送：safety-01 起只锁它自己的会话。"""
    return row.get('cmd') in CONV_CMDS and bool(row.get('conv_key'))


def _in_scope(row, scope, conv_key):
    if scope == 'all' or not _conv_row(row):
        return True
    if scope == 'outbound':
        return False
    return bool(conv_key) and row.get('conv_key') == conv_key


def conv_pending(cfg):
    """对话模块未核实的发送：{conv_key: [event_id, ...]}。"""
    pending = {}
    for row in ledger_latest(cfg):
        if row['status'] in {'pending', 'unknown'} and _conv_row(row):
            pending.setdefault(row['conv_key'], []).append(row['event_id'])
    return pending


def ledger_guard(cfg, verify=None, scope='all', conv_key=None):
    """Only positive, conversation-bound evidence may close an unknown send.

    Without such evidence stop before any browser/model action. Never retry an
    unresolved event. An offline operator can inspect the reported event IDs.
    safety-01：scope='outbound' 不看对话模块的未核实行；scope='conv' 只看本会话的对话行。
    非对话行、缺会话键的对话行、损坏尾巴在任何 scope 都挡。
    """
    if scope not in GUARD_SCOPES:
        raise ValueError('ledger_guard_scope: ' + str(scope))
    if ledger_scan(cfg)[2]:
        raise RuntimeError('skip_ledger_corrupt: pending_verification；请先运行 ledger-repair --dry-run，再用 --apply 隔离')
    unresolved = []
    for row in ledger_latest(cfg):
        if row['status'] in {'pending', 'unknown'}:
            if not _in_scope(row, scope, conv_key):
                continue
            has_baseline = all(k in row for k in ('msg_count', 'last_message_id', 'click_ts_epoch'))
            if has_baseline and verify is not None and verify(row) is True:
                ledger_append(cfg, dict(row, status='sent'))
            else:
                unresolved.append(row['event_id'])
    if unresolved:
        raise RuntimeError('unresolved_send: 必须先核对发送结果，禁止重发: ' + ','.join(unresolved) + '; 请运行 ledger-ack --list，再用 ledger-ack <event_id> --sent --conv-key <key> --text-sha256 <hash> 或 --not-sent')


def ledger_count(cfg, commands=None):
    today = stamp(cfg, '%Y-%m-%d')
    return sum(r['status'] == 'sent' and r['ts'].startswith(today)
               and (commands is None or r['cmd'] in commands) for r in ledger_latest(cfg))


def ledger_ack(cfg, event_id, sent=False, not_sent=False, conv_key=None, text_sha256=None, note=''):
    if ledger_scan(cfg)[2]:
        raise RuntimeError('skip_ledger_corrupt: 请先 ledger-repair --apply')
    row = next((r for r in ledger_latest(cfg) if r['event_id'] == event_id), None)
    if row is None or sent == not_sent:
        raise ValueError('ack_invalid_event')
    if sent and (not conv_key or not text_sha256 or conv_key != row.get('conv_key')
                 or text_sha256 != row.get('text_sha256')):
        raise ValueError('ack_mismatch')
    status = 'sent' if sent else 'voided'
    if row['status'] not in {'pending', 'unknown'}:
        if row['status'] != status:
            raise ValueError('ack_terminal_conflict')
        return {'event': row}
    event = dict(row, status=status, note=note, ack_ts=stamp(cfg),
                 ack_ts_epoch=int((cfg.get('_clock') or now_ts)() * 1000))
    ledger_append(cfg, event)
    return {'event': event}


def ledger_send(cfg, cmd, conv_key, job_id, text, send, metadata=None):
    with state_lock(cfg['paths']['state'], reentrant=True):
        return _ledger_send(cfg, cmd, conv_key, job_id, text, send, metadata)


def _ledger_send(cfg, cmd, conv_key, job_id, text, send, metadata=None):
    if cmd in CONV_CMDS:
        ledger_guard(cfg, scope='conv', conv_key=conv_key)
    elif cmd == 'send':
        ledger_guard(cfg, scope='outbound')
    else:
        ledger_guard(cfg)
    # Terminal events are authoritative even if the state save never happened.
    for previous in ledger_latest(cfg):
        if previous['status'] != 'sent' or previous['cmd'] != cmd:
            continue
        if cmd == 'send' and job_id and previous.get('job_id') == job_id:
            return 'sent'
    gap = min_gap_seconds(cfg)
    latest = max((r['ts_epoch'] / 1000 for r in ledger_latest(cfg)
                  if r['status'] == 'sent' and r.get('ts_epoch') is not None), default=None)
    if latest is not None:
        remaining = math.ceil(gap - ((cfg.get('_clock') or now_ts)() - latest))
        if remaining > 0:
            return 'skip_min_gap(%d)' % remaining
    row = dict(metadata or {}, event_id=uuid.uuid4().hex, ts=stamp(cfg),
               ts_epoch=int((cfg.get('_clock') or now_ts)() * 1000), cmd=cmd,
               conv_key=conv_key, job_id=job_id, text_sha256=hashlib.sha256(text.encode()).hexdigest(),
               text=text, status='pending')
    ledger_append(cfg, row)
    try:
        result = send()
    except BaseException:
        ledger_append(cfg, dict(row, status='unknown'))
        raise
    status = 'sent' if result in {'sent', 'replied'} else ('failed' if (result in {'fill_err', 'fill_mismatch', 'stale_draft', 'error: chat_not_opened'} or result.startswith('send_aborted:')) else 'unknown')
    ledger_append(cfg, dict(row, status=status, ts=stamp(cfg),
                           ts_epoch=int((cfg.get('_clock') or now_ts)() * 1000)))
    if status == 'unknown':
        raise RuntimeError('unresolved_send: ' + row['event_id'])
    return result
