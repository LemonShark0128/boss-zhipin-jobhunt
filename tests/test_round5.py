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
import boss_hunt as B
import hunt_config as HC
import hunt_state as HS
import outbound as O
import send_safety as S
from test_round4 import TEXT

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
    def test_p1_2_nearby_snapshot_is_unknown_and_listed(self):
        now=HC.now_local(self.cfg)
        item={'encryptJobId':'j','conv_key':'id:1','brandName':'甲','name':'王','lastMessageInfo':{'showText':'请给几个面试时段','msgTime':int(now.timestamp()*1000)}}
        sent={'ts':now.strftime('%Y-%m-%d %H:%M'),'brand':'甲'}
        self.assertIsNone(B.last_is_ours(self.cfg,sent,item))
        st=HS.load_state(self.cfg); st['sent']={'j':sent}; st['conv_state']={'id:1':{'status':'stale','last_side':'me','last_ts':1,'job_id':'j'}}
        br=Mock(); br.network.return_value={'text':json.dumps({'code':0,'zpData':{'result':[item]}})}; out=[]
        with patch.object(B,'scan_conv_states',return_value={'checked':0,'failed':0,'skipped_failures':0,'deadline':False}): result=B.inbox(self.cfg,br,1,st,out)
        self.assertEqual(result['unanswered']['counts']['unknown'],1); self.assertEqual(result['interviews'],[]); self.assertEqual(st['replied'],[])
        self.assertEqual(B.load_rejects(self.cfg),{}); self.assertFalse(any('待核对' in s for s in out))  # audit-01 Q1：runs.log 不再每跳报待核对数，计数仍在 JSON
        code,data=self.cli('unanswered','--unknown'); self.assertEqual(code,0); self.assertEqual(len(data['items']['unknown']),1)
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
    def test_p1_5_new_process_observes_gap(self):
        self.cfg['sending']={'min_gap_seconds':60}; self.event(ts_epoch=980000)
        path=self.root/'config.json'; path.write_text(json.dumps({k:v for k,v in self.cfg.items() if k!='_clock'}))
        program="import json,sys;sys.path.insert(0,sys.argv[1]);import send_safety as S;cfg=json.load(open(sys.argv[2]));cfg['_clock']=lambda:1000;print(S.ledger_send(cfg,'send','job:j','j','hello',lambda:(_ for _ in ()).throw(AssertionError('clicked too early'))))"
        run=subprocess.run([sys.executable,'-c',program,str(Path(B.__file__).parent),str(path)],capture_output=True,text=True)
        self.assertEqual(run.returncode,0,run.stderr); self.assertEqual(run.stdout.strip(),'skip_min_gap(40)')
    def test_p1_5_no_history_no_wait(self):
        self.cfg['sending']={'min_gap_seconds':60}
        self.assertEqual(S.ledger_send(self.cfg,'send','job:j','j','hello',lambda:'sent'),'sent')