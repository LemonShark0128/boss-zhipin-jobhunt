"""Round-three offline conversation state regressions."""
import contextlib
import io
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import Mock, patch
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
import chat_dom as CD
import boss_hunt as B
import hunt_config as HC
import hunt_schedule as HQ
import hunt_state as HS


class StateTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.cfg = {'paths': {'workdir': str(self.root), 'state': str(self.root/'state.json')},
                    'inbox': {'stale_hours': 72, 'status_scan_max': 2}}

    def update(self, prev, side, ts, now):
        from conv_state import update_conv_state
        return update_conv_state(prev, {'side': side, 'ts': ts, 'text': str(ts)}, now, self.cfg)

    def test_turns_and_status_since(self):
        a = self.update({}, 'them', 10, 11)
        self.assertEqual(a['status'], 'waiting_me')
        b = self.update(a, 'me', 20, 21)
        self.assertEqual(b['status'], 'waiting_them')
        c = self.update(b, 'them', 30, 31)
        self.assertEqual((c['status'], c['status_since'], c['checks']), ('waiting_me', 30, 3))

    def test_old_timestamp_and_unknown_do_not_advance(self):
        a = self.update({}, 'me', 100, 101)
        for side, ts in [('them', 99), (None, 500)]:
            b = self.update(a, side, ts, 999999)
            self.assertEqual({k: b[k] for k in a if k != 'checks'}, {k: a[k] for k in a if k != 'checks'})
            self.assertEqual(b['checks'], 2)
        self.assertEqual(self.update({}, None, 0, 100)['status'], 'unknown')

    def test_stale_boundary_once_and_new_outbound_resets(self):
        a = self.update({}, 'me', 100, 100 + 72*3600 - 1)
        self.assertEqual(a['status'], 'waiting_them')
        b = self.update(a, 'me', 100, 100 + 72*3600)
        self.assertEqual(b['status'], 'stale')
        c = self.update(b, 'me', 100, 100 + 80*3600)
        self.assertEqual(c['stale_since'], b['stale_since'])
        d = self.update(c, 'me', 100 + 81*3600, 100 + 81*3600)
        self.assertEqual(d['status'], 'waiting_them'); self.assertNotIn('stale_since', d)

    def test_report_counts_sort_limit_and_empty(self):
        states = {str(i): self.update({}, side, ts, 1000000) for i, (side, ts) in enumerate(
            [('them', 1), ('them', 2), ('me', 999999), ('me', 1), (None, 0)])}
        report = B.unanswered_report({'conv_state': states}, 1000000, 72, 1)
        self.assertEqual(report['counts'], dict(waiting_me=2, waiting_them=1, stale=1, unknown=1, pending_verify=0))
        self.assertEqual(report['items']['waiting_me'][0]['last_ts'], 2)
        self.assertEqual(B.unanswered_report({}, 100, 72, 5)['counts']['unknown'], 0)
        # hours changes the offline threshold, without mutating stored states.
        self.assertEqual(B.unanswered_report({'conv_state': states}, 1000000, 1000, 5)['counts']['stale'], 0)

    def test_unanswered_cli_is_offline_and_json(self):
        self.root.joinpath('state.json').write_text('{"conv_state":{}}')
        cfgfile=self.root/'config.json'; cfgfile.write_text(json.dumps(self.cfg))
        args=B.build_parser().parse_args(['--config',str(cfgfile),'unanswered','--json'])
        out=io.StringIO()
        with patch.object(B.CM,'Browser',side_effect=AssertionError('browser')), contextlib.redirect_stdout(out):
            self.assertEqual(args.func(args),0)
        data=json.loads(out.getvalue()); self.assertTrue(data['ok']); self.assertIn('items',data)

    def test_scan_priority_limit_failures_and_partial_deadline(self):
        states = {'a': {'status': 'waiting_them', 'checked_ts': 1},
                  'b': {'status': 'waiting_me', 'checked_ts': 9},
                  'c': {'status': 'waiting_me', 'checked_ts': 2},
                  'd': {'failures': 3}}
        st={'conv_state':states}
        rows=[{'conv_key':k,'brand':'公司'+k,'boss':'招聘者'} for k in 'abcd']
        with patch.object(CD,'conversation_rows',return_value=rows), patch.object(B,'read_status_message',return_value={'side':'them','ts':100,'text':'hi'}) as read:
            result=B.scan_conv_states(self.cfg,Mock(),1,st,now=200)
        self.assertEqual([c.args[2]['conv_key'] for c in read.call_args_list],['c','b'])
        self.assertEqual((result['checked'],result['skipped_failures']), (2,1))
        with patch.object(CD,'conversation_rows',return_value=rows), patch.object(B,'read_status_message',side_effect=ValueError('unknown_conversation')):
            for _ in range(3): B.scan_conv_states(self.cfg,Mock(),1,st,now=300)
        self.assertGreaterEqual(st['conv_state']['b']['failures'],3)
        st={'conv_state':{}}
        with patch.object(CD,'conversation_rows',return_value=rows), patch.object(B,'read_status_message',return_value={'side':'me','ts':100,'text':'hi'}), patch.object(HQ,'check_deadline',side_effect=[None,None,TimeoutError('deadline')]):
            result=B.scan_conv_states(self.cfg,Mock(),1,st,now=200)
        self.assertTrue(result['deadline']); self.assertEqual(result['checked'],1); self.assertEqual(len(st['conv_state']),1)

    def test_status_reader_header_and_dom_side(self):
        br=Mock(); row={'conv_key':'id:1','brand':'甲科技','boss':'王招聘'}
        with patch.object(CD,'conversation_rows',return_value=[row]), patch.object(CD,'open_by_index',return_value=('ok','',[])):
            br.json.return_value={'header':'乙公司 李先生','side':'them','ts':100,'text':'hi'}
            with self.assertRaisesRegex(ValueError,'unknown_conversation'): B.read_status_message(br,1,row)
            br.json.return_value={'header':'甲科技 王招聘','classes':'item-self','ts':100,'text':'hello'}
            self.assertEqual(B.read_status_message(br,1,row)['side'],'me')
        expr=br.json.call_args.args[1]
        self.assertIn('classes:el ? el.className',expr); self.assertIn('.message-content',expr); self.assertIn('.chat-title',expr)

    def test_stale_reject_once_explicit_precedence_and_corruption(self):
        st={'sent':{'j':{'brand':'甲','name':'经理','grade':'A','ts':'2026-01-01 00:00'}},
            'conv_state':{'id:1':dict(self.update({},'me',100,1000000),job_id='j',brand='甲')}}
        B.collect_stale_rejects(self.cfg,st,now=1000000)
        p=self.root/'rejects.json'; data=json.loads(p.read_text()); row=next(iter(data.values()))
        self.assertEqual(row['kind'],'stale_no_reply'); self.assertEqual(row['last_out_ts'],100)
        self.assertEqual(row['reason'],'我已回复后72h无回应')
        before=p.read_bytes(); B.collect_stale_rejects(self.cfg,st,now=2000000); self.assertEqual(p.read_bytes(),before)
        p.write_text(json.dumps({'j':{'kind':'explicit'}})); B.collect_stale_rejects(self.cfg,st,now=2000000)
        self.assertEqual(json.loads(p.read_text()),{'j':{'kind':'explicit'}})
        for raw in ('{bad','{"j":[]}', '[]'):
            p.write_text(raw)
            with self.assertRaises(ValueError): B.collect_stale_rejects(self.cfg,st,now=2000000)
            self.assertEqual(p.read_text(),raw)

    def test_scan_uses_exact_snapshot_time_and_job_metadata(self):
        rows=[{'conv_key':'id:1','brand':'甲科技','boss':'王招聘'},
              {'conv_key':'id:2','brand':'乙科技','boss':'李招聘'}]
        items={'j':{'brandName':'甲科技','name':'王招聘','lastTS':1000000000000},
               'k':{'brandName':'乙科技','name':'李招聘','lastTS':1000000001000}}
        st={}
        def read(br,tab,row):
            return {'side':'me','ts':row.get('last_ts',0),'text':'手动回复'}
        with patch.object(CD,'conversation_rows',return_value=rows),patch.object(B,'read_status_message',side_effect=read) as reader:
            B.scan_conv_states(self.cfg,Mock(),1,st,now=1000001000,items=items)
        self.assertEqual(reader.call_args_list[0].args[2]['job_id'],'k')
        self.assertEqual(st['conv_state']['id:1']['last_ts'],1000000000)
        self.assertEqual(st['conv_state']['id:1']['job_id'],'j')

    def test_status_reader_retains_dom_side_with_snapshot_timestamp(self):
        br=Mock(); row={'conv_key':'id:1','brand':'甲科技','boss':'王招聘','last_ts':100}
        with patch.object(CD,'conversation_rows',return_value=[row]), patch.object(CD,'open_by_index',return_value=('ok','',[])):
            br.json.return_value={'header':'甲科技','classes':'message-mine','ts':0,'text':'手动回复'}
            result=B.read_status_message(br,1,row)
        self.assertEqual((result['side'],result['ts']),('me',100))

    def test_inbox_partial_scan_persists_and_never_sends(self):
        self.cfg['paths']['raw_dir']=str(self.root/'raw'); self.root.joinpath('raw').mkdir()
        st=HS.load_state(self.cfg); br=Mock(); br.network.return_value={'text':json.dumps({'code':0,'zpData':{'result':[]}})}
        def scan(*args,**kwargs):
            st['conv_state']['id:1']=self.update({},'them',100,200)
            return {'checked':1,'deadline':True,'failed':0,'skipped_failures':0}
        out=[]
        with patch.object(B,'scan_conv_states',side_effect=scan),patch.object(B,'send_in_chat') as send:
            result=B.inbox(self.cfg,br,1,st,out)
        self.assertTrue(result['status_scan']['deadline']); send.assert_not_called()
        self.assertIn('id:1',json.loads(self.root.joinpath('state.json').read_text())['conv_state'])
        self.assertTrue(any('待我回 1' in line for line in out))

    def test_no_reply_reason_is_distinct_and_locked_atomic_failure_preserves(self):
        from datetime import datetime
        now=datetime(2026,10,27,tzinfo=HC.cfg_tz(self.cfg))
        st={'sent':{'j':{'brand':'甲科技','name':'经理','ts':'2026-10-20 00:00','greet_text':'您好希望进一步了解岗位方便沟通'}}}
        items={'j':{'brandName':'甲科技','lastMessageInfo':{'showText':'您好希望进一步了解岗位方便沟通','fromSelf':True,'msgTime':int(now.timestamp()*1000)}}}
        rows=B.collect_rejects(self.cfg,st,items,now=now)
        self.assertEqual(rows['j']['reason'],'投递后72h无回应')
        st['conv_state']={'id:1':dict(self.update({},'me',100,1000000),job_id='j')}
        before=self.root.joinpath('rejects.json').read_bytes()
        with patch.object(B.os,'replace',side_effect=OSError('atomic failure')):
            with self.assertRaises(OSError):B.collect_stale_rejects(self.cfg,st,now=1000000)
        self.assertEqual(self.root.joinpath('rejects.json').read_bytes(),before)
        self.assertFalse(list(self.root.glob('*.tmp.*')))

    def test_explicit_upgrades_conv_key_reject_without_duplicate(self):
        self.cfg['paths']['raw_dir']=str(self.root)
        now=HC.now_local(self.cfg)
        key='pair:甲科技|王招聘'
        st={'conv_state':{key:dict(self.update({},'me',100,1000000),brand='甲科技',boss='王招聘')}}
        B.collect_stale_rejects(self.cfg,st,now=1000000)
        rows=B.collect_rejects(self.cfg,st,{'j':{'conv_key':key,'brandName':'甲科技','name':'王招聘',
            'lastMessageInfo':{'showText':'不合适','fromSelf':False,'msgTime':int(now.timestamp()*1000)}}},now=now)
        self.assertEqual(len(rows),1)
        self.assertEqual(next(iter(rows.values()))['kind'],'explicit')

    def test_old_no_reply_can_upgrade_to_stale_after_real_reply(self):
        key='pair:甲科技|王招聘'
        self.root.joinpath('rejects.json').write_text(json.dumps({'j':{'kind':'no_reply','conv_key':key,
            'reason':'投递后72h无回应'}}))
        st={'sent':{},'conv_state':{key:dict(self.update({},'me',100,1000000),job_id='j')}}
        rows=B.collect_stale_rejects(self.cfg,st,now=1000000)
        self.assertEqual(len(rows),1);self.assertEqual(rows['j']['kind'],'stale_no_reply')

    def test_unanswered_human_output_sorted_and_default_limit(self):
        states={str(i):self.update({},'them',i+1,100) for i in range(8)}
        self.root.joinpath('state.json').write_text(json.dumps({'conv_state':states}))
        cfgfile=self.root/'config.json';cfgfile.write_text(json.dumps(self.cfg))
        args=B.build_parser().parse_args(['--config',str(cfgfile),'unanswered'])
        out=io.StringIO()
        with contextlib.redirect_stdout(out):args.func(args)
        lines=out.getvalue().splitlines();self.assertIn('待我回 8',lines[0]);self.assertEqual(sum('  · ' in x for x in lines),5)
        self.assertIn('｜8',next(x for x in lines if '  · ' in x))
