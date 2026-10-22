#!/usr/bin/env python3
"""BOSS直聘 求职助手（可移植版）：扫描 -> 按用户档案筛选 -> 发招呼 -> 盯收件箱。

所有个人化内容都在 config.json 里，代码不含任何个人经历/城市/关键词。

  python3 boss_hunt.py init                 # 生成 ~/.boss-hunt/config.json
  python3 boss_hunt.py validate             # 校验配置（不联网）
  python3 boss_hunt.py login-check          # 只读：确认浏览器里已登录
  python3 boss_hunt.py preflight            # 登录 + 招呼语开关 + 额度提示
  python3 boss_hunt.py scan                 # 抓岗位列表到 raw/
  python3 boss_hunt.py plan                 # 干跑：只报告会投谁，不发
  python3 boss_hunt.py send 2               # 真发（首跑建议 1-2 个）
  python3 boss_hunt.py inbox                # 盯新招呼 + 分类回复
  python3 boss_hunt.py run --send 5         # 扫描 + 发送 + 收件箱（定时任务用这个）
  python3 boss_hunt.py template --job-id X  # 预览渲染后的招呼语

配置查找顺序：--config PATH > $BOSS_HUNT_CONFIG > ~/.boss-hunt/config.json
"""
import argparse
import glob
import json
import os
import re
import sys
import time
from datetime import datetime
from zoneinfo import ZoneInfo

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import chrome_mcp as CM  # noqa: E402

DEFAULT_DIR = os.path.expanduser("~/.boss-hunt")
TZ_DEFAULT = "Asia/Shanghai"

EXP_MIN = {"经验不限": 0, "不限": 0, "应届生": 0, "在校/应届": 0, "在校生": 0, "1年以内": 0,
           "1-3年": 1, "3-5年": 3, "5-10年": 5, "10年以上": 10}
EXP_MAX = {"经验不限": 99, "不限": 99, "应届生": 1, "在校/应届": 1, "在校生": 1, "1年以内": 1,
           "1-3年": 3, "3-5年": 5, "5-10年": 10, "10年以上": 99}
DEG_RANK = {"学历不限": 0, "初中及以下": 1, "中专/中技": 2, "高中": 3, "大专": 4,
            "本科": 5, "硕士": 6, "博士": 7}

CAPTCHA_RE = r"验证码|滑动验证|安全验证|人机验证|拼图"
APPWALL_RE = r"需(要)?(下载|跳转|打开)\s*App|App内(查看|沟通)|去小程序"


# --------------------------------------------------------------------------
# 配置
# --------------------------------------------------------------------------
def config_path(args):
    if args.config:
        return os.path.abspath(os.path.expanduser(args.config))
    env = os.environ.get("BOSS_HUNT_CONFIG")
    if env:
        return os.path.abspath(os.path.expanduser(env))
    return os.path.join(DEFAULT_DIR, "config.json")


def load_config(path):
    with open(path, encoding="utf-8") as f:
        cfg = json.load(f)
    workdir = os.path.join(os.path.dirname(path), "data")
    p = cfg.setdefault("paths", {})
    p.setdefault("workdir", workdir)
    p["workdir"] = os.path.abspath(os.path.expanduser(p["workdir"]))
    os.makedirs(p["workdir"], exist_ok=True)
    p.setdefault("raw_dir", os.path.join(p["workdir"], "raw"))
    p.setdefault("state", os.path.join(p["workdir"], "state.json"))
    p.setdefault("log", os.path.join(p["workdir"], "runs.log"))
    os.makedirs(p["raw_dir"], exist_ok=True)
    return cfg


def cfg_tz(cfg):
    try:
        return ZoneInfo((cfg.get("schedule") or {}).get("timezone") or TZ_DEFAULT)
    except Exception:
        return ZoneInfo(TZ_DEFAULT)


def now_local(cfg):
    return datetime.now(cfg_tz(cfg))


def stamp(cfg, fmt="%Y-%m-%d %H:%M"):
    return now_local(cfg).strftime(fmt)


def validate_cfg(cfg):
    errs, warns = [], []
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
            errs.append("targets[%s].city_code 必须是 BOSS 数字城市码（如上海 101020100）" % t.get("name"))
    s = cfg.get("sending") or {}
    cap = int(s.get("daily_cap") or 0)
    gap = int(s.get("min_gap_seconds") or 0)
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
    if (cfg.get("schedule") or {}).get("timezone") and cfg_tz(cfg).key != ((cfg.get("schedule") or {}).get("timezone")):
        warns.append("schedule.timezone 无法识别，已回退 %s" % TZ_DEFAULT)
    return errs, warns


# --------------------------------------------------------------------------
# 筛选
# --------------------------------------------------------------------------
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
    return s


def load_jobs(cfg):
    raw = cfg["paths"]["raw_dir"]
    seen = {}
    for path in sorted(glob.glob(os.path.join(raw, "*.json"))):
        try:
            d = json.load(open(path, encoding="utf-8"))
        except Exception:
            continue
        if not isinstance(d, dict) or d.get("code") != 0:
            continue
        for j in ((d.get("zpData") or {}).get("jobList") or []):
            jid = j.get("encryptJobId")
            if not jid:
                continue
            if jid in seen:
                seen[jid]["_src"].append(os.path.basename(path))
                continue
            j["_src"] = [os.path.basename(path)]
            seen[jid] = j
    return list(seen.values())


def candidates(cfg, jobs=None):
    sc = cfg.get("scoring") or {}
    out = []
    for j in (jobs if jobs is not None else load_jobs(cfg)):
        verdict, reasons, flags = judge(j, cfg)
        if verdict == "reject":
            continue
        s = score(j, cfg)
        if s < int(sc.get("min_score", 0)):
            continue
        grade = "A" if s >= int(sc.get("a_min", 3)) else "B"
        out.append({"grade": grade, "score": s, "reasons": reasons, "flags": flags, "job": j})
    out.sort(key=lambda x: (0 if x["grade"] == "A" else 1, -x["score"]))
    return out


# --------------------------------------------------------------------------
# 记账
# --------------------------------------------------------------------------
def load_state(cfg):
    path = cfg["paths"]["state"]
    st = {"sent": {}, "contacts": [], "day": "", "day_count": 0, "day_by_target": {},
          "inbox_seen": [], "inbox_ts": {}, "replied": [], "followed": []}
    if os.path.exists(path):
        try:
            st.update(json.load(open(path, encoding="utf-8")))
        except Exception:
            pass
    for k, v in (("sent", {}), ("contacts", []), ("day_by_target", {}),
                 ("inbox_seen", []), ("inbox_ts", {}), ("replied", []), ("followed", [])):
        st.setdefault(k, v)
    today = stamp(cfg, "%Y-%m-%d")
    if st.get("day") != today:
        st["day"], st["day_count"], st["day_by_target"] = today, 0, {}
    return st


def save_state(cfg, st):
    path = cfg["paths"]["state"]
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(st, f, ensure_ascii=False, indent=1)
    os.replace(tmp, path)


# --------------------------------------------------------------------------
# 招呼语
# --------------------------------------------------------------------------
def render(template, cfg, job=None):
    u = cfg.get("user") or {}
    job = job or {}
    values = {
        "years": u.get("years_experience", ""),
        "industry": u.get("industry", "") or u.get("current_title", ""),
        "current_title": u.get("current_title", ""),
        "highlights": "；".join(u.get("highlights") or []),
        "name": u.get("display_name", ""),
        "job_name": job.get("jobName", ""),
        "brand": job.get("brandName", ""),
        "city": job.get("cityName", ""),
    }
    out = template
    for k, v in values.items():
        out = out.replace("{%s}" % k, str(v))
    return re.sub(r"\s+", " ", out).strip()


def verify_marker(cfg, job=None):
    s = (cfg.get("sending") or {}).get("verify_text")
    if s:
        return s
    rendered = render((cfg.get("user") or {}).get("greeting_template") or "", cfg, job)
    core = re.sub(r"[^\S]+", "", rendered)
    return core[:8] if core else ""


# --------------------------------------------------------------------------
# 浏览器动作
# --------------------------------------------------------------------------
def login_state(br, tab):
    """只读探测登录态；绝不代填账号密码/验证码。"""
    expr = ("(()=>{const t=document.body.innerText||'';"
            "return JSON.stringify({url:location.href,"
            "login:/扫码登录|登录\\/注册|手机号登录|密码登录|请登录/.test(t),"
            "avatar:!!document.querySelector('.nav-figure,.user-nav,.user-info,[class*=nav-figure]'),"
            "chat:!!document.querySelector('[href*=\"/web/geek/chat\"]')})})()")
    return br.json(tab, expr, default={"raw": "eval_failed"}) or {}


def check_login(br, cfg):
    tab = br.new_tab("https://www.zhipin.com/web/geek/jobs")
    time.sleep(3)
    st = login_state(br, tab)
    return tab, st


def greeting_switch(br, tab, fix=False):
    br.nav(tab, "https://www.zhipin.com/web/geek/notify-set?type=greetSet", wait=3.5)
    expr = ("(()=>{const rows=[...document.querySelectorAll('.ui-switch, .switch-wrap, label')];"
            "return JSON.stringify(rows.map(e=>({t:(e.closest('li,div')?e.closest('li,div').innerText:'').replace(/\\s+/g,' ').slice(0,40),"
            "on:e.classList.contains('ui-switch-checked')})))})()")
    r = br.js(tab, expr)
    switches = CM.parse_json_payload(r.get("text") or "", []) or []
    auto = [s for s in switches if "招呼" in (s.get("t") or "") and s.get("on")]
    if auto and fix:
        br.js(tab, "(()=>{const rows=[...document.querySelectorAll('.ui-switch')];"
                   "const hit=rows.find(e=>((e.closest('li,div')||{}).innerText||'').includes('招呼')&&e.classList.contains('ui-switch-checked'));"
                   "if(hit){hit.click();return 'clicked'}return 'notfound'})()")
        time.sleep(2)
        after = CM.parse_json_payload(br.js(tab, expr).get("text") or "", []) or []
        still = [s for s in after if "招呼" in (s.get("t") or "") and s.get("on")]
        return "auto_greeting_on_still" if still else "auto_greeting_off"
    return "auto_greeting_on" if auto else "auto_greeting_ok"


def scan(cfg, br, targets=None, keywords=None, pages=3):
    import urllib.parse
    saved, errs = 0, []
    for t in [x for x in (cfg.get("targets") or []) if x.get("enabled", True)]:
        if targets and t.get("name") not in targets:
            continue
        kws = t.get("keywords") or []
        if keywords:
            kws = [k for k in kws if k in keywords] or keywords
        city, code = t.get("city") or "", str(t.get("city_code") or "")
        tab = None
        for kw in kws:
            url = ("https://www.zhipin.com/web/geek/jobs?query=%s&city=%s"
                   % (urllib.parse.quote(kw), code))
            if tab is None:
                tab = br.new_tab(url)
                time.sleep(4)
            else:
                br.nav(tab, url, wait=4)
            st = login_state(br, tab)
            if st.get("login") and not st.get("avatar"):
                errs.append("not_logged_in")
                break
            br.scroll(tab, times=10, wait=800)
            time.sleep(2)
            for i in range(max(1, int(pages))):
                r = br.network(tab, "joblist.json", index=i, max_body=200000)
                d = CM.parse_network_json(r.get("text") or "")
                if not d or d.get("code") != 0 or not ((d.get("zpData") or {}).get("jobList")):
                    if d and d.get("code") == 37:
                        errs.append("risk_code_37")
                    continue
                safe_kw = re.sub(r"[^\w\u4e00-\u9fa5-]+", "_", kw)
                path = os.path.join(cfg["paths"]["raw_dir"], "%s_%s_%d.json" % (city or "city", safe_kw, i))
                with open(path, "w", encoding="utf-8") as f:
                    json.dump(d, f, ensure_ascii=False)
                saved += 1
            time.sleep(2)
    return saved, errs


def send_one(br, tab, job, cfg):
    jid = job.get("encryptJobId")
    br.nav(tab, "https://www.zhipin.com/job_detail/%s.html" % jid, wait=2.5)
    expr = ("(()=>{const t=(document.body.innerText||'');"
            "return JSON.stringify({s:!!document.querySelector('.btn-startchat'),"
            "c:!!document.querySelector('.btn-continue-chat')||/继续沟通|已沟通/.test(t),"
            "cap:/%s/.test(t),app:/%s/.test(t),"
            "title:(document.querySelector('h1')||{}).innerText||''})})()"
            % (CAPTCHA_RE, APPWALL_RE))
    state = br.json(tab, expr, default={}) or {}
    if state.get("cap"):
        return "skip_captcha"
    if state.get("app"):
        return "skip_need_app"
    if state.get("c"):
        return "skip_already_chatted"
    if not state.get("s"):
        return "skip_no_button"
    br.click(tab, ".btn-startchat")
    time.sleep(2.5)
    text = render((cfg.get("user") or {}).get("greeting_template") or "", cfg, job)
    if len(text) > 380:
        return "err_template_too_long"
    f = br.fill(tab, "textarea.input-area", text)
    if f.get("isError"):
        return "err_fill:" + (f.get("text") or "")[:80]
    time.sleep(0.8)
    br.click(tab, ".send-message")
    time.sleep(2.5)
    v = br.js(tab, "(()=>{const d=document.querySelector('.startchat-dialog');"
                   "return (d?d.innerText:'').replace(/\\s+/g,' ')})()")
    return "sent" if verify_marker(cfg, job) in CM.unwrap(v.get("text") or "") else "unverified"


INTERVIEW_DEFAULT = ("您好，方便安排线上面试吗？我这边时间需要先确认一下，"
                     "您先给几个可选时段，我和您确认后再回复您，谢谢！")


def classify(msg, cfg):
    pats = (cfg.get("inbox") or {}).get("patterns") or {}
    rej = pats.get("reject") or r"不合适|不匹配|不符合|已招满|招满了|暂不考虑|不太适合|遗憾|已招到"
    itv = pats.get("interview") or r"面试|面谈|复试|初试|约个?时间|什么时候方便|线上|线下|面试官"
    itr = pats.get("interested") or r"感兴趣|有兴趣|简历|方便沟通|聊聊|沟通一下|想了解|加个微信|加微信"
    for label, p in (("reject", rej), ("interview", itv), ("interested", itr)):
        if re.search(p, msg or ""):
            return label
    return "other"


def reply_in_chat(br, tab, brand, msg, marker):
    br.nav(tab, "https://www.zhipin.com/web/geek/chat", wait=5)
    sel = ("(() => { const els = [...document.querySelectorAll('div,li')]"
           ".filter(e => /%s/.test(e.textContent || '') && (e.textContent||'').length < 400);"
           "els.sort((a,b)=>(a.textContent||'').length-(b.textContent||'').length);"
           "if(!els[0]) return 'nf'; els[0].click(); return 'ok'; })()" % re.escape(brand))
    r = br.js(tab, sel)
    if "ok" not in (r.get("text") or ""):
        return "no_conv"
    time.sleep(2)
    hdr = CM.unwrap(br.js(tab, "(()=>{const h=document.querySelector('.chat-title,.title-box,[class*=chat-title]');"
                              "return h?h.innerText.replace(/\\s+/g,' '):''})()").get("text") or "")
    if brand[:4] and brand[:4] not in hdr:
        return "wrong_conv"
    f = br.fill(tab, ".chat-input", msg)
    if f.get("isError"):
        return "fill_err"
    time.sleep(0.8)
    br.click(tab, ".btn-v2.btn-sure-v2.btn-send")
    time.sleep(2)
    v = CM.unwrap(br.js(tab, "(()=>((document.body.innerText||'').replace(/\\s+/g,' ')))()").get("text") or "")
    return "replied" if marker and marker in v else "unverified"


def inbox(cfg, br, tab, st, out):
    br.nav(tab, "https://www.zhipin.com/web/geek/chat", wait=5)
    myuid, items = None, {}
    for i in range(3):
        r = br.network(tab, "friend/getG", index=i, max_body=400000)
        d = CM.parse_network_json(r.get("text") or "")
        if not d:
            continue
        for it in ((d.get("zpData") or {}).get("result") or []):
            if isinstance(it, dict) and it.get("encryptJobId"):
                items[it["encryptJobId"]] = it
    u = br.network(tab, "getUserInfo", index=0, max_body=20000)
    d = CM.parse_network_json(u.get("text") or "")
    if d:
        myuid = ((d.get("zpData") or {}).get("userId") or (d.get("zpData") or {}).get("uid"))

    lookback = float((cfg.get("inbox") or {}).get("lookback_hours", 72))
    max_listed = int((cfg.get("inbox") or {}).get("max_listed", 5))
    sys_pat = ((cfg.get("inbox") or {}).get("patterns") or {}).get("system") or \
        r"附件简历.{0,20}已发送|已发送给对方|对方已同意|简历已收到|感谢投递|自动回复|系统消息|投递成功|您的附件简历"
    now_ms = int(time.time() * 1000)
    jobs = {j["encryptJobId"]: j for j in load_jobs(cfg)}
    sent_jids = st.get("sent") or {}

    fresh_hits, interest, interviews = [], [], []
    replied_ok, reply_failed, followed_up = [], [], []
    n_fresh = 0
    for jid, it in items.items():
        info = it.get("lastMessageInfo") or {}
        ts = info.get("msgTime") or it.get("lastTS") or 0
        if not ts or (now_ms - ts) > lookback * 3600 * 1000:
            continue
        from_them = (info.get("fromId") != myuid) if myuid else True
        if not from_them:
            continue
        n_fresh += 1
        txt = re.sub(r"\s+", " ", info.get("showText") or "").strip()
        if re.search(sys_pat, txt):
            continue
        job = jobs.get(jid) or {}
        verdict = judge(job, cfg)[0] if job else "pass"
        sc = score(job, cfg) if job else 0
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
            interviews.append((jid, it, who, meta))
        elif label == "interested" and verdict == "pass":
            interest.append((sc, who, meta))

    fresh_hits.sort(key=lambda x: -x[0])
    interest.sort(key=lambda x: -x[0])
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

    if (cfg.get("inbox") or {}).get("interview_auto_reply", True):
        reply = (cfg.get("user") or {}).get("interview_reply") or INTERVIEW_DEFAULT
        marker = "面试" if "面试" in reply else reply[:6]
        for jid, it, who, meta in interviews:
            if jid in st["replied"]:
                continue
            res = reply_in_chat(br, tab, it.get("brandName") or "", reply, marker)
            if res == "replied":
                st["replied"].append(jid)
                out.append("📅 约面试 %s → 已回「先给时段，我确认后再答复」" % who)
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
            if myuid and info.get("fromId") != myuid:
                continue  # 对方已回，不需要跟进
            res = reply_in_chat(br, tab, it.get("brandName") or "", text, text[:6])
            st["followed"].append(jid)
            out.append("🔁 跟进 %s → %s" % (it.get("brandName") or "?", res))
            followed_up.append({"job_id": jid, "brand": it.get("brandName"), "result": res})
    return {
        "window_hours": int(lookback),
        "messages_total": len(items),
        "new_messages": n_fresh,
        "new_matches": fresh_meta,
        "interest": interest_meta,
        "interviews": [dict(m, replied=(jid in st["replied"]))
                       for jid, it, who, m in interviews],
        "replied_ok": replied_ok,
        "reply_failed": reply_failed,
        "followed_up": followed_up,
        "unread_semantics": "打开消息页会清零未读；本条按 msgTime + fromId 判定",
    }


# --------------------------------------------------------------------------
# 子命令
# --------------------------------------------------------------------------
def emit(cfg, lines, quiet=False):
    head = "【%s %s】" % (stamp(cfg), cfg_tz(cfg).key.split("/")[-1])
    block = head + "\n" + "\n".join("- " + str(x) for x in lines)
    try:
        with open(cfg["paths"]["log"], "a", encoding="utf-8") as f:
            f.write(block + "\n")
    except Exception:
        pass
    if not quiet:
        print(block)


SCHEMA = 1


def emit_json(payload, cfg=None, lines=None):
    """Machine-readable output for harnesses.

    stdout gets exactly one JSON object (nothing else). The human-readable
    block still lands in runs.log so the daily log stays greppable.
    """
    if cfg is not None and lines:
        try:
            head = "【%s %s】" % (stamp(cfg), cfg_tz(cfg).key.split("/")[-1])
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
    target = config_path(args)
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
    path = config_path(args)
    cfg = load_config(path)
    errs, warns = validate_cfg(cfg)
    if want_json(args):
        cap = (cfg.get("sending") or {}).get("daily_cap")
        emit_json({"cmd": "validate", "ok": not errs, "config": path,
                   "workdir": cfg["paths"]["workdir"], "errors": errs, "warnings": warns,
                   "targets": [{"name": t.get("name"), "city": t.get("city"),
                                "city_code": t.get("city_code"),
                                "keywords": t.get("keywords") or []}
                               for t in (cfg.get("targets") or []) if t.get("enabled", True)],
                   "daily_cap": cap, "timezone": cfg_tz(cfg).key})
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
        print("✅ 配置可用：目标 %s｜日上限 %s｜时区 %s" % (tgt, cap, cfg_tz(cfg).key))
    return 1 if errs else 0


def cmd_template(args):
    cfg = load_config(config_path(args))
    job = {"jobName": args.job_name or "示例岗位", "brandName": args.brand or "示例公司",
           "cityName": args.city or "示例城市"}
    text = render((cfg.get("user") or {}).get("greeting_template") or "", cfg, job)
    if want_json(args):
        emit_json({"cmd": "template", "ok": 0 < len(text) <= 380, "text": text,
                   "chars": len(text), "marker": verify_marker(cfg, job),
                   "recommended_chars": [80, 200], "hard_max": 380})
        return 0
    print(text)
    print("\n--- 校验标记：%r（发送后必须在弹窗里出现）" % verify_marker(cfg, job))
    print("字数：%d（建议 80–200 字；>380 会被拦）" % len(text))
    return 0


def cmd_login(args):
    cfg = load_config(config_path(args))
    br = CM.Browser(cfg)
    bridge = br.connect()
    ext = br.wake
    lines = ["bridge:%s" % bridge, "extension:%s" % ext]
    logged, url, closed = False, "", 0
    try:
        tab, stt = check_login(br, cfg)
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
        closed = br.close_own_tabs()
        lines.append("已关自己开的标签页 %d 个" % closed)
        br.close()
    if want_json(args):
        emit_json({"cmd": "login-check", "ok": logged, "logged": logged, "bridge": bridge,
                   "extension": ext, "url": url, "tabs_closed": closed}, cfg, lines)
        return 0
    emit(cfg, lines)
    return 0


def cmd_preflight(args):
    cfg = load_config(config_path(args))
    br = CM.Browser(cfg)
    bridge = br.connect()
    ext = br.wake
    lines = ["bridge:%s" % bridge, "extension:%s" % ext]
    logged, gs, closed = False, "", 0
    cap = int((cfg.get("sending") or {}).get("daily_cap") or 0)
    day_count = None
    try:
        tab, stt = check_login(br, cfg)
        logged = bool(stt.get("avatar")) and not stt.get("login")
        lines.append("登录：%s" % ("ok" if logged else "未确认/未登录"))
        gs = greeting_switch(br, tab, fix=args.fix)
        lines.append("招呼语开关：%s" % gs)
        state = load_state(cfg)
        day_count = state.get("day_count")
        lines.append("今日已发 %s/%s（%s）" % (day_count, cap, cfg_tz(cfg).key))
    finally:
        closed = br.close_own_tabs()
        lines.append("已关自己开的标签页 %d 个" % closed)
        br.close()
    if want_json(args):
        emit_json({"cmd": "preflight", "ok": logged and "auto_greeting_on" not in gs,
                   "logged": logged, "greeting_switch": gs, "day_count": day_count,
                   "daily_cap": cap, "bridge": bridge, "extension": ext,
                   "tabs_closed": closed}, cfg, lines)
        return 0
    emit(cfg, lines)
    return 0


def cmd_scan(args):
    cfg = load_config(config_path(args))
    br = CM.Browser(cfg)
    bridge = br.connect()
    ext = br.wake
    lines = ["bridge:%s" % bridge, "extension:%s" % ext]
    saved, errs, closed = 0, [], 0
    jobs, cs = [], []
    try:
        saved, errs = scan(cfg, br, targets=args.target, keywords=args.keyword,
                           pages=args.pages)
        lines.append("抓取 %d 页响应" % saved)
        lines.extend("⚠️ " + e for e in errs)
        jobs = load_jobs(cfg)
        cs = candidates(cfg)
        lines.append("库内岗位 %d，其中 A%d/B%d"
                     % (len(jobs), sum(1 for x in cs if x["grade"] == "A"),
                        sum(1 for x in cs if x["grade"] == "B")))
    finally:
        closed = br.close_own_tabs()
        lines.append("已关自己开的标签页 %d 个" % closed)
        br.close()
    if want_json(args):
        emit_json({"cmd": "scan", "ok": saved > 0, "pages_saved": saved, "errors": errs,
                   "jobs_total": len(jobs), "candidates": len(cs),
                   "grade_a": sum(1 for x in cs if x["grade"] == "A"),
                   "grade_b": sum(1 for x in cs if x["grade"] == "B"),
                   "bridge": bridge, "extension": ext, "tabs_closed": closed}, cfg, lines)
        return 0
    emit(cfg, lines)
    return 0


def cmd_plan(args):
    cfg = load_config(config_path(args))
    st = load_state(cfg)
    cs = candidates(cfg)
    todo = [x for x in cs if x["job"]["encryptJobId"] not in (st.get("sent") or {})]
    cap = int((cfg.get("sending") or {}).get("daily_cap") or 0)
    left = max(0, cap - int(st.get("day_count") or 0))
    lines = ["候选 %d（A%d/B%d），未投 %d，今日剩 %d/%d"
             % (len(cs), sum(1 for x in cs if x["grade"] == "A"),
                sum(1 for x in cs if x["grade"] == "B"), len(todo), left, cap)]
    for x in todo[:args.top]:
        j = x["job"]
        lines.append("%s%s｜%s｜%s｜%s｜%s·%s｜%s"
                     % (x["grade"], x["score"], j.get("jobName"), j.get("salaryDesc") or "面议",
                        j.get("brandName"), j.get("cityName"), j.get("areaDistrict") or "",
                        " / ".join(x["flags"]) if x["flags"] else "对口"))
    if len(todo) > args.top:
        lines.append("…另有 %d 个未列出" % (len(todo) - args.top))
    if want_json(args):
        emit_json({"cmd": "plan", "ok": True, "ts": stamp(cfg), "timezone": cfg_tz(cfg).key,
                   "summary": {"candidates": len(cs),
                               "grade_a": sum(1 for x in cs if x["grade"] == "A"),
                               "grade_b": sum(1 for x in cs if x["grade"] == "B"),
                               "unsent": len(todo), "remaining_today": left,
                               "daily_cap": cap, "day_count": int(st.get("day_count") or 0)},
                   "items": [{"grade": x["grade"], "score": x["score"],
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


def do_send(cfg, br, tab, st, want, out):
    cap = int((cfg.get("sending") or {}).get("daily_cap") or 0)
    budget = min(int(want), max(0, cap - int(st.get("day_count") or 0)))
    if budget <= 0:
        out.append("今日额度已用完 %d/%d，停止发送" % (st.get("day_count"), cap))
        return {"sent": 0, "day_count": st.get("day_count"), "daily_cap": cap,
                "items": [], "skipped": [], "reason": "quota_exhausted"}
    shares = {t.get("name"): t.get("daily_share") for t in (cfg.get("targets") or [])
              if t.get("daily_share")}
    cs = candidates(cfg)
    todo = [x for x in cs if x["job"]["encryptJobId"] not in (st.get("sent") or {})]
    gap = int((cfg.get("sending") or {}).get("min_gap_seconds") or 60)
    sent = 0
    sent_items, skipped = [], []
    for x in todo:
        if sent >= budget:
            break
        j = x["job"]
        tname = target_of(cfg, j)
        if shares.get(tname):
            used = (st.get("day_by_target") or {}).get(tname, 0)
            if used >= int(shares[tname]):
                continue
        ck = "%s|%s" % (j.get("brandName"), j.get("bossName"))
        if ck in (st.get("contacts") or []):
            out.append("跳过(同 HR 已投) %s" % (j.get("jobName") or "")[:20])
            skipped.append({"result": "skip_same_hr", "job_id": j.get("encryptJobId"),
                            "title": j.get("jobName"), "brand": j.get("brandName")})
            continue
        res = send_one(br, tab, j, cfg)
        if res == "sent":
            st["sent"][j["encryptJobId"]] = {
                "ts": stamp(cfg), "name": j.get("jobName"), "brand": j.get("brandName"),
                "salary": j.get("salaryDesc"), "grade": x["grade"], "city": j.get("cityName"),
                "target": tname}
            st["day_count"] = int(st.get("day_count") or 0) + 1
            st.setdefault("day_by_target", {})[tname] = (st.get("day_by_target") or {}).get(tname, 0) + 1
            st.setdefault("contacts", []).append(ck)
            sent += 1
            save_state(cfg, st)
            out.append("%s 已发｜%s｜%s｜%s" % (x["grade"], j.get("jobName"),
                                              j.get("brandName"), j.get("salaryDesc") or ""))
            sent_items.append({"result": "sent", "grade": x["grade"], "score": x["score"],
                               "job_id": j.get("encryptJobId"), "title": j.get("jobName"),
                               "brand": j.get("brandName"), "salary": j.get("salaryDesc"),
                               "city": j.get("cityName"), "target": tname})
            time.sleep(gap)
        elif res.startswith("skip_already") or res in ("skip_no_button",):
            out.append("跳过(%s) %s" % (res, (j.get("jobName") or "")[:20]))
            skipped.append({"result": res, "job_id": j.get("encryptJobId"),
                            "title": j.get("jobName"), "brand": j.get("brandName")})
            st.setdefault("contacts", []).append(ck)  # 已沟通过的岗位别再试
            save_state(cfg, st)
            time.sleep(4)
        else:
            out.append("跳过(%s) %s" % (res, (j.get("jobName") or "")[:20]))
            skipped.append({"result": res, "job_id": j.get("encryptJobId"),
                            "title": j.get("jobName"), "brand": j.get("brandName")})
            time.sleep(5)
    out.append("本轮发出 %d，今日累计 %d/%d" % (sent, st.get("day_count"), cap))
    return {"sent": sent, "day_count": st.get("day_count"), "daily_cap": cap,
            "items": sent_items, "skipped": skipped}


def target_of(cfg, job):
    city = job.get("cityName") or ""
    for t in cfg.get("targets") or []:
        if t.get("city") == city and t.get("enabled", True):
            return t.get("name")
    return city or "default"


def cmd_send(args):
    cfg = load_config(config_path(args))
    st = load_state(cfg)
    br = CM.Browser(cfg)
    bridge = br.connect()
    ext = br.wake
    out = ["bridge:%s" % bridge, "extension:%s" % ext]
    tab = None
    closed = 0
    res = {"sent": 0, "day_count": st.get("day_count"),
           "daily_cap": int((cfg.get("sending") or {}).get("daily_cap") or 0),
           "items": [], "skipped": []}
    try:
        first = (cfg.get("targets") or [{}])[0]
        tab = br.new_tab("https://www.zhipin.com/web/geek/jobs?city=%s"
                         % str(first.get("city_code") or ""))
        time.sleep(3)
        res = do_send(cfg, br, tab, st, args.n, out)
    finally:
        closed = br.close_own_tabs()
        out.append("已关自己开的标签页 %d 个" % closed)
        br.close()
        save_state(cfg, st)
    if want_json(args):
        emit_json(dict(res, cmd="send", ts=stamp(cfg), bridge=bridge, extension=ext,
                       tabs_closed=closed), cfg, out)
        return 0
    emit(cfg, out)
    return 0


def cmd_inbox(args):
    cfg = load_config(config_path(args))
    st = load_state(cfg)
    br = CM.Browser(cfg)
    bridge = br.connect()
    ext = br.wake
    out = ["bridge:%s" % bridge, "extension:%s" % ext]
    tab = None
    data, closed = {}, 0
    try:
        tab = br.new_tab("https://www.zhipin.com/web/geek/chat")
        time.sleep(4)
        data = inbox(cfg, br, tab, st, out)
    finally:
        closed = br.close_own_tabs()
        out.append("已关自己开的标签页 %d 个" % closed)
        br.close()
        save_state(cfg, st)
    if want_json(args):
        emit_json(dict(data, cmd="inbox", ts=stamp(cfg), bridge=bridge, extension=ext,
                       tabs_closed=closed), cfg, out)
        return 0
    emit(cfg, out)
    return 0


def cmd_run(args):
    cfg = load_config(config_path(args))
    st = load_state(cfg)
    br = CM.Browser(cfg)
    bridge = br.connect()
    ext = br.wake
    out = ["bridge:%s" % bridge, "extension:%s" % ext]
    tab = None
    cs, closed = [], 0
    scan_res, send_res, inbox_res = {"pages_saved": 0, "errors": []}, None, None
    try:
        scan_out = scan(cfg, br, pages=args.pages)
        scan_res = {"pages_saved": scan_out[0], "errors": scan_out[1]}
        out.append("scan: +%d 页%s" % (scan_out[0], (" ⚠️ " + ",".join(scan_out[1])) if scan_out[1] else ""))
        cs = candidates(cfg)
        out.append("候选 %d（A%d/B%d）" % (len(cs), sum(1 for x in cs if x["grade"] == "A"),
                                         sum(1 for x in cs if x["grade"] == "B")))
        per = args.send if args.send is not None else int(
            (cfg.get("schedule") or {}).get("per_window") or 0)
        if per:
            tab = br.new_tab("https://www.zhipin.com/web/geek/jobs?city=%s"
                             % str((cfg.get("targets") or [{}])[0].get("city_code") or ""))
            time.sleep(3)
            send_res = do_send(cfg, br, tab, st, per, out)
        if args.inbox:
            if tab is None:
                tab = br.new_tab("https://www.zhipin.com/web/geek/chat")
                time.sleep(4)
            inbox_res = inbox(cfg, br, tab, st, out)
    finally:
        closed = br.close_own_tabs()
        out.append("已关自己开的标签页 %d 个" % closed)
        br.close()
        save_state(cfg, st)
    if want_json(args):
        emit_json({"cmd": "run", "ok": True, "ts": stamp(cfg), "bridge": bridge,
                   "extension": ext, "tabs_closed": closed, "scan": scan_res,
                   "candidates": {"total": len(cs),
                                  "grade_a": sum(1 for x in cs if x["grade"] == "A"),
                                  "grade_b": sum(1 for x in cs if x["grade"] == "B")},
                   "send": send_res, "inbox": inbox_res}, cfg, out)
        return 0
    emit(cfg, out)
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

    p = sub.add_parser("send", help="真发招呼")
    p.add_argument("n", type=int, help="本轮最多发几个（首跑建议 1-2）")
    add_json_flag(p)
    p.set_defaults(func=cmd_send)

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


def main():
    ap = build_parser()
    args = ap.parse_args()
    if not getattr(args, "func", None):
        ap.print_help()
        return 2
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
