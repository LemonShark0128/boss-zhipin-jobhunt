"""岗位筛选、判级与指纹计算。"""
import glob
import hashlib
import json
import os
import re
import datetime as DT


GRADE_RULES_VERSION = 2


EXP_MIN = {"经验不限": 0, "不限": 0, "应届生": 0, "在校/应届": 0, "在校生": 0, "1年以内": 0,
           "1-3年": 1, "3-5年": 3, "5-10年": 5, "10年以上": 10}


EXP_MAX = {"经验不限": 99, "不限": 99, "应届生": 1, "在校/应届": 1, "在校生": 1, "1年以内": 1,
           "1-3年": 3, "3-5年": 5, "5-10年": 10, "10年以上": 99}


DEG_RANK = {"学历不限": 0, "初中及以下": 1, "中专/中技": 2, "高中": 3, "大专": 4,
            "本科": 5, "硕士": 6, "博士": 7}


def parse_salary(desc):
    """'15-25K·13薪' -> {'low':15,'high':25,'per':'K'}；面议/无法解析 -> None"""
    if not desc:
        return None
    d = desc.replace(" ", "")
    m = re.search(r"(\d+(?:\.\d+)?)-(\d+(?:\.\d+)?)K", d)
    if m:
        return {"low": float(m.group(1)), "high": float(m.group(2)), "per": "K"}
    m = re.search(r"(\d+(?:\.\d+)?)K", d)
    if m:
        return {"low": float(m.group(1)), "high": float(m.group(1)), "per": "K"}
    m = re.search(r"(\d+)-(\d+)元/月", d)
    if m:
        return {"low": int(m.group(1)) / 1000.0, "high": int(m.group(2)) / 1000.0, "per": "元/月"}
    m = re.search(r"(\d+)-(\d+)元/(天|时|小时)", d)
    if m:
        return {"low": 0, "high": 0, "per": "元/" + m.group(3)}
    m = re.search(r"(\d+)-(\d+)万/年", d)
    if m:
        return {"low": int(m.group(1)) / 1.2, "high": int(m.group(2)) / 1.2, "per": "万/年"}
    return None


def any_match(patterns, text):
    for p in patterns or []:
        if not p:
            continue
        if re.search(p, text):
            return p
    return None


def exp_bounds(level):
    lv = (level or "").strip()
    return EXP_MIN.get(lv, 0), EXP_MAX.get(lv, 99)


def judge(job, cfg):
    """返回 (verdict, reasons, flags)；verdict: reject|pass"""
    f = cfg.get("filters") or {}
    u = cfg.get("user") or {}
    title = job.get("jobName") or ""
    brand = job.get("brandName") or ""
    ind = job.get("brandIndustry") or ""
    boss = job.get("bossName") or ""
    labels = " ".join(job.get("jobLabels") or [])
    skills = " ".join(job.get("skills") or [])
    welfare = " ".join(job.get("welfareList") or [])
    blob = " ".join([title, labels, skills, ind, welfare])
    reasons, flags = [], []

    if job.get("jobValidStatus") == 0:
        return "reject", ["岗位已下线"], []
    if f.get("exclude_headhunter", True) and job.get("goldHunter"):
        return "reject", ["猎头职位"], []
    if f.get("exclude_proxy", True) and job.get("proxyJob"):
        return "reject", ["代招岗位"], []

    block = any_match(f.get("title_block"), title)
    allow = any_match(f.get("title_allow"), title)
    # 硬排除：技术/工程/产品岗——allow 词（如「解决方案」「海外」）命中也不放行，
    # 否则「解决方案架构师」「弱电安防工程师（派驻马来西亚）」会被 allow 带进来。
    hard = any_match(f.get("title_hard_block"), title)
    if hard:
        return "reject", ["头衔命中硬排除:%s" % hard], []
    if block and not allow:
        return "reject", ["头衔命中排除:%s" % block], []
    if f.get("require_title_match") and not allow:
        return "reject", ["头衔未命中目标词"], []

    sal = parse_salary(job.get("salaryDesc"))
    min_k = f.get("salary_min_k")
    if min_k is not None:
        if sal is None:
            if f.get("reject_unknown_salary"):
                return "reject", ["薪资面议"], []
            flags.append("薪资面议")
        elif sal["per"] not in ("K", "元/月"):
            return "reject", ["非月薪:%s" % job.get("salaryDesc")], []
        elif sal["low"] < float(min_k):
            return "reject", ["薪资下限%.0fK<%sK" % (sal["low"], min_k)], []

    hit = any_match(f.get("industry_block"), ind)
    if hit:
        return "reject", ["行业排除:%s(%s)" % (ind, hit)], []
    hit = any_match(f.get("brand_block"), brand)
    if hit:
        return "reject", ["公司排除:%s" % brand], []
    hit = any_match(f.get("hr_block"), boss)
    if hit:
        return "reject", ["HR排除:%s" % boss], []
    hit = any_match(f.get("text_block"), blob)
    if hit:
        return "reject", ["话术/硬条件:%s" % hit], []
    dists = f.get("districts_allow") or []
    if dists and not any_match([re.escape(d) for d in dists], job.get("areaDistrict") or ""):
        return "reject", ["区域不在偏好:%s" % (job.get("areaDistrict") or "?")], []

    lo, hi = exp_bounds(job.get("jobExperience"))
    years = float(u.get("years_experience") or 0)
    exp_cfg = f.get("experience") or {}
    if lo > float(exp_cfg.get("job_min_years_max", years + 1)):
        return "reject", ["经验要求超过本人:%s" % job.get("jobExperience")], []
    if hi <= float(exp_cfg.get("job_max_years_min", 0)):
        return "reject", ["岗位过于初级:%s" % job.get("jobExperience")], []

    deg_max = f.get("degree_max")
    if deg_max:
        jd = DEG_RANK.get(job.get("jobDegree") or "", 0)
        if jd > DEG_RANK.get(deg_max, 9):
            return "reject", ["学历要求:%s" % job.get("jobDegree")], []

    if not allow:
        flags.append("头衔待核")
    if sal is None:
        flags.append("面议")
    return "pass", reasons, flags


def score(job, cfg):
    sc = cfg.get("scoring") or {}
    s = 0
    title = job.get("jobName") or ""
    blob = " ".join([title, " ".join(job.get("skills") or []),
                     job.get("brandIndustry") or "", " ".join(job.get("jobLabels") or [])])
    if any_match([re.escape(b) for b in (sc.get("big_brands") or [])], job.get("brandName") or ""):
        s += int(sc.get("big_brand", 3))
    if any_match([re.escape(b) for b in (sc.get("big_brands") or [])], title):
        s += int(sc.get("big_brand", 3))
    if any_match(cfg.get("filters", {}).get("industry_allow"), job.get("brandIndustry") or ""):
        s += int(sc.get("industry_allow", 2))
    if any_match(cfg.get("filters", {}).get("title_allow"), title):
        s += int(sc.get("title_allow", 2))
    if any_match([re.escape(k) for t in (cfg.get("targets") or []) for k in (t.get("keywords") or [])], title):
        s += int(sc.get("keyword_in_title", 2))
    if job.get("bossOnline"):
        s += int(sc.get("boss_online", 1))
    sal = parse_salary(job.get("salaryDesc"))
    if sal and sal["per"] in ("K", "元/月") and sal["low"] >= float(sc.get("salary_high_from_k", 99)):
        s += int(sc.get("salary_high", 1))
    hit = any_match([re.escape(w) for w in (sc.get("welfare") or [])],
                    " ".join(job.get("welfareList") or []))
    if hit:
        s += int(sc.get("welfare_hit", 1))
    # 城市加权：本地岗优先（sc.get("city_bonus") 为城市到分数的映射），按 cityName 里是否含该词判
    for cname, pts in (sc.get("city_bonus") or {}).items():
        if cname and cname in (job.get("cityName") or ""):
            s += int(pts or 0)
    # 单边加分：命中任一 term 就加，不要求组合（具体词来自配置）
    for item in (sc.get("blob_bonus") or []):
        if any_match(item.get("terms") or [], blob):
            s += int(item.get("score") or 0)
    # 组合加分：两边都命中才加（两组配置词），比单关键词更贴候选人方向
    for combo in sc.get("combo_bonus") or []:
        if (any_match(combo.get("a") or [], blob) and any_match(combo.get("b") or [], blob)):
            s += int(combo.get("score") or 2)
    return s


def load_jobs(cfg):
    raw = cfg["paths"]["raw_dir"]
    seen = {}
    paths = glob.glob(os.path.join(raw, "*.json"))

    def freshness(path):
        name = os.path.basename(path)
        numbers = tuple(int(n) for n in re.findall(r"\d+", name))
        try:
            modified = os.stat(path).st_mtime
        except OSError:
            # scan 文件名含 YYYYMMDD-HHMMSS；其余数字作为同日序号排序。
            match = re.search(r"(\d{8})[-_](\d{6})", name)
            date = re.search(r"(\d{4})[-_]?(\d{2})[-_]?(\d{2})", name)
            try:
                modified = (DT.datetime.strptime(''.join(match.groups()), "%Y%m%d%H%M%S").timestamp()
                            if match else DT.datetime(*map(int, date.groups())).timestamp() if date else 0)
            except ValueError:
                modified = 0
        return modified, numbers, name

    for path in sorted(paths, key=freshness):
        try:
            with open(path, encoding="utf-8") as f:
                d = json.load(f)
        except Exception:
            continue
        if not isinstance(d, dict) or d.get("code") != 0:
            continue
        for j in ((d.get("zpData") or {}).get("jobList") or []):
            jid = j.get("encryptJobId")
            if not jid:
                continue
            sources = seen[jid]["_src"] if jid in seen else []
            j = dict(j, _src=sources + [os.path.basename(path)])
            seen[jid] = j
    return list(seen.values())


def grading_enabled(cfg):
    # 缺段保留旧版按分数定级；词表只来自配置，不加载个人词表。
    return bool((cfg.get("grading") or {}).get("enabled", "grading" in cfg))


def grade_job(job: dict, cfg: dict, jd_text: str = "") -> dict:
    """纯函数：列表只粗标，非空 JD 按配置的信号计分终判。"""
    if not grading_enabled(cfg):
        pts = score(job, cfg)
        grade = "A" if pts >= int((cfg.get("scoring") or {}).get("a_min", 3)) else "B"
        return {"grade": grade, "reason": "旧配置：按 scoring.a_min 定级",
                "scores": {"score": pts}, "final": True, "cause": "legacy", "hits": {}, "mins": {}}
    g = cfg.get("grading") or {}
    tracks = g.get("tracks") or {"A": {}, "B": {}}
    role = any_match(g.get("roles") or [], job.get("jobName") or "")
    if g.get("require_role_for_ab", True) and not role:
        return {"grade": "C", "reason": "职能未命中 grading.roles", "scores": {}, "final": True, "cause": "role_miss", "hits": {}, "mins": {}}
    final = bool(jd_text.strip())
    scores, hits = {}, {}
    for key, tr in tracks.items():
        if final:
            matched = [w for w in tr.get("jd_signals") or [] if w and re.search(w, jd_text, re.I)]
        else:
            matched = [w for field, words in (("brandIndustry", tr.get("industries")),
                                              ("brandName", tr.get("brands")))
                       for w in words or [] if w and re.search(w, job.get(field) or "", re.I)]
        hits[key], scores[key] = matched, len(matched)
    # 终判要够强的证据：每档可配 min_hits（粗标阶段只用列表弱信号，不设门槛）。
    # 无任何档位达标时按 no_signal 处理（默认 C，不投）——否则弱信号岗位全被
    # unknown_side 兜进 B，实测 24 条真实 JD 里 15 条是这么进来的。
    unknown = g.get("unknown_side", "B")
    no_signal = g.get("no_signal", "C")
    mins = {k: (int((tr or {}).get("min_hits") or 1) if final else 1) for k, tr in tracks.items()}
    qualified = [k for k in tracks if scores.get(k, 0) >= mins.get(k, 1)]
    best = max([scores[k] for k in qualified], default=0)
    winners = [k for k in qualified if scores[k] == best]
    if not best:
        grade = no_signal
    elif len(winners) == 1:
        grade = winners[0]
    else:
        grade = unknown
    if grade not in tracks:
        grade = "C"  # 不认识的回退档位绝不放行
    why = "JD 信号" if final else "列表行业/公司弱信号（待 JD 复核）"
    detail = "；".join("%s=%d[%s]" % (key, scores[key], "/".join(hits[key])) for key in tracks)
    reason = "职能:%s；%s：%s" % (role or "未要求", why, detail)
    if not best:
        reason += "；未达 min_hits 门槛，按 no_signal=%s" % grade
    elif len(winners) != 1:
        reason += "；多档同分，按 unknown_side=%s" % grade
    return {"grade": grade if final else grade + "?", "reason": reason,
            "scores": scores, "final": final, "hits": hits, "mins": mins,
            "cause": "no_signal" if not best else "tie_unknown" if len(winners) > 1 else "ok"}


def jd_duty_scope(jd: str) -> str:
    """回绝只采用可识别的职责小节；没有职责标题则不提供 JD 证据。"""
    duties = ("岗位职责", "工作职责", "职位描述", "工作内容", "岗位定位")
    requirements = ("任职要求", "任职资格", "岗位要求", "职位要求", "加分项", "优先条件",
                    "要求", "任职条件", "资格要求", "应聘要求", "我们希望你", "你需要具备",
                    "岗位任职要求")
    excluded = ("公司介绍", "公司简介", "关于我们", "企业介绍", "团队介绍",
                "我们可以为你提供", "我们能为你提供", "我们提供", "福利待遇", "福利",
                "薪资福利", "岗位福利", "公司福利", "职业发展", "晋升路径", "发展空间")
    titles = sorted(duties + requirements + excluded, key=len, reverse=True)
    headings = list(re.finditer(r"(?:【[ \t]*)?(?P<title>" + "|".join(titles)
                                + r")(?=[:：】\s]|$)[ \t]*】?[ \t]*[:：]?", jd))
    paragraph_start = re.compile(r"(?:^|[\n【]|[一二三四五六七八九十]+、|\d+[、.．])\s*$")
    # 允许明确的长标题出现在同一行；「要求」只在段首/括号/编号后认作标题。
    headings = [m for m in headings
                if (m.group('title') in duties + requirements and m.group('title') != '要求')
                or paragraph_start.search(jd[:m.start('title')])]
    return "\n".join(jd[m.end():headings[i + 1].start() if i + 1 < len(headings) else len(jd)].strip()
                     for i, m in enumerate(headings) if m.group('title') in duties)


def jd_redline_scope(jd: str) -> str:
    """只保留职责与要求小节；无法识别纳入标题时保守返回原文。"""
    included = ("岗位职责", "工作职责", "职位描述", "工作内容", "岗位定位",
                "任职要求", "任职资格", "岗位要求", "职位要求", "加分项", "优先条件")
    excluded = ("公司介绍", "公司简介", "关于我们", "企业介绍", "团队介绍",
                "我们可以为你提供", "我们能为你提供", "我们提供", "福利待遇",
                "薪资福利", "岗位福利", "公司福利", "职业发展", "晋升路径", "发展空间")
    headings = list(re.finditer("(?:" + "|".join(included + excluded) + r")(?=[:：】\s]|$)", jd))
    # 排除必须有段首证据，避免正文中的“职业发展”等说法截掉真实职责。
    paragraph_start = re.compile(r"(?:^|[\n【]|[一二三四五六七八九十]+、|\d+[、.．])\s*$")
    headings = [m for m in headings
                if m.group() in included or paragraph_start.search(jd[:m.start()])]
    if not any(m.group() in included for m in headings):
        return jd
    return "\n".join(jd[m.start():headings[i + 1].start() if i + 1 < len(headings) else len(jd)]
                     for i, m in enumerate(headings) if m.group() in included)


def grade_with_jd(job, cfg, jd):
    """纯函数：JD 终判及全部红线；保持原判级阈值与匹配语义。"""
    gr = grade_job(job, cfg, jd)
    scope = jd_redline_scope(jd)
    redlines = [w for w in (cfg.get("filters") or {}).get("text_block") or []
                if w and re.search(w, scope)]
    gr.update(redlines=redlines, pre_redline_grade=gr["grade"])
    if redlines:
        gr.update(grade="C", reason="JD 红线:%s" % redlines[0], cause="redline")
    return gr


def near_miss(gr, cfg):
    """仅诊断终判 C 的近线原因，不写复核池、不改变判级。"""
    if not gr.get("final") or gr.get("grade") != "C" or gr.get("cause") == "role_miss":
        return None
    tracks = (cfg.get("grading") or {}).get("tracks") or {"A": {}, "B": {}}
    scores, mins, hits = gr.get("scores") or {}, gr.get("mins") or {}, gr.get("hits") or {}
    short = [k for k in tracks if scores.get(k, 0) >= 1 and mins.get(k, 0) - scores[k] == 1]
    if short:
        k = max(short, key=lambda key: mins[key])
        return {"kind": "short_hits", "track": k, "need": mins[k], "got": scores[k], "hits": hits.get(k, [])}
    redlines = gr.get("redlines") or []
    if gr.get("cause") == "redline" and len(redlines) == 1 and gr.get("pre_redline_grade") in tracks:
        return {"kind": "single_redline", "redline": redlines[0], "track": gr["pre_redline_grade"]}
    return None


def grade_sort_key(item):
    order = {"A": 0, "B": 1, "A?": 2, "B?": 3, "C": 6, "C?": 6}
    return (order.get(item["grade"], 4 if item.get("grade_final") else 5), -item["score"])


def stable_fingerprint(job, cfg):
    """重试只跟踪职位内容与规则，不因快照路径/mtime 或运行时字段重置。"""
    raw = {k: v for k, v in job.items() if not k.startswith("_")}
    blob = json.dumps([raw, cfg.get("grading"), cfg.get("filters")], ensure_ascii=False, sort_keys=True)
    return hashlib.sha256(blob.encode()).hexdigest()


def legacy_grade_fingerprint(job, cfg):
    # raw 字段或规则变化后重新读取 JD；_src 不是职位内容。
    raw = {k: v for k, v in job.items() if not k.startswith("_")}
    snapshots = []
    for name in job.get("_src") or []:
        path = os.path.join((cfg.get("paths") or {}).get("raw_dir") or "", os.path.basename(name))
        try:
            snapshots.append((name, os.stat(path).st_mtime_ns))
        except FileNotFoundError:
            snapshots.append((name, None))
    blob = json.dumps([raw, snapshots, cfg.get("grading"), cfg.get("filters")], ensure_ascii=False, sort_keys=True)
    return hashlib.sha256(blob.encode()).hexdigest()


JD_FIELDS = ("encryptJobId", "jobName", "salaryDesc", "brandName", "brandIndustry", "bossName",
             "cityName", "areaDistrict", "jobExperience", "jobDegree", "jobLabels", "skills")


def jd_fingerprint(job):
    blob = json.dumps({k: job.get(k) for k in JD_FIELDS}, sort_keys=True, ensure_ascii=False)
    return hashlib.sha256(blob.encode()).hexdigest()


def rule_fingerprint(cfg):
    blob = json.dumps([GRADE_RULES_VERSION, cfg.get("grading"), cfg.get("filters")], sort_keys=True, ensure_ascii=False)
    return hashlib.sha256(blob.encode()).hexdigest()


def grade_fingerprint(job, cfg):
    return hashlib.sha256((jd_fingerprint(job) + "|" + rule_fingerprint(cfg)).encode()).hexdigest()
