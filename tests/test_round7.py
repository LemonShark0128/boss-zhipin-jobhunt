"""Real page shape, executed offline in Node; all ledger data is temporary."""
import hashlib
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

TEXT = '方便，岗位负责哪个区域？'
JOB = {'encryptJobId': 'j', 'jobName': '虚构渠道经理', 'brandName': '戊示例科技'}
FIXTURE = r'''
let clicks=0;
const location={pathname:state.path};
const getComputedStyle=()=>({display:'block',visibility:'visible'});
const InputEvent=function(type,opts){this.type=type;this.bubbles=opts.bubbles};
const KeyboardEvent=function(type,opts){this.type=type;this.bubbles=opts.bubbles;this.key=opts.key};
const mutate=()=>{
 if(state.mutate==='identity')state.key='2';
 if(state.mutate==='text')state.text='改写';
 if(state.mutate==='history')state.messages.push({id:'h2',who:'friend',text:'岗位关闭了',ts:'101'});
};
const input={focus(){state.focused=true},dispatchEvent(e){state.inputEvent=e.type;mutate();},
 get innerText(){return state.text},get textContent(){return state.text},
 set textContent(t){state.text=state.fillMismatch?'截断':t},getClientRects:()=>[1]};
const header={getAttribute:n=>n==='data-conversation-id'?state.key:null,querySelector:()=>null};
const button={disabled:false,classList:{contains:c=>c==='disabled'&&state.disabled},
 getAttribute:()=>null,getClientRects:()=>[1],click(){clicks++;if(state.bubble)state.messages.push({id:'m1',who:'mine',text:state.text,ts:'102'})}};
const messageNodes=()=>state.messages.map(m=>({className:'message-item item-'+(m.who==='mine'?'mine':m.who==='friend'?'friend':'unknown'),
 getAttribute:n=>n==='data-message-id'?m.id:n==='data-timestamp'?m.ts:null,
 querySelector:()=>({innerText:m.text})}));
const panel={innerText:state.panelText,getAttribute:header.getAttribute,getClientRects:()=>state.visible?[1]:[],contains:()=>true,
 querySelector:s=>s.includes('title')?header:s.includes('input')?input:null,
 querySelectorAll:s=>s==='.chat-input[contenteditable="true"]'?[input]:s==='.btn-v2.btn-sure-v2.btn-send'?Array(state.buttons).fill(button):s.includes('message-item')?messageNodes():[]};
input.closest=()=>panel;
const document={title:'BOSS直聘',querySelector:s=>s.startsWith('.chat-conversation')?panel:null,
 querySelectorAll:s=>s==='.chat-conversation'?Array(state.panels).fill(panel):[],
 createRange:()=>({selectNodeContents(){},collapse(){}}),
 execCommand(cmd,ui,t){state.exec=(state.exec||[]).concat([cmd]);mutate();
  state.text=state.fillMismatch?'截断':t;return true;}};
const window={getSelection:()=>({removeAllRanges(){},addRange(){}})};
'''

class Page:
    def __init__(self, **kwargs):
        self.state = dict(path='/web/geek/chat', key='1', text='', panelText='虚构HR 戊示例科技 HRBP 更多 虚构渠道经理 20-30K',
                          panels=1, visible=True, buttons=1, disabled=False, bubble=True,
                          messages=[{'id':'h1','who':'friend','text':'方便聊聊吗？','ts':'100'}])
        self.state.update(kwargs)
        self.clicks=0
        self.paths=[]
    def json(self, tab, expr, default=None):
        source='let state='+json.dumps(self.state,ensure_ascii=False)+';\n'+FIXTURE+'\nconst result='+expr+';console.log(JSON.stringify({result,state,clicks}));'
        run=subprocess.run(['node','-e',source],text=True,capture_output=True)
        if run.returncode:
            raise AssertionError(run.stderr)
        data=json.loads(run.stdout); self.state=data['state'];self.clicks+=data['clicks']
        result=data['result']
        # 模仿 chrome_mcp.parse_json_payload：JSON.stringify 出来的字符串会被解析成 dict
        if isinstance(result,str) and result.strip().startswith('{'):
            try: return json.loads(result)
            except ValueError: return default
        return result
    def click(self, tab, sel):
        if sel=='#bh-send-btn':
            self.paths.append(sel); self.state['sent_via']='trusted_click'; self.clicks+=1
            self.state.setdefault('messages',[]).append({'id':'m1','who':'mine',
                'text':self.state.get('text') or '', 'ts':'102'})
            if not self.state.get('bubble',True): self.state['messages'].pop()
            return {}
        assert sel=='.btn-startchat'
        self.paths.append(sel)
        if not self.state.get('timeout'): self.state['path']='/web/geek/chat'
        return {}

class Round7(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        self.cfg={'paths':{'workdir':self.tmp.name,'state':str(Path(self.tmp.name)/'state.json')},'user':{}}
    def module(self):
        try:
            import chat_send
        except ImportError:
            self.fail('missing shared contenteditable chat sender')
        return chat_send
    def test_identity_requires_brand_and_job_without_whitespace(self):
        c=self.module()
        self.assertTrue(c.chat_identity_evidence('戊 示例 科技 虚构渠道经理',JOB))
        for text in ('戊示例科技','虚构渠道经理','别家公司 其他岗位',''):
            self.assertFalse(c.chat_identity_evidence(text,JOB))
        self.assertFalse(c.chat_identity_evidence('戊示例科技 虚构渠道经理',{}))
    def test_contenteditable_dispatches_input_and_checks_readback(self):
        c=self.module();br=Page()
        self.assertTrue(c.type_into_chat(br,1,TEXT)['ok'])
        self.assertEqual(br.state['text'],TEXT);self.assertTrue(br.state['focused'])
        # 必须走 execCommand：合成 input 事件在真机不解锁发送按钮（round8 复现原缺陷）
        self.assertEqual(br.state.get('exec'),['insertText'])
        self.assertEqual(c.type_into_chat(Page(fillMismatch=True),1,TEXT)['reason'],'fill_mismatch')
    def test_atomic_guards_and_disabled_class(self):
        c=self.module()
        for changes,reason in [({'panels':2},'panel_not_unique'),({'visible':False},'panel_not_visible'),
                               ({'panelText':'戊示例科技 其他岗位'},'identity_mismatch'),({'text':'改写'},'input_mismatch'),
                               ({'buttons':2},'button_not_unique'),({'disabled':True},'button_disabled')]:
            with self.subTest(reason=reason):
                br=Page(text=TEXT,**{k:v for k,v in changes.items() if k!='text'})
                br.state.update(changes)
                result=c.click_send_scoped(br,1,TEXT,job=JOB)
                self.assertEqual(result['reason'],reason);self.assertEqual(br.clicks,0)
        br=Page(text=TEXT)
        self.assertTrue(c.click_send_scoped(br,1,TEXT,job=JOB)['clicked']);self.assertEqual(br.clicks,1)
    def test_confirmation_requires_new_id_own_bubble_and_exact_hash(self):
        c=self.module();sha=hashlib.sha256(TEXT.encode()).hexdigest()
        for m,want in [({'id':'old','who':'mine','text':TEXT},'pending_verification'),
                       ({'id':'new','who':'friend','text':TEXT},'pending_verification'),
                       ({'id':'','who':'mine','text':TEXT},'pending_verification'),
                       ({'id':'new','who':'mine','text':TEXT+'!'},'pending_verification'),
                       ({'id':'new','who':'mine','text':TEXT},'sent')]:
            self.assertEqual(c.confirm_new_bubble(['old'],[m],sha),want)
    def greet(self, br):
        with patch.object(O,'inspect_job',return_value=('ready','')),patch.object(O,'job_identity_matches',return_value=True),patch.object(O,'greeting_switch',return_value='auto_greeting_off'),patch.object(B.G,'generate',return_value={'text':TEXT}),patch.object(B.time,'sleep'):
            return O.send_one(br,1,dict(JOB),self.cfg)
    def test_greeting_follows_full_page_chat_navigation(self):
        br=Page(path='/job_detail/j.html')
        self.assertEqual(self.greet(br),'sent');self.assertEqual(br.clicks,1)
        self.assertEqual(br.paths[0],'.btn-startchat')
    def test_greeting_waits_for_chat_and_times_out_without_typing(self):
        br=Page(path='/job_detail/j.html',timeout=True)
        self.assertEqual(self.greet(br),'error: chat_not_opened')
        self.assertEqual(br.clicks,0);self.assertEqual(br.state['text'],'')
    def test_greeting_wrong_panel_never_types_or_clicks(self):
        br=Page(panelText='别家公司 虚构渠道经理')
        self.assertEqual(self.greet(br),'send_aborted:identity_mismatch')
        self.assertEqual(br.clicks,0);self.assertEqual(br.state['text'],'')
    def test_greeting_without_new_bubble_stays_pending(self):
        br=Page(bubble=False)
        with self.assertRaisesRegex(RuntimeError,'unresolved_send'):
            self.greet(br)
        self.assertEqual(S.ledger_latest(self.cfg)[0]['status'],'unknown')