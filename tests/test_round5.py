"""Round five regressions: temp stores, browser/model doubles only."""
import contextlib
import hashlib
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
import hunt_state as HS
import outbound as O
import greet as G
import reply as R
import send_safety as S
from conv_state import update_conv_state
from test_round4 import ChatBrowser, TEXT

class Round5(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        self.root=Path(self.tmp.name)
        self.cfg={'paths':{'workdir':str(self.root),'state':str(self.root/'state.json'),'raw_dir':str(self.root/'raw')},'user':{},'_clock':lambda:1000}
        (self.root/'raw').mkdir()
    def event(self, **kw):
        row=dict(event_id='e',status='sent',cmd='reply-to',conv_key='id:1',text=TEXT,text_sha256=hashlib.sha256(TEXT.encode()).hexdigest(),ts='1970-01-01 08:16',ts_epoch=1000000)
        row.update(kw); S.ledger_append(self.cfg,row); return row
    def cli(self,*words):
        path=self.root/'config.json'; path.write_text(json.dumps({k:v for k,v in self.cfg.items() if k!='_clock'}))
        args=B.build_parser().parse_args(['--config',str(path),*words,'--json']); out=io.StringIO()
        with contextlib.redirect_stdout(out): code=args.func(args)
        return code,json.loads(out.getvalue())
    def test_p0_force_stops_before_generation(self):
        with patch.object(G,'_generate_raw',side_effect=AssertionError('must not generate')):
            result=R.generate(self.cfg,{},'带6人团队',force=True,history=[{'who':'mine','text':TEXT}])
        self.assertEqual(result['reasons'],['last_turn_is_ours'])
    def test_p0_retry_without_ids_never_clicks_again(self):
        br=ChatBrowser(); br.messages[0].pop('id')
        with patch.object(B.time,'sleep'):
            self.assertEqual(B.send_in_chat(br,1,TEXT,'方便','id:1',self.cfg,'reply-to'),'replied')
            result=B.send_in_chat(br,1,TEXT,'方便','id:1',self.cfg,'reply-to')
        self.assertEqual(result,'duplicate_op'); self.assertEqual(br.clicks,1)
        self.assertEqual(len(S.ledger_latest(self.cfg)),1)
    def test_p0_new_turn_is_explicit_and_journalled(self):
        br=ChatBrowser(); br.messages.append({'who':'mine','text':'前一条'})
        with patch.object(B.time,'sleep'):
            self.assertEqual(B.send_in_chat(br,1,TEXT,'方便','id:1',self.cfg,'reply-to'),'last_turn_is_ours')
            self.assertEqual(br.clicks,0)
            self.assertEqual(B.send_in_chat(br,1,TEXT,'方便','id:1',self.cfg,'reply-to',new_turn=True,op_id='additional-1'),'replied')
            self.assertEqual(B.send_in_chat(br,1,TEXT,'方便','id:1',self.cfg,'reply-to',new_turn=True,op_id='additional-1'),'duplicate_op')
        self.assertEqual(br.clicks,1); self.assertEqual(S.ledger_latest(self.cfg)[0]['reason'],'explicit_new_turn')
    def test_p0_success_state_save_failure_retry_repairs(self):
        br=ChatBrowser(); br.messages.append({'who':'mine','text':TEXT}); self.event(op_id='operation-1',trigger='h1')
        br.connect=lambda:'ok'; br.new_tab=lambda url:1; br.wake='ok'
        with patch.object(B.CM,'Browser',return_value=br),patch.object(CD,'goto_tab',return_value=True),patch.object(CD,'conversation_rows',return_value=[{'conv_key':'id:1','text':'甲科技'}]),patch.object(CD,'open_by_index',return_value=('ok','甲科技',br.messages)),patch.object(G,'_generate_raw',side_effect=AssertionError('must not regenerate')),patch.object(O,'cleanup_browser'),patch.object(B.time,'sleep'):
            code,data=self.cli('reply-to','甲科技','--brief','方便','--op-id','operation-1')
        self.assertEqual(code,0); self.assertEqual(data['result'],'duplicate_op'); self.assertEqual(br.clicks,0)
        self.assertIn('e',json.loads((self.root/'state.json').read_text())['auto_replies'])
    def test_p1_1_claim_without_numbers_needs_fact_binding(self):
        cfg=dict(self.cfg,user={'greeting':{'facts':['带6人团队','新签80家']}})
        for text in ('我曾在腾讯负责全国渠道，带6人团队，新签80家','我负责过全国渠道，方便说说岗位负责哪个区域？'):
            ok,reasons=G.basic_safety(text,cfg); self.assertFalse(ok); self.assertTrue(any('fact_not_bound' in r for r in reasons))
        ok,reasons=R.check('我负责过全国渠道，方便说说岗位负责哪个区域？',cfg)
        self.assertFalse(ok); self.assertTrue(any('fact_not_bound' in r for r in reasons))
        self.assertTrue(R.check('方便说说岗位负责哪个区域？',cfg)[0])
        self.assertTrue(R.check('我带6人团队，岗位负责哪个区域？',cfg,allow_numbers=['8'])[0])
    def test_p1_1_normal_reply_is_controlled_template(self):
        with patch.object(G,'_generate_raw',return_value=('我负责过全国渠道，岗位负责哪个区域？','')) as gen:
            result=R.generate(self.cfg,{},'方便聊聊吗？')
        self.assertTrue(result['text']); self.assertEqual(result['source'],'template'); self.assertEqual(gen.call_count,0)
    def test_p1_2_nearby_snapshot_is_unknown_and_listed(self):
        now=HC.now_local(self.cfg)
        item={'encryptJobId':'j','conv_key':'id:1','brandName':'甲','name':'王','lastMessageInfo':{'showText':'请给几个面试时段','msgTime':int(now.timestamp()*1000)}}
        sent={'ts':now.strftime('%Y-%m-%d %H:%M'),'brand':'甲'}
        self.assertIsNone(B.last_is_ours(self.cfg,sent,item))
        st=HS.load_state(self.cfg); st['sent']={'j':sent}; st['conv_state']={'id:1':{'status':'stale','last_side':'me','last_ts':1,'job_id':'j'}}
        br=Mock(); br.network.return_value={'text':json.dumps({'code':0,'zpData':{'result':[item]}})}; out=[]
        with patch.object(B,'scan_conv_states',return_value={'checked':0,'failed':0,'skipped_failures':0,'deadline':False}): result=B.inbox(self.cfg,br,1,st,out)
        self.assertEqual(result['unanswered']['counts']['unknown'],1); self.assertEqual(result['interviews'],[]); self.assertEqual(st['replied'],[])
        self.assertEqual(B.load_rejects(self.cfg),{}); self.assertTrue(any('待核对 1' in s for s in out))
        code,data=self.cli('unanswered','--unknown'); self.assertEqual(code,0); self.assertEqual(len(data['items']['unknown']),1)
    def test_p1_3_same_minute_reply_advances_with_epoch(self):
        self.cfg['_clock']=lambda:1000.789
        st={'conv_state':{'id:1':{'status':'waiting_me','last_side':'them','last_ts':1000.1,'last_text':'HR','last_seq':1}}}
        S.ledger_send(self.cfg,'reply-to','id:1','',TEXT,lambda:'replied',{'message_seq':2}); HS.remember_reply(self.cfg,st,'id:1','reply-to')
        self.assertEqual(S.ledger_latest(self.cfg)[0].get('ts_epoch'),1000789)
        self.assertEqual(st['conv_state']['id:1']['status'],'waiting_them'); self.assertEqual(st['conv_state']['id:1']['last_text'],TEXT)
        state=update_conv_state(st['conv_state']['id:1'],{'side':'them','ts_epoch':1000789,'seq':3,'text':'新消息'},1001,self.cfg)
        self.assertEqual(state['status'],'waiting_me')
    def test_p1_3_legacy_collision_never_becomes_stale(self):
        self.event(); path=self.root/'send_ledger.jsonl'; row=json.loads(path.read_text()); row.pop('ts_epoch'); path.write_text(json.dumps(row)+'\n')
        st={'conv_state':{'id:1':{'last_ts':1000.1,'last_side':'them','last_text':'HR','status':'waiting_me'}}}
        HS.remember_reply(self.cfg,st,'id:1','reply-to'); state=st['conv_state']['id:1']
        self.assertTrue(state.get('ts_precision_coarse')); self.assertEqual(state['status'],'waiting_them')
        self.assertEqual(B.unanswered_report(st,now=1000+100*3600)['counts']['stale'],0)
        self.assertEqual(B.collect_stale_rejects(self.cfg,st,now=1000+100*3600),{})
    def test_p1_4_pending_guard_gives_ack_command(self):
        self.event(status='unknown')
        with self.assertRaisesRegex(RuntimeError,'ledger-ack'): O.send_one(Mock(),1,{'encryptJobId':'j'},self.cfg)
    def test_p1_4_ack_evidence_and_history(self):
        row=self.event(status='unknown'); code,data=self.cli('ledger-ack','--list')
        self.assertEqual(code,0); self.assertEqual(data['items'][0]['event_id'],'e')
        code,data=self.cli('ledger-ack','e','--sent','--conv-key','wrong','--text-sha256',row['text_sha256'])
        self.assertEqual(code,1); self.assertEqual(data['error'],'ack_mismatch')
        code,data=self.cli('ledger-ack','e','--sent','--conv-key','id:1','--text-sha256',row['text_sha256'],'--note','核对气泡')
        self.assertEqual(code,0); S.ledger_guard(self.cfg)
        lines=[json.loads(x) for x in (self.root/'send_ledger.jsonl').read_text().splitlines()]
        self.assertEqual([r['status'] for r in lines],['unknown','sent']); self.assertEqual(lines[-1]['note'],'核对气泡')
    def test_p1_4_void_quarantine_converges(self):
        self.event(status='pending')
        with (self.root/'send_ledger.jsonl').open('ab') as f:f.write(b'{bad')
        S.ledger_repair(self.cfg,apply=True)
        for row in list(S.ledger_latest(self.cfg)):
            code,data=self.cli('ledger-ack',row['event_id'],'--not-sent','--note','人工核对未发送'); self.assertEqual(code,0)
        S.ledger_guard(self.cfg); self.assertTrue(list(self.root.glob('send_ledger.corrupt-*')))
        self.assertTrue(all(r['status']=='voided' for r in S.ledger_latest(self.cfg)))
    def test_p1_4_production_verifies_own_bubble(self):
        self.event(status='unknown'); br=ChatBrowser(); br.messages.append({'who':'mine','text':TEXT})
        with patch.object(B.time,'sleep'), self.assertRaisesRegex(RuntimeError,'ledger-ack'):
            B.send_in_chat(br,1,TEXT,'方便','id:1',self.cfg,'reply-to',op_id='fresh')
        self.assertEqual(S.ledger_latest(self.cfg)[0]['status'],'unknown'); self.assertEqual(br.clicks,0)
    def test_p1_5_new_process_observes_gap(self):
        self.cfg['sending']={'min_gap_seconds':60}; self.event(ts_epoch=980000)
        path=self.root/'config.json'; path.write_text(json.dumps({k:v for k,v in self.cfg.items() if k!='_clock'}))
        program="import json,sys;sys.path.insert(0,sys.argv[1]);import send_safety as S;cfg=json.load(open(sys.argv[2]));cfg['_clock']=lambda:1000;print(S.ledger_send(cfg,'send','job:j','j','hello',lambda:(_ for _ in ()).throw(AssertionError('clicked too early'))))"
        run=subprocess.run([sys.executable,'-c',program,str(Path(B.__file__).parent),str(path)],capture_output=True,text=True)
        self.assertEqual(run.returncode,0,run.stderr); self.assertEqual(run.stdout.strip(),'skip_min_gap(40)')
    def test_p1_5_no_history_no_wait(self):
        self.cfg['sending']={'min_gap_seconds':60}
        self.assertEqual(S.ledger_send(self.cfg,'send','job:j','j','hello',lambda:'sent'),'sent')
    def test_p0_explicit_new_operation_after_success_sends_once(self):
        br = ChatBrowser()
        with patch.object(B.time, 'sleep'):
            self.assertEqual(B.send_in_chat(br, 1, TEXT, '方便', 'id:1', self.cfg,
                                           'reply-to', op_id='first'), 'replied')
            self.assertEqual(B.send_in_chat(br, 1, TEXT, '方便', 'id:1', self.cfg,
                                           'reply-to', new_turn=True, op_id='second'), 'replied')
            self.assertEqual(B.send_in_chat(br, 1, TEXT, '方便', 'id:1', self.cfg,
                                           'reply-to', new_turn=True, op_id='second'), 'duplicate_op')
        self.assertEqual(br.clicks, 2)
        self.assertEqual(len(S.ledger_latest(self.cfg)), 2)
