"""会话页 DOM 读取、定位与发送身份校验。"""
import json
import re
import time
import unicodedata
import chat_send as CS
import conv_parse
from conv_parse import owner_parser_js, parse_message_owner


CHAT_URL = "https://www.zhipin.com/web/geek/chat"


# Shared parser for list entries and the independent active chat header/panel.
# Never derive the active identity from the selected sidebar row: a click may
# select a row while leaving the old messages/input panel mounted.
CONVERSATION_JS = owner_parser_js() + r"""
const norm = s => (s || '').normalize('NFKC').replace(/\s+/g,'').toLowerCase();
const fields = el => {
  const textOf = sel => {const e=el && el.querySelector(sel); return e ? (e.textContent || '').trim() : '';};
  // 列表行用 .name-box，聊天页头部（.top-info-content/.base-info）用 .name-content + 裸 span，
  // 两处结构相同（.name-text=HR 名，后面第 1 个 span=公司），所以两个容器都要认。
  const box = el && (el.querySelector('.name-box') || el.querySelector('.base-info'));
  const spans = box ? [...box.querySelectorAll('span:not(.name-text):not(.vline)')]
                    .filter(n => !n.className || !/base-title|name-content/.test(n.className)) : [];
  const spanText = i => spans[i] ? (spans[i].textContent || '').trim() : '';
  return {brand: spanText(0) || textOf('[data-brand],.brand-name,.company-name'),
          boss: textOf('.name-text') || textOf('[data-boss]') || textOf('.boss-name') || textOf('.geek-name') || textOf('.name'),
          title: spanText(1) || textOf('.base-title')};
};
const identity = el => {
  if (!el) return '';
  // 注意：d-c 实测 40 行全是 "62001"（组件 id，不是会话 id），绝对不能当 conv_key
  const attrs = ['data-conversation-id','data-conv-id','data-session-id','data-chat-id'];
  for (const attr of attrs) {
    const value = el.getAttribute(attr);
    if (value) return 'id:' + norm(value);
  }
  const {brand, boss} = fields(el);
  return brand && boss ? 'pair:' + norm(brand) + '|' + norm(boss) : '';
};
"""


def conversation_rows(br, tab):
    rows = br.json(tab, "(() => {" + CONVERSATION_JS + """
      return [...document.querySelectorAll('.friend-content')].map(el => ({
        ...fields(el),
        conv_key: identity(el.closest('[data-conversation-id],[data-conv-id],[data-session-id],[data-chat-id]') || el),
        receipt: !!el.querySelector('.message-status'),
        text: (el.innerText || '').replace(/\s+/g,' ').trim()
      })); })()""", default=[]) or []
    if not isinstance(rows, list) or any(not isinstance(row, dict) for row in rows):
        return []
    # 同一个 conv_key 出现多次 ⇒ 这个键不是「每会话唯一」的（实测属性缺失时会全表同值），
    # 退回「品牌|HR」唯一化，再撞就补序号。否则 40 行会被折叠成 1 个目标，自动回话等于没跑。
    counts = {}
    for r in rows:
        k = r.get('conv_key') or ''
        if k:
            counts[k] = counts.get(k, 0) + 1
    seen = {}
    for i, r in enumerate(rows):
        key = r.get('conv_key') or ''
        brand, boss = (r.get('brand') or '').strip(), (r.get('boss') or '').strip()
        pair = ('pair:' + conversation_norm(brand) + '|' + conversation_norm(boss)
                if brand and boss else '')
        # 只有「全局唯一」的键才保留；重复键的**每一行**（含第一次出现的那行）都要重算
        if key and counts.get(key, 0) == 1 and key not in seen:
            seen[key] = i
            continue
        if pair and pair not in seen:
            new_key = pair
        elif pair or key:
            new_key = (pair or key) + '#%d' % i
        else:
            new_key = ''          # 既无属性键也无「品牌|HR」，无法识别 —— 留空让上层报警
        r['conv_key'] = new_key
        if new_key:
            seen[new_key] = i
    return rows


def reveal_conversation(br, tab, item):
    """Scroll a uniquely identified source row into the virtual DOM, without opening it."""
    script = "(async () => { const item = " + json.dumps(item, ensure_ascii=False) + r""";
      const v = document.querySelector('.user-list-content')?.__vue__;
      if (!v || v.$options?.name !== 'virtual-list')
        return {ok:false, reason:'no_virtual_list'};
      const present = x => x !== undefined && x !== null && x !== '';
      const matches = (v.dataSources || []).map((row, index) => ({row, index})).filter(({row}) =>
        ['encryptJobId', 'name', 'brandName'].every(k => present(item[k]) && row[k] === item[k]) &&
        ['friendId', 'securityId'].every(k =>
          !present(item[k]) || !present(row[k]) || row[k] === item[k]));
      if (matches.length === 0) return {ok:false, reason:'not_found'};
      if (matches.length !== 1) return {ok:false, reason:'ambiguous'};
      const index = matches[0].index;
      v.scrollToIndex(index);
      v.onScroll(new Event('scroll'));
      await v.$nextTick();
      return {ok:true, index};
    })()"""
    try:
        result = br.json(tab, script, default={})
        return result if isinstance(result, dict) else {'ok': False, 'reason': 'invalid_response'}
    except Exception:
        return {'ok': False, 'reason': 'reveal_error'}


def pin_conversation(br, tab, item, wait_ms=3000):
    """conv-13/13b：只调用该行 last-msg-content 组件的 setTop(boss, 1)；不悬停、不点菜单。
    绝不调用 deleteBoss / setCancelNotInterested。返回 {ok, already?, reason?}。"""
    script = "(async () => { const item = " + json.dumps(item, ensure_ascii=False) + \
        "; const waitMs = " + str(int(wait_ms)) + r""";
      const present = x => x !== undefined && x !== null && x !== '';
      const KEYS = ['encryptJobId', 'name', 'brandName'];
      if (!KEYS.every(k => present(item[k]))) return {ok:false, reason:'bad_item'};
      const same = row => !!row && KEYS.every(k => row[k] === item[k]) &&
        ['friendId', 'securityId'].every(k => !present(item[k]) || !present(row[k]) || row[k] === item[k]);
      const compOf = fc => {
        const op = fc.querySelector('.user-operation');
        let node = op ? op.parentElement : fc;
        for (let depth = 0; node && depth <= 6; depth++, node = node.parentElement) {
          const vm = node.__vue__;
          if (vm && vm.$options && vm.$options.name === 'last-msg-content') return vm;
        }
        return null;
      };
      // conv-13b：置顶后该行离开 virtual-list dataSources、移到 DOM 第 0 行（class friend-top），
      // 所以每次都从 DOM 重新按三键找，不查 dataSources。
      const find = () => {
        const hits = [];
        for (const fc of document.querySelectorAll('.friend-content')) {
          const vm = compOf(fc);
          if (vm && same(vm.boss) && !hits.some(h => h.vm === vm)) hits.push({vm, fc});
        }
        return hits;
      };
      const isTopRow = h => h.vm.boss.isTop === 1 || h.vm.boss.isTop === true ||
        /(^|\s)friend-top(\s|$)/.test(String(h.fc.className || ''));
      const hits = find();
      if (hits.length === 0) return {ok:false, reason:'not_in_view'};
      if (hits.length !== 1) return {ok:false, reason:'ambiguous'};
      const vm = hits[0].vm, boss = vm.boss;
      if (present(boss.groupId)) return {ok:false, reason:'group'};
      if (isTopRow(hits[0])) return {ok:true, already:true};
      if (typeof vm.setTop !== 'function') return {ok:false, reason:'no_set_top'};
      // 真机：第二个参数传布尔真 → friendIsTop.json code 19「参数值错误」；菜单用 1/0
      vm.setTop(boss, 1);
      for (let waited = 0; waited <= waitMs; waited += 200) {
        const now = find();
        if (now.length === 1 && isTopRow(now[0])) return {ok:true};
        await new Promise(resolve => setTimeout(resolve, 200));
      }
      return {ok:false, reason:'not_confirmed'};
    })().catch(() => ({ok:false, reason:'pin_error'}))"""
    try:
        result = br.json(tab, script, default={})
    except Exception:
        return {'ok': False, 'reason': 'pin_error'}
    if not isinstance(result, dict) or not isinstance(result.get('ok'), bool):
        return {'ok': False, 'reason': 'invalid_response'}
    return result


def wait_rows_ready(br, tab, attempts=5, delay=2.0, sleep=time.sleep):
    """conv-06b：等会话列表 DOM 渲染出来。True=就绪；False=等满仍是 0 行；
    None=页面返回无法判断（非 dict / 抛错），不空等，交给调用方照旧处理。"""
    script = r"""(() => {
      const v = document.querySelector('.user-list-content')?.__vue__;
      const vl = !!v && v.$options?.name === 'virtual-list';
      return {rows: document.querySelectorAll('.friend-content').length,
              vl, ds: vl ? (v.dataSources || []).length : 0};
    })()"""
    for attempt in range(max(1, attempts)):
        try:
            state = br.json(tab, script, default={})
        except Exception:
            return None
        if not isinstance(state, dict) or not isinstance(state.get('rows'), int):
            return None
        if state['rows'] > 0 and (not state.get('vl') or (state.get('ds') or 0) > 0):
            return True
        if attempt + 1 < max(1, attempts):
            sleep(delay)
    return False


def reset_list_scroll(br, tab):
    """Best-effort restoration of the initial virtual-list window."""
    try:
        result = br.json(tab, r"""(async () => {
          const v = document.querySelector('.user-list-content')?.__vue__;
          if (!v || v.$options?.name !== 'virtual-list') return {ok:false};
          v.scrollToIndex(0);
          v.onScroll(new Event('scroll'));
          await v.$nextTick();
          return {ok:true};
        })()""", default={})
        if not isinstance(result, dict):
            return
    except Exception:
        return


def conversation_norm(text):
    return re.sub(r'\s+', '', unicodedata.normalize('NFKC', text or '')).lower()


def source_conversation_key(item):
    if item.get('conv_key'):
        return item['conv_key']
    brand, boss = conversation_norm(item.get('brandName')), conversation_norm(item.get('name'))
    return 'pair:' + brand + '|' + boss if brand and boss else ''


def select_conversation(rows, text='', boss=None, conv_key=None):
    if conv_key:
        matches = [r for r in rows if r.get('conv_key') == conv_key]
    else:
        parts = text.split('/') if boss is None and '/' in text else None
        if parts is not None:
            if len(parts) != 2 or not all(conversation_norm(p) for p in parts):
                raise ValueError('error: unknown_conversation')
            text, boss = parts
        def matches_text(row):
            tokens = {conversation_norm(t) for t in row.get('text', '').split()}
            def exact(field, value):
                wanted = conversation_norm(value)
                actual = conversation_norm(row.get(field))
                return bool(wanted) and (actual == wanted if actual else wanted in tokens)
            if boss is not None:
                return exact('brand', text) and exact('boss', boss)
            return exact('brand', text) or exact('boss', text)
        matches = [r for r in rows if matches_text(r)]
    if len(matches) > 1:
        raise ValueError('ambiguous_conversation: ' + '；'.join(r['text'] for r in matches))
    if len(matches) != 1 or not matches[0].get('conv_key'):
        raise ValueError('error: unknown_conversation')
    key = matches[0]['conv_key']
    if sum(r.get('conv_key') == key for r in rows) != 1:
        raise ValueError('ambiguous_conversation: ' + '；'.join(r['text'] for r in rows if r.get('conv_key') == key))
    return matches[0]


def active_conversation(br, tab, limit=10):
    data = br.json(tab, "(() => {" + CONVERSATION_JS + """
      const panels = [...document.querySelectorAll('.chat-conversation')];
      const panel = panels.length === 1 ? panels[0] : null;
      const header = panel && panel.querySelector('.top-info-content,.chat-title,.title-box');
      const key = identity(header) || identity(panel);
      if (!key || !panel) return {conv_key:'',messages:[]};
      const messages = [...panel.querySelectorAll('.message-item')].map(e => {
        const c=e.querySelector('.message-content');
        const b=e.querySelector('.text-content') || c;
        return {who: parseMessageOwner(e.className, b ? b.innerText : ''),
          classes:e.className || '',
          id: e.getAttribute('data-mid') || e.getAttribute('data-message-id') || e.getAttribute('data-id') || '',
          text: (b ? b.innerText : ''),
          ts:e.getAttribute('data-timestamp') || e.getAttribute('data-time') || ''};
      });
      return {conv_key:key, messages:messages}; })()""", default={}) or {}
    if not isinstance(data, dict):
        return {}
    for message in data.get('messages') or []:
        if 'classes' in message:
            message['who'] = parse_message_owner(message['classes'], message.get('text'))
    return data


def open_by_index(br, tab, i, limit=10, conv_key=None):
    rows = conversation_rows(br, tab)
    if i < 0 or i >= len(rows):
        return 'error: unknown_conversation', '', []
    target = rows[i]
    key = conv_key or target.get('conv_key')
    if not key or key != target.get('conv_key') or sum(r.get('conv_key') == key for r in rows) != 1:
        return 'error: unknown_conversation', target.get('text', ''), []
    # Resolve by stable identity again in the same DOM turn as marking the click.
    marked = br.json(tab, "(() => {" + CONVERSATION_JS + """
      document.querySelectorAll('#boss-hunt-target').forEach(e => e.removeAttribute('id'));
      const els = [...document.querySelectorAll('.friend-content')].filter(el =>
        identity(el.closest('[data-conversation-id],[data-conv-id],[data-session-id],[data-chat-id]') || el) === %s);
      if (els.length !== 1) return false;
      els[0].id='boss-hunt-target'; els[0].scrollIntoView({block:'center'}); return true;
      })()""" % json.dumps(key), default=False)
    if marked is not True:
        return 'error: unknown_conversation', target.get('text', ''), []
    clicked = br.click(tab, '#boss-hunt-target')
    if clicked and clicked.get('isError'):
        return 'error: unknown_conversation', target.get('text', ''), []
    time.sleep(2.5)
    active = active_conversation(br, tab, limit)
    if active.get('conv_key') != key:
        return 'error: unknown_conversation', target.get('text', ''), []
    br.__dict__.setdefault('_boss_sessions', {})[tab] = key
    return 'ok', target.get('text', ''), active.get('messages') or []


def normalized_history(history):
    return [{k: m.get(k) or '' for k in ('who', 'id', 'text', 'ts')} for m in history]


def atomic_chat_click(br, tab, key, text, expected_history=None, *, system_texts=None):
    """Bind the shared sender to independent conversation and round evidence."""
    guard = "const expected = %s, expectedHistory = %s;" % (
        json.dumps(key), json.dumps(expected_history, ensure_ascii=False)) + conv_parse.system_message_parser_js(system_texts) + r"""
      const header = panel.querySelector('.chat-title,.title-box');
      const key = identity(header) || identity(panel);
      if (key !== expected) return fail('identity_changed');
      {
        const messages = [...panel.querySelectorAll('.message-item')].map(e => {
          const c=e.querySelector('.message-content');
          const b=e.querySelector('.text-content') || c;
          return {classes:e.className,who:parseMessageOwner(e.className,b ? b.innerText : ''),
            id:e.getAttribute('data-mid')||e.getAttribute('data-message-id')||e.getAttribute('data-id')||'',
            text:(b?b.innerText:''),
            ts:e.getAttribute('data-timestamp')||e.getAttribute('data-time')||''};
        });
        if (messages.some(m => !isSystemMessage(m) && m.who === 'unknown')) return fail('owner_unknown');
        // Compare every raw bubble, including systems, against the saved draft baseline.
        messages.forEach(m => delete m.classes);
        if (expectedHistory !== null && JSON.stringify(messages) !== JSON.stringify(expectedHistory)) return fail('stale_draft');
      }
    """
    result = CS.click_send_scoped(br, tab, text, identity_js=CONVERSATION_JS,
                                  guard_js=guard, conv_key=key)
    # Preserve the reply API's existing diagnostic names.
    if result.get('reason') == 'panel_not_visible':
        result['reason'] = 'panel_hidden'
    return result

