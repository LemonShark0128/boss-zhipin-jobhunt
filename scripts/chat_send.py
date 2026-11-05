"""Shared, fail-closed chat DOM operations. No browser or store is created here.

2026-10-28 真机教训：BOSS 聊天输入框是 `div.chat-input[contenteditable=true]`，发送按钮的
disabled 由页面框架的输入模型决定。写 `textContent` + `dispatchEvent(new InputEvent('input'))`
只改 DOM，框架模型收不到，按钮永远不解锁（线上 `send_aborted:button_disabled`）。
因此输入一律走 `document.execCommand('insertText')`：浏览器会为它生成原生 beforeinput/input，
框架模型随之更新；写完还要轮询等按钮解锁再点。
"""
import hashlib
import json
import re
import time

from conv_parse import PEER_MARKERS, SELF_MARKERS

CHAT_INPUT_SEL = '.chat-input[contenteditable="true"]'
SEND_BTN_SEL = '.btn-v2.btn-sure-v2.btn-send'
# 守卫轮给目标按钮打的临时 id：CDP 点击只认它，避免点到别的面板/隐藏按钮
SEND_BTN_ID = 'bh-send-btn'
PANEL_SEL = '.chat-conversation'

# 按钮解锁的轮询间隔与默认等待上限（页面框架 debounce 通常 < 300ms）
POLL_S = 0.15
WAIT_ENABLED_S = 3.0


JOB_CODE_RE = re.compile(r'[（(]\s*(?:[A-Za-z]{1,3}[0-9]{2,}|[0-9]{5,})\s*[)）]')

PRECLICK_FAILURES = frozenset({
    'identity_changed', 'identity_mismatch', 'owner_unknown', 'stale_draft',
    'button_disabled', 'button_not_unique', 'panel_hidden', 'panel_not_visible',
    'panel_not_ready', 'panel_not_unique', 'input_mismatch', 'fill_mismatch',
})


def click_outcome(clicked):
    """An attempted send without a positive receipt must remain unresolved."""
    if not isinstance(clicked, dict):
        return 'pending'
    if clicked.get('attempted') is True and clicked.get('ok') is not True:
        return 'pending'
    if (clicked.get('attempted') is not True and clicked.get('clicked') is False
            and clicked.get('ok') is False and clicked.get('reason') in PRECLICK_FAILURES):
        return 'aborted'
    if clicked.get('ok') is True and clicked.get('clicked') is True:
        return 'clicked'
    return 'pending'


def norm_job_name(name):
    """岗位名统一口径：去内部编号、空白和尾部「招聘」，保留区域等正文。"""
    return re.sub(r'招聘$', '', re.sub(r'\s+', '', JOB_CODE_RE.sub('', str(name or ''))))


def chat_identity_evidence(panel_text, job):
    norm = lambda s: re.sub(r'\s+', '', str(s or ''))
    text = norm(panel_text)
    brand = norm(job.get('brandName'))
    name = norm_job_name(job.get('jobName'))
    return bool(brand and name and brand in text and name in norm_job_name(panel_text))


_COMMON = r'''
const visible = e => !!e && e.getClientRects().length > 0 &&
  getComputedStyle(e).display !== 'none' && getComputedStyle(e).visibility !== 'hidden';
const fail = reason => ({ok:false,reason,clicked:false,conv_key:''});
// 只认「可见」的会话面板：聊天页初始渲染时会话节点数会短暂为 0，
// 那属于还没画出来（panel_not_ready），不是身份不明，调用方应轮询等待。
const allPanels = [...document.querySelectorAll('.chat-conversation')];
if (allPanels.length === 0) return fail('panel_not_ready');
const panels = allPanels.filter(visible);
if (panels.length === 0) return fail('panel_not_visible');
if (panels.length !== 1) return fail('panel_not_unique');
const panel = panels[0];
'''

# 输入框读回值比对：contenteditable 会给出 &nbsp;/零宽字符与尾随换行，按语义比较，
# 但仍要求逐字相等（截断、改写都会被抓到）。
_INPUT_TEXT_JS = r'''
const clean = s => String(s == null ? '' : s)
  .replace(/[\u00a0\u200b\ufeff]/g, ' ').replace(/\s+$/, '');
'''


def type_into_chat(br, tab, text):
    """用 execCommand('insertText') 写入 contenteditable，触发页面框架的输入模型。"""
    expr = '(() => {' + _COMMON + _INPUT_TEXT_JS + r'''
      const text = %s;
      const inputs = [...panel.querySelectorAll(%s)];
      if (inputs.length !== 1 || !visible(inputs[0])) return fail('fill_mismatch');
      const el = inputs[0];
      el.focus();
      // 先全选旧内容：insertText 会替换选区，顺带清掉上次残留的草稿。
      try {
        const sel = window.getSelection();
        const range = document.createRange();
        range.selectNodeContents(el);
        sel.removeAllRanges();
        sel.addRange(range);
      } catch (e) {}
      let ok = false;
      try { ok = document.execCommand('insertText', false, text); } catch (e) { ok = false; }
      let synced = clean(el.innerText) === clean(text);
      if (!synced && ok) return fail('fill_mismatch');   // 已插入但不完整：不覆盖，避免模型再被写坏
      if (!synced) {
        // execCommand 不可用时的退路：写值 + 原生事件序列（beforeinput/input/keyup）
        el.textContent = text;
        const fireInput = (type, opts) => {
          try { if (typeof InputEvent === 'undefined') return; el.dispatchEvent(new InputEvent(type, opts)); }
          catch (e) {}
        };
        fireInput('beforeinput', {bubbles:true, cancelable:true, inputType:'insertText', data:text});
        fireInput('input', {bubbles:true, cancelable:true, inputType:'insertText', data:text});
        try {
          if (typeof KeyboardEvent !== 'undefined')
            el.dispatchEvent(new KeyboardEvent('keyup', {bubbles:true, key:'Unidentified'}));
        } catch (e) {}
        synced = clean(el.innerText) === clean(text);
      }
      if (!synced) return fail('fill_mismatch');
      return {ok:true, method: ok ? 'execCommand' : 'fallback'};
    })()''' % (json.dumps(text), json.dumps(CHAT_INPUT_SEL))
    result = br.json(tab, expr, default={})
    return result if isinstance(result, dict) else {}


def type_via_bridge(br, tab, text):
    """兜底输入：走浏览器扩展的 fill（真机等同原生输入），再校验读回值。"""
    fill = getattr(br, 'fill', None)
    if not fill:
        return {'ok': False, 'reason': 'bridge_fill_unavailable'}
    try:
        result = fill(tab, CHAT_INPUT_SEL, text)
    except Exception:
        return {'ok': False, 'reason': 'bridge_fill_error'}
    if isinstance(result, dict) and result.get('isError'):
        return {'ok': False, 'reason': 'bridge_fill_error'}
    check = br.json(tab, '(() => {' + _COMMON + _INPUT_TEXT_JS + r'''
      const inputs = [...panel.querySelectorAll(%s)];
      if (inputs.length !== 1 || !visible(inputs[0])) return fail('fill_mismatch');
      if (clean(inputs[0].innerText) !== clean(%s)) return fail('fill_mismatch');
      return {ok:true, method:'bridge'};
    })()''' % (json.dumps(CHAT_INPUT_SEL), json.dumps(text)), default={})
    return check if isinstance(check, dict) else {'ok': False, 'reason': 'fill_mismatch'}


def _identity_guard_js(text, job, guard_js):
    if job is not None:
        job = dict(job, jobName=norm_job_name(job.get('jobName')))
    return r'''
      const text = %s, job = %s;
      if (job !== null) {
        const sq = s => (s || '').replace(/\s+/g,'');
        const label = sq(panel.innerText);
        const jobLabel = label.replace(new RegExp(%s, 'g'), '');
        if (!sq(job.brandName) || !sq(job.jobName) ||
            !label.includes(sq(job.brandName)) || !jobLabel.includes(sq(job.jobName)))
          return fail('identity_mismatch');
      } else if (!%s) return fail('identity_mismatch');
      %s
    ''' % (json.dumps(text), json.dumps(job), json.dumps(JOB_CODE_RE.pattern), json.dumps(bool(guard_js)), guard_js)


def _click_expr(text, job, guard_js, identity_js, conv_key):
    """守卫轮：校验身份与输入，按钮可点时打上 id 标记，把「点」留给真实（可信）点击。

    真机实测：JS 的 `button.click()` 是 untrusted，页面会先乐观渲染气泡、随后被服务端
    丢弃（列表预览不变；用 CDP 可信点击则 `[送达]` 正常）。因此这里只做守卫+标记。
    """
    return '(() => {' + identity_js + _COMMON + _identity_guard_js(text, job, guard_js) + _INPUT_TEXT_JS + r'''
      const inputs = [...panel.querySelectorAll(%s)];
      if (inputs.length !== 1 || !visible(inputs[0]) || clean(inputs[0].innerText) !== clean(text))
        return fail('input_mismatch');
      const buttons = [...panel.querySelectorAll(%s)];
      if (buttons.length !== 1) return fail('button_not_unique');
      const button = buttons[0];
      if (!visible(button) || button.disabled || button.classList?.contains('disabled') ||
          button.getAttribute?.('aria-disabled') === 'true') return fail('button_disabled');
      button.id = %s;
      return {ok:true,ready:true,reason:'',conv_key:%s};
    })()''' % (json.dumps(CHAT_INPUT_SEL), json.dumps(SEND_BTN_SEL),
               json.dumps(SEND_BTN_ID), json.dumps(conv_key))


def _js_click_expr(text, job, guard_js, identity_js, conv_key):
    """退路：没有可信点击能力时用 JS 点击（真机不稳，仅最后兜底）。"""
    return _click_expr(text, job, guard_js, identity_js, conv_key).replace(
        'return {ok:true,ready:true,reason:\'\',conv_key:' + json.dumps(conv_key) + '};',
        'button.click();\n      return {ok:true,clicked:true,attempted:true,reason:\'\',conv_key:'
        + json.dumps(conv_key) + '};')


def click_send_scoped(br, tab, text, *, job=None, guard_js='', identity_js='', conv_key='',
                      wait_enabled_s=WAIT_ENABLED_S, sleep=None):
    """Optional reply guard supplies conv_key/history checks in the same DOM turn.

    Callers must provide either job evidence or an independent identity guard.
    The three positional arguments alone deliberately fail identity validation.

    `button_disabled` 只在一整段轮询预算耗尽后才返回：框架同步输入模型是异步的，
    写完后立刻判 disabled 会在真机上误报（2026-10-28 实测）。
    守卫通过后用浏览器的**可信点击**（CDP，`br.click`）落点，JS 点击只在没有该能力时兜底。
    """
    expr = _click_expr(text, job, guard_js, identity_js, conv_key)
    sleep = time.sleep if sleep is None else sleep
    polls = max(1, int(round(max(0.0, wait_enabled_s) / POLL_S)) + 1)
    result = {}
    for attempt in range(polls):
        result = br.json(tab, expr, default={})
        if not isinstance(result, dict):
            return {}
        if result.get('reason') != 'button_disabled':
            break
        if attempt + 1 < polls:
            sleep(POLL_S)
    if result.get('ready') is not True:
        return dict(result, attempted=False)
    clicker = getattr(br, 'click', None)
    if clicker is not None:
        failed = {'ok': False, 'clicked': False, 'reason': 'trusted_click_failed',
                  'conv_key': conv_key, 'attempted': True}
        try:
            clicked = clicker(tab, '#' + SEND_BTN_ID)
        except Exception:
            return failed
        if not isinstance(clicked, dict) or clicked.get('isError') or clicked.get('ok') is False:
            return failed
        return {'ok': True, 'clicked': True, 'reason': '', 'conv_key': conv_key,
                'via': 'trusted_click', 'attempted': True}
    try:
        fallback = br.json(tab, _js_click_expr(text, job, guard_js, identity_js, conv_key), default={})
    except Exception:
        return {'via': 'js_fallback', 'attempted': True}
    if isinstance(fallback, dict):
        precheck_failed = (fallback.get('ok') is False and fallback.get('clicked') is False
                           and fallback.get('reason') in PRECLICK_FAILURES)
        return dict(fallback, via='js_fallback', attempted=not precheck_failed)
    return {'via': 'js_fallback', 'attempted': True}


def press_enter_scoped(br, tab, text, *, job=None, guard_js='', identity_js='', conv_key=''):
    """兵底：页面提示「按 Enter 键发送」。身份与输入在同一 DOM 轮里校验后才派发 Enter。"""
    expr = '(() => {let attempted = false; try {' + identity_js + _COMMON + _identity_guard_js(text, job, guard_js) + _INPUT_TEXT_JS + r'''
      const inputs = [...panel.querySelectorAll(%s)];
      if (inputs.length !== 1 || !visible(inputs[0]) || clean(inputs[0].innerText) !== clean(text))
        return fail('input_mismatch');
      const el = inputs[0];
      el.focus();
      const opts = {bubbles:true, cancelable:true, key:'Enter', code:'Enter',
                    keyCode:13, which:13, composed:true};
      attempted = true;
      el.dispatchEvent(new KeyboardEvent('keydown', opts));
      el.dispatchEvent(new KeyboardEvent('keypress', opts));
      el.dispatchEvent(new KeyboardEvent('keyup', opts));
      return {ok:true,clicked:true,attempted:true,reason:'',via:'enter',conv_key:%s};
      } catch (e) {return {ok:false,clicked:false,attempted,reason:''};}
    })()''' % (json.dumps(CHAT_INPUT_SEL), json.dumps(conv_key))
    expr = expr.replace("({ok:false,reason,clicked:false,conv_key:''})",
                        "({ok:false,reason,clicked:false,attempted:false,conv_key:''})")
    try:
        result = br.json(tab, expr, default={})
    except Exception:
        return {'ok': False, 'clicked': False, 'attempted': True, 'reason': 'enter_failed'}
    return result if isinstance(result, dict) else {}


def confirm_new_bubble(before_ids, after_msgs, text_sha):
    before = set(before_ids)
    for message in after_msgs:
        if (message.get('id') and message['id'] not in before and message.get('who') == 'mine'
                and hashlib.sha256((message.get('text') or '').encode()).hexdigest() == text_sha):
            return 'sent'
    return 'pending_verification'


def chat_snapshot(br, tab):
    """Read only the unique visible active panel, never the conversation sidebar.

    真机结构（2026-10-28）：`<div class="message-item item-myself" data-mid="...">`
    → 正文在 `.message-content .text p .text-content`，`.message-content` 里还混着
    「送达/已读」状态文本，直接取 innerText 会把状态算进正文（哈希永远对不上）。
    """
    expr = '(() => {' + _COMMON + r'''
      return {ok:true,panel_text:panel.innerText,messages:
        [...panel.querySelectorAll('.message-item')].map(e => {
          const content = e.querySelector('.message-content');
          const body = e.querySelector('.text-content') || content;
          const tokens = new Set((e.className || '').split(/\s+/));
          const mine = MINE.some(c => tokens.has(c)) && !PEER.some(c => tokens.has(c));
          return {id:e.getAttribute('data-mid') || e.getAttribute('data-message-id') || e.getAttribute('data-id') || '',
            who:mine ? 'mine' : 'unknown',
            text:body ? body.innerText : ''};
        })};
    })()'''.replace('MINE', json.dumps(list(SELF_MARKERS))).replace('PEER', json.dumps(list(PEER_MARKERS)))
    result = br.json(tab, expr, default={})
    return result if isinstance(result, dict) else {}
