"""投递配额、排班与配置校验。"""
import math
import re
import signal
import contextlib as CL
import time
import greet as G
import hunt_config as HC
import hunt_filter as HF


def validate_cfg(cfg):
    """校验配置；字段类型不对时报错而不是让校验器自己崩（audit-01 R5）。"""
    errs, warns = [], []
    shape = section_shape_errors(cfg)
    if shape:  # audit-01 R6：段的形状不对就先指名报错，后面的细查会到处 .get 崩
        return shape, warns
    try:
        _validate_cfg(cfg, errs, warns)
    except (TypeError, ValueError, AttributeError, KeyError) as exc:
        # 兜底不丢已收集的错误（audit-01 R6）
        errs.append("配置里有字段类型不对，校验无法继续（%s）：请对照 assets/config.example.json 检查" % exc)
    return errs, warns


SECTION_KEYS = ("user", "filters", "scoring", "sending", "schedule", "grading", "conversation", "inbox")


def section_shape_errors(cfg):
    """各配置段必须是对象（或不写 / null），targets 必须是对象列表（audit-01 R6）。"""
    errs = []
    for key in SECTION_KEYS:
        if cfg.get(key) is not None and not isinstance(cfg.get(key), dict):
            errs.append("%s 必须是对象（现在是 %s）" % (key, type(cfg.get(key)).__name__))
    targets = cfg.get("targets")
    if targets is not None and (not isinstance(targets, list) or any(not isinstance(t, dict) for t in targets)):
        errs.append("targets 必须是对象列表")
    return errs


def _int_field(errs, name, value):
    """数字字段：缺省按 0；不是整数（或整数字符串）时记错并按 0 继续。"""
    if value in (None, ""):
        return 0
    try:
        return int(value)
    except (TypeError, ValueError):
        errs.append("%s 必须是整数（现在是 %r）" % (name, value))
        return 0


def _validate_cfg(cfg, errs, warns):
    error = HC.same_hr_window_error(cfg)
    if error:
        errs.append(error)
    u = cfg.get("user") or {}
    for key in ("years_experience", "greeting_template"):
        if u.get(key) in (None, ""):
            errs.append("user.%s 缺失" % key)
    gt = u.get("greeting_template") or ""
    if gt and len(re.sub(r"\{[^}]+\}", "", gt).strip()) < 10:
        errs.append("user.greeting_template 太短（去掉占位符后不足 10 字），HR 会当成群发")
    targets = [t for t in (cfg.get("targets") or []) if t.get("enabled", True)]
    if not targets:
        errs.append("targets 为空：至少一个城市+关键词组合")
    for t in targets:
        if not t.get("keywords"):
            errs.append("targets[%s] 缺 keywords" % t.get("name"))
        code = str(t.get("city_code") or "")
        if not code.isdigit():
            errs.append("targets[%s].city_code 必须是 BOSS 数字城市码（请从目标城市 URL 获取）" % t.get("name"))
    s = cfg.get("sending") or {}
    cap = _int_field(errs, "sending.daily_cap", s.get("daily_cap"))
    gap = _int_field(errs, "sending.min_gap_seconds", s.get("min_gap_seconds"))
    if cap <= 0:
        errs.append("sending.daily_cap 必须 > 0")
    if cap > 25:
        warns.append("daily_cap=%d 偏高：15–20/天转化最好，>30 易撞风控" % cap)
    if cap > 20 and gap < 60:
        warns.append("发出量大时应把 min_gap_seconds 提到 ≥60")
    if gap < 25:
        errs.append("sending.min_gap_seconds < 25 会被判机械行为，请设 ≥60")
    # 模板个性化
    if not re.search(r"\{years\}|\{highlights\}|\{industry\}|\{title\}", gt):
        warns.append("greeting_template 没有个人化占位符（{years}/{industry}/{highlights}），不同岗位话术完全一致，回复率会低")
    shots = (cfg.get("scoring") or {}).get("big_brands") or []
    if shots and not isinstance(shots, list):
        errs.append("scoring.big_brands 必须是列表")
    if (cfg.get("schedule") or {}).get("timezone") and HC.cfg_tz(cfg).key != ((cfg.get("schedule") or {}).get("timezone")):
        warns.append("schedule.timezone 无法识别，已回退 %s" % HC.TZ_DEFAULT)
    # 招呼语模式：llm = 按岗位 JD 现写（greet.py）
    gb = G.block(cfg)
    if gb.get("mode") not in ("llm", "template"):
        errs.append("user.greeting.mode 只能是 llm 或 template")
    elif gb.get("mode") == "llm":
        if not (gb["llm"].get("model") or "").strip():
            errs.append("user.greeting.llm.model 缺失（llm 模式必须指定本地模型）")
        fs = G.facts(cfg)
        if not fs:
            errs.append("user.greeting.facts 为空：llm 模式没有可引用的事实，会满嘴编造")
        elif not any(re.search(r"\d", f) for f in fs):
            warns.append("greeting.facts 里没有带数字的成绩，招呼语会缺说服力")
        lo, hi = int(gb["length"]["min"]), int(gb["length"]["max"])
        if not (40 <= lo < hi <= 380):
            errs.append("user.greeting.length 不合理：需 40 ≤ min < max ≤ 380（BOSS 超 380 必拦）")
        if hi > 220:
            warns.append("greeting.length.max=%d 偏长，HR 在手机上多半看不到结尾" % hi)
    _llm_provider_checks(cfg, gb, errs, warns)
    g = cfg.get("grading") or {}
    tracks = g.get("tracks") or {"A": {}, "B": {}}
    if not isinstance(tracks, dict):
        errs.append("grading.tracks 必须是对象")
        tracks = {}
    ttl = g.get("jd_cache_ttl_days", 7)
    if type(ttl) is not int or not 0 <= ttl <= 60:
        errs.append("grading.jd_cache_ttl_days 必须是 0–60 的整数")
    wordlists = [("grading.roles", g.get("roles", []))]
    f = cfg.get("filters") or {}
    for field in ("title_block", "title_allow", "title_hard_block", "industry_block",
                  "brand_block", "hr_block", "text_block"):
        wordlists.append(("filters." + field, f.get(field, [])))
    for key, track in tracks.items():
        if not isinstance(track, dict):
            errs.append("grading.tracks.%s 必须是对象" % key)
            continue
        for field in ("roles", "industries", "brands", "jd_signals"):
            wordlists.append(("grading.tracks.%s.%s" % (key, field), track.get(field, [])))
    for key, words in wordlists:
        if not isinstance(words, list):
            errs.append(key + " 必须是列表")
            continue
        for word in words:
            try:
                re.compile(word)
            except (re.error, TypeError):
                errs.append("%s 含无效正则:%r" % (key, word))
    if HF.grading_enabled(cfg) and g.get("unknown_side", "B") not in tracks:
        errs.append("grading.unknown_side 必须对应一个 track")
    for key in ("grade_quota",):
        quotas = s.get(key)
        if quotas is not None:
            if not isinstance(quotas, dict) or any(not isinstance(v, int) or isinstance(v, bool) or v < 0 for v in quotas.values()):
                errs.append("sending.grade_quota 必须是非负整数的对象")
    if "max_per_window" in s and (not isinstance(s["max_per_window"], int) or isinstance(s["max_per_window"], bool) or s["max_per_window"] <= 0):
        errs.append("sending.max_per_window 必须是正整数")
    schedule = cfg.get("schedule") or {}
    windows = schedule.get("windows") or []
    if "catch_up" in schedule and not isinstance(schedule["catch_up"], bool):
        errs.append("schedule.catch_up 必须是布尔值")
    for field in ("windows", "scan_windows", "inbox_windows"):
        values = schedule.get(field) or []
        if not isinstance(values, list):
            errs.append("schedule.%s 必须是列表" % field)
            continue
        for value in values:
            try:
                if not isinstance(value, str) or not re.fullmatch(r"\d{2}:\d{2}", value):
                    raise ValueError()
                window_minutes(value)
            except (ValueError, AttributeError):
                errs.append("schedule.%s 含无效 HH:MM: %r" % (field, value))
            if field != "windows" and value not in windows:
                errs.append("schedule.%s 的窗口必须包含在 windows 中" % field)
    if schedule.get("summary_window") and schedule["summary_window"] not in windows:
        errs.append("schedule.summary_window 必须包含在 windows 中")
    for key in ("scan_pages", "per_window"):
        if key in schedule and (not isinstance(schedule[key], int) or isinstance(schedule[key], bool) or schedule[key] < (1 if key == "scan_pages" else 0)):
            errs.append("schedule.%s 数值不合理" % key)
    if not errs and HF.grading_enabled(cfg):
        roles = g.get("roles") or []
        if any("roles" in track and set(track["roles"]) != set(roles) for track in tracks.values()):
            warns.append("档位 roles 与 grading.roles 不一致（判级不读档位 roles）")
        for kw in dict.fromkeys(kw for t in targets for kw in t.get("keywords") or []):
            # roles 是正则词表；在关键词各分词中保留原有匹配语义。
            tokens = re.split(r"[\s,，、/;；]+", kw)
            if not any(HF.any_match(roles, token) for token in tokens):
                warns.append("关键词「%s」与 roles 无交集，扫回来的大概率判 C" % kw)
    errs.extend(conversation_errors(cfg))
    return errs, warns



def _llm_provider_checks(cfg, gb, errs, warns):
    """user.greeting.llm 的服务商配置（harness-01）。招呼语 llm 模式或对话模块开着时模型真会被调，
    写错算错误；都没用到时只提醒，不挡投递跳。"""
    llm = gb["llm"]
    conv = cfg.get("conversation") if isinstance(cfg.get("conversation"), dict) else {}
    used = gb.get("mode") == "llm" or bool(conv.get("enabled"))
    problems = []
    provider = G.provider_name(llm)
    if provider not in G.PROVIDERS:
        problems.append("user.greeting.llm.provider=%r 不认识：只能是 %s；Kimi、MiniMax 等 OpenAI 兼容服务写 "
                        "\"openai\"，再填它的 base_url 与 api_key_env" % (llm.get("provider"), " / ".join(G.PROVIDERS)))
    elif G._cloud(llm):
        raw = (((cfg.get("user") or {}).get("greeting") or {}).get("llm") or {})
        base = raw.get("base_url")
        if not base:
            problems.append("user.greeting.llm.base_url 缺失：provider=%s 时必须写服务商的 OpenAI 兼容地址"
                            "（默认值是本地 Ollama，不能用）" % provider)
        elif not re.match(r"https?://", str(base)):
            problems.append("user.greeting.llm.base_url 必须以 http:// 或 https:// 开头")
        elif used and not (llm.get("api_key") or G._env_key(llm.get("api_key_env") or "DEEPSEEK_API_KEY", llm)):
            warns.append("没找到模型 key %s（环境变量、llm.env_file / $BOSS_HUNT_ENV_FILE、~/.boss-hunt/.env、"
                         "~/.hermes/.env 都没有）：招呼语会回退模板、对话承接句会留空"
                         % (llm.get("api_key_env") or "DEEPSEEK_API_KEY"))
    (errs if used else warns).extend(problems)


def conversation_errors(cfg):
    """对话跳只需要的配置校验（conversation 段 + 各处正则）；validate 与 chat 入口共用（audit-01 Q5）。"""
    errs = []
    if cfg.get('conversation') is not None:  # audit-01 B1：null 视同没写（运行时本来就当关闭）
        conversation = cfg['conversation']
        if not isinstance(conversation, dict):
            errs.append('conversation 必须是对象')
        else:
            # audit-01 B1：pin_manual 写成 "false" 会被 bool() 当真、把置顶打开，必须是真正的布尔值
            # audit-01 R7-B（用户 11-07「放开」）：pin_manual / decline_text 写 null 按没写处理（运行时本就落到默认值）
            for key in ('enabled', 'dry', 'auto_accept_resume_request', 'pin_manual', 'decline_not_interested'):
                if key == 'pin_manual' and conversation.get(key) is None:
                    continue
                if key in conversation and type(conversation[key]) is not bool:
                    errs.append('conversation.%s 必须是布尔值' % key)
            if 'pin_max_per_hop' in conversation and (type(conversation['pin_max_per_hop']) is not int
                                                       or conversation['pin_max_per_hop'] < 0):
                errs.append('conversation.pin_max_per_hop 必须是非负整数')
            if 'decline_industries' in conversation and (
                    not isinstance(conversation['decline_industries'], list)
                    or any(not isinstance(w, str) or not w.strip() for w in conversation['decline_industries'])):
                errs.append('conversation.decline_industries 必须是非空字符串列表')
            if conversation.get('decline_text') is not None and not isinstance(conversation['decline_text'], str):
                errs.append('conversation.decline_text 必须是字符串')
            for key in ('daily_cap', 'per_hop', 'lookback_hours'):
                if key in conversation and (type(conversation[key]) is not int or conversation[key] <= 0):
                    errs.append('conversation.%s 必须是正整数' % key)
            if 'hours' in conversation and (not isinstance(conversation['hours'], list) or
                    any(type(h) is not int or not 0 <= h <= 23 for h in conversation['hours'])):
                errs.append('conversation.hours 必须是 0–23 整数列表')
            if 'system_texts' in conversation and (not isinstance(conversation['system_texts'], list) or
                    any(not isinstance(text, str) for text in conversation['system_texts'])):
                errs.append('conversation.system_texts 必须是字符串列表')
            if 'resume_request_card' in conversation and not isinstance(conversation['resume_request_card'], str):
                errs.append('conversation.resume_request_card 必须是字符串')
            if 'resume_sent_pattern' in conversation:
                pattern = conversation['resume_sent_pattern']
                if not isinstance(pattern, str):
                    errs.append('conversation.resume_sent_pattern 必须是字符串')
                elif not pattern.strip():
                    errs.append('conversation.resume_sent_pattern 不能为空（空正则会把任何新气泡当成简历已发送）')
                else:
                    try:
                        re.compile(pattern)
                    except re.error:
                        errs.append('conversation.resume_sent_pattern 必须是可编译正则')
            if 'templates' in conversation and (not isinstance(conversation['templates'], dict) or
                    any(not isinstance(k, str) or not isinstance(v, str)
                        for k, v in conversation['templates'].items())):
                errs.append('conversation.templates 必须是字符串字典')
            if 'opener_fallback' in conversation and not isinstance(conversation['opener_fallback'], str):
                errs.append('conversation.opener_fallback 必须是字符串')
    errs.extend(_regex_errors(cfg))
    return errs


def _regex_errors(cfg):
    """audit-01 Q5：配置里的正则要能编译，否则跑到一半才炸或静默匹配不到。"""
    errs = []

    def check(name, pattern):
        if not isinstance(pattern, str):
            errs.append('%s 必须是字符串' % name)
            return
        if not pattern.strip():
            # audit-01 R4：空串正则匹配任何文本（全部判拒信 / 任何新气泡当简历回执）
            errs.append('%s 不能为空（空正则会匹配所有消息）' % name)
            return
        try:
            re.compile(pattern)
        except re.error as exc:
            errs.append('%s 不是可编译正则：%s' % (name, exc))

    for section in ('inbox', 'conversation'):
        block = cfg.get(section)
        if not isinstance(block, dict) or 'patterns' not in block:
            continue
        patterns = block['patterns']
        if not isinstance(patterns, dict):
            errs.append('%s.patterns 必须是「名称 → 正则」字典' % section)
            continue
        for key, pattern in patterns.items():
            check('%s.patterns.%s' % (section, key), pattern)
    conversation = cfg.get('conversation')
    if isinstance(conversation, dict) and 'system_patterns' in conversation:
        pats = conversation['system_patterns']
        if not isinstance(pats, list):
            errs.append('conversation.system_patterns 必须是正则列表')
        else:
            for i, pattern in enumerate(pats):
                check('conversation.system_patterns[%d]' % i, pattern)
    return errs


def grade_limits(cfg):
    sending = cfg.get("sending") or {}
    quotas = sending.get("grade_quota")
    if quotas is None:
        # 老配置不改变按分数发送的分布，仍受总日上限约束。
        cap = max(0, int(sending.get("daily_cap") or 0))
        return {"A": cap, "B": cap}
    return {key: max(0, int(value)) for key, value in quotas.items() if key != "C"}


def remaining_windows(cfg, now):
    current = now.hour * 60 + now.minute
    windows = (cfg.get("schedule") or {}).get("windows") or []
    return max(1, sum(1 for w in set(windows) if window_minutes(w) >= current))


def window_minutes(value):
    hh, mm = map(int, value.split(":"))
    if not (0 <= hh < 24 and 0 <= mm < 60):
        raise ValueError("窗口必须是 HH:MM: %s" % value)
    return hh * 60 + mm


def quotas_for_now(cfg, st, now):
    sending = cfg.get("sending") or {}
    limits = grade_limits(cfg)
    n_win = remaining_windows(cfg, now)
    carry = sending.get("carry_over", "grade_quota" in sending)
    fixed = max(0, int((cfg.get("schedule") or {}).get("per_window") or 0))
    max_win = max(0, int(sending.get("max_per_window", 8)))
    result = {}
    for key, cap in limits.items():
        remain = max(0, cap - int((st.get("day_by_grade") or {}).get(key, 0)))
        result[key] = min(max_win, math.ceil(remain / n_win)) if carry else min(remain, fixed)
    return result


def shrink_quotas(quotas, budget):
    """把本跳配额按比例压到 budget 内（最大余数法），避免 A 先发把预算吃光、B 一条不发。"""
    total = sum(int(v or 0) for v in quotas.values())
    if total <= budget:
        return {k: int(v or 0) for k, v in quotas.items()}
    keys = [k for k, v in quotas.items() if int(v or 0) > 0]
    if not keys or budget <= 0:
        return {k: 0 for k in quotas}
    shares = {k: int(quotas[k]) * budget / total for k in keys}
    out = {k: int(shares[k]) for k in keys}
    rest = budget - sum(out.values())
    for k in sorted(keys, key=lambda x: -(shares[x] - out[x])):
        if rest <= 0:
            break
        out[k] += 1
        rest -= 1
    for k in quotas:
        out.setdefault(k, 0)
    return out


def target_of(cfg, job):
    city = job.get("cityName") or ""
    for t in cfg.get("targets") or []:
        if t.get("city") == city and t.get("enabled", True):
            return t.get("name")
    return city or "default"


def check_deadline(cfg):
    if cfg.get("_deadline") and time.monotonic() >= cfg["_deadline"]:
        raise TimeoutError("tick 已达到 40 分钟执行预算")


@CL.contextmanager
def tick_deadline(cfg):
    cfg["_deadline"] = time.monotonic() + 40 * 60
    previous = None
    if hasattr(signal, "SIGALRM"):
        def expired(signum, frame):
            raise TimeoutError("tick 已达到 40 分钟执行预算")
        previous = signal.signal(signal.SIGALRM, expired)
        signal.alarm(40 * 60)
    try:
        yield
    finally:
        if previous is not None:
            signal.alarm(0)
            signal.signal(signal.SIGALRM, previous)
        cfg.pop("_deadline", None)


def tick_plan(cfg, now):
    schedule = cfg.get("schedule") or {}
    current = now.hour * 60 + now.minute
    matches = [(abs(window_minutes(w) - current), w) for w in set(schedule.get("windows") or [])
               if abs(window_minutes(w) - current) <= 10]
    window = min(matches)[1] if matches else None
    actions = []
    if window:
        # 每个窗口都投递；scan 在投递前补池，inbox/summary 在投递后做。
        if window in (schedule.get("scan_windows") or []):
            actions.append("scan")
        actions.append("send")
        if window in (schedule.get("inbox_windows") or []):
            actions.append("inbox")
        if window == schedule.get("summary_window"):
            actions.append("summary")
    return {"window": window, "actions": actions, "timezone": HC.cfg_tz(cfg).key,
            "now": now.strftime("%Y-%m-%d %H:%M")}


def missed_windows(cfg, st, now):
    """Find recent unreported missed windows without changing state."""
    current = now.hour * 60 + now.minute
    day = now.strftime("%Y-%m-%d")
    excluded = set(st.get("ticks_done") or []) | set(st.get("missed_reported") or [])
    excluded.update(row["key"] for row in (st.get("ticks_failed") or [])
                    if isinstance(row, dict) and isinstance(row.get("key"), str)
                    and row["key"].startswith(day + " "))
    return [{"window": w, "delay_min": current - window_minutes(w)}
            for w in sorted(set((cfg.get("schedule") or {}).get("windows") or []), key=window_minutes)
            if 10 < current - window_minutes(w) <= 90 and day + " " + w not in excluded]


def tick_today_sent(st):
    """Read the existing sent ledger projection without changing state."""
    day = st.get("day")
    rows = [row for row in (st.get("sent") or {}).values()
            if day and (row.get("applied_ts") or row.get("ts") or "").startswith(day)]
    rows.sort(key=lambda row: row.get("applied_ts") or row.get("ts") or "", reverse=True)
    return {"count": len(rows), "items": [
        {"brand": str(row.get("brand") or ""), "name": str(row.get("name") or ""),
         "salary": str(row.get("salary") or ""), "city": str(row.get("city") or ""),
         "grade": str(row.get("grade") or "")}
        for row in rows[:8]]}
