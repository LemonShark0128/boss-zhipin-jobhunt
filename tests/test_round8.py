"""Real-page input model: Vue/framework editor ignores synthetic 'input' events.

离线用 Node 执行真实页面形状：contenteditable + 由框架模型驱动的发送按钮。
键的差别在于——写 textContent 再 dispatchEvent(new InputEvent('input')) 只改 DOM，
不改变框架模型，所以按钮始终带 disabled（线上 send_aborted:button_disabled 的成因）。
只有 document.execCommand('insertText') 会生成浏览器原生 beforeinput/input，
框架模型才会更新。所有账本数据都在临时目录。
"""
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
import boss_hunt as B
import outbound as O
import send_safety as S
import chat_send as CS

TEXT = '您好，我做过 5 年渠道，方便聊两句吗？'
JOB = {'encryptJobId': 'j', 'jobName': '虚构渠道经理', 'brandName': '戊示例科技'}

# 真实页面形状：按钮 disabled 由框架模型 state.model 决定，DOM 文本无关。
FIXTURE = r'''
let clicks=0;
const location={pathname:state.path};
const getComputedStyle=()=>({display:'block',visibility:'visible'});
const InputEvent=function(type,opts){this.type=type;this.bubbles=opts.bubbles;this.data=opts.data;};
const KeyboardEvent=function(type,opts){this.type=type;this.bubbles=opts.bubbles;this.key=opts.key;};
const messages=()=>state.messages;
function pageAfterEdit(){
  if(state.mutate==='identity')state.key='2';
  if(state.mutate==='text')state.text='改写';
}
const input={
  focus(){state.focused=true},
  // 框架只认真实输入事件：合成事件到这里只记账，不改 state.model（线上实测行为）
  dispatchEvent(e){
    state.events=(state.events||[]).concat([e.type]);
    if(e.type==='keydown'&&e.key==='Enter'&&state.enterSends){
      state.model=state.text;
      state.messages.push({id:'e1',who:'mine',text:state.text,ts:'103'});
    }
    return true;
  },
  get innerText(){return state.text},
  get textContent(){return state.text},
  set textContent(t){state.text=state.fillMismatch?'截断':t},
  getClientRects:()=>[1]
};
const button={
  get disabled(){return !state.model},
  classList:{contains:c=>c==='disabled'&&!state.model},
  getAttribute:()=>null,getClientRects:()=>[1],
  click(){clicks++;if(state.model)state.messages.push({id:'m1',who:'mine',text:state.model,ts:'102'})}
};
const messageNodes=()=>state.messages.map(m=>({
  // 真机：我方气泡 class 是 item-myself；正文在 .text-content，.message-content 里混着「送达」
  className:'message-item '+(m.who==='mine'?'item-myself':'item-friend'),
  getAttribute:n=>n==='data-mid'?m.id:n==='data-timestamp'?m.ts:null,
  querySelector:s=>s==='.text-content'?(m.who==='mine'?{innerText:m.text}:null)
    :s==='.message-content'?{innerText:(m.who==='mine'?'送达\n\n'+m.text:m.text)}:null}));
const panel={innerText:state.panelText,getAttribute:()=>state.key,getClientRects:()=>state.visible?[1]:[],
  contains:()=>true,
  querySelector:s=>s.includes('input')?input:null,
  querySelectorAll:s=>s==='.chat-input[contenteditable="true"]'?[input]:
    s==='.btn-v2.btn-sure-v2.btn-send'?Array(state.buttons).fill(button):
    s.includes('message-item')?messageNodes():[]};
const document={title:'BOSS直聘',
  querySelector:s=>s.startsWith('.chat-conversation')?panel:null,
  querySelectorAll:s=>s==='.chat-conversation'?Array(state.panels).fill(panel):[],
  createRange:()=>({selectNodeContents(){},collapse(){}}),
  // 浏览器原生插入：DOM 与框架模型一起更新（execCommand 会触发原生 beforeinput/input）
  execCommand(cmd,ui,text){
    state.exec=(state.exec||[]).concat([cmd]);
    if(state.execBroken)return false;
    pageAfterEdit();
    state.text=state.fillMismatch?'截断':text;
    if(state.defer>0)state.pendingSync=state.defer; else state.model=state.text;
    return true;
  }};
const window={getSelection:()=>({removeAllRanges(){},addRange(){}})};
'''

class Page:
    def __init__(self, **kwargs):
        self.state = dict(path='/web/geek/chat', key='1', text='', model='',
                          panelText='虚构HR 戊示例科技 HRBP 更多 虚构渠道经理 20-30K',
                          panels=1, visible=True, buttons=1, bubble=True,
                          defer=0, enterSends=False,
                          messages=[{'id':'h1','who':'friend','text':'方便聊聊吗？','ts':'100'}])
        self.state.update(kwargs)
        self.clicks = 0
        self.evals = 0
        self.paths = []

    def json(self, tab, expr, default=None):
        self.evals += 1
        if self.state.get('pendingSync'):
            # 模拟框架 debounce：模型要过几次轮询才同步（按钮此时才解锁）
            self.state['pendingSync'] -= 1
            if self.state['pendingSync'] <= 0:
                self.state['model'] = self.state['text']
        source = ('let state=' + json.dumps(self.state, ensure_ascii=False) + ';\n' + FIXTURE
                  + '\nconst result=' + expr + ';console.log(JSON.stringify({result,state,clicks}));')
        run = subprocess.run(['node', '-e', source], text=True, capture_output=True)
        if run.returncode:
            raise AssertionError(run.stderr)
        data = json.loads(run.stdout)
        self.state = data['state']
        self.clicks += data['clicks']
        result = data['result']
        if isinstance(result, str) and result.strip().startswith('{'):
            try:
                return json.loads(result)
            except ValueError:
                return default
        return result

    def click(self, tab, sel):
        if sel == '#bh-send-btn':
            self.paths.append(sel)
            self.clicks += 1
            self.state['sent_via'] = 'trusted_click'
            if self.state.get('model'):
                self.state['messages'].append({'id':'m1','who':'mine','text':self.state['model'],'ts':'102'})
            return {}
        assert sel == '.btn-startchat'
        self.paths.append(sel)
        self.state['path'] = '/web/geek/chat'
        return {}

    def fill(self, tab, selector, text, timeout=90):
        # MCP fill：扩展注入的输入等同原生输入，会推进框架模型
        self.state['bridge_fill'] = text
        self.state['text'] = text
        self.state['model'] = text
        return {}

class NoBridgeFill(Page):
    fill = None


class Round8(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.cfg = {'paths': {'workdir': self.tmp.name,
                              'state': str(Path(self.tmp.name) / 'state.json')}, 'user': {}}

    def module(self):
        import chat_send
        return chat_send

    def test_typing_uses_execcommand_so_framework_model_syncs(self):
        """原缺陷：textContent + 合成 input 事件只改 DOM，按钮仍 disabled。"""
        c = self.module()
        br = Page()
        self.assertTrue(c.type_into_chat(br, 1, TEXT)['ok'])
        self.assertEqual(br.state['text'], TEXT)
        self.assertEqual(br.state['model'], TEXT, '框架模型必须与输入框文本同步')
        self.assertIn('insertText', br.state.get('exec') or [])
        # 旧写法只留下合成事件，模型仍是空 → 按钮 disabled
        self.assertTrue(c.click_send_scoped(br, 1, TEXT, job=JOB)['clicked'])
        self.assertEqual(br.clicks, 1)

    def test_send_button_waits_for_async_unlock_before_clicking(self):
        c = self.module()
        br = Page(defer=2)   # 写完后框架再等 2 次轮询才同步模型
        self.assertTrue(c.type_into_chat(br, 1, TEXT)['ok'])
        self.assertEqual(br.state['model'], '', '模型此刻还没同步，按钮应仍 disabled')
        with patch.object(c.time, 'sleep') as slept:
            result = c.click_send_scoped(br, 1, TEXT, job=JOB)
        self.assertTrue(result['clicked'], result)
        self.assertEqual(br.clicks, 1)
        self.assertGreaterEqual(slept.call_count, 1, '必须轮询等待而不是立即判定 disabled')

    def test_disabled_after_full_poll_budget_still_fails_without_click(self):
        c = self.module()
        br = Page()
        self.assertTrue(c.type_into_chat(br, 1, TEXT)['ok'])
        br.state['model'] = ''       # 永远不解锁
        with patch.object(c.time, 'sleep'):
            result = c.click_send_scoped(br, 1, TEXT, job=JOB, wait_enabled_s=0.45)
        self.assertEqual(result['reason'], 'button_disabled')
        self.assertEqual(br.clicks, 0)
        self.assertGreater(br.evals, 1, '超时前应该反复轮询按钮状态')

    def test_execcommand_unavailable_falls_back_to_native_events(self):
        c = self.module()
        br = Page(execBroken=True)
        result = c.type_into_chat(br, 1, TEXT)
        self.assertTrue(result['ok'], result)
        self.assertEqual(br.state['text'], TEXT)
        self.assertIn('input', br.state.get('events') or [])

    def test_own_bubble_is_item_myself_and_text_excludes_delivery_status(self):
        """真机两项：class 是 item-myself（不是 item-mine）、正文在 .text-content。"""
        c = self.module()
        snap = c.chat_snapshot(Page(messages=[{'id':'x1','who':'mine','text':'你好','ts':'1'}]),
                               1)
        self.assertEqual(snap['messages'][0]['who'], 'mine')
        self.assertEqual(snap['messages'][0]['text'], '你好')

    def test_send_uses_trusted_click_not_js_click(self):
        """JS 的 button.click() 在真机会被服务端丢弃，必须走 CDP 可信点击。"""
        c = self.module()
        br = Page()
        self.assertTrue(c.type_into_chat(br, 1, TEXT)['ok'])
        result = c.click_send_scoped(br, 1, TEXT, job=JOB)
        self.assertEqual(result.get('via'), 'trusted_click')
        self.assertIn('#bh-send-btn', br.paths)
        self.assertEqual(br.state.get('sent_via'), 'trusted_click')

    def test_bubble_ids_come_from_data_mid_attribute(self):
        """真机消息节点只有 data-mid（无 data-message-id/data-id），读错就永远 pending。"""
        c = self.module()
        snap = c.chat_snapshot(Page(), 1)
        self.assertEqual([m['id'] for m in snap['messages']], ['h1'])

    def test_readback_mismatch_still_reports_fill_mismatch(self):
        c = self.module()
        self.assertEqual(c.type_into_chat(Page(fillMismatch=True), 1, TEXT)['reason'], 'fill_mismatch')

    def greet(self, br):
        with patch.object(O, 'inspect_job', return_value=('ready', '')), \
             patch.object(O, 'job_identity_matches', return_value=True), \
             patch.object(O, 'greeting_switch', return_value='auto_greeting_off'), \
             patch.object(B.G, 'generate', return_value={'text': TEXT}), \
             patch.object(B.time, 'sleep'):
            return O.send_one(br, 1, dict(JOB), self.cfg)

    def test_greeting_sends_when_button_only_unlocks_from_native_input(self):
        br = Page(path='/job_detail/j.html')
        self.assertEqual(self.greet(br), 'sent')
        self.assertEqual(br.clicks, 1)
        sent = [m for m in br.state['messages'] if m['who'] == 'mine']
        self.assertEqual([m['text'] for m in sent], [TEXT])
        self.assertEqual(S.ledger_latest(self.cfg)[0]['status'], 'sent')

    def test_greeting_falls_back_to_enter_when_button_never_unlocks(self):
        br = NoBridgeFill(path='/job_detail/j.html', enterSends=True)
        with patch.object(CS, 'type_into_chat', side_effect=lambda br_, tab, text: (
                br_.state.update(text=text), {'ok': True})[-1]):
            self.assertEqual(self.greet(br), 'sent')
        self.assertEqual(br.clicks, 0, '按钮 disabled 时不能点')
        self.assertIn('keydown', br.state.get('events') or [])
        self.assertEqual([m['text'] for m in br.state['messages'] if m['who'] == 'mine'], [TEXT])

    def test_greeting_stays_pending_when_click_and_enter_both_fail(self):
        br = NoBridgeFill(path='/job_detail/j.html')   # Enter 也不发送
        with patch.object(CS, 'type_into_chat', side_effect=lambda br_, tab, text: (
                br_.state.update(text=text), {'ok': True})[-1]), \
             self.assertRaisesRegex(RuntimeError, 'unresolved_send'):
            self.greet(br)
        self.assertEqual(br.clicks, 0)
        self.assertEqual(S.ledger_latest(self.cfg)[0]['status'], 'unknown')


if __name__ == '__main__':
    unittest.main()
