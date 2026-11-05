"""R1–R5: offline regressions; all runtime data lives in TemporaryDirectory."""
import io
import json
import os
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import Mock, patch

from test_phase_a import Base, B
import hunt_config as HC
import hunt_filter as HF
import hunt_state as HS
import outbound as O
from test_round8 import Page, JOB, TEXT
import chat_send as CS
import send_safety as S


class FixRegressions(Base):
    def test_r1_trusted_failure_never_falls_back(self):
        for failure in ({'isError': True}, None, {'ok': False}, TypeError('bad signature'), RuntimeError('lost bridge')):
            with self.subTest(failure=failure):
                br = Mock()
                br.json.return_value = {'ok': True, 'ready': True}
                if isinstance(failure, Exception):
                    br.click.side_effect = failure
                else:
                    br.click.return_value = failure
                result = CS.click_send_scoped(br, 1, TEXT, job=JOB, conv_key='job:j')
                self.assertFalse(result['ok'])
                self.assertFalse(result['clicked'])
                self.assertEqual(result['reason'], 'trusted_click_failed')
                self.assertEqual(result['conv_key'], 'job:j')
                self.assertTrue(all('button.click()' not in c.args[1] for c in br.json.call_args_list))

    def test_r1_bridge_without_click_uses_labelled_js_fallback(self):
        br = Mock(spec=['json'])
        br.json.side_effect = [{'ok': True, 'ready': True}, {'ok': True, 'clicked': True}]
        result = CS.click_send_scoped(br, 1, TEXT, job=JOB)
        self.assertEqual(result.get('via'), 'js_fallback')
        self.assertIn('button.click()', br.json.call_args_list[-1].args[1])

    def test_r1_send_one_failure_preserves_pending_without_charge(self):
        self.raw([self.job()])
        st = HS.load_state(self.cfg)
        br = Mock()
        br.click.return_value = {}
        br.json.return_value = {'path': '/web/geek/chat'}
        panel = {'ok': True, 'panel_text': '示例公司j1 城市经理', 'messages': []}
        with patch.object(O, 'inspect_job', return_value=('ready', '商户拓展开城')), \
             patch.object(O, 'job_identity_matches', return_value=True), \
             patch.object(O, 'greeting_switch', return_value='auto_greeting_off'), \
             patch.object(B.G, 'generate', return_value={'text': TEXT}), \
             patch.object(CS, 'chat_snapshot', return_value=panel), \
             patch.object(CS, 'type_into_chat', return_value={'ok': True}), \
             patch.object(CS, 'click_send_scoped', return_value={
                 'ok': False, 'clicked': False, 'attempted': True,
                 'reason': 'trusted_click_failed', 'conv_key': 'job:j1'}), \
             patch.object(B.time, 'sleep'):
            with self.assertRaisesRegex(RuntimeError, 'unresolved_send'):
                O.do_send(self.cfg, br, 1, st, 1, [])
        self.assertEqual(st['contacts'], [])
        self.assertEqual(st['day_count'], 0)
        self.assertEqual(S.ledger_latest(self.cfg)[0]['status'], 'unknown')
        with self.assertRaisesRegex(RuntimeError, 'unresolved_send'):
            S.ledger_guard(self.cfg)

    def test_r2_cached_c_does_not_starve_later_b(self):
        self.cfg["sending"]["grade_quota"] = {"A": 0, "B": 8}
        jobs = [self.job(str(i)) for i in range(30)]
        self.raw(jobs)
        st = HS.load_state(self.cfg)
        with patch.object(O, 'inspect_job', return_value=('ready', '普通职责')):
            for job in HF.load_jobs(self.cfg)[:24]:
                O.prepare_grade(self.cfg, None, None, job, st)
        with patch.object(O, 'inspect_job', return_value=('ready', '门店运营')) as reader, \
             patch.object(O, 'send_one', return_value='sent') as sender, patch.object(B.time, 'sleep'):
            first = O.do_send(self.cfg, None, None, st, 1, [])
            self.assertEqual(sender.call_args.args[2]['encryptJobId'], '24')
            self.assertLessEqual(reader.call_count, 6)
            HS.save_state(self.cfg, st)
            st = HS.load_state(self.cfg)
            reader.reset_mock()
            second = O.do_send(self.cfg, None, None, st, 1, [])
        self.assertLess(first['inspects'], 24)
        self.assertNotEqual(second['inspects'], 24)
        self.assertTrue(st['checked_jobs'])
        st['day'] = '2000-01-01'
        HS.save_state(self.cfg, st)
        self.assertEqual(HS.load_state(self.cfg)['checked_jobs'], {})

    def test_r2_uncached_budget_advances_after_reload(self):
        self.cfg["sending"]["grade_quota"] = {"A": 0, "B": 8}
        self.raw([self.job(str(i), '示例品牌行业') for i in range(30)])
        st = HS.load_state(self.cfg)
        with patch.object(O, 'inspect_job', side_effect=lambda br, tab, j, cfg: (
                'ready', '门店运营' if j['encryptJobId'] == '24' else '普通职责')), \
             patch.object(O, 'send_one', return_value='sent') as sender, patch.object(B.time, 'sleep'):
            first = O.do_send(self.cfg, None, None, st, 1, [])
            self.assertEqual(first['sent'], 0)
            second = O.do_send(self.cfg, None, None, HS.load_state(self.cfg), 1, [])
        self.assertEqual(sender.call_args.args[2]['encryptJobId'], '24')
        self.assertEqual(first['inspects'], 24)
        self.assertLess(second['inspects'], 24)

    def test_r2_budget_exhaustion_still_allows_cached_b(self):
        self.cfg['sending']['grade_quota'] = {'A': 0, 'B': 8}
        self.raw([self.job(str(i), '示例品牌行业') for i in range(25)])
        st = HS.load_state(self.cfg)
        with patch.object(O, 'inspect_job', return_value=('ready', '门店运营')):
            O.prepare_grade(self.cfg, None, None, HF.load_jobs(self.cfg)[24], st)
        with patch.object(O, 'inspect_job', return_value=('ready', '普通职责')) as reader, \
             patch.object(O, 'send_one', return_value='sent') as sender, patch.object(B.time, 'sleep'):
            result = O.do_send(self.cfg, None, None, st, 1, [])
        self.assertEqual(result['inspects'], 24)
        self.assertEqual(reader.call_count, 24)
        self.assertEqual(sender.call_args.args[2]['encryptJobId'], '24')

    def test_r2_prepare_grade_reports_cache_hit(self):
        st = HS.load_state(self.cfg)
        j = self.job()
        with patch.object(O, 'inspect_job', return_value=('ready', '普通职责')) as reader:
            self.assertFalse(O.prepare_grade(self.cfg, None, None, j, st)['cached'])
            self.assertTrue(O.prepare_grade(self.cfg, None, None, j, st)['cached'])
        self.assertEqual(reader.call_count, 1)

    def test_r3_all_identity_guards_share_name_normalization(self):
        name = '区域零售高级经理（华东大区）'
        for suffix in ('(J13893)', '（J13893）', '(A12)', ' 招聘'):
            with self.subTest(suffix=suffix):
                j = dict(JOB, jobName=name + suffix)
                br = Mock()
                br.json.return_value = {'job_id': 'j', 'title': name, 'brand': JOB['brandName']}
                self.assertTrue(O.job_identity_matches(br, 1, j))
                self.assertTrue(CS.chat_identity_evidence(JOB['brandName'] + ' ' + name, j))
                page = Page(panelText=JOB['brandName'] + ' ' + name, text=TEXT, model=TEXT)
                self.assertTrue(CS.click_send_scoped(page, 1, TEXT, job=j)['ok'])
                br.json.return_value['brand'] = '另一家公司'
                self.assertFalse(O.job_identity_matches(br, 1, j))
                self.assertFalse(CS.chat_identity_evidence('另一家公司 ' + name, j))
                page = Page(panelText='另一家公司 ' + name, text=TEXT, model=TEXT)
                self.assertEqual(CS.click_send_scoped(page, 1, TEXT, job=j)['reason'], 'identity_mismatch')
                page = Page(panelText=JOB['brandName'] + ' 不同岗位', text=TEXT, model=TEXT)
                self.assertEqual(CS.click_send_scoped(page, 1, TEXT, job=j)['reason'], 'identity_mismatch')
        self.assertEqual(CS.norm_job_name(' 区域　经理（A12）招聘 '), '区域经理')
        br.json.return_value = {'job_id': 'j', 'title': '不同岗位', 'brand': JOB['brandName']}
        self.assertFalse(O.job_identity_matches(br, 1, j))

    def test_r4_newest_snapshot_wins_whole_record(self):
        raw = Path(self.cfg['paths']['raw_dir'])
        for filename, row, mtime in [('a.json', dict(self.job(), old_only=True), 10),
                                     ('b.json', dict(self.job(), jobName='新版岗位', salaryDesc='30-40K'), 20)]:
            p = raw / filename
            p.write_text(json.dumps({'code': 0, 'zpData': {'jobList': [row]}}))
            os.utime(p, (mtime, mtime))
        j, = HF.load_jobs(self.cfg)
        self.assertEqual(j['jobName'], '新版岗位')
        self.assertEqual(j['salaryDesc'], '30-40K')
        self.assertNotIn('old_only', j)
        self.assertEqual(set(j['_src']), {'a.json', 'b.json'})
        os.utime(raw / 'a.json', (30, 30))
        self.assertIn('old_only', HF.load_jobs(self.cfg)[0])

    def test_r4_missing_mtime_falls_back_to_filename_date_and_sequence(self):
        raw = Path(self.cfg['paths']['raw_dir'])
        for filename, title in [('snap-20261027-9.json', '旧日期'),
                                ('snap-20261028-2.json', '新日期'),
                                ('snap-20261028-10.json', '最新序号')]:
            (raw / filename).write_text(json.dumps({'code': 0, 'zpData': {
                'jobList': [dict(self.job(), jobName=title)]}}))
        with patch.object(B.os, 'stat', side_effect=OSError('mtime unavailable')):
            j, = HF.load_jobs(self.cfg)
        self.assertEqual(j['jobName'], '最新序号')
        self.assertEqual(len(j['_src']), 3)

    def test_r5_dry_run_reports_actual_remaining_budget_without_browser(self):
        st = HS.load_state(self.cfg)
        st.update(day_count=29, day_by_grade={'A': 22, 'B': 7})
        HS.save_state(self.cfg, st)
        now = HC.now_local(self.cfg).replace(hour=22, minute=0)
        self.cfg['schedule']['inbox_windows'] = ['22:00']
        self.path.write_text(json.dumps(self.cfg))
        args = B.build_parser().parse_args(['--config', str(self.path), 'tick', '--dry-run', '--json'])
        stream = io.StringIO()
        before = Path(self.cfg['paths']['state']).read_bytes()
        with patch.object(HC, 'now_local', return_value=now), patch.object(B.CM, 'Browser') as browser, redirect_stdout(stream):
            self.assertEqual(B.cmd_tick(args), 0)
        data = json.loads(stream.getvalue())
        self.assertEqual(data['budget']['limit'], 1)
        self.assertEqual(data['quotas'], {'A': 0, 'B': 1})
        self.assertEqual(data['actions'], ['send', 'inbox'])
        browser.assert_not_called()
        self.assertEqual(Path(self.cfg['paths']['state']).read_bytes(), before)
        self.assertFalse(Path(self.cfg['paths']['workdir'], '.lock').exists())
