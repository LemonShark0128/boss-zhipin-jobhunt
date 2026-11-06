"""对话文字与附件简历的身份校验、可信点击和发送记账。"""
import hashlib
import itertools
import json
import re
import time
import uuid
from datetime import datetime
from pathlib import Path

import chat_dom
import chat_send as CS
import conv_rules
import conv_parse
import greet as G
import hunt_config as HC
import message_policy
import send_safety as SS


DEFAULT_RESUME_SENT_PATTERN = (
    r'附件简历.{0,40}已发送|简历已发送|已向.{0,10}发送.{0,6}简历|点击预览附件简历'
)

def _template_allowed(cfg, text):
    templates = {**conv_rules.DEFAULT_TEMPLATES,
                 **((cfg.get('conversation') or {}).get('templates') or {})}
    allowed = list(templates.values()) + [
        (cfg.get('conversation') or {}).get('decline_text') or conv_rules.DECLINE_TEXT,
        conv_rules.INTERVIEW_REPLY,
                                         (cfg.get('user') or {}).get('interview_reply')]
    singles = [templates[k] for k in conv_rules.ANSWER_KEYS]
    for n in range(2, len(singles) + 1):
        allowed.extend(''.join(parts) for parts in itertools.permutations(singles, n)
                       if all(isinstance(p, str) and p for p in parts))
    return isinstance(text, str) and bool(text) and text in allowed


def _metadata(cfg, before):
    messages = before.get('messages') or []
    return {'msg_count': len(messages),
            'last_message_id': (messages or [{}])[-1].get('id') or '',
            'click_ts_epoch': int((cfg.get('_clock') or time.time)() * 1000)}


def _journal(cfg, cmd, key, job_id, text, send, meta):
    outcome = []
    def deliver():
        result = send()
        outcome.append(result)
        return result
    try:
        return SS.ledger_send(cfg, cmd, key, job_id, text, deliver, meta)
    except RuntimeError as exc:
        if not str(exc).startswith('unresolved_send:') or outcome != ['pending_verification']:
            raise
        # ledger_send marks an unconfirmed receipt unknown; preserve it as pending.
        event_id = str(exc).split(':', 1)[1].strip()
        row = next((r for r in SS.ledger_latest(cfg) if r['event_id'] == event_id), None)
        if row is not None:
            SS.ledger_append(cfg, dict(row, status='pending'))
        return 'pending_verification'


def send_text(br, tab, cfg, conv_key, job, text, template_part, opener_part=''):
    if not _template_allowed(cfg, template_part):
        return 'send_aborted:template_mismatch'
    if opener_part and (len(opener_part) > 60 or
                        not G.basic_safety(opener_part, cfg, job)[0] or
                        message_policy.commitment(opener_part) or
                        message_policy.self_negation(opener_part)):
        return 'manual_required'
    if text != opener_part + template_part:
        return 'send_aborted:text_mismatch'
    before = chat_dom.active_conversation(br, tab)
    if not conv_key or before.get('conv_key') != conv_key:
        return 'error: unknown_conversation'
    # Keep raw history for stale-draft and ledger baselines; ignore systems only for turns.
    raw_messages = before.get('messages') or []
    system_texts = conv_parse.system_message_texts(cfg)
    messages = [m for m in raw_messages if not conv_parse.is_system_message(m, system_texts)]
    if any(m.get('who') not in {'mine', 'friend'} for m in messages):
        return 'send_aborted:owner_unknown'
    with SS.state_lock(cfg['paths']['state'], reentrant=True):
        SS.ledger_guard(cfg, verify=lambda row: SS.confirms_new_bubble(row, before),
                        scope='conv', conv_key=conv_key)
        if messages and messages[-1].get('who') == 'mine':
            return 'last_turn_is_ours'
        meta = _metadata(cfg, before)
        def send():
            if CS.type_into_chat(br, tab, text).get('ok') is not True:
                return 'fill_err'
            clicked = chat_dom.atomic_chat_click(
                br, tab, conv_key, text, chat_dom.normalized_history(raw_messages),
                system_texts=system_texts)
            outcome = CS.click_outcome(clicked)
            if outcome == 'aborted':
                return 'send_aborted:' + clicked['reason']
            if outcome == 'clicked' and clicked.get('conv_key') != conv_key:
                return 'pending_verification'
            meta['via'] = 'confirm_only' if outcome == 'pending' else clicked.get('via')
            for _ in range(3 if outcome == 'pending' else 1):
                time.sleep(2)
                after = chat_dom.active_conversation(br, tab)
                if after.get('conv_key') != conv_key:
                    break
                if CS.confirm_new_bubble([m.get('id') for m in raw_messages],
                                         after.get('messages') or [],
                                         hashlib.sha256(text.encode()).hexdigest()) == 'sent':
                    return 'sent'
            return 'pending_verification'
        return _journal(cfg, 'conv_text', conv_key, job.get('encryptJobId') or job.get('job_id') or '',
                        text, send, meta)


# Independent active panel identity is checked in the DOM turn that marks each button.
_RESUME_SCOPE = r"""
const panels = [...document.querySelectorAll('.chat-conversation')];
const panel = panels.length === 1 ? panels[0] : null;
const visible = el => el && el.offsetParent !== null && getComputedStyle(el).display !== 'none';
const header = panel && panel.querySelector('.top-info-content,.chat-title,.title-box');
if (!visible(panel) || (identity(header) || identity(panel)) !== expected)
  return {ok:false,reason:'unknown_conversation'};
"""


def _resume_button(br, tab, key):
    data = br.json(tab, '(() => {' + chat_dom.CONVERSATION_JS +
                   'const expected = ' + json.dumps(key) + ';' + _RESUME_SCOPE + r"""
      document.querySelectorAll('#bh-conv-resume').forEach(e => e.removeAttribute('id'));
      const buttons = [...panel.querySelectorAll('.chat-controls .toolbar-btn')]
        .filter(e => (e.innerText || '').trim() === '发简历');
      if (buttons.length !== 1 || !visible(buttons[0])) return {ok:false,reason:'resume_button'};
      const button = buttons[0];
      const conversation = (button.__vue__ && button.__vue__.conversation$)
        || (button.parentElement && button.parentElement.__vue__
          && button.parentElement.__vue__.conversation$);
      if (conversation) {
        if (!(conversation.bothTalked || conversation.exchangeOptGray))
          return {ok:false,reason:'resume_not_both_talked'};
        if (conversation.requestResume) return {ok:false,reason:'resume_requested'};
      }
      buttons[0].id = 'bh-conv-resume';
      return {ok:true};
    })()""", default={}) or {}
    return data if isinstance(data, dict) else {}


def _accept_button(br, tab, key, card):
    data = br.json(tab, '(() => {' + chat_dom.CONVERSATION_JS +
                   'const expected = ' + json.dumps(key) + ';' + _RESUME_SCOPE +
                   'const card = ' + json.dumps(card) + ';' + r"""
      document.querySelectorAll('#bh-conv-accept').forEach(e => e.removeAttribute('id'));
      const messages = [...panel.querySelectorAll('.message-item.item-friend')]
        .filter(e => card && (e.innerText || '').includes(card));
      const message = messages[messages.length - 1];
      if (!message) return {ok:false,reason:'accept_button'};
      const matches = [...message.querySelectorAll('*')]
        .filter(e => visible(e) && (e.innerText || '').trim() === '同意');
      const buttons = matches.filter(e => !matches.some(other => other !== e && e.contains(other)));
      if (buttons.length !== 1) return {ok:false,reason:'accept_button'};
      buttons[0].id = 'bh-conv-accept';
      return {ok:true};
    })()""", default={}) or {}
    return data if isinstance(data, dict) else {}


def _resume_dialog(br, tab, key):
    data = br.json(tab, '(() => {' + chat_dom.CONVERSATION_JS +
                   'const expected = ' + json.dumps(key) + ';' + _RESUME_SCOPE + r"""
      document.querySelectorAll('#bh-conv-resume-confirm').forEach(e => e.removeAttribute('id'));
      const dialogs = [...new Set([
        ...document.querySelectorAll('[role="dialog"],.dialog,.dialog-wrap,.dialog-container,.modal,.boss-dialog'),
        ...panel.querySelectorAll('.chat-controls .panel-resume.sentence-popover')
      ])].filter(e => visible(e) && !e.closest('.panel-contact')
        && /简历/.test(e.innerText || ''));
      if (!dialogs.length) return {};
      // Nested wrappers represent one popup; only the innermost visible dialog is actionable.
      const inner = dialogs.filter(e => !dialogs.some(other => other !== e && e.contains(other)));
      if (inner.length !== 1) return {html:dialogs[0].outerHTML.slice(0,2000),confirm:false};
      const dialog = inner[0];
      const buttons = [...dialog.querySelectorAll('button,[role="button"],.btn,.btn-sure-v2,span.btn-v2')]
        .filter(e => visible(e) && !e.closest('.panel-contact')
          && !e.disabled && e.getAttribute('aria-disabled') !== 'true'
          && /^(确定|发送|确认)$/.test((e.innerText || '').trim()));
      if (buttons.length === 1) buttons[0].id = 'bh-conv-resume-confirm';
      return {html:dialog.outerHTML.slice(0,2000),confirm:buttons.length === 1};
    })()""", default={}) or {}
    return data if isinstance(data, dict) else {}


def _trusted_click(br, tab, selector):
    try:
        result = br.click(tab, selector)
        return isinstance(result, dict) and not result.get('isError') and result.get('ok') is not False
    except Exception:
        return False


# Per-call diagnostic, reset even when delivery exits before clicking.
LAST_RESUME_BUBBLES = 0


def send_resume(br, tab, cfg, conv_key, job_id):
    return _resume_action(br, tab, cfg, conv_key, job_id,
                          lambda: _resume_button(br, tab, conv_key),
                          '#bh-conv-resume', '<resume>', 'resume_button')


def accept_resume_request(br, tab, cfg, conv_key, job_id, card):
    return _resume_action(br, tab, cfg, conv_key, job_id,
                          lambda: _accept_button(br, tab, conv_key, card),
                          '#bh-conv-accept', '<resume_accept>', 'accept_button')


def _resume_close_button(br, tab, key):
    data = br.json(tab, '(() => {' + chat_dom.CONVERSATION_JS +
                   'const expected = ' + json.dumps(key) + ';' + _RESUME_SCOPE + r"""
      document.querySelectorAll('#bh-conv-resume-close').forEach(e => e.removeAttribute('id'));
      const dialogs = [...document.querySelectorAll('[role="dialog"],.dialog,.dialog-wrap,.dialog-container,.modal,.boss-dialog')]
        .filter(e => visible(e) && /简历/.test(e.innerText || ''));
      if (!dialogs.length) return {present:false,close:false};
      const inner = dialogs.filter(e => !dialogs.some(other => other !== e && e.contains(other)));
      if (inner.length !== 1) return {present:true,close:false,html:dialogs[0].outerHTML.slice(0,2000)};
      const buttons = [...inner[0].querySelectorAll('button,[role="button"],.btn')]
        .filter(e => visible(e) && !e.disabled && e.getAttribute('aria-disabled') !== 'true'
          && /^(取消|关闭|知道了)$/.test((e.innerText || '').trim()));
      if (buttons.length === 1) buttons[0].id = 'bh-conv-resume-close';
      return {present:true,close:buttons.length === 1,html:inner[0].outerHTML.slice(0,2000)};
    })()""", default={}) or {}
    return data if isinstance(data, dict) else {}


def _resume_diag(cfg, event, **fields):
    """Best-effort bounded local diagnostics; never interfere with delivery."""
    try:
        if (cfg.get('conversation') or {}).get('dry'):
            return

        def bounded(value, key=''):
            if isinstance(value, str):
                return value[:1500 if key == 'html' else 300]
            if isinstance(value, dict):
                return {k: bounded(v, k) for k, v in value.items()}
            if isinstance(value, (list, tuple)):
                return [bounded(v) for v in value]
            return value

        path = Path(cfg['paths']['workdir']) / 'resume_trace.jsonl'
        row = dict(bounded(fields), ts=datetime.now(HC.cfg_tz(cfg)).isoformat(), event=event[:300])
        with open(path, 'a', encoding='utf-8') as stream:
            stream.write(json.dumps(row, ensure_ascii=False) + '\n')
        with open(path, encoding='utf-8') as stream:
            lines = stream.readlines()
        if len(lines) > 500:
            with open(path, 'w', encoding='utf-8') as stream:
                stream.writelines(lines[-300:])
    except Exception:
        pass


def _send_resume_and_confirm(br, tab, cfg, conv_key, before, selector, diag=None):
    diag = diag or (lambda event, **fields: None)
    HC.trace('conv_resume click selector=%s' % selector)
    clicked = _trusted_click(br, tab, selector)
    diag('click', result=clicked)
    if not clicked:
        HC.trace('conv_resume initial click failed')
        return 'pending_verification'
    ids = {m.get('id') for m in before.get('messages') or []}
    pattern = re.compile((cfg.get('conversation') or {}).get(
        'resume_sent_pattern', DEFAULT_RESUME_SENT_PATTERN))

    def wait_receipt(attempts, phase):
        global LAST_RESUME_BUBBLES
        for attempt in range(attempts):
            time.sleep(.5)
            after = chat_dom.active_conversation(br, tab)
            if after.get('conv_key') != conv_key:
                # Observe the changed panel without using it to confirm delivery.
                try:
                    observed = [m for m in after.get('messages') or []
                                if m.get('id') and m['id'] not in ids]
                    receipts = {m['id'] for m in observed if m.get('who') != 'friend'
                                and pattern.search(m.get('text') or '')}
                    diag('poll', phase=phase, attempt=attempt + 1, conv_key_matches=False,
                         messages=[{'who': m.get('who'), 'text': (m.get('text') or '')[:60]}
                                   for m in observed[:5]], receipts=len(receipts))
                except Exception:
                    pass
                HC.trace('conv_resume poll phase=%s attempt=%d unknown_conversation' % (phase, attempt + 1))
                return 'pending_verification'
            new_messages = [m for m in after.get('messages') or []
                            if m.get('id') and m['id'] not in ids]
            sent_bubbles = {m['id'] for m in new_messages
                            if m.get('who') != 'friend' and pattern.search(m.get('text') or '')}
            diag('poll', phase=phase, attempt=attempt + 1, conv_key_matches=True,
                 messages=[{'who': m.get('who'), 'text': (m.get('text') or '')[:60]}
                           for m in new_messages[:5]], receipts=len(sent_bubbles))
            # Mine messages remain a compatible success signal, but do not
            # inflate the count of new system receipts.
            LAST_RESUME_BUBBLES = len({m['id'] for m in new_messages
                                      if m.get('who') == 'unknown' and m['id'] in sent_bubbles})
            HC.trace('conv_resume poll phase=%s attempt=%d receipts=%d bubbles=%d' %
                     (phase, attempt + 1, len(sent_bubbles), LAST_RESUME_BUBBLES))
            if sent_bubbles or any(m.get('who') == 'mine' and '简历' in (m.get('text') or '')
                                   for m in new_messages):
                HC.trace('conv_resume sent bubbles=%d' % LAST_RESUME_BUBBLES)
                # Delivery is already verified. Dismissal must never resend or
                # turn a successful delivery into a pending ledger entry.
                try:
                    dialog = _resume_close_button(br, tab, conv_key)
                    diag('close_dialog', present=dialog.get('present'), close=dialog.get('close'),
                         html=(dialog.get('html') or '')[:800])
                    HC.trace('conv_resume close dialog=%s unique=%s' %
                             (bool(dialog.get('present')), bool(dialog.get('close'))))
                    if dialog.get('html'):
                        HC.trace(dialog['html'][:2000])
                    if dialog.get('close'):
                        closed = _trusted_click(br, tab, '#bh-conv-resume-close')
                        HC.trace('conv_resume close clicked=%s' % closed)
                    else:
                        HC.trace('conv_resume close skipped: absent or ambiguous button')
                except Exception as exc:
                    HC.trace('conv_resume close failed: %s' % type(exc).__name__)
                return 'sent'
        return None

    result = wait_receipt(8, 'initial')
    if result:
        return result
    dialog = _resume_dialog(br, tab, conv_key)
    diag('dialog', reason=dialog.get('reason'), confirm=dialog.get('confirm'),
         html=(dialog.get('html') or '')[:1500])
    HC.trace('conv_resume confirm dialog=%s unique=%s' %
             (bool(dialog.get('html') or dialog.get('confirm')), bool(dialog.get('confirm'))))
    if dialog.get('reason') == 'unknown_conversation':
        return 'pending_verification'
    if dialog.get('html'):
        HC.trace(dialog['html'][:2000])
    if not dialog.get('confirm'):
        HC.trace('conv_resume confirm skipped: absent or ambiguous button')
        return wait_receipt(8, 'late') or 'pending_verification'
    confirmed = _trusted_click(br, tab, '#bh-conv-resume-confirm')
    diag('confirm_click', result=confirmed)
    HC.trace('conv_resume confirm clicked=%s' % confirmed)
    if not confirmed:
        return 'pending_verification'
    return wait_receipt(12, 'confirmed') or 'pending_verification'


def _resume_action(br, tab, cfg, conv_key, job_id, locate, selector, marker, reason):
    global LAST_RESUME_BUBBLES
    LAST_RESUME_BUBBLES = 0
    if (cfg.get('conversation') or {}).get('dry'):
        before = chat_dom.active_conversation(br, tab)
        if not conv_key or before.get('conv_key') != conv_key:
            return 'error: unknown_conversation'
        located = locate()
        return 'dry_ok' if located.get('ok') is True else 'send_aborted:' + located.get('reason', reason)
    run_id = uuid.uuid4().hex
    kind = 'accept' if marker == '<resume_accept>' else 'send'

    def diag(event, **fields):
        _resume_diag(cfg, event, run_id=run_id, kind=kind, **fields)

    def perform():
        with SS.state_lock(cfg['paths']['state'], reentrant=True):
            if any(r.get('cmd') == 'conv_resume' and r.get('conv_key') == conv_key and
                   r.get('status') == 'sent' for r in SS.ledger_latest(cfg)):
                return 'duplicate'
            SS.ledger_guard(cfg, scope='conv', conv_key=conv_key)
            before = chat_dom.active_conversation(br, tab)
            diag('start', conv_key=conv_key, selector=selector,
                 before=[{'who': m.get('who'), 'text': (m.get('text') or '')[:40], 'id': m.get('id')}
                         for m in (before.get('messages') or [])[-3:]])
            if not conv_key or before.get('conv_key') != conv_key:
                return 'error: unknown_conversation'
            located = locate()
            if located.get('ok') is not True:
                return 'send_aborted:' + located.get('reason', reason)
            meta = _metadata(cfg, before)
            def send():
                return _send_resume_and_confirm(br, tab, cfg, conv_key, before, selector, diag=diag)
            return _journal(cfg, 'conv_resume', conv_key, job_id, marker, send, meta)

    try:
        result = perform()
    except Exception as exc:
        diag('end', result='error:' + type(exc).__name__)
        raise
    diag('end', result=result)
    return result


def _not_interested_target(br, tab, key, stage):
    """Mark one target in the active conversation; never select the sidebar label."""
    data = br.json(tab, '(() => {' + chat_dom.CONVERSATION_JS +
                   'const stage = ' + json.dumps(stage) + ';' + r"""
      // Confirmation can close the conversation panel as well as the dialog.
      // Check disappearance before requiring the active panel identity.
      if (stage === 'disappeared') {
        const dialogs = [...document.querySelectorAll('.zp-feedback-dialog')]
          .filter(e => e.offsetParent !== null && getComputedStyle(e).display !== 'none');
        return {ok:true,absent:dialogs.length === 0};
      }
    """ + 'const expected = ' + json.dumps(key) + ';' + _RESUME_SCOPE + r"""
      const id = 'bh-conv-decline-' + stage;
      document.querySelectorAll('#' + id).forEach(e => e.removeAttribute('id'));
      const top = panel.querySelector('.top-info-content');
      let matches = [], html = '';
      if (stage === 'dry' || stage === 'menu' || stage === 'item') {
        if (!top) return {ok:false,reason:'top_missing'};
        const items = [...top.querySelectorAll('ul.more-setting > li')]
          .filter(e => (e.innerText || '').trim() === '不感兴趣');
        if (items.length !== 1) return {ok:false,reason:'item_ambiguous'};
        if (stage === 'dry') return {ok:true};
        matches = stage === 'menu' ? [...top.querySelectorAll(
          '.user-info .right-content .ui-dropmenu-v1.ui-dropmenu .ui-dropmenu-label .top-operation-item')]
          .filter(e => visible(e) && (e.innerText || '').trim() === '更多') : items.filter(visible);
      } else {
        const dialogs = [...document.querySelectorAll('.zp-feedback-dialog')]
          .filter(visible);
        html = dialogs.map(e => e.outerHTML).join('\n').slice(0,2000);
        if (dialogs.length !== 1) return {ok:false,
          reason:dialogs.length ? 'dialog_ambiguous' : stage + '_missing',html};
        const dialog = dialogs[0];
        const actionable = e => visible(e) && !e.disabled && e.getAttribute('aria-disabled') !== 'true';
        if (stage === 'selected') {
          const selected = [...dialog.querySelectorAll('#bh-conv-decline-company')];
          return {ok:true,selected:selected.length === 1 && visible(selected[0])
            && selected[0].classList.contains('actived'),html};
        }
        if (stage === 'company') {
          const sections = [...dialog.querySelectorAll('.zp-type-cont')].filter(e => {
            const title = e.querySelector('.zp-type-title');
            return title && (title.innerText || '').replace(/\s+/g,'') === '不太感兴趣';
          });
          const companyName = e => [e.getAttribute('title'), e.innerText || '']
            .map(s => (s || '').replace(/\s+/g,'')).find(s => /^“.+”公司$/.test(s));
          matches = [...new Set(sections.flatMap(e => [...e.querySelectorAll('li.zp-type-item')]))]
            .filter(e => actionable(e) && companyName(e));
          if (matches.length === 1) {
            const brand = (fields(header).brand || '').replace(/\s+/g,'');
            const company = companyName(matches[0]).slice(1,-3);
            matches[0].id = id;
            return {ok:true,html,brand_mismatch:!!brand && !company.includes(brand),brand,company};
          }
        } else {
          const selector = stage === 'confirm' ? '.zp-dialog-footer .zp-btn-sure' :
            '.zp-dialog-footer .zp-btn-cancel';
          matches = [...dialog.querySelectorAll(selector)].filter(e => actionable(e) &&
            (e.innerText || '').trim() === (stage === 'confirm' ? '确定' : '取消'));
        }
      }
      if (matches.length !== 1) return {ok:false,
        reason:stage + (matches.length ? '_ambiguous' : '_missing'),html};
      matches[0].id = id;
      return {ok:true,html};
    })()""", default={}) or {}
    return data if isinstance(data, dict) else {}


def mark_not_interested_company(br, tab, cfg, conv_key):
    """Best effort UI action after a confirmed decline; no send ledger entry."""
    dry = (cfg.get('conversation') or {}).get('dry', False)

    def locate(stage):
        target = _not_interested_target(br, tab, conv_key, stage)
        HC.trace('conv_decline_%s %s' % (stage, target))
        return target

    def click(stage):
        ok = _trusted_click(br, tab, '#bh-conv-decline-' + stage)
        HC.trace('conv_decline_click_%s ok=%s' % (stage, ok))
        return ok

    def abort(reason):
        if not dry:
            try:
                if locate('cancel').get('ok'):
                    click('cancel')
            except Exception:
                pass
        return 'aborted:' + reason

    stage = 'dry' if dry else 'menu'
    try:
        target = locate(stage)
        if not target.get('ok'):
            return abort(target.get('reason') or stage)
        if dry:
            return 'dry_ok'
        if not click('menu'):
            return abort('menu_click')
        for stage in ('item', 'company'):
            deadline = time.monotonic() + 5
            for attempt in range(21):
                target = locate(stage)
                if target.get('ok'):
                    break
                if (target.get('reason') != stage + '_missing' or
                        time.monotonic() >= deadline or attempt == 20):
                    return abort(target.get('reason') or stage)
                time.sleep(min(.25, max(0, deadline - time.monotonic())))
            if not click(stage):
                return abort(stage + '_click')
        # Selection is asynchronous; never confirm an unselected company.
        stage = 'selected'
        deadline = time.monotonic() + 2
        for attempt in range(9):
            target = locate(stage)
            if target.get('ok') and target.get('selected'):
                break
            if time.monotonic() >= deadline or attempt == 8:
                return abort('company_not_selected')
            time.sleep(min(.25, max(0, deadline - time.monotonic())))
        stage = 'confirm'
        target = locate(stage)
        if not target.get('ok'):
            return abort(target.get('reason') or stage)
        if not click(stage):
            return abort('confirm_click')
        stage = 'disappeared'
        deadline = time.monotonic() + 3
        for attempt in range(13):
            target = locate(stage)
            if target.get('ok') and target.get('absent'):
                return 'done'
            if time.monotonic() >= deadline or attempt == 12:
                return abort('confirm_no_effect')
            time.sleep(min(.25, max(0, deadline - time.monotonic())))
    except Exception:
        return abort(stage + '_error')
