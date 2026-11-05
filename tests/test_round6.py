"""Round six: reproduce unsafe branches using temporary stores and DOM doubles."""
import hashlib
import json
import re
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
import chat_dom as CD
import boss_hunt as B
import hunt_config as HC
import hunt_state as HS
import outbound as O
import greet as G
import reply as R
import send_safety as S
from conv_state import update_conv_state
from test_round4 import ChatBrowser, TEXT, dom_eval


class Round6(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.cfg = {'paths': {'workdir': str(self.root), 'state': str(self.root/'state.json'),
                             'raw_dir': str(self.root/'raw')}, 'user': {}, '_clock': lambda: 1000}
        (self.root/'raw').mkdir()

    def test_p0_unknown_generation_never_calls_model(self):
        with patch.object(G, '_generate_raw', side_effect=AssertionError('model')):
            result = R.generate(self.cfg, {}, '负责渠道', force=True,
                                history=[{'who': 'unknown', 'text': '欢迎聊聊'}])
        self.assertEqual(result['reasons'], ['owner_unknown'])

    def test_p0_unknown_history_aborts_before_fill(self):
        br = ChatBrowser(); br.messages.insert(0, {'who':'unknown', 'text':'系统消息'})
        br.fill = Mock(side_effect=AssertionError('fill'))
        self.assertEqual(B.send_in_chat(br,1,TEXT,'方便','id:1',self.cfg), 'send_aborted:owner_unknown')
        self.assertEqual(br.clicks,0)

    def test_p0_unknown_replaces_old_state(self):
        prev = update_conv_state({}, {'side':'me','ts':1,'text':'old'}, 2, self.cfg)
        state = update_conv_state(prev, {'owner':'unknown','side':None,'ts':3,'text':'system'}, 4, self.cfg)
        report = B.unanswered_report({'conv_state':{'id:1':state}}, 1000000)
        self.assertEqual(state.get('owner'),'unknown')
        self.assertEqual([report['counts'][k] for k in ('waiting_me','waiting_them','stale','unknown')],[0,0,0,1])

    def test_p0_no_old_default_friend_parser(self):
        source = Path(B.__file__).read_text()
        self.assertIsNone(re.search(r"\?\s*'mine'\s*:\s*'friend'", source))

    def test_p1_1_brief_facts_reach_send_gate(self):
        br = ChatBrowser(); text = '我带6人团队，岗位负责哪个区域？'
        with patch.object(G,'_generate_raw',return_value=(text,'')):
            draft = R.generate(self.cfg,{},'带6人团队',force=True,history=br.messages)
        self.assertEqual(draft['text'],text)
        # Use real send gate and atomic DOM; only replace the browser transport.
        original = br.json
        def read(tab,expr,default=None):
            result = original(tab,expr,default)
            if '.click()' in expr and br.clicks:
                br.messages[-1]['text'] = text
            return result
        br.json = read
        with patch.object(B.time,'sleep'):
            result = B.send_in_chat(br,1,text,'我带','id:1',self.cfg,'reply-to',
                                    expected_history=br.messages[:], fact_context=dict(draft.get('fact_context') or {}, conv_key='id:1',
                                        op_id=B.reply_operation('id:1',br.messages)[1],
                                        draft_fingerprint=CD.history_fingerprint(br.messages)))
        self.assertEqual(result,'replied'); self.assertEqual(br.clicks,1)
        self.assertNotIn('带6人团队', (self.root/'send_ledger.jsonl').read_text().replace(text,''))

    def test_p1_2_question_claim_not_exempt(self):
        ok,reasons = G.fact_binding('在腾讯担任总监是否符合要求？',self.cfg)
        self.assertFalse(ok); self.assertTrue(any('fact_not_bound' in r for r in reasons))
        self.assertTrue(G.fact_binding('这个岗位负责哪个区域？',self.cfg)[0])
        cfg = dict(self.cfg,user={'greeting':{'facts':['带6人团队']}})
        self.assertTrue(G.basic_safety('我带6人团队，这个岗位负责哪个区域？',cfg)[0])

    def test_p1_3_each_invitation_clause_must_be_safe(self):
        for text in ('欢迎聊聊，能接受单休吗？','有没有兴趣，需要自备车辆','欢迎聊聊，愿意夜班吗？'):
            with self.subTest(text=text):
                self.assertFalse(R.self_contained(text)[0])
                self.assertFalse(R.judge([{'who':'friend','text':text}])[0])
                result = R.generate(self.cfg,{},text,history=[{'who':'friend','text':text}])
                self.assertEqual(result['source'],'manual_required')
        for text in ('您好，方便聊聊吗？','方便的话发份简历'):
            self.assertTrue(R.self_contained(text)[0],text)
            self.assertTrue(R.judge([{'who':'friend','text':text}])[0],text)

    def pending(self, **extra):
        row = dict(event_id='e',status='unknown',cmd='reply-to',conv_key='id:1',
                   text=TEXT,text_sha256=hashlib.sha256(TEXT.encode()).hexdigest(),ts='x',**extra)
        S.ledger_append(self.cfg,row)

    def test_p1_4_old_bubble_does_not_resolve_pending(self):
        self.pending(msg_count=2,last_message_id='old',click_ts_epoch=1000000)
        br=ChatBrowser(); br.messages.append({'id':'old','who':'mine','text':TEXT,'ts':'900'})
        with self.assertRaisesRegex(RuntimeError,'ledger-ack'):
            B.send_in_chat(br,1,TEXT,'方便','id:1',self.cfg,'reply-to',expected_history=[])
        self.assertEqual(S.ledger_latest(self.cfg)[0]['status'],'unknown'); self.assertEqual(br.clicks,0)

    def test_p1_4_no_baseline_requires_ack(self):
        self.pending()
        br=ChatBrowser(); br.messages.append({'id':'new','who':'mine','text':TEXT,'ts':'1001'})
        with self.assertRaisesRegex(RuntimeError,'ledger-ack'):
            B.send_in_chat(br,1,TEXT,'方便','id:1',self.cfg)
        self.assertEqual(S.ledger_latest(self.cfg)[0]['status'],'unknown')

    def test_p1_5_failure_cooldown_expiry_and_new_message(self):
        row={'conv_key':'id:1','brand':'甲科技','boss':'王招聘'}; st={}
        with patch.object(CD,'conversation_rows',return_value=[row]), patch.object(B,'read_status_message',side_effect=ValueError('read')) as reader:
            for now in (100,101,102): B.scan_conv_states(self.cfg,Mock(),1,st,now=now)
            self.assertEqual(st['conv_state']['id:1'].get('retry_after_epoch'),702)
            for now in (103,104):
                report=B.scan_conv_states(self.cfg,Mock(),1,st,now=now)
                self.assertEqual(report['skipped_failures'],1)
            self.assertEqual(reader.call_count,3)
            self.assertEqual(B.unanswered_report(st)['counts'].get('pending_verify'),1)
            B.scan_conv_states(self.cfg,Mock(),1,st,now=702)
            self.assertEqual(reader.call_count,4)
        items={'j':{'conv_key':'id:1','lastMessageInfo':{'msgTime':800000}}}
        with patch.object(CD,'conversation_rows',return_value=[row]), patch.object(B,'read_status_message',return_value={'side':'them','ts':800,'text':'new'}) as reader:
            B.scan_conv_states(self.cfg,Mock(),1,st,now=703,items=items)
        self.assertEqual(reader.call_count,1); self.assertEqual(st['conv_state']['id:1']['failures'],0)
        self.assertEqual(st['conv_state']['id:1']['status'],'waiting_me')

    def test_p0_snapshot_text_cannot_establish_owner(self):
        text='您好希望进一步了解岗位方便沟通'
        item={'lastMessageInfo':{'showText':text,'msgTime':1000000}}
        self.assertIsNone(B.last_is_ours(self.cfg,{'greet_text':text},item))
        item['lastMessageInfo']['showText']='不合适'
        self.assertEqual(B.collect_rejects(self.cfg,{}, {'j':item}),{})

    def test_p0_owner_table_and_atomic_dom_parity(self):
        from conv_parse import parse_message_owner
        cases=[('item-mine','mine'),('item-self','mine'),('message-mine','mine'),
               ('item-friend','friend'),('item-peer','friend'),('message-friend','friend'),
               ('message-item','unknown'),('item-mine item-friend','unknown'),
               ('item-system item-mine','unknown'),('item-tip','unknown'),('item-time','unknown'),
               ('item-mine-other','unknown')]
        for classes,want in cases:
            with self.subTest(classes=classes):
                self.assertEqual(parse_message_owner(classes,'text'),want)
                br=Mock()
                br.click = None  # DOM fixture only: no trusted bridge capability.
                br.json.side_effect=lambda tab,expr,default=None: dom_eval(expr,messages=[{'classes':classes,'text':'text'}])['result']
                self.assertEqual(CD.active_conversation(br,1)['messages'][0]['who'],want)
                result=CD.atomic_chat_click(br,1,'id:1',TEXT)
                system = classes in ('item-system item-mine', 'item-tip', 'item-time')
                self.assertEqual(result.get('reason'), 'owner_unknown' if want=='unknown' and not system else '')

    def test_p0_unknown_arrives_during_fill_no_click(self):
        br=ChatBrowser(); fill=br.fill
        def mutate(*args):
            result=fill(*args)
            br.messages.append({'who':'unknown','text':'','classes':'message-item'})
            return result
        br.fill=mutate
        self.assertEqual(B.send_in_chat(br,1,TEXT,'方便','id:1',self.cfg),'send_aborted:owner_unknown')
        self.assertEqual(br.clicks,0)

    def test_p0_unknown_snapshot_inbox_counts_without_reject(self):
        now=HC.now_local(self.cfg)
        item={'encryptJobId':'j','conv_key':'id:1','brandName':'甲','name':'王',
              'lastMessageInfo':{'showText':'不合适','msgTime':int(now.timestamp()*1000)}}
        st=HS.load_state(self.cfg); br=Mock()
        br.network.return_value={'text':json.dumps({'code':0,'zpData':{'result':[item]}})}
        with patch.object(B,'scan_conv_states',return_value={'checked':0,'failed':0,'skipped_failures':0,'deadline':False}):
            result=B.inbox(self.cfg,br,1,st,[])
        self.assertEqual(result.get('owner_unknown'),1)
        self.assertEqual(st['inbox_seen'],[]); self.assertEqual(B.load_rejects(self.cfg),{})
        self.assertEqual(st['conv_state']['id:1']['owner'],'unknown')

    def context(self,text,facts,messages):
        return dict(facts=tuple(facts),text_sha256=hashlib.sha256(text.encode()).hexdigest(),
                    conv_key='id:1',op_id=B.reply_operation('id:1',messages)[1],
                    draft_fingerprint=CD.history_fingerprint(messages))

    def test_p1_1_unconfirmed_fact_stale_and_commitment_still_block(self):
        for text,facts,want in [('我负责全国渠道，岗位负责哪个区域？',['带6人团队'],'manual_required'),
                                ('我明天可以到岗，岗位负责哪个区域？',['明天可以到岗'],'manual_required')]:
            br=ChatBrowser(); context=self.context(text,facts,br.messages)
            self.assertEqual(B.send_in_chat(br,1,text,'x','id:1',self.cfg,'reply-to',fact_context=context),want)
            self.assertEqual(br.clicks,0)
        br=ChatBrowser(); context=self.context(TEXT,['带6人团队'],br.messages)
        br.messages.append({'who':'friend','text':'新问题','id':'h2'})
        self.assertEqual(B.send_in_chat(br,1,TEXT,'x','id:1',self.cfg,'reply-to',fact_context=context),'stale_draft')
        self.assertEqual(br.clicks,0)

    def test_p1_1_config_and_brief_facts_both_available(self):
        self.cfg['user']={'greeting':{'facts':['新签80家']}}
        text='我带6人团队，新签80家，岗位负责哪个区域？'
        with patch.object(G,'_generate_raw',return_value=(text,'')):
            result=R.generate(self.cfg,{},'带6人团队',force=True,history=ChatBrowser().messages)
        self.assertEqual(result['text'],text)
        self.assertEqual(self.cfg['user']['greeting']['facts'],['新签80家'])

    def test_p1_2_trusted_question_whitelist_extension(self):
        cfg=dict(self.cfg,user={'greeting':{'question_whitelist':[r'岗位是否包含海外渠道']}})
        self.assertTrue(G.fact_binding('岗位是否包含海外渠道？',cfg)[0])
        self.assertFalse(G.fact_binding('在深圳负责海外渠道是否合适？',cfg)[0])

    def test_p1_4_new_count_time_and_hash_evidence_resolves(self):
        self.pending(msg_count=1,last_message_id='h1',click_ts_epoch=1000000)
        br=ChatBrowser(); br.messages.append({'id':'m2','who':'mine','text':TEXT,'ts':'1001'})
        result=B.send_in_chat(br,1,TEXT,'方便','id:1',self.cfg,'reply-to')
        self.assertEqual(result,'last_turn_is_ours'); self.assertEqual(br.clicks,0)
        self.assertEqual(S.ledger_latest(self.cfg)[0]['status'],'sent')

    def test_p1_4_missing_timestamp_and_wrong_hash_cannot_confirm(self):
        row=dict(conv_key='id:1',msg_count=1,last_message_id='h1',click_ts_epoch=1000000,
                 text_sha256=hashlib.sha256(TEXT.encode()).hexdigest())
        for message in ({'who':'mine','text':TEXT,'ts':'999'},
                        {'who':'mine','text':TEXT},
                        {'who':'mine','text':'different','ts':'1001'},
                        {'who':'friend','text':TEXT,'ts':'1001'}):
            self.assertFalse(S.confirms_new_bubble(row,{'conv_key':'id:1','messages':[{'id':'h1'},message]}))

    def test_p1_4_pending_written_with_baseline(self):
        br=ChatBrowser()
        with patch.object(B.time,'sleep'):
            self.assertEqual(B.send_in_chat(br,1,TEXT,'方便','id:1',self.cfg),'replied')
        pending=json.loads((self.root/'send_ledger.jsonl').read_text().splitlines()[0])
        self.assertEqual((pending.get('msg_count'),pending.get('last_message_id'),pending.get('click_ts_epoch')),(1,'h1',1000000))

    def test_p1_5_backoff_tiers_are_capped(self):
        row={'conv_key':'id:1'}
        for failures,delay in ((5,1800),(11,7200),(20,7200)):
            st={'conv_state':{'id:1':{'failures':failures,'retry_after_epoch':100,'checked_ts':50}}}
            with patch.object(CD,'conversation_rows',return_value=[row]), patch.object(B,'read_status_message',side_effect=ValueError('read')):
                B.scan_conv_states(self.cfg,Mock(),1,st,now=100)
            self.assertEqual(st['conv_state']['id:1']['retry_after_epoch'],100+delay)

    def test_p1_1_reply_to_does_not_learn_operation_brief(self):
        import human as H
        br=ChatBrowser(); br.connect=lambda:'ok'; br.new_tab=lambda url:1; br.wake='ok'
        args=B.build_parser().parse_args(['reply-to','甲科技','--brief','带6人团队'])
        with patch.object(HC,'load_config',return_value=self.cfg), patch.object(B.CM,'Browser',return_value=br), \
             patch.object(CD,'goto_tab',return_value=True), patch.object(CD,'conversation_rows',return_value=[{'conv_key':'id:1','text':'甲科技'}]), \
             patch.object(CD,'open_by_index',return_value=('ok','甲科技',br.messages[:])), \
             patch.object(G,'_generate_raw',return_value=(TEXT,'')), patch.object(B.time,'sleep'), \
             patch.object(O,'cleanup_browser'), patch.object(B,'emit'):
            code=B.cmd_reply_to(args)
        self.assertEqual(code,0); self.assertEqual(br.clicks,1)
        self.assertFalse(Path(H.path_of(self.cfg)).exists(), 'one-operation facts must not become learned hints')

    def test_p1_1_cli_confirmed_brief_sends_actual_fact(self):
        br=ChatBrowser(); br.connect=lambda:'ok'; br.new_tab=lambda url:1; br.wake='ok'
        text='我带6人团队，岗位负责哪个区域？'
        original=br.json
        def transport(tab,expr,default=None):
            result=original(tab,expr,default)
            if '.click()' in expr and br.clicks:
                br.messages[-1]['text']=text
            return result
        br.json=transport
        args=B.build_parser().parse_args(['reply-to','甲科技','--brief','带6人团队'])
        with patch.object(HC,'load_config',return_value=self.cfg), patch.object(B.CM,'Browser',return_value=br), \
             patch.object(CD,'goto_tab',return_value=True), patch.object(CD,'conversation_rows',return_value=[{'conv_key':'id:1','text':'甲科技'}]), \
             patch.object(CD,'open_by_index',return_value=('ok','甲科技',br.messages[:])), \
             patch.object(G,'_generate_raw',return_value=(text,'')), patch.object(B.time,'sleep'), \
             patch.object(O,'cleanup_browser'), patch.object(B,'emit'):
            code=B.cmd_reply_to(args)
        self.assertEqual(code,0); self.assertEqual(br.clicks,1)
        self.assertEqual(S.ledger_latest(self.cfg)[0]['status'],'sent')

    def test_p1_4_legacy_guard_cannot_bypass_missing_baseline(self):
        self.pending()
        with self.assertRaisesRegex(RuntimeError,'ledger-ack'):
            S.ledger_guard(self.cfg,verify=lambda row: True)
        self.assertEqual(S.ledger_latest(self.cfg)[0]['status'],'unknown')
