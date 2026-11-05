"""配置读取、时区与通用时间工具。"""
import json
import os
import sys
import datetime as DT
import zoneinfo as ZI


DEFAULT_DIR = os.path.expanduser("~/.boss-hunt")


TZ_DEFAULT = "Asia/Shanghai"


TICK_WAKE_WAIT_SECONDS = 3


def config_path(args):
    if args.config:
        return os.path.abspath(os.path.expanduser(args.config))
    env = os.environ.get("BOSS_HUNT_CONFIG")
    if env:
        return os.path.abspath(os.path.expanduser(env))
    return os.path.join(DEFAULT_DIR, "config.json")


def same_hr_window_error(cfg):
    days = (cfg.get("filters") or {}).get("same_hr_window_days")
    if days is not None and (type(days) is not int or days < 0):
        return "filters.same_hr_window_days 必须是 null 或非负整数"
    return None


def load_config(path, create_dirs=True):
    with open(path, encoding="utf-8") as f:
        cfg = json.load(f)
    error = same_hr_window_error(cfg)
    if error:
        raise ValueError(error)
    workdir = os.path.join(os.path.dirname(path), "data")
    p = cfg.setdefault("paths", {})
    p.setdefault("workdir", workdir)
    p["workdir"] = os.path.abspath(os.path.expanduser(p["workdir"]))
    if create_dirs:
        os.makedirs(p["workdir"], exist_ok=True)
    p.setdefault("raw_dir", os.path.join(p["workdir"], "raw"))
    p.setdefault("state", os.path.join(p["workdir"], "state.json"))
    p.setdefault("log", os.path.join(p["workdir"], "runs.log"))
    if create_dirs:
        os.makedirs(p["raw_dir"], exist_ok=True)
    return cfg


def cfg_tz(cfg):
    try:
        return ZI.ZoneInfo((cfg.get("schedule") or {}).get("timezone") or TZ_DEFAULT)
    except Exception:
        return ZI.ZoneInfo(TZ_DEFAULT)


def now_local(cfg):
    from clock import local_now
    return local_now(cfg)


def stamp(cfg, fmt="%Y-%m-%d %H:%M"):
    return now_local(cfg).strftime(fmt)


def parse_local_ts(value, cfg):
    try:
        dt = DT.datetime.fromisoformat(str(value))
        return dt.replace(tzinfo=cfg_tz(cfg)) if dt.tzinfo is None else dt.astimezone(cfg_tz(cfg))
    except (ValueError, TypeError):
        return None


def trace(msg):
    """BOSS_TRACE=1 时把发送链路的每一步打到 stderr（真机排障用，默认静默）。"""
    if os.environ.get('BOSS_TRACE'):
        print('[trace] ' + str(msg), file=sys.stderr, flush=True)
