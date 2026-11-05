"""Shared wall clock. Tests can inject cfg['_clock'] without sleeping."""
import time
from datetime import datetime
from zoneinfo import ZoneInfo


def now_ts():
    return time.time()


def local_now(cfg):
    try:
        tz = ZoneInfo((cfg.get('schedule') or {}).get('timezone') or 'Asia/Shanghai')
    except Exception:
        tz = ZoneInfo('Asia/Shanghai')
    return datetime.fromtimestamp((cfg.get('_clock') or now_ts)(), tz)


def stamp(cfg, fmt='%Y-%m-%d %H:%M'):
    return local_now(cfg).strftime(fmt)
