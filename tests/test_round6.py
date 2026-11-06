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
import greet as G
import send_safety as S
from conv_state import update_conv_state
from test_round4 import TEXT, dom_eval


class Round6(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.cfg = {'paths': {'workdir': str(self.root), 'state': str(self.root/'state.json'),
                             'raw_dir': str(self.root/'raw')}, 'user': {}, '_clock': lambda: 1000}
        (self.root/'raw').mkdir()

    def test_p0_unknown_replaces_old_state(self):
        prev = update_conv_state({}, {'side':'me','ts':1,'text':'old'}, 2, self.cfg)
        state = update_conv_state(prev, {'owner':'unknown','side':None,'ts':3,'text':'system'}, 4, self.cfg)
        report = B.unanswered_report({'conv_state':{'id:1':state}}, 1000000)
        self.assertEqual(state.get('owner'),'unknown')
        self.assertEqual([report['counts'][k] for k in ('waiting_me','waiting_them','stale','unknown')],[0,0,0,1])

    def test_p0_no_old_default_friend_parser(self):
        source = Path(B.__file__).read_text()
        self.assertIsNone(re.search(r"\?\s*'mine'\s*:\s*'friend'", source))

    def test_p1_2_question_claim_not_exempt(self):
        ok,reasons = G.fact_binding('在腾讯担任总监是否符合要求？',self.cfg)
        self.assertFalse(ok); self.assertTrue(any('fact_not_bound' in r for r in reasons))
        self.assertTrue(G.fact_binding('这个岗位负责哪个区域？',self.cfg)[0])
        cfg = dict(self.cfg,user={'greeting':{'facts':['带6人团队']}})
        self.assertTrue(G.basic_safety('我带6人团队，这个岗位负责哪个区域？',cfg)[0])

    def pending(self, **extra):
        row = dict(event_id='e',status='unknown',cmd='reply-to',conv_key='id:1',
                   text=TEXT,text_sha256=hashlib.sha256(TEXT.encode()).hexdigest(),ts='x',**extra)
        S.ledger_append(self.cfg,row)

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

    def test_p1_2_trusted_question_whitelist_extension(self):
        cfg=dict(self.cfg,user={'greeting':{'question_whitelist':[r'岗位是否包含海外渠道']}})
        self.assertTrue(G.fact_binding('岗位是否包含海外渠道？',cfg)[0])
        self.assertFalse(G.fact_binding('在深圳负责海外渠道是否合适？',cfg)[0])

    def test_p1_4_missing_timestamp_and_wrong_hash_cannot_confirm(self):
        row=dict(conv_key='id:1',msg_count=1,last_message_id='h1',click_ts_epoch=1000000,
                 text_sha256=hashlib.sha256(TEXT.encode()).hexdigest())
        for message in ({'who':'mine','text':TEXT,'ts':'999'},
                        {'who':'mine','text':TEXT},
                        {'who':'mine','text':'different','ts':'1001'},
                        {'who':'friend','text':TEXT,'ts':'1001'}):
            self.assertFalse(S.confirms_new_bubble(row,{'conv_key':'id:1','messages':[{'id':'h1'},message]}))

    def test_p1_5_backoff_tiers_are_capped(self):
        row={'conv_key':'id:1'}
        for failures,delay in ((5,1800),(11,7200),(20,7200)):
            st={'conv_state':{'id:1':{'failures':failures,'retry_after_epoch':100,'checked_ts':50}}}
            with patch.object(CD,'conversation_rows',return_value=[row]), patch.object(B,'read_status_message',side_effect=ValueError('read')):
                B.scan_conv_states(self.cfg,Mock(),1,st,now=100)
            self.assertEqual(st['conv_state']['id:1']['retry_after_epoch'],100+delay)

    def test_p1_4_legacy_guard_cannot_bypass_missing_baseline(self):
        self.pending()
        with self.assertRaisesRegex(RuntimeError,'ledger-ack'):
            S.ledger_guard(self.cfg,verify=lambda row: True)
        self.assertEqual(S.ledger_latest(self.cfg)[0]['status'],'unknown')
