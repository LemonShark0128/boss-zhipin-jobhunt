"""Phase A 离线回归：只构造临时数据，浏览器动作全部用替身。"""
import copy
import json
import os
import sys
import tempfile
import unittest
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
import boss_hunt as B
import hunt_config as HC
import hunt_filter as HF
import hunt_schedule as HQ
import hunt_state as HS
import outbound as O


def grading():
    return {'enabled': True, 'roles': ['城市经理'], 'tracks': {
        'A': {'industries': ['示例平台行业'], 'brands': [], 'jd_signals': ['商户拓展', '开城'], 'min_hits': 2},
        'B': {'industries': ['示例品牌行业'], 'brands': [], 'jd_signals': ['门店运营', '加盟商', '督导'], 'min_hits': 1}},
        'no_signal': 'C',
        'unknown_side': 'B', 'require_role_for_ab': True}


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix='boss-test-')
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.cfg = {'grading': grading(), 'filters': {}, 'scoring': {},
                    'user': {'years_experience': 5, 'greeting_template': '您好，我有{years}年经验，看到在招{job_name}，方便详细聊聊。'},
                    'targets': [{'name': '示例方向', 'city': '示例城市', 'city_code': '100000000', 'keywords': ['城市经理']}],
                    'sending': {'daily_cap': 30, 'grade_quota': {'A': 22, 'B': 8}, 'carry_over': True, 'max_per_window': 8, 'min_gap_seconds': 60},
                    'schedule': {'timezone': 'Asia/Shanghai', 'windows': ['08:00','10:00','12:00','14:00','16:00','18:00','20:00','22:00'], 'per_window': 0},
                    'inbox': {'interview_auto_reply': False, 'followup': {'enabled': False}},
                    'paths': {'workdir': str(self.root / 'data')}}
        self.path = self.root / 'config.json'
        self.path.write_text(json.dumps(self.cfg))
        self.cfg = HC.load_config(str(self.path))

    def job(self, jid='j1', industry='示例平台行业'):
        return {'encryptJobId': jid, 'jobName': '城市经理', 'brandName': '示例公司'+jid,
                'bossName': '招聘者'+jid, 'brandIndustry': industry, 'cityName': '示例城市', 'salaryDesc': '20-30K'}

    def raw(self, jobs):
        Path(self.cfg['paths']['raw_dir'], 'sample.json').write_text(json.dumps({'code': 0, 'zpData': {'jobList': jobs}}))


class GradingTests(Base):
    def test_two_stage_role_and_tie(self):
        j = self.job()
        before = copy.deepcopy((j, self.cfg))
        self.assertEqual(HF.grade_job(j, self.cfg)['grade'], 'A?')
        self.assertEqual(HF.grade_job(j, self.cfg, '商户拓展开城')['grade'], 'A')
        self.assertEqual(HF.grade_job(j, self.cfg, '门店运营加盟商督导')['grade'], 'B')
        # 无信号不再兜到 B（实测 24 条真实 JD 里 15 条是这么误投的），按 no_signal=C 拦下
        self.assertEqual(HF.grade_job(j, self.cfg, '普通职责')['grade'], 'C')
        self.assertEqual(HF.grade_job(j, self.cfg, '门店运营')['grade'], 'B')
        self.assertEqual(HF.grade_job(j, self.cfg, '商户拓展')['grade'], 'C')  # A 要 2 个信号
        self.assertIn('开城', HF.grade_job(j, self.cfg, '开城')['reason'])
        self.assertEqual((j, self.cfg), before)
        j['jobName'] = '其它职能'
        self.assertEqual(HF.grade_job(j, self.cfg, '开城')['grade'], 'C')

    def test_tracks_and_candidates(self):
        self.cfg['grading']['tracks']['D'] = {'jd_signals': ['研发合作'], 'industries': ['示例研发行业']}
        self.assertEqual(HF.grade_job(self.job(), self.cfg, '研发合作')['grade'], 'D')
        c = self.job('c'); c['jobName'] = '其它职能'
        result = HS.candidates(self.cfg, [c, self.job('b', '示例品牌行业'), self.job('a')])
        self.assertEqual([x['grade'] for x in result], ['A?', 'B?', 'C'])
        self.assertFalse(result[0]['grade_final'])

    def test_legacy(self):
        self.cfg.pop('grading')
        self.assertEqual(HQ.validate_cfg(self.cfg)[0], [])
        self.assertIn(HS.candidates(self.cfg, [self.job()])[0]['grade'], ['A', 'B'])


class QuotaTests(Base):
    def test_formula_and_fixed_window(self):
        st = HS.load_state(self.cfg); st['day_by_grade'] = {'A': 18, 'B': 0}
        now = datetime.fromisoformat(st['day']+'T12:00').replace(tzinfo=HC.cfg_tz(self.cfg))
        self.assertEqual(HQ.quotas_for_now(self.cfg, st, now), {'A': 1, 'B': 2})
        self.assertEqual(HQ.quotas_for_now(self.cfg, st, now.replace(hour=14)), {'A': 1, 'B': 2})
        self.assertEqual(HQ.quotas_for_now(self.cfg, st, now.replace(hour=22)), {'A': 4, 'B': 8})
        self.cfg['sending']['carry_over'] = False; self.cfg['schedule']['per_window'] = 3
        self.assertEqual(HQ.quotas_for_now(self.cfg, st, now), {'A': 3, 'B': 3})

    def test_send_quota_and_pool_without_repeated_jd(self):
        self.raw([self.job('a'+str(i)) for i in range(30)] + [self.job('b'+str(i), '示例品牌行业') for i in range(15)])
        st=HS.load_state(self.cfg)
        now=HC.now_local(self.cfg).replace(hour=22,minute=0)
        def inspect(br, tab, job, cfg):
            return 'ready', '商户拓展与开城' if job['encryptJobId'].startswith('a') else '门店运营'
        with patch.object(O, 'inspect_job', side_effect=inspect) as reader, patch.object(O, 'send_one', return_value='sent'), patch.object(B.time, 'sleep'), patch.object(HC, 'now_local', return_value=now):
            results=[]
            for _ in range(6):
                results.extend(O.do_send(self.cfg,None,None,st,30,[])['items'])
            self.assertEqual(st['day_count'],30)
            self.assertEqual(st['day_by_grade'],{'A':22,'B':8})
            # 配额按比例压进 max_per_window：22:00 剩 A8/B8 时本跳是 4A+4B，A 只保证先发，不再吃满预算
            self.assertEqual([x['grade'] for x in results[:4]],['A']*4)
            self.assertEqual(len(st['sent']),30)
            self.assertLessEqual(reader.call_count,45)
            self.assertTrue(st['b_pool'])
        self.assertTrue(Path(self.cfg['paths']['state']).exists())

    def test_failed_send_does_not_charge_or_contact(self):
        self.raw([self.job()]); st=HS.load_state(self.cfg)
        with patch.object(O,'inspect_job',return_value=('ready','商户拓展与开城')), patch.object(O,'send_one',return_value='skip_no_button'), patch.object(B.time,'sleep'):
            O.do_send(self.cfg,None,None,st,1,[])
        self.assertEqual(st['day_count'],0); self.assertEqual(st['contacts'],[])

    def test_empty_jd_and_changed_hash(self):
        st=HS.load_state(self.cfg); j=self.job()
        with patch.object(O,'inspect_job',return_value=('ready','')):
            self.assertEqual(O.prepare_grade(self.cfg,None,None,j,st)['grade'],'C')
        with patch.object(O,'inspect_job',return_value=('ready','商户拓展与开城')):
            self.assertEqual(O.prepare_grade(self.cfg,None,None,j,st)['grade'],'A')
        old=st['grade_cache']['j1']['jd_hash']
        j['brandName']='更改后的公司'
        with patch.object(O,'inspect_job',return_value=('ready','门店运营')):
            self.assertEqual(O.prepare_grade(self.cfg,None,None,j,st)['grade'],'B')
        self.assertNotEqual(old,st['grade_cache']['j1']['jd_hash'])

class TickTests(Base):
    def test_actions_and_tolerance(self):
        self.cfg['schedule'].update(scan_windows=['08:00','14:00','20:00'], inbox_windows=['10:00','16:00','22:00'], summary_window='22:00')
        now=HC.now_local(self.cfg)
        for hour, actions in [(8,['scan','send']),(10,['send','inbox']),(12,['send']),(14,['scan','send']),(22,['send','inbox','summary']),(4,[])]:
            plan=HQ.tick_plan(self.cfg,now.replace(hour=hour,minute=0))
            self.assertEqual(plan['actions'],actions)
        self.assertEqual(HQ.tick_plan(self.cfg,now.replace(hour=8,minute=10))['window'],'08:00')
        self.assertEqual(HQ.tick_plan(self.cfg,now.replace(hour=8,minute=11))['actions'],[])

    def test_lock_and_failed_login_cleanup(self):
        self.cfg['schedule'].update(windows=['08:00'],scan_windows=['08:00'])
        now=HC.now_local(self.cfg).replace(hour=8,minute=0)
        st=HS.load_state(self.cfg)
        browser=unittest.mock.Mock();browser.connect.return_value='bridge_ok'
        browser.close_own_tabs.return_value=1
        with patch.object(B.CM,'Browser',return_value=browser),patch.object(O,'check_login',return_value=(1,{'login':True})),patch.object(O,'scan') as scanner:
            data=B.run_tick(self.cfg,st,now,[])
            self.assertFalse(data['ok']);scanner.assert_not_called()
            browser.close_own_tabs.assert_called_once()
        self.assertTrue((self.root/'data/.lock').is_file())
        self.assertEqual(st['day_count'],0)
        (self.root/'data/.lock').unlink()
        (self.root/'data/.lock').mkdir()
        with patch.object(B.CM,'Browser') as maker:
            data=B.run_tick(self.cfg,st,now,[])
            self.assertEqual(data['reason'],'locked');maker.assert_not_called()

class RejectTests(Base):
    def conv(self,jid,text,ts,brand=None):
        return {'encryptJobId':jid,'brandName':brand or '示例公司'+jid,
                'lastMessageInfo':{'showText':text,'msgTime':int(ts.timestamp()*1000),'fromId':'unreliable','fromSelf':False}}

    def test_no_reply_and_upgrade_idempotent(self):
        now=HC.now_local(self.cfg);applied=now-timedelta(hours=73)
        st=HS.load_state(self.cfg)
        text='您好，我有五年工作经验，看到你们正在招人想进一步了解。'
        st['sent']['j1']={'ts':applied.strftime('%Y-%m-%d %H:%M'),'grade':'A','brand':'示例公司j1','name':'城市经理','target':'示例方向','greet_text':text}
        items={'j1':self.conv('j1',text,applied),'vague':self.conv('vague','保持联系',now)}
        items['j1']['lastMessageInfo']['fromSelf']=True
        B.collect_rejects(self.cfg,st,items,{},now)
        rows=B.load_rejects(self.cfg)
        self.assertEqual(list(rows),['j1']);self.assertEqual(rows['j1']['kind'],'no_reply')
        detected=rows['j1']['detected_ts']
        items['j1']=self.conv('j1','不合适，需要相关经验',now)
        B.collect_rejects(self.cfg,st,items,{},now+timedelta(minutes=5))
        rows=B.load_rejects(self.cfg)
        self.assertEqual(rows['j1']['kind'],'explicit');self.assertEqual(rows['j1']['reason'],'经验不符')
        self.assertEqual(rows['j1']['detected_ts'],detected)
        self.assertEqual(rows['j1']['attribution'],'our_apply')
        B.collect_rejects(self.cfg,st,items,{},now+timedelta(minutes=10))
        self.assertEqual(B.load_rejects(self.cfg),rows)

    def test_missing_or_null_conversation_not_no_reply(self):
        now=HC.now_local(self.cfg);st=HS.load_state(self.cfg)
        for jid in ['absent','null','recent','replied']:
            st['sent'][jid]={'ts':(now-timedelta(hours=80)).strftime('%Y-%m-%d %H:%M'),'brand':jid,'greet_text':'您好这是一条很长的我方招呼文本用于比对'}
        st['sent']['recent']['ts']=(now-timedelta(hours=71)).strftime('%Y-%m-%d %H:%M')
        items={'null':{'lastMessageInfo':None},'recent':self.conv('recent',st['sent']['recent']['greet_text'],now-timedelta(hours=71)),
               'replied':self.conv('replied','欢迎介绍具体情况',now)}
        B.collect_rejects(self.cfg,st,items,{},now)
        self.assertEqual(B.load_rejects(self.cfg),{})

    def test_reject_attribution_and_grouping(self):
        now=HC.now_local(self.cfg); st=HS.load_state(self.cfg)
        B.collect_rejects(self.cfg,st,{'hr':self.conv('hr','已招满',now)}, {}, now)
        rows=B.load_rejects(self.cfg)
        self.assertEqual(rows['hr']['attribution'],'hr_initiated')
        self.assertEqual(rows['hr']['title'],'')
        report=B.reject_report(self.cfg,30,now)
        self.assertEqual(report['total'],1)
        self.assertEqual(report['by_reason'],{'已招满':1})

class SummaryTests(Base):
    def test_numbers_and_empty_day(self):
        st=HS.load_state(self.cfg)
        self.assertFalse(B.daily_summary(self.cfg,st)['activity'])
        self.assertEqual(B.summary_lines(B.daily_summary(self.cfg,st)),['今天没有活动'])
        st.update(day_count=29,day_by_grade={'A':22,'B':7},pending={'i1':{},'i2':{}})
        rows={str(i):{'kind':kind,'reason':reason,'detected_ts':HC.stamp(self.cfg)} for i,(kind,reason) in enumerate([('explicit','经验不符'),('explicit','已招满'),('no_reply','无回复')])}
        Path(self.cfg['paths']['workdir'],'rejects.json').write_text(json.dumps(rows))
        result=B.daily_summary(self.cfg,st)
        self.assertEqual((result['sent'],result['rejects'],result['no_reply'],result['pending']),(29,2,1,2))
        self.assertEqual(result['reason_breakdown'],{'经验不符':1,'已招满':1})
        st['day']='2000-01-01'
        self.assertEqual(B.daily_summary(self.cfg,st)['sent'],0)

    def test_inbox_persists_pending_and_rejection(self):
        st=HS.load_state(self.cfg); now=HC.now_local(self.cfg)
        items=[{'encryptJobId':'interview','brandName':'示例公司','lastMessageInfo':{'showText':'请给几个面试时间','msgTime':int(now.timestamp()*1000),'fromSelf':False}}]
        browser=unittest.mock.Mock()
        browser.network.return_value={'text':json.dumps({'code':0,'zpData':{'result':items}})}
        result=B.inbox(self.cfg,browser,1,st,[])
        self.assertIn('interview',st['pending'])
        HS.save_state(self.cfg,st)
        self.assertEqual(B.daily_summary(self.cfg,HS.load_state(self.cfg))['pending'],1)

class BoundaryTests(Base):
    def test_new_config_validation(self):
        self.cfg['grading']['roles']=['[']
        self.assertTrue(HQ.validate_cfg(self.cfg)[0])
        self.cfg['grading']['roles']=['城市经理']
        self.cfg['schedule']['inbox_windows']=['03:00']
        self.assertTrue(HQ.validate_cfg(self.cfg)[0])

    def test_pool_age_preserved_and_old_b_first(self):
        j1,j2=self.job('old'),self.job('new')
        st=HS.load_state(self.cfg)
        st['b_pool']={'old':{'ts':'2000-01-01 01:00','job':j1}}
        self.raw([j2,j1])
        now=HC.now_local(self.cfg).replace(hour=22,minute=0)
        self.cfg['sending']['grade_quota']={'A':0,'B':8}
        with patch.object(O,'inspect_job',return_value=('ready','门店运营')),patch.object(O,'send_one',return_value='sent'),patch.object(B.time,'sleep'),patch.object(HC,'now_local',return_value=now):
            result=O.do_send(self.cfg,None,None,st,1,[])
        self.assertEqual(result['items'][0]['job_id'],'old')

    def test_cleanup_error_still_releases_lock(self):
        self.cfg['schedule']['windows']=['08:00']
        now=HC.now_local(self.cfg).replace(hour=8,minute=0)
        browser=unittest.mock.Mock();browser.close_own_tabs.side_effect=RuntimeError('close failed')
        with patch.object(B.CM,'Browser',return_value=browser),patch.object(O,'check_login',return_value=(1,{'login':True})):
            try: B.run_tick(self.cfg,HS.load_state(self.cfg),now,[])
            except RuntimeError: pass
        self.assertTrue((self.root/'data/.lock').is_file())

    def test_network_failure_never_uses_stale_detail(self):
        browser=unittest.mock.Mock()
        browser.nav.return_value={'isError':True}
        status,jd=O.inspect_job(browser,1,self.job(),self.cfg)
        self.assertEqual(status,'skip_network')
        browser.json.assert_not_called()

    def test_send_one_duplicate_guard_and_c_block(self):
        browser=unittest.mock.Mock();browser.nav.return_value={}
        browser.json.return_value={'c':True}
        with patch.object(B.time,'sleep'):
            self.assertEqual(O.send_one(browser,1,self.job(),self.cfg),'skip_already_chatted')
        browser.click.assert_not_called()
        with patch.object(O,'inspect_job',return_value=('ready','商户拓展与开城')),patch.object(B.time,'sleep'):
            j=self.job();j['jobName']='其它职能'
            self.assertEqual(O.send_one(browser,1,j,self.cfg),'skip_grade')
        browser.click.assert_not_called()

    def test_no_reply_system_message_is_not_evidence(self):
        now=HC.now_local(self.cfg);applied=now-timedelta(hours=80)
        row={'ts':applied.strftime('%Y-%m-%d %H:%M')}
        it={'lastMessageInfo':{'showText':'您的附件简历已发送给对方','msgTime':int(applied.timestamp()*1000)}}
        self.assertIsNone(B.last_is_ours(self.cfg,row,it))


class OutputTests(Base):
    def test_plan_counts_provisional_grades(self):
        import io
        from contextlib import redirect_stdout
        self.raw([self.job('a'),self.job('b','示例品牌行业')])
        args=B.build_parser().parse_args(['--config',str(self.path),'plan','--json'])
        stream=io.StringIO()
        with redirect_stdout(stream): B.cmd_plan(args)
        data=json.loads(stream.getvalue())
        self.assertEqual(data['summary']['grade_a'],1)
        self.assertEqual(data['summary']['grade_b'],1)
        self.assertFalse(data['items'][0]['grade_final'])


class FastFailureTests(Base):
    def test_inbox_tick_network_failure_raises(self):
        self.cfg['_deadline']=B.time.monotonic()+60
        browser=unittest.mock.Mock()
        browser.nav.return_value={'isError':True}
        browser.network.return_value={'text':json.dumps({'code':0,'zpData':{'result':[]}})}
        with self.assertRaises(RuntimeError):
            B.inbox(self.cfg,browser,1,HS.load_state(self.cfg),[])
        browser.network.assert_not_called()
        browser.nav.return_value={}
        browser.network.return_value={'text':'bad network','isError':True}
        with self.assertRaises(RuntimeError):
            B.inbox(self.cfg,browser,1,HS.load_state(self.cfg),[])

    def test_legitimate_empty_inbox_successful(self):
        self.cfg['_deadline']=B.time.monotonic()+60
        browser=unittest.mock.Mock();browser.nav.return_value={}
        browser.network.return_value={'text':json.dumps({'code':0,'zpData':{'result':[]}})}
        self.assertEqual(B.inbox(self.cfg,browser,1,HS.load_state(self.cfg),[])['messages_total'],0)

    def test_send_tick_network_failure_stops_candidates(self):
        self.cfg['_deadline']=B.time.monotonic()+60
        self.raw([self.job('one'),self.job('two')])
        with patch.object(O,'inspect_job',return_value=('skip_network','')) as reader,patch.object(B.time,'sleep'):
            with self.assertRaises(RuntimeError):
                O.do_send(self.cfg,None,None,HS.load_state(self.cfg),2,[])
        self.assertEqual(reader.call_count,1)


class CacheFreshnessTests(Base):
    def test_rescan_same_fields_reuses_jd_cache(self):
        self.raw([self.job()]); st=HS.load_state(self.cfg)
        j=HF.load_jobs(self.cfg)[0]
        with patch.object(O,'inspect_job',return_value=('ready','商户拓展与开城')):
            O.prepare_grade(self.cfg,None,None,j,st)
        raw=Path(self.cfg['paths']['raw_dir'],'sample.json')
        ns=raw.stat().st_mtime_ns+1_000_000_000
        os.utime(raw,ns=(ns,ns))
        with patch.object(O,'inspect_job',return_value=('ready','门店运营')) as reader:
            gr=O.prepare_grade(self.cfg,None,None,HF.load_jobs(self.cfg)[0],st)
            self.assertEqual(gr['grade'],'A');reader.assert_not_called()

    def test_pool_must_not_override_new_raw_rejection(self):
        old=self.job(); st=HS.load_state(self.cfg)
        st['b_pool']={'j1':{'job':old,'ts':'2000-01-01 00:00'}}
        current=dict(old,jobValidStatus=0)
        self.raw([current])
        with patch.object(O,'inspect_job',return_value=('ready','门店运营')),patch.object(O,'send_one',return_value='sent') as sender,patch.object(B.time,'sleep'):
            result=O.do_send(self.cfg,None,None,st,1,[])
        self.assertEqual(result['sent'],0);sender.assert_not_called()


class LoginLossTests(Base):
    def test_detail_login_wall_never_clicks(self):
        browser=unittest.mock.Mock(); browser.nav.return_value={}
        browser.json.return_value={'login':True,'s':False,'c':False}
        with patch.object(B.time,'sleep'):
            status,jd=O.inspect_job(browser,1,self.job(),self.cfg)
        self.assertEqual(status,'skip_not_logged_in')
        self.assertEqual(browser.json.call_count,1)
        browser.click.assert_not_called()

if __name__ == '__main__':
    unittest.main()
