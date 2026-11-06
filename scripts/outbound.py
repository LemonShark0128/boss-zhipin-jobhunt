"""投递链路、页面检查与发送执行。"""
import hashlib
import json
import os
import re
import send_safety as SS
import time
import chat_send as CS
import chrome_mcp as CM
import greet as G
import hunt_config as HC
import hunt_filter as HF
import hunt_schedule as HQ
import hunt_state as HS
from jd_page import read_jd  # audit-01 Q8：JD 读取挪到 jd_page，投递与对话共用


APPWALL_RE = r"需(要)?(下载|跳转|打开)\s*App|App内(查看|沟通)|去小程序"


def prepare_grade(cfg, br, tab, job, st):
    """在点击前定级。缓存命中免读 JD，页面按钮守卫发送时仍执行。"""
    jid = job["encryptJobId"]
    fp = HF.grade_fingerprint(job, cfg)
    cached = HS.cached_grade(cfg, job, st)
    if cached:
        job["_jd"] = cached["jd_text"]
        return dict(cached, cached=True)
    status, jd = inspect_job(br, tab, job, cfg)
    if status != "ready":
        return {"grade": "C", "final": False, "reason": status, "status": status, "cached": False}
    job["_prepared"], job["_jd"] = True, jd
    if not jd.strip():
        return {"grade": "C", "final": False, "reason": "缺少 JD，保守跳过", "status": "skip_missing_jd", "cached": False}
    gr = HF.grade_with_jd(job, cfg, jd)
    gr.update(cached=False, jd_hash=hashlib.sha256(jd.encode()).hexdigest(), jd_text=jd, fingerprint=fp,
              jd_fp=HF.jd_fingerprint(job), jd_ts=HC.stamp(cfg), rule_fp=HF.rule_fingerprint(cfg),
              job={k: job.get(k) for k in HF.JD_FIELDS})
    st.setdefault("grade_cache", {})[jid] = gr
    HS.save_state(cfg, st)
    return gr


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


def verify_marker(cfg, job=None, text=None):
    """发送后要在页面上核对的字符串。

    llm 模式下招呼语每次现写，没法从模板推出来 —— 调用方必须把实际发出去的 text 传进来；
    这时也**优先用 text**，忽略 sending.verify_text（否则配了固定校验词会让每条都被判 unverified）。
    """
    if text and G.enabled(cfg):
        core = re.sub(r"[^\S]+", "", text)
        return core[:8] if core else ""
    s = (cfg.get("sending") or {}).get("verify_text")
    if s:
        return s
    rendered = text or render((cfg.get("user") or {}).get("greeting_template") or "", cfg, job)
    core = re.sub(r"[^\S]+", "", rendered)
    return core[:8] if core else ""


def login_state(br, tab):
    """只读探测登录态；绝不代填账号密码/验证码。"""
    expr = ("(()=>{const t=document.body.innerText||'';"
            "return JSON.stringify({url:location.href,"
            "login:/扫码登录|登录\\/注册|手机号登录|密码登录|请登录/.test(t),"
            "avatar:!!document.querySelector('.nav-figure,img[class*=avatar],[class*=user-avatar]'),"
            "chat:!!document.querySelector('[href*=\"/web/geek/chat\"]')})})()")
    return br.json(tab, expr, default={"raw": "eval_failed"}) or {}


LOGIN_RETRY_WAIT = 5     # heal-01：标签没开成，等一下再开一次
LOGIN_READ_WAIT = 4      # heal-01：页面没渲染出头像/登录墙，等一下再读
LOGIN_EXTRA_READS = 2
NAV_RETRY_WAIT = 6       # heal-01：详情页导航瞬时出错，等一下在同一标签页再导航一次
GREETING_RETRY_WAIT = 4  # heal-02：招呼语开关未渲染时，先在同一页等一下再读


def check_login(br, cfg):
    """只读探测登录态。瞬时失败（标签没开成、页面没渲染完）各重试；明确的登录墙不重试、不代登录。"""
    url = "https://www.zhipin.com/web/geek/jobs"
    tab = br.new_tab(url)
    if not tab:
        time.sleep(LOGIN_RETRY_WAIT)
        tab = br.new_tab(url)
        if not tab:
            return None, {}
    time.sleep(3)
    st = login_state(br, tab)
    for _ in range(LOGIN_EXTRA_READS):
        if st.get("login") or st.get("avatar"):
            break
        time.sleep(LOGIN_READ_WAIT)
        st = login_state(br, tab)
    return tab, st


def switch_status(rows):
    """把页面抓到的开关行判成 on/off/unknown。

    只认明确证据（2026-10-28 实测真实 DOM）：
    - `.ui-switch` 里 `<input type=hidden value="0|1">`（线上就是这个结构，value 0=关）
    - `aria-checked="true|false"`
    - class 含 `ui-switch-checked`（视为开）
    证据缺失、两处证据冲突、或同一行命中多个开关且状态不一致 → unknown。
    """
    if not isinstance(rows, list):
        return 'auto_greeting_unknown'
    relevant = [s for s in rows if isinstance(s, dict) and '招呼' in (s.get('t') or '')]
    if not relevant:
        return 'auto_greeting_unknown'
    states = set()
    for s in relevant:
        on = s.get('on')
        if type(on) is not bool:
            return 'auto_greeting_unknown'
        states.add(on)
    if len(states) != 1:
        return 'auto_greeting_unknown'
    return 'auto_greeting_on' if states.pop() else 'auto_greeting_off'


def greeting_switch(br, tab, fix=False):
    url = "https://www.zhipin.com/web/geek/notify-set?type=greetSet"
    br.nav(tab, url, wait=3.5)
    expr = ("(()=>{const rows=[...document.querySelectorAll('.ui-switch')];"
            "return JSON.stringify(rows.map(e=>{"
            "const aria=e.getAttribute('aria-checked');"
            "const hid=(e.querySelector?e.querySelector('input[type=hidden]'):null);"
            "const hv=hid?String(hid.value):null;"
            "const checked=e.classList.contains('ui-switch-checked');"
            "let on=null;"
            "if(hv==='1'||aria==='true')on=true;"
            "else if(hv==='0'||aria==='false')on=false;"
            "else if(checked)on=true;"
            "if(hv!==null&&(hv==='0'||hv==='1')&&aria!==null&&aria!==''&&"
            "((hv==='1')!==(aria==='true')))on=null;"
            "return {t:(e.closest('li,div')?e.closest('li,div').innerText:'').replace(/\\s+/g,' ').slice(0,80),on,"
            "hid:hv,aria,checked};}));})()")
    for attempt in range(3):
        if attempt == 1:
            time.sleep(GREETING_RETRY_WAIT)
        elif attempt == 2:
            br.nav(tab, url, wait=3.5)
        r = br.js(tab, expr)
        switches = CM.parse_json_payload(r.get("text") or "", []) or []
        current = switch_status(switches)
        if current != 'auto_greeting_unknown':
            break
    if current == 'auto_greeting_on' and fix:
        br.js(tab, "(()=>{const rows=[...document.querySelectorAll('.ui-switch')];"
                   "const hit=rows.find(e=>((e.closest('li,div')||{}).innerText||'').includes('招呼')"
                   "&&(e.classList.contains('ui-switch-checked')||String((e.querySelector('input[type=hidden]')||{}).value)!=='0'));"
                   "if(hit){hit.click();return 'clicked'}return 'notfound'})()")
        time.sleep(2)
        after = switch_status(CM.parse_json_payload(br.js(tab, expr).get('text') or '', []))
        return 'auto_greeting_on_still' if after == 'auto_greeting_on' else after
    return current


def page_guard(br, tab):
    """Read structural captcha/login evidence without navigating or clicking."""
    try:
        state = br.json(tab, (
            "(()=>{return JSON.stringify({"
            "cap:/验证码中间页|安全验证|人机验证/.test(document.title)"
            "||!!document.querySelector('[class*=captcha],[id*=captcha],.nc_wrapper,.nc_container'),"
            "login:/扫码登录|手机号登录|请先登录/.test(document.body.innerText)})})()"
        ), default={}) or {}
        if state.get("cap"):
            return "captcha"
        if state.get("login"):
            return "not_logged_in"
    except Exception:
        pass
    return ""


def scan(cfg, br, targets=None, keywords=None, pages=3):
    import urllib.parse
    saved, errs = 0, []
    for t in [x for x in (cfg.get("targets") or []) if x.get("enabled", True)]:
        HQ.check_deadline(cfg)
        if targets and t.get("name") not in targets:
            continue
        kws = t.get("keywords") or []
        if keywords:
            kws = [k for k in kws if k in keywords] or keywords
        city, code = t.get("city") or "", str(t.get("city_code") or "")
        tab = None
        for kw in kws:
            HQ.check_deadline(cfg)
            url = ("https://www.zhipin.com/web/geek/jobs?query=%s&city=%s"
                   % (urllib.parse.quote(kw), code))
            if tab is None:
                tab = br.new_tab('about:blank')
                # huashu starts recording only after the first network call.
                br.network(tab, "joblist.json", index=0, max_body=200000)
            br.nav(tab, url, wait=4)
            st = login_state(br, tab)
            if st.get("login") and not st.get("avatar"):
                errs.append("not_logged_in")
                if cfg.get("_deadline"):
                    raise RuntimeError("not_logged_in")
                break
            br.scroll(tab, times=10, wait=800)
            time.sleep(2)
            for i in range(max(1, int(pages))):
                r = br.network(tab, "joblist.json", index=i, max_body=200000)
                d = CM.parse_network_json(r.get("text") or "")
                if i >= 1 and (r.get("isError") or "NO_MATCH" in (r.get("text") or "") or not d):
                    # A missing later capture means this keyword has no next page.
                    break
                if i == 0 and (r.get("isError") or not d):
                    # Retry once on the owned tab with recording already active.
                    br.nav(tab, 'about:blank', wait=1)
                    br.nav(tab, url, wait=4)
                    br.scroll(tab, times=10, wait=800)
                    time.sleep(2)
                    r = br.network(tab, "joblist.json", index=0, max_body=200000)
                    d = CM.parse_network_json(r.get("text") or "")
                if cfg.get("_deadline") and (r.get("isError") or (i == 0 and not d)):
                    raise RuntimeError("scan_network_failed")
                if not d or d.get("code") != 0 or not ((d.get("zpData") or {}).get("jobList")):
                    if d and d.get("code") == 37:
                        errs.append("risk_code_37")
                        if cfg.get("_deadline"):
                            raise RuntimeError("risk_code_37")
                    continue
                safe_kw = re.sub(r"[^\w\u4e00-\u9fa5-]+", "_", kw)
                path = os.path.join(cfg["paths"]["raw_dir"], "%s_%s_%d.json" % (city or "city", safe_kw, i))
                with open(path, "w", encoding="utf-8") as f:
                    json.dump(d, f, ensure_ascii=False)
                saved += 1
            time.sleep(2)
    return saved, errs


LAST_GREET = {}   # send_one 顺带把本条招呼语的生成信息留在这里，do_send 记进 state


def _nav_detail(br, tab, jid):
    """heal-01：详情页导航出错先等一下重试一次；两次都错才算网络问题。"""
    url = "https://www.zhipin.com/job_detail/%s.html" % jid
    nav = br.nav(tab, url, wait=4.0)
    if nav and nav.get("isError"):
        time.sleep(NAV_RETRY_WAIT)
        nav = br.nav(tab, url, wait=4.0)
    return not (nav and nav.get("isError"))


def inspect_job(br, tab, job, cfg):
    jid = job.get("encryptJobId")
    if not _nav_detail(br, tab, jid):
        return "skip_network", ""
    # 只按结构判验证码：详情页底部有隐藏的登录框，正文里常带「发送验证码」，
    # 用整页文本匹配「验证码」会把正常岗位全判成 skip_captcha（2026-10-23 实测 4/4 误判）。
    expr = ("(()=>{const t=(document.body.innerText||'');"
            "return JSON.stringify({s:!!document.querySelector('.btn-startchat'),"
            "c:!!document.querySelector('.btn-continue-chat'),"
            "login:/扫码登录|手机号登录|请先登录/.test(t),"
            "cap:/验证码中间页|安全验证|人机验证/.test(document.title)"
            "||!!document.querySelector('[class*=captcha],[id*=captcha],.nc_wrapper,.nc_container'),"
            "app:/%s/.test(t),"
            "title:(document.querySelector('h1')||{}).innerText||''})})()"
            % APPWALL_RE)
    # 详情页 DOM 有时 2-3 秒才渲染出按钮；只探一次会把正常岗位误判成 skip_no_button
    # （2026-10-23 实测：一轮 206 个岗位全被判无按钮，实际打开都有 .btn-startchat）。
    state = {}
    for attempt in range(2):
        for _ in range(8):
            state = br.json(tab, expr, default={}) or {}
            if state.get("s") or state.get("c") or state.get("cap") or state.get("app") or state.get("login"):
                break
            time.sleep(1.2)
        if state or not cfg.get("_deadline") or attempt == 1:
            break
        # 定时模式读不到状态时，仅重开同一岗位一次。
        if not _nav_detail(br, tab, jid):
            return "skip_network", ""
    if state.get("login"):
        return "skip_not_logged_in", ""
    if cfg.get("_deadline") and not state:
        try:
            raw = br.js(tab, expr)
            diagnostic = re.sub(r"\s+", " ", CM.unwrap(raw.get("text") or "")).strip()[:80] or "empty"
        except Exception as exc:
            diagnostic = "exc:" + type(exc).__name__
        raise RuntimeError("detail_unavailable: " + diagnostic)
    if state.get("cap"):
        return "skip_captcha", ""
    if state.get("app"):
        return "skip_need_app", ""
    if state.get("c"):
        return "skip_already_chatted", ""
    if not state.get("s"):
        return "skip_no_button", ""
    jd = job.get("_jd")
    if jd is None:
        jd = read_jd(br, tab, cfg) if HF.grading_enabled(cfg) or G.enabled(cfg) else ""
    return "ready", jd


def job_identity_matches(br, tab, job):
    data = br.json(tab, r"""(() => {
      const match = location.pathname.match(/^\/job_detail\/([^/]+)\.html$/);
      const text = selector => { const e=document.querySelector(selector);
        return e ? e.innerText.trim() : ''; };
      // 公司名只认 document.title（实测：.company-name 指向侧栏"同类公司"，会拿到别家的名字）。
      // 标题形如 「虚构渠道经理招聘」_戊示例科技招聘-BOSS直聘
      const tail = (document.title || '').split('_')[1] || '';
      const bm = tail.match(/^(.+?)招聘/);
      return {job_id:match ? match[1] : '',
        title:text('.job-banner h1,.job-name,h1'),
        brand:bm ? bm[1].trim() : ''};
      })()""", default={}) or {}
    norm = lambda value: re.sub(r'\s+', '', str(value or ''))
    return (isinstance(data, dict) and bool(job.get('encryptJobId'))
            and data.get('job_id') == job['encryptJobId']
            and bool(CS.norm_job_name(job.get('jobName')))
            and CS.norm_job_name(data.get('title')) == CS.norm_job_name(job['jobName'])
            and bool(job.get('brandName')) and norm(data.get('brand')) == norm(job['brandName']))


def send_one(br, tab, job, cfg):
    SS.ledger_guard(cfg, scope='outbound')
    jid = job.get("encryptJobId")
    if job.pop("_prepared", False):
        status, jd = "ready", job.get("_jd", "")
    else:
        status, jd = inspect_job(br, tab, job, cfg)
    if status != "ready":
        return status
    if HF.grading_enabled(cfg):
        gr = HF.grade_job(job, cfg, jd)
        if not jd or not gr["final"] or gr["grade"] == "C":
            return "skip_grade"
        if HF.grade_with_jd(job, cfg, jd)["redlines"]:
            return "skip_jd_redline"
    if not job_identity_matches(br, tab, job):
        return 'error: unknown_conversation'
    # Settings navigation changes this tab: restore and revalidate the job
    # before any generation or send click. Unknown is a closed gate.
    switch = greeting_switch(br, tab)
    if switch != 'auto_greeting_off':
        raise RuntimeError(('platform_greeting_unknown' if switch == 'auto_greeting_unknown' else
                            'platform_greeting_not_disabled') + ': ' + switch + '；请手动确认招呼语开关已关闭')
    status, jd = inspect_job(br, tab, job, cfg)
    if status != 'ready':
        return status
    if not job_identity_matches(br, tab, job):
        return 'error: unknown_conversation'
    gr = G.generate(cfg, job, jd,
                    fallback=lambda j: render((cfg.get("user") or {}).get("greeting_template") or "",
                                              cfg, j))
    text = gr.get("text") or ""
    LAST_GREET.clear()
    LAST_GREET.update({"job_id": jid, "source": gr.get("source"), "chars": len(text),
                       "attempts": gr.get("attempts"), "secs": gr.get("secs"),
                       "reasons": gr.get("reasons"),
                       "fallback_reason": gr.get("fallback_reason"),
                       "jd_chars": len(jd), "text": text})
    if not text:
        return "err_no_greeting"
    if len(text) > 380:
        return "err_template_too_long"
    send_meta = {}
    def send():
        if not job_identity_matches(br, tab, job):
            return 'unknown_conversation'
        clicked = br.click(tab, ".btn-startchat")
        if clicked.get('isError'):
            return 'click_err'
        # The start button navigates the whole tab; there is no greeting dialog.
        for attempt in range(16):
            # 必须 JSON.stringify 后解析：br.json 走 parse_json_payload，裸字符串会被丢掉。
            where = br.json(tab, '(() => JSON.stringify({path: location.pathname}))()', default={}) or {}
            if isinstance(where, dict) and where.get('path') == '/web/geek/chat':
                break
            if attempt == 15:
                return 'error: chat_not_opened'
            time.sleep(0.8)
        # 聊天页是异步渲染的：路径先变，会话面板晚几百毫秒才出现，必须轮询等它画出来。
        panel = {}
        for ready in range(12):
            panel = CS.chat_snapshot(br, tab)
            if panel.get('ok'):
                break
            if panel.get('reason') != 'panel_not_ready':
                return 'send_aborted:' + panel.get('reason', 'panel_not_unique')
            if ready == 11:
                return 'send_aborted:panel_not_ready'
            time.sleep(0.8)
        # 头部文本也是异步填充的：面板出现 ≠ 头部已写完，等它出现岗位名再动手。
        for ident in range(10):
            if CS.chat_identity_evidence(panel.get('panel_text'), job):
                break
            if ident == 9:
                return 'send_aborted:identity_mismatch'
            time.sleep(0.8)
            panel = CS.chat_snapshot(br, tab)
            if not panel.get('ok'):
                return 'send_aborted:' + panel.get('reason', 'panel_not_ready')
        filled = CS.type_into_chat(br, tab, text)
        HC.trace('type_into_chat -> %s' % json.dumps(filled, ensure_ascii=False))
        if filled.get('ok') is not True:
            return 'fill_err' if filled.get('reason') == 'fill_mismatch' else 'send_aborted:' + filled.get('reason', 'input_mismatch')
        # Snapshot immediately before clicking so existing identical bubbles do not count.
        before = CS.chat_snapshot(br, tab)
        HC.trace('before snapshot ok=%s msgs=%d ids=%s' % (
            before.get('ok'), len(before.get('messages') or []),
            [m.get('id') for m in before.get('messages') or []][-3:]))
        if not before.get('ok') or not CS.chat_identity_evidence(before.get('panel_text'), job):
            return 'send_aborted:identity_mismatch'
        # 首条招呼走与自动回复同一个发送原语（面板内按钮、身份+输入双重校验）。
        # click_send_scoped 内部已轮询等按钮解锁（框架同步输入模型是异步的）。
        clicked = CS.click_send_scoped(br, tab, text, job=job, conv_key='job:' + str(jid))
        HC.trace('click -> %s' % json.dumps(clicked, ensure_ascii=False))
        # 兜底 1：execCommand 写了字但框架模型没动 → 让扩展注入一次真实输入，再等解锁。
        if isinstance(clicked, dict) and clicked.get('reason') == 'button_disabled':
            refilled = CS.type_via_bridge(br, tab, text)
            HC.trace('bridge refill -> %s' % json.dumps(refilled, ensure_ascii=False))
            if refilled.get('ok'):
                clicked = CS.click_send_scoped(br, tab, text, job=job, conv_key='job:' + str(jid))
                HC.trace('click after refill -> %s' % json.dumps(clicked, ensure_ascii=False))
        # 兜底 2：按钮始终 disabled → 按页面提示的 Enter 键发送（身份+输入同轮校验）。
        if isinstance(clicked, dict) and clicked.get('reason') == 'button_disabled':
            clicked = CS.press_enter_scoped(br, tab, text, job=job, conv_key='job:' + str(jid))
            HC.trace('enter -> %s' % json.dumps(clicked, ensure_ascii=False))
            if isinstance(clicked, dict) and clicked.get('ok') is True:
                LAST_GREET['via'] = 'enter'
        outcome = CS.click_outcome(clicked)
        if outcome == 'aborted':
            if clicked['reason'] == 'stale_draft':
                return 'stale_draft'
            return 'send_aborted:' + clicked['reason']
        if outcome == 'clicked' and clicked.get('conv_key') != 'job:' + str(jid):
            return 'pending_verification'
        # A lost receipt may still have delivered: retry only the readback.
        send_meta['via'] = 'confirm_only' if outcome == 'pending' else clicked.get('via')
        try:
            confirmed = 'pending_verification'
            for attempt in range(3 if outcome == 'pending' else 1):
                time.sleep(2.0 if outcome == 'pending' else 2.5)
                after = CS.chat_snapshot(br, tab)
                if not after.get('ok') or not CS.chat_identity_evidence(after.get('panel_text'), job):
                    break
                confirmed = CS.confirm_new_bubble(
                    [m.get('id') for m in before.get('messages', [])],
                    after.get('messages', []), hashlib.sha256(text.encode()).hexdigest())
                if confirmed == 'sent':
                    break
            HC.trace('after snapshot ok=%s msgs=%d ids=%s' % (
                after.get('ok'), len(after.get('messages') or []),
                [m.get('id') for m in after.get('messages') or []][-3:]))
            if confirmed == 'sent':
                LAST_GREET['via'] = send_meta['via']
                HC.trace('send confirmed via=%s' % LAST_GREET['via'])
            return confirmed
        except Exception as e:
            HC.trace('confirm error: %r' % e)
            return 'pending_verification'

    gr = HF.grade_job(job, cfg, jd)
    return SS.ledger_send(cfg, 'send', 'job:' + str(jid), jid, text, send,
                       {'meta': send_meta, 'grade': gr['grade'], 'target': HQ.target_of(cfg, job),
                        'brand': job.get('brandName'), 'boss': job.get('bossName'), 'name': job.get('jobName')})


cleanup_browser = CM.cleanup_browser  # audit-01 Q8：实现挪到 chrome_mcp，保留旧名


def send_budget(cfg, st, now, want=None):
    """本跳预算与各级配额：(budget, quotas)。do_send 与 tick --dry-run 共用（audit-01 #6），
    两边不再各写一份「daily_cap − day_count → max_per_window → per_window → shrink_quotas」。"""
    sending = cfg.get("sending") or {}
    per_window = max(0, int((cfg.get("schedule") or {}).get("per_window") or 0))
    budget = max(0, int(sending.get("daily_cap") or 0) - int(st.get("day_count") or 0))
    if want is not None:
        budget = min(budget, max(0, int(want)))
    modern = HF.grading_enabled(cfg) or "grade_quota" in sending
    quotas = HQ.quotas_for_now(cfg, st, now) if modern else HQ.grade_limits(cfg)
    if not modern and want is None:
        budget = min(budget, per_window)
    if modern:
        budget = min(budget, max(0, int(sending.get("max_per_window", 8))))
        if not sending.get("carry_over", "grade_quota" in sending):
            budget = min(budget, per_window)
        # A 先发，配额不压缩时 budget 会被 A 吃光，B 永远发不出去（实测 22:00 A8/B8 全发成 A）。
        quotas = HQ.shrink_quotas(quotas, budget)
    return budget, quotas


def do_send(cfg, br, tab, st, want, out, window_key=None):
    SS.ledger_guard(cfg, scope='outbound')
    HS.refresh_ledger_counts(cfg, st)
    sending = cfg.get("sending") or {}
    if window_key is None:
        now = HC.now_local(cfg)
        window = HQ.tick_plan(cfg, now)["window"]
        window_key = now.strftime("%Y-%m-%d ") + window if window else now.strftime("%Y-%m-%d %H:%M")
    retries = st.setdefault("retry_jobs", {})
    stop_reason = "none"
    halted = False
    quiet = {}
    cap = int(sending.get("daily_cap") or 0)
    budget, quotas = send_budget(cfg, st, HC.now_local(cfg), want)
    sent_items, skipped = [], []
    sent_contacts = set()
    if budget <= 0 or not any(quotas.values()):
        out.append("本跳或今日额度已用完 %d/%d，停止发送" % (st.get("day_count", 0), cap))
        return {"sent": 0, "day_count": st.get("day_count", 0), "daily_cap": cap,
                "by_grade": {}, "quotas": quotas,
                "items": [], "skipped": [], "inspects": 0, "reason": "quota_exhausted",
                "quiet": quiet, "stop_reason": "quota_exhausted", "retry_pending": len(retries)}
    shares = {t.get("name"): t.get("daily_share") for t in cfg.get("targets") or [] if t.get("daily_share")}
    gap = int(SS.min_gap_seconds(cfg) or 60)  # 显式 0 在投递里仍按 60（原行为）
    used = {}
    pool = st.setdefault("b_pool", {})
    snapshot_jobs = HF.load_jobs(cfg)
    snapshot_ids = {j["encryptJobId"] for j in snapshot_jobs}
    todo = HS.candidates(cfg, snapshot_jobs, st=st)
    # 池中岗位即使不再出现在新快照，也保留原始字段；仍重新执行硬过滤。
    jobs = {x["job"]["encryptJobId"]: x for x in todo}
    for jid, row in pool.items():
        if jid not in jobs and jid not in snapshot_ids and row.get("job"):
            extra = HS.candidates(cfg, [row["job"]], st=st)
            if extra:
                jobs[jid] = extra[0]
    todo = [x for jid, x in jobs.items() if jid not in st.get("platform_chatted", {})
            and jid not in st.get("opened_no_greet", {})]
    # 稳定排序保留原候选优先级，但新候选先于同指纹的重试候选。
    todo.sort(key=lambda x: (retries.get(x["job"]["encryptJobId"]) or {}).get("fp")
              == HF.stable_fingerprint(x["job"], cfg))

    def halt(status):
        nonlocal stop_reason, halted
        halted = True
        if status == "skip_captcha":
            stop_reason = "captcha"
        elif status in ("skip_network", "skip_not_logged_in", "detail_unavailable") or status.startswith("detail_unavailable:"):
            stop_reason = "network"
        elif status.startswith("skip_min_gap") or status == "skip_daily_cap":
            stop_reason = "quota_exhausted"
        HS.save_state(cfg, st)
        if cfg.get("_deadline") and stop_reason in ("captcha", "network"):
            raise RuntimeError("captcha_wall" if status == "skip_captcha" else status)

    def skip(j, result):
        out.append("跳过(%s) %s" % (result, j.get("jobName") or ""))
        skipped.append({"result": result, "job_id": j["encryptJobId"],
                        "title": j.get("jobName"), "brand": j.get("brandName")})

    # Candidate filtering happens before allowed(); retain unknown-time reasons in JSON.
    for j in snapshot_jobs + [row["job"] for jid, row in pool.items()
                              if jid not in snapshot_ids and row.get("job")]:
        if (j["encryptJobId"] not in st.get("sent", {})
                and HS.same_hr_filter(st, cfg, j) == "skip_same_hr_unknown_ts"):
            skip(j, "skip_same_hr_unknown_ts")

    def allowed(j):
        HS.refresh_ledger_counts(cfg, st)
        if int(st.get("day_count") or 0) >= cap:
            return False
        if j["encryptJobId"] in st.get("sent", {}):
            return False
        contact_result = HS.same_hr_filter(st, cfg, j)
        if "%s|%s" % (j.get("brandName"), j.get("bossName")) in sent_contacts:
            contact_result = "skip_same_hr"
        if contact_result in ("skip_same_hr", "skip_same_hr_unknown_ts"):
            skip(j, contact_result)
            return False
        tname = HQ.target_of(cfg, j)
        if shares.get(tname) and st.get("day_by_target", {}).get(tname, 0) >= int(shares[tname]):
            return False
        return True

    def deliver(x, grade):
        j = x["job"]
        if len(sent_items) >= budget or used.get(grade, 0) >= quotas.get(grade, 0) or not allowed(j):
            return
        LAST_GREET.clear()
        res = send_one(br, tab, j, cfg)
        outcome = HS.record_outcome(cfg, st, j, res, "send", window_key)
        if res != "sent":
            skip(j, res)
            if res in ("err_no_greeting", "err_template_too_long"):
                out.append("⚠️ 招呼语配置问题：%s，请检查招呼语配置" % res)
            if outcome == "halt":
                halt(res)
                return
            time.sleep(4)
            return
        jid, tname = j["encryptJobId"], HQ.target_of(cfg, j)
        row = {"ts": HC.stamp(cfg), "applied_ts": HC.stamp(cfg), "name": j.get("jobName"),
               "brand": j.get("brandName"), "salary": j.get("salaryDesc"), "grade": grade,
               "city": j.get("cityName"), "target": tname, "greet_source": LAST_GREET.get("source"),
               "greet_chars": LAST_GREET.get("chars"), "greet_text": LAST_GREET.get("text"),
               "src": j.get("_src") or "unknown"}  # 仅诊断；发送决策不得读取 src。
        st.setdefault("sent", {})[jid] = row
        if os.path.exists(SS.ledger_path(cfg)):
            HS.refresh_ledger_counts(cfg, st)
        else:
            st["day_count"] = int(st.get("day_count") or 0) + 1
            st.setdefault("day_by_target", {})[tname] = st.get("day_by_target", {}).get(tname, 0) + 1
            st.setdefault("day_by_grade", {})[grade] = st.get("day_by_grade", {}).get(grade, 0) + 1
        contact = "%s|%s" % (j.get("brandName"), j.get("bossName"))
        st.setdefault("contacts", []).append(contact)
        HS.contact_last_sent(st, cfg)[contact] = HC.now_local(cfg).timestamp()
        sent_contacts.add(contact)
        used[grade] = used.get(grade, 0) + 1
        pool.pop(jid, None)
        HS.save_state(cfg, st)
        out.append("%s 已发｜%s｜%s｜%s｜招呼语:%s %s 字" % (
            grade, j.get("jobName"), j.get("brandName"), j.get("salaryDesc") or "",
            LAST_GREET.get("source") or "-", LAST_GREET.get("chars") or 0))
        sent_items.append(dict(row, result="sent", job_id=jid, title=j.get("jobName"), score=x["score"],
                               greet_secs=LAST_GREET.get("secs")))
        time.sleep(gap)

    deferred = []
    inspects = 0
    max_inspect = int(sending.get("max_inspect_per_window") or 24)
    probes = 0
    max_probe = int(sending.get("hidden_b_probe", 6))
    checked = st.setdefault("checked_jobs", {})
    retry_max = int(sending.get("transient_retry_max", 2))
    inspect_cap_hit = False

    def quiet_skip(reason):
        quiet[reason] = quiet.get(reason, 0) + 1

    for x in todo:
        HQ.check_deadline(cfg)
        if len(sent_items) >= budget:
            break
        if all(used.get(g, 0) >= quotas.get(g, 0) for g in quotas):
            break
        j = x["job"]
        if not allowed(j):
            continue
        fingerprint = HF.grade_fingerprint(j, cfg)
        jid = j["encryptJobId"]
        if checked.get(jid) == fingerprint:
            quiet_skip("今日已检查")
            continue
        retry = retries.get(jid) or {}
        if retry.get("fp") == HF.stable_fingerprint(j, cfg):
            if retry["n"] > retry_max:
                checked[jid] = fingerprint
                retries.pop(jid, None)
                quiet_skip("今日重试已用完")
                continue
            if retry.get("window") == window_key:
                quiet_skip("本跳已试")
                continue
        if HF.grading_enabled(cfg):
            # 粗标（纯函数、不联网）先筛一轮：职能不对口的 C 类绝不打开详情页，
            # 否则每跳会把整池候选逐个 inspect（实测 652 次），定时任务必被超时杀掉。
            # b_pool 里的岗位此前已按 JD 判过 B，粗标只看列表字段，不能拿它否掉旧候选。
            pre = HF.grade_job(j, cfg)
            in_pool = j["encryptJobId"] in pool
            cached = HS.cached_grade(cfg, j, st)
            if pre["grade"] == "C" and not in_pool:
                HS.record_outcome(cfg, st, j, "skip_grade", "grade", window_key)
                quiet_skip(pre.get("reason") or "粗标C")
                continue
            # A 配额已满：粗标 A? 的候选只在 probes 额度内读 JD（行业字段常不准，
            # 留一点余量捞出「自报互联网、实际门店运营」的隐藏 B），其余直接跳过不消耗请求。
            if (not in_pool and cached is None and used.get("A", 0) >= quotas.get("A", 0)
                    and not str(pre["grade"]).startswith("B")):
                if probes >= max_probe:
                    quiet_skip("本跳A配额已满，A候选留到下跳")
                    continue
                probes += 1
            if inspects >= max_inspect and cached is None:
                inspect_cap_hit = True
                quiet_skip("JD读取上限，留到下一跳")
                continue
            gr = prepare_grade(cfg, br, tab, j, st)
            if not gr.get("cached"):
                inspects += 1
        else:
            gr = HF.grade_job(j, cfg)
        grade = gr["grade"]
        status = gr.get("status") or "ready"
        if status == "ready" and (grade == "C" or not gr.get("final")):
            status = "skip_grade"
        outcome = HS.record_outcome(cfg, st, j, status, "grade", window_key)
        if outcome == "halt":
            skip(j, status)
            halt(status)
            break
        if grade == "C" or not gr.get("final") or grade not in quotas:
            skip(j, gr.get("status") or "skip_grade")
            continue
        if grade == "A":
            deliver(x, grade)
            if halted:
                break
        else:
            if grade == "B":
                pool[j["encryptJobId"]] = {"title": j.get("jobName"), "brand": j.get("brandName"),
                     "reason": gr["reason"], "ts": (pool.get(j["encryptJobId"]) or {}).get("ts") or HC.stamp(cfg),
                     "src": j.get("_src") or "unknown",  # 仅诊断。
                     "job": {k: v for k, v in j.items() if not k.startswith("_")}}
                HS.save_state(cfg, st)
            # A 阶段结束后导航已变化，不再沿用详情页就绪标记。
            j.pop("_prepared", None)
            deferred.append((x, grade))
    deferred.sort(key=lambda pair: (0 if pair[1] == "B" else 1,
                                   (pool.get(pair[0]["job"]["encryptJobId"]) or {}).get("ts", "")))
    for x, grade in deferred:
        if halted:
            break
        HQ.check_deadline(cfg)
        deliver(x, grade)
    if not halted:
        if len(sent_items) >= budget or all(used.get(g, 0) >= quotas.get(g, 0) for g in quotas):
            stop_reason = "quota_exhausted"
        else:
            stop_reason = "inspect_cap" if inspect_cap_hit else "pool_exhausted"
    HS.save_state(cfg, st)
    if quiet:
        top = sorted(quiet.items(), key=lambda kv: -kv[1])[:3]
        out.append("静默跳过 %d 个（%s）" % (sum(quiet.values()), "；".join("%s %d" % kv for kv in top)))
    out.append("本跳配额 " + " · ".join("%s %d/%d" % (g, used.get(g, 0), quota)
                                      for g, quota in quotas.items()))
    out.append("本轮发出 %d，今日累计 %d/%d" % (len(sent_items), st.get("day_count", 0), cap))
    return {"sent": len(sent_items), "day_count": st.get("day_count", 0), "daily_cap": cap,
            "by_grade": used, "quotas": quotas, "items": sent_items, "skipped": skipped, "inspects": inspects,
            "quiet": quiet, "stop_reason": stop_reason, "retry_pending": len(retries)}
