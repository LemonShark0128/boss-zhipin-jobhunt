"""Offline, monotone conversation state machine. Timestamps are epoch seconds."""

def update_conv_state(prev, last_msg, now, cfg):
    old = dict(prev or {})
    state = dict(last_side=None, last_ts=0, last_text='', status='unknown',
                 status_since=0, stale_hours=0, checks=0)
    state.update(old)
    state['checks'] += 1
    if last_msg.get('owner') == 'unknown':
        state.update(owner='unknown', status='unknown', last_side=None, stale_hours=0)
        state.pop('stale_since', None)
        return state
    side = last_msg.get('side')
    ts = (float(last_msg['ts_epoch']) / 1000 if last_msg.get('ts_epoch') is not None
          else float(last_msg.get('ts') or 0))
    seq = int(last_msg.get('seq') or 0)
    old_seq = int(state.get('last_seq') or 0)
    if side not in ('me', 'them') or ts < state['last_ts']:
        return state
    # Equal timestamps with conflicting content cannot establish a new turn.
    if ts == state['last_ts'] and seq < old_seq:
        return state
    if ts == state['last_ts'] and seq <= old_seq and state['last_side'] and (
            side != state['last_side'] or last_msg.get('text', '') != state['last_text']):
        return state
    changed = ts > state['last_ts'] or seq > old_seq or side != state['last_side']
    coarse = last_msg.get('ts_precision_coarse', state.get('ts_precision_coarse', False))
    status = 'waiting_me' if side == 'them' else 'waiting_them'
    hours = max(0, (now - ts) / 3600) if side == 'me' else 0
    threshold = (72.0 if (cfg.get('inbox') or {}).get('stale_hours') is None else float(cfg['inbox']['stale_hours']))
    if side == 'me' and hours >= threshold and not coarse:
        status = 'stale'
    if changed:
        state.pop('stale_since', None)
    if changed or status != state['status']:
        state['status_since'] = now if status == 'stale' else ts
    state.update(owner='mine' if side == 'me' else 'friend', last_side=side, last_ts=ts, last_text=last_msg.get('text', ''),
                 last_ts_epoch=int(ts * 1000), last_seq=seq,
                 ts_precision_coarse=coarse, status=status, stale_hours=hours)
    if status == 'stale':
        state.setdefault('stale_since', now)
    return state
