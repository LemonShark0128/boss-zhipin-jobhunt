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
    if cfg.get("filters") is not None and not isinstance(cfg.get("filters"), dict):
        return "filters 必须是对象"  # audit-01 R6：load_config 抛 ValueError，validate 能指名
    days = (cfg.get("filters") or {}).get("same_hr_window_days")
    if days is not None and (type(days) is not int or days < 0):
        return "filters.same_hr_window_days 必须是 null 或非负整数"
    return None


def stale_hours(cfg):
    """inbox.stale_hours；没写或写成 null 都按 72（audit-01 R5）。"""
    value = (cfg.get("inbox") or {}).get("stale_hours")
    return 72.0 if value is None else float(value)


def load_config(path, create_dirs=True):
    with open(path, encoding="utf-8") as f:
        cfg = json.load(f)
    # audit-01 R5：顶层和 paths 必须是对象，否则后面到处 .get 崩成堆栈
    if not isinstance(cfg, dict):
        raise ValueError("配置顶层必须是 JSON 对象")
    if cfg.get("paths") is not None and not isinstance(cfg.get("paths"), dict):
        raise ValueError("paths 必须是对象")
    if cfg.get("paths") is None:
        cfg.pop("paths", None)
    elif cfg["paths"].get("workdir") is None:
        cfg["paths"].pop("workdir", None)  # audit-01 R6：null 按没写处理，与 wrapper 一致
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
