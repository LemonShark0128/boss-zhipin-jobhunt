"""Round four: offline regression tests, temporary stores and in-memory DOM only."""
import io
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
import chat_dom as CD
import boss_hunt as B
import hunt_config as HC
import outbound as O
import greet as G
import reply as R
import send_safety as S

TEXT = '方便，岗位负责哪个区域？'


def dom_eval(expr, *, key='1', text=TEXT, panels=1, visible=True, switch=None, messages=None):
    """Execute the production page program against a small in-memory DOM."""
    fixture = r'''
let clicks=0;
const input={value:INPUT,innerText:INPUT, textContent:INPUT, getClientRects:()=>[1]};
const button={click:()=>{clicks++},getClientRects:()=>[1],disabled:false};
const header={getAttribute:n=>n==='data-conversation-id'?KEY:null,querySelector:()=>null};
const messageNodes=MESSAGES.map(m=>({className:m.classes || (m.who==='mine'?'message-item item-mine':m.who==='friend'?'message-item item-friend':'message-item'),
getAttribute:n=>n==='data-message-id'?(m.id||''):n==='data-timestamp'?(m.ts||''):null,
querySelector:()=>({innerText:m.text})}));
const panel={contains:()=>true,getAttribute:header.getAttribute, getClientRects:()=>VISIBLE?[1]:[],
 querySelector:s=>s.includes('title')?header:s.includes('input')?input:s.includes('send')?button:null,
 querySelectorAll:s=>s.includes('input')?[input]:s.includes('send')?[button]:s.includes('message-item')?messageNodes:[]};
input.closest=()=>panel; button.closest=()=>panel; header.closest=()=>panel;
const hidden={value: SWITCH===true?'1':SWITCH===false?'0':'x'};
const sw={classList:{contains:s=>s==='ui-switch' || (s==='ui-switch-checked' && SWITCH===true)},
 getAttribute:n=>n==='aria-checked' && SWITCH!==null?String(SWITCH):null,
 querySelector:s=>s.includes('hidden')?hidden:null,
 closest:()=>({innerText:'自动招呼语'}),getClientRects:()=>[1]};
const document={querySelector:()=>panel,querySelectorAll:s=>s.includes('switch')?[sw]:Array(PANELS).fill(panel)};
const getComputedStyle=()=>({visibility:'visible',display:'block'});
'''.replace('INPUT', json.dumps(text)).replace('KEY', json.dumps(key)).replace('PANELS',str(panels)).replace('VISIBLE',str(visible).lower()).replace('SWITCH', json.dumps(switch)).replace('MESSAGES',json.dumps(messages or []))
    result = subprocess.run(['node','-e',fixture+'\nconst result='+expr+'; console.log(JSON.stringify({result,clicks}));'], capture_output=True,text=True,check=True)
    return json.loads(result.stdout)


class ChatBrowser:
    def __init__(self, change=None):
        self.key='1'; self.text=''; self.clicks=0; self.change=change
        self.messages=[{'id':'h1','who':'friend','text':'方便聊聊吗？','ts':'100'}]
    def fill(self, tab, selector, text):
        self.text=text
        if self.change=='identity': self.key='2'
        if self.change=='text': self.text='被改写'
        if self.change=='history': self.messages.append({'id':'h2','who':'friend','text':'岗位关闭了','ts':'101'})
        return {}
    def click(self,*args):
        selector=args[1] if len(args)>1 else ''
        self.clicks+=1
        # 可信点击（#bh-send-btn）才真正发出消息
        if 'bh-send-btn' in selector or 'btn-send' in selector:
            # 可信点击发出的就是输入框里的文本
            self.messages.append({'id':'m'+str(self.clicks),'who':'mine','text':self.text or TEXT})
        return {}
    def json(self, tab, expr, default=None):
        if 'dispatchEvent' in expr:
            import re
            text=json.loads(re.search(r'const text = (.*);', expr).group(1))
            self.fill(tab, '.chat-input[contenteditable="true"]', text)
            return {'ok':True}
        if '.click()' in expr or 'button_disabled' in expr:
            data=dom_eval(expr,key=self.key,text=self.text,messages=self.messages)
            self.clicks+=data['clicks']
            if data['clicks']: self.messages.append({'id':'m'+str(self.clicks),'who':'mine','text':TEXT})
            return data['result']
        return {'conv_key':'id:'+self.key,'messages':list(self.messages)}


class Round4(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        self.root=Path(self.tmp.name)
        self.cfg={'paths':{'workdir':str(self.root),'state':str(self.root/'state.json')},'user':{}}

    def test_p0_1_identity_switch_aborts_without_click(self):
        br=ChatBrowser('identity')
        try:
            result=B.send_in_chat(br,1,TEXT,'方便','id:1',self.cfg)
        except RuntimeError as exc: result=str(exc)
        self.assertEqual(result,'send_aborted:identity_changed')
        self.assertEqual(br.clicks,0)

    def test_p0_1_changed_input_aborts_without_click(self):
        br=ChatBrowser('text')
        try: result=B.send_in_chat(br,1,TEXT,'方便','id:1',self.cfg)
        except RuntimeError as exc: result=str(exc)
        self.assertEqual(result,'send_aborted:input_mismatch'); self.assertEqual(br.clicks,0)

    def test_p0_1_normal_click_is_scoped_and_bound(self):
        br=ChatBrowser()
        with patch.object(B.time,'sleep'):
            self.assertEqual(B.send_in_chat(br,1,TEXT,'方便','id:1',self.cfg),'replied')
        self.assertEqual(br.clicks,1)
        self.assertEqual(S.ledger_latest(self.cfg)[0]['conv_key'],'id:1')

    def test_p0_2_dom_requires_explicit_switch_evidence(self):
        br=Mock()
        for switch, want in [(True,'auto_greeting_on'),(False,'auto_greeting_off'),(None,'auto_greeting_unknown')]:
            with self.subTest(switch=switch):
                br.js.side_effect=lambda tab,expr: {'text':dom_eval(expr,switch=switch)['result']}
                self.assertEqual(O.greeting_switch(br,1),want)

    def test_p0_2_unknown_blocks_send_with_manual_instruction(self):
        with patch.object(O,'inspect_job',return_value=('ready','')), patch.object(O,'job_identity_matches',return_value=True), patch.object(O,'greeting_switch',return_value='auto_greeting_unknown'):
            with self.assertRaisesRegex(RuntimeError,'platform_greeting_unknown.*手动确认'):
                O.send_one(Mock(),1,{'encryptJobId':'j'},self.cfg)

    def test_p1_1_example_template_and_llm_fallback_are_sendable(self):
        cfg=json.loads((Path(__file__).resolve().parents[1]/'assets/config.example.json').read_text())
        cfg['paths']=self.cfg['paths'];job={'jobName':'城市经理'}
        fallback=lambda j:O.render(cfg['user']['greeting_template'],cfg,j)
        for mode in ('template','llm'):
            with self.subTest(mode=mode):
                cfg['user']['greeting']['mode']=mode
                with patch.object(G,'_generate_raw',return_value=(None,'llm_unreachable')):
                    result=G.generate(cfg,job,fallback=fallback,use_cache=False)
                self.assertTrue(result['text']); self.assertTrue(G.basic_safety(result['text'],cfg)[0])

    def test_p1_2_draft_is_bound_to_generation_history(self):
        br=ChatBrowser(); history=list(br.messages)
        br.messages.append({'id':'h2','who':'friend','text':'明天到公司可以吗？','ts':'101'})
        try:
            result=B.send_in_chat(br,1,TEXT,'方便','id:1',self.cfg,expected_history=history)
        except TypeError as exc: result=str(exc)
        self.assertEqual(result,'stale_draft'); self.assertEqual(br.clicks,0)
        self.assertFalse((self.root/'send_ledger.jsonl').exists())

    def test_p1_3_hint_binds_metrics_and_unwritten_experience(self):
        brief='带6人团队，新签80家'
        for output, want in [('带80人团队，新签6家，岗位负责哪个区域？',False),
                             ('带6人团队，新签80家，曾在腾讯负责渠道，岗位负责哪个区域？',False),
                             ('我带6人团队，新签80家，岗位负责哪个区域？',True)]:
            with self.subTest(output=output), patch.object(G,'_generate_raw',return_value=(output,'')):
                result=R.generate(self.cfg,{},brief,force=True)
                self.assertEqual(bool(result['text']),want)
                if not want: self.assertEqual(result['source'],'manual_required')

    def test_p1_4_greeting_prefix_does_not_authorize_request(self):
        for text in ('你好，明天十点来公司可以吗？','您好，电话是多少？','hi，地址发我','你好，上海能来吗？'):
            with self.subTest(text=text): self.assertFalse(R.judge([{'who':'friend','text':text}])[0])
        for text in ('你好','您好呀','Hello!'):
            self.assertTrue(R.judge([{'who':'friend','text':text}])[0])
            self.assertTrue(R.self_contained(text)[0])

    def test_p1_5_custom_interview_template_is_safe(self):
        br=ChatBrowser(); msg='您好，请给几个线上面试时段，我确认后答复。'
        # Click occurs; confirmation uses an own bubble for exactly this text.
        original=br.json
        def read(tab,expr,default=None):
            result=original(tab,expr,default)
            if '.click()' in expr and br.clicks: br.messages[-1]['text']=msg
            return result
        br.json=read
        with patch.object(B.time,'sleep'):
            self.assertEqual(B.send_in_chat(br,1,msg,'您好','id:1',self.cfg,'interview_reply'),'replied')
        self.assertEqual(br.clicks,1)
        br=ChatBrowser()
        self.assertEqual(B.send_in_chat(br,1,'明天下午三点我准时到','明天','id:1',self.cfg,'interview_reply'),'manual_required')
        self.assertEqual(br.clicks,0)

    def test_p1_6_no_reply_records_share_fields_and_separate_summary(self):
        from datetime import timedelta
        now=HC.now_local(self.cfg)
        old=(now-timedelta(hours=80)).strftime('%Y-%m-%d %H:%M')
        greet='您好希望进一步了解岗位方便沟通'
        st={'sent':{'j':{'brand':'甲','ts':old,'grade':'A','target':'平台','greet_text':greet},
                    'k':{'brand':'乙','ts':old,'grade':'B','target':'品牌'}},
            'conv_state':{'id:k':{'status':'stale','job_id':'k','last_ts':now.timestamp()-80*3600,'stale_hours':80}}}
        B.collect_rejects(self.cfg,st,{'j':{'brandName':'甲','lastMessageInfo':{'showText':greet,'fromSelf':True,'msgTime':int(now.timestamp()*1000)}}},now=now)
        rows=B.collect_stale_rejects(self.cfg,st,now.timestamp())
        fields={'attribution','target','grade','applied_ts','last_out_ts','stale_hours','detected_ts','reason'}
        for jid in ('j','k'): self.assertTrue(fields <= rows[jid].keys(),rows[jid])
        self.assertEqual(rows['k']['attribution'],'our_apply'); self.assertEqual(rows['k']['target'],'品牌')
        summary=B.daily_summary(self.cfg,st)
        self.assertEqual(summary['no_reply'],1); self.assertEqual(summary.get('stale_no_reply'),1)
        self.assertIn('沟通后无回复 1',' '.join(B.summary_lines(summary)))

    def test_p2_reject_repository_used_by_both_collectors(self):
        save=getattr(B,'save_rejects',None)
        self.assertIsNotNone(save)
        with patch.object(B,'save_rejects',wraps=save) as writer:
            from datetime import timedelta
            now=HC.now_local(self.cfg)
            old=(now-timedelta(hours=80)).strftime('%Y-%m-%d %H:%M')
            greeting='您好希望进一步了解岗位方便沟通'
            B.collect_rejects(self.cfg,{'sent':{'j':{'ts':old,'greet_text':greeting}}},
                {'j':{'lastMessageInfo':{'showText':greeting,'fromSelf':True,'msgTime':int(now.timestamp()*1000)}}},now=now)
            B.collect_stale_rejects(self.cfg,{'conv_state':{'id:k':{'status':'stale'}}},now.timestamp())
            self.assertEqual(writer.call_count,2)
        self.root.joinpath('rejects.json').write_text('{"legacy":{"kind":"stale_no_reply"}}')
        self.assertEqual(B.daily_summary(self.cfg,{})['cumulative'],1)

    def corrupt_ledger(self):
        row={'event_id':'e1','status':'pending','cmd':'auto_reply','ts':'2026-10-27 10:00','conv_key':'id:1'}
        prefix=(json.dumps(row)+'\n').encode(); tail=b'{"event_id":"e1","status":"se'
        self.root.joinpath('send_ledger.jsonl').write_bytes(prefix+tail)
        return prefix,tail

    def test_p1_7_partial_ledger_is_readable_and_blocks_send(self):
        self.corrupt_ledger()
        try: rows=S.ledger_latest(self.cfg)
        except ValueError: rows=[]
        self.assertTrue(any(r.get('pending_verification') for r in rows))
        br=Mock()
        try: result=O.send_one(br,1,{'encryptJobId':'j'},self.cfg)
        except RuntimeError as exc: result=str(exc)
        self.assertIn('skip_ledger_corrupt',result); br.click.assert_not_called();br.nav.assert_not_called()

    def test_p1_7_repair_dry_then_apply_preserves_bytes(self):
        prefix,tail=self.corrupt_ledger(); path=self.root/'send_ledger.jsonl'
        repair=getattr(S,'ledger_repair',None); self.assertIsNotNone(repair)
        result=repair(self.cfg)
        self.assertTrue(result['pending_verification']); self.assertEqual(result['quarantined_bytes'],0)
        self.assertEqual(result['corrupt_bytes'],len(tail)); self.assertEqual(path.read_bytes(),prefix+tail)
        result=repair(self.cfg,apply=True)
        self.assertEqual(path.read_bytes(),prefix)
        self.assertEqual(Path(result['quarantine']).read_bytes(),tail)
        self.assertEqual(result['quarantined_bytes'],len(tail))
        self.assertIn('e1',result['pending_events'])
        with self.assertRaisesRegex(RuntimeError,'unresolved_send'): S.ledger_guard(self.cfg)

    def test_p2_ledger_uses_injected_clock_without_main_import(self):
        self.cfg['_clock']=lambda:0
        self.cfg['schedule']={'timezone':'UTC'}
        with patch.object(HC,'stamp',side_effect=AssertionError('reverse import')):
            try: result=S.ledger_send(self.cfg,'send','id:1','j','hello',lambda:'sent')
            except AssertionError as exc: result=str(exc)
        self.assertEqual(result,'sent')
        self.assertEqual(S.ledger_latest(self.cfg)[0]['ts'],'1970-01-01 00:00')
        self.assertEqual(S.ledger_count(self.cfg),1)

    def test_atomic_missing_receipt_keeps_send_unknown(self):
        br=ChatBrowser()
        with patch.object(CD,'atomic_chat_click',return_value={}):
            with self.assertRaisesRegex(RuntimeError,'unresolved_send'):
                B.send_in_chat(br,1,TEXT,'方便','id:1',self.cfg)
        self.assertEqual(S.ledger_latest(self.cfg)[0]['status'],'unknown')

    def test_input_region_button_only(self):
        exprs=[]
        br=Mock();br.json.side_effect=lambda tab,expr,default=None:exprs.append(expr) or {}
        CD.atomic_chat_click(br,1,'id:1',TEXT)
        expr=exprs[0]
        # A visible chat panel with a send button outside the editor must fail.
        fixture=r'''
let clicks=0;
const header={getAttribute:()=> '1'};
const button={click:()=>clicks++,getClientRects:()=>[1]};
const input={innerText:TEXT,getClientRects:()=>[1],closest:()=>null};
const panel={getClientRects:()=>[1],querySelector:()=>header,
querySelectorAll:s=>s.includes('input')?[input]:[]};
const document={querySelectorAll:()=>[panel]};
const getComputedStyle=()=>({display:'block',visibility:'visible'});
'''.replace('TEXT',json.dumps(TEXT))
        data=subprocess.run(['node','-e',fixture+'const result='+expr+'; console.log(JSON.stringify({result,clicks}));'],capture_output=True,text=True,check=True)
        result=json.loads(data.stdout)
        self.assertEqual(result['clicks'],0);self.assertFalse(result['result']['ok'])

    def test_greeting_with_time_or_number_request_is_manual(self):
        for text in ('你好，明天十点方便聊吗？','你好，团队8人感兴趣吗？','您好，微信沟通一下？'):
            with self.subTest(text=text):self.assertFalse(R.judge([{'who':'friend','text':text}])[0])

    def test_initial_greeting_also_checks_identity_and_input_atomically(self):
        from test_round7 import Page, JOB
        for change, want in [('identity','send_aborted:identity_mismatch'),
                             ('input','send_aborted:input_mismatch'),
                             ('recipient','send_aborted:identity_mismatch'),('normal','sent')]:
            with self.subTest(change=change):
                br=Page(); read=br.json
                def js(tab,expr,default=None):
                    # Change after fill/snapshot, immediately before the atomic send.
                    if 'button_disabled' in expr:   # 守卫轮：真实点击前一刻
                        if change=='identity': br.state['panelText']='别家公司 其他岗位'
                        if change=='input': br.state['text']='被改写'
                        if change=='recipient': br.state['panelText']='何女士 HRBP'
                    return read(tab,expr,default)
                br.json=js
                with patch.object(O,'inspect_job',return_value=('ready','')), patch.object(O,'job_identity_matches',return_value=True), patch.object(O,'greeting_switch',return_value='auto_greeting_off'), patch.object(G,'generate',return_value={'text':TEXT}),patch.object(B.time,'sleep'):
                    result=O.send_one(br,1,dict(JOB),self.cfg)
                self.assertEqual(result,want)
                self.assertEqual(br.clicks,1 if change=='normal' else 0)
                self.assertEqual(br.paths[0],'.btn-startchat')

    def test_ledger_repair_cli_dry_does_not_create_runtime_dirs(self):
        cfgfile=self.root/'config.json';cfgfile.write_text('{}')
        args=B.build_parser().parse_args(['--config',str(cfgfile),'ledger-repair','--dry-run','--json'])
        import contextlib
        with contextlib.redirect_stdout(io.StringIO()): self.assertEqual(args.func(args),0)
        self.assertFalse((self.root/'data').exists())

    def test_interview_markers_do_not_authorize_attendance_promise(self):
        from message_policy import safe_interview_reply
        for text in ('时段已确认，我参加面试。','请给几个线上面试时段，我确认后答复。我保证参加。',
                     '给我面试时段，我确认，我来现场。','时段已确认，我去面试。'):
            with self.subTest(text=text): self.assertFalse(safe_interview_reply(text,{}))

    def test_no_reply_timestamp_is_seconds_not_divided_twice(self):
        from datetime import timedelta
        now=HC.now_local(self.cfg); applied=now-timedelta(hours=80)
        greeting='您好希望进一步了解岗位方便沟通'
        st={'sent':{'j':{'ts':applied.strftime('%Y-%m-%d %H:%M'),'greet_text':greeting}}}
        rows=B.collect_rejects(self.cfg,st,{'j':{'lastMessageInfo':{'showText':greeting,'fromSelf':True,'msgTime':int(applied.timestamp()*1000)}}},now=now)
        self.assertAlmostEqual(rows['j']['last_out_ts'],applied.timestamp(),places=2)
        self.assertAlmostEqual(rows['j']['stale_hours'],80,places=2)

    def test_compaction_cannot_promote_invented_hint_facts(self):
        import human as H
        H.save({'entries':[], 'hints':{'团队规模':{'brief':'带6人团队，新签80家','count':1,'last':'2026-10-27 00:00'}}},self.cfg)
        with patch.object(G,'_generate_raw',return_value=(json.dumps({'团队规模':'曾在腾讯负责渠道，带400人团队'}),'')):
            result=H.compact(self.cfg)
        self.assertEqual(result['items'][0]['method'],'latest')
        self.assertEqual(H.load(self.cfg)['hints']['团队规模']['brief'],'带6人团队，新签80家')

    def test_snapshot_interview_or_followup_is_rechecked_after_open(self):
        for cmd, messages in [('interview_reply',[{'who':'friend','text':'岗位关闭了，不用再联系'}]),
                              ('followup',[{'who':'friend','text':'还有问题想问您'}])]:
            with self.subTest(cmd=cmd), patch.object(CD,'conversation_rows',return_value=[{'conv_key':'id:1','text':'甲 王'}]),patch.object(CD,'open_by_index',return_value=('ok','甲 王',messages)),patch.object(B,'send_in_chat') as send:
                result=B.reply_in_chat(Mock(),1,'甲',B.INTERVIEW_DEFAULT,'面试',self.cfg,cmd=cmd,conv_key='id:1')
                self.assertEqual(result,'stale_draft');send.assert_not_called()

    def test_generation_history_unchanged_sends_once(self):
        br=ChatBrowser()
        with patch.object(B.time,'sleep'):
            self.assertEqual(B.send_in_chat(br,1,TEXT,'方便','id:1',self.cfg,expected_history=list(br.messages)),'replied')
        self.assertEqual(br.clicks,1)
        self.assertTrue(S.ledger_latest(self.cfg)[0]['draft_fingerprint'])

    def test_message_arriving_during_fill_is_caught_atomically(self):
        br=ChatBrowser('history')
        self.assertEqual(B.send_in_chat(br,1,TEXT,'方便','id:1',self.cfg,expected_history=list(br.messages)),'stale_draft')
        self.assertEqual(br.clicks,0)
        self.assertEqual(S.ledger_latest(self.cfg)[0]['status'],'failed')

    def test_hidden_or_ambiguous_panel_never_clicks(self):
        for panels, visible, reason in [(2,True,'panel_not_unique'),(1,False,'panel_hidden')]:
            br=Mock()
            def execute(tab,expr,default=None):
                result=dom_eval(expr,panels=panels,visible=visible)
                self.assertEqual(result['clicks'],0)
                return result['result']
            br.json.side_effect=execute
            self.assertEqual(CD.atomic_chat_click(br,1,'id:1',TEXT)['reason'],reason)

    def test_switch_conflicting_states_are_unknown(self):
        br=Mock();br.js.return_value={'text':json.dumps([{'t':'招呼语','on':True},{'t':'招呼语','on':False}])}
        self.assertEqual(O.greeting_switch(br,1),'auto_greeting_unknown')

    def test_preflight_unknown_reports_manual_action(self):
        import contextlib
        cfgfile=self.root/'config.json';cfgfile.write_text(json.dumps(self.cfg))
        args=B.build_parser().parse_args(['--config',str(cfgfile),'preflight','--json'])
        br=Mock();br.close_own_tabs.return_value=0;br.wake='ok';br.connect.return_value='ok'
        output=io.StringIO()
        with patch.object(B.CM,'Browser',return_value=br),patch.object(O,'check_login',return_value=(1,{'avatar':True})),patch.object(O,'greeting_switch',return_value='auto_greeting_unknown'),contextlib.redirect_stdout(output):
            self.assertEqual(args.func(args),0)
        result=json.loads(output.getvalue())
        self.assertFalse(result['ok']);self.assertEqual(result['greeting_switch'],'auto_greeting_unknown')
        self.assertIn('手动确认',result['manual_action'])
