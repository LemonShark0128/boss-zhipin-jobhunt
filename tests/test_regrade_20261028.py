"""P2-2a: pure grading and read-only replay against temporary fixtures."""
import copy
import io
import json
import os
import subprocess
import sys
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import patch

from test_phase_a import Base, B
import hunt_filter as HF
import hunt_state as HS
import outbound as O


class ReplayTests(Base):
    def replay(self, *options):
        self.path.write_text(json.dumps(self.cfg))
        args = B.build_parser().parse_args(['--config', str(self.path), 'regrade', '--json', *options])
        stream = io.StringIO()
        with redirect_stdout(stream):
            code = args.func(args)
        return code, json.loads(stream.getvalue())

    def fixture(self):
        jobs = [self.job(str(i)) for i in range(3)]
        self.raw(jobs)
        self.cfg['filters']['text_block'] = ['外呼', '扫街']
        st = HS.load_state(self.cfg)
        st['grade_cache'] = {str(i): {'grade': 'C', 'jd_text': jd, 'fingerprint': 'legacy'}
                             for i, jd in enumerate(['商户拓展', '门店运营 外呼', '普通职责'])}
        HS.save_state(self.cfg, st)
        alt = copy.deepcopy(self.cfg)
        alt['grading']['tracks']['A']['min_hits'] = 1
        alt['filters']['text_block'] = ['扫街']
        alt['paths'] = {'workdir': str(self.root / 'must-not-exist')}
        alt_path = self.root / 'alt.json'
        alt_path.write_text(json.dumps(alt))
        return alt_path

    def test_grade_metadata_and_redline_parity(self):
        self.cfg['filters']['text_block'] = ['外呼', '扫街', 'CALL']
        for jd, grade, cause, redlines in [
                ('商户拓展开城', 'A', 'ok', []), ('门店运营', 'B', 'ok', []),
                ('普通职责', 'C', 'no_signal', []),
                ('商户拓展开城 门店运营加盟商', 'B', 'tie_unknown', []),
                ('门店运营 外呼', 'C', 'redline', ['外呼']),
                ('门店运营 外呼扫街', 'C', 'redline', ['外呼', '扫街']),
                ('门店运营 call', 'B', 'ok', [])]:
            with self.subTest(jd=jd):
                job = self.job()
                before = copy.deepcopy((job, self.cfg))
                gr = HF.grade_with_jd(job, self.cfg, jd)
                self.assertEqual((gr['grade'], gr['cause'], gr['redlines']), (grade, cause, redlines))
                self.assertEqual((job, self.cfg), before)
                original = HF.grade_job(job, self.cfg, jd)
                if redlines:
                    original.update(grade='C', reason='JD 红线:' + redlines[0])
                for key in ('grade', 'reason', 'scores', 'final'):
                    self.assertEqual(gr[key], original[key])
                with patch.object(O, 'inspect_job', return_value=('ready', jd)):
                    prepared = O.prepare_grade(self.cfg, None, None, job, {'grade_cache': {}})
                for key in ('grade', 'reason', 'scores', 'final', 'cause'):
                    self.assertEqual(prepared[key], gr[key])
        role = HF.grade_job(dict(self.job(), jobName='其它'), self.cfg, '开城')
        self.assertEqual(role['cause'], 'role_miss')
        legacy = copy.deepcopy(self.cfg); legacy.pop('grading')
        self.assertEqual({k: HF.grade_job(self.job(), legacy)[k] for k in ('cause', 'hits', 'mins')},
                         {'cause': 'legacy', 'hits': {}, 'mins': {}})

    def test_near_miss_boundaries(self):
        self.cfg['filters']['text_block'] = ['外呼', '扫街']
        for jd, expected in [('商户拓展', {'kind': 'short_hits', 'track': 'A', 'need': 2, 'got': 1, 'hits': ['商户拓展']}),
                             ('普通职责', None), ('门店运营 外呼', {'kind': 'single_redline', 'redline': '外呼', 'track': 'B'}),
                             ('门店运营 外呼扫街', None)]:
            with self.subTest(jd=jd):
                self.assertEqual(HF.near_miss(HF.grade_with_jd(self.job(), self.cfg, jd), self.cfg), expected)
        role = HF.grade_with_jd(dict(self.job(), jobName='其它'), self.cfg, '商户拓展')
        self.assertIsNone(HF.near_miss(role, self.cfg))
        self.assertIsNone(HF.near_miss(HF.grade_job(self.job(), self.cfg), self.cfg))
        self.cfg['grading']['tracks']['B']['min_hits'] = 3
        gr = HF.grade_with_jd(self.job(), self.cfg, '商户拓展 门店运营加盟商')
        self.assertEqual(HF.near_miss(gr, self.cfg)['track'], 'B')

    def test_replay_matrices_and_diagnosis(self):
        alt = self.fixture()
        code, data = self.replay('--alt-config', str(alt), '--near-miss-threshold', '2', '--pool-threshold', '1')
        self.assertEqual(code, 0)
        self.assertEqual(data['schema'], 2)
        self.assertEqual(data['cmd'], 'regrade')
        self.assertEqual(data['matrix']['stored_to_current'], {'C->C': 3})
        self.assertEqual(data['matrix']['current_to_alt'], {'C->A': 1, 'C->B': 1, 'C->C': 1})
        self.assertEqual(data['jd']['legacy'], 3)
        self.assertEqual(data['jd']['current']['grades'], {'A': 0, 'B': 0, 'C': 3})
        self.assertEqual(data['jd']['current']['near_miss'], {'count': 2, 'by_kind': {'short_hits': 1, 'single_redline': 1}})
        self.assertEqual(data['list']['current']['fresh_pool'], 0)
        self.assertEqual(data['diagnosis']['verdict'], 'both')
        self.assertEqual([r['job_id'] for r in data['items'][:2]], ['0', '1'])
        self.assertFalse((self.root / 'must-not-exist').exists())
        _, data = self.replay('--near-miss-threshold', '3', '--pool-threshold', '0')
        self.assertEqual(data['diagnosis']['verdict'], 'inconclusive')
        _, data = self.replay('--near-miss-threshold', '2', '--pool-threshold', '0')
        self.assertEqual(data['diagnosis']['verdict'], 'rules_tight')
        _, data = self.replay('--near-miss-threshold', '3', '--pool-threshold', '1')
        self.assertEqual(data['diagnosis']['verdict'], 'pool_dry')

    def test_replay_never_writes_or_opens_browser(self):
        alt = self.fixture()
        self.path.write_text(json.dumps(self.cfg))
        def snapshot():
            return {str(p.relative_to(self.root)): (p.read_bytes() if p.is_file() else None, p.stat().st_mtime_ns)
                    for p in self.root.rglob('*')}
        before = snapshot()
        args = B.build_parser().parse_args(['--config', str(self.path), 'regrade', '--json', '--alt-config', str(alt)])
        with patch.object(B.CM, 'Browser', side_effect=AssertionError('browser')), \
             patch.object(O, 'inspect_job', side_effect=AssertionError('inspect')), \
             patch.object(HS, 'save_state', side_effect=AssertionError('save')), \
             patch.object(HS, 'migrate_ledger', side_effect=AssertionError('migrate')), \
             patch.object(HS, 'cached_grade', side_effect=AssertionError('cache mutation')), redirect_stdout(io.StringIO()):
            self.assertEqual(args.func(args), 0)
        self.assertEqual(snapshot(), before)
        self.assertFalse(Path(self.cfg['paths']['workdir'], '.lock').exists())

    def test_funnel_and_job_fallbacks(self):
        jobs = [self.job(str(i)) for i in range(6)]
        jobs[1]['jobName'] = '其它'
        jobs[2]['salaryDesc'] = '1-2K'
        self.cfg['filters']['salary_min_k'] = 10
        self.raw(jobs)
        st = HS.load_state(self.cfg)
        st['sent'] = {'3': {'name': '城市经理', 'brand': '已投'}, 'sent-only': {'name': '城市经理', 'brand': '旧公司'}}
        st['contacts'] = [jobs[4]['brandName'] + '|' + jobs[4]['bossName']]
        st['platform_chatted']['5'] = 'today'
        st['b_pool']['pool-only'] = {'job': self.job('pool-only')}
        st['grade_cache'] = {jid: {'grade': 'B', 'jd_text': '门店运营'}
                             for jid in ['pool-only', 'sent-only', 'orphan']}
        HS.save_state(self.cfg, st)
        _, data = self.replay()
        funnel = data['list']['current']
        self.assertEqual({k: funnel[k] for k in ('total', 'role_miss', 'sent', 'same_hr', 'open_role_matched', 'fresh_pool')},
                         {'total': 6, 'role_miss': 1, 'sent': 1, 'same_hr': 1, 'open_role_matched': 1, 'fresh_pool': 1})
        self.assertEqual(sum(funnel['judge_reject'].values()), 1)
        self.assertEqual(data['jd']['orphan'], 1)
        self.assertEqual(data['jd']['current']['grades']['B'], 2)

    def test_cache_job_survives_missing_raw(self):
        st = HS.load_state(self.cfg)
        st['grade_cache']['saved'] = {'job': self.job('saved'), 'jd_text': '门店运营', 'grade': 'B'}
        HS.save_state(self.cfg, st)
        _, data = self.replay()
        self.assertEqual(data['jd']['orphan'], 0)
        self.assertEqual(data['jd']['current']['grades']['B'], 1)

    def test_existing_sources_take_priority_over_saved_cache_job(self):
        st = HS.load_state(self.cfg)
        for jid in ('pool', 'sent'):
            st['grade_cache'][jid] = {'job': dict(self.job(jid), jobName='其它'),
                                      'jd_text': '门店运营', 'grade': 'B'}
        st['b_pool']['pool'] = {'job': self.job('pool')}
        st['sent']['sent'] = {'name': '城市经理', 'brand': '已投公司'}
        HS.save_state(self.cfg, st)
        _, data = self.replay()
        self.assertEqual(data['jd']['current']['grades']['B'], 2)
        self.assertEqual(data['jd']['current']['causes']['role_miss'], 0)

    def test_alt_upgrades_alone_can_trigger_rules_tight(self):
        alt_path = self.fixture()
        self.cfg['grading']['tracks']['A']['min_hits'] = 4
        self.cfg['filters']['text_block'] = ['外呼', '扫街']
        st = HS.load_state(self.cfg)
        st['grade_cache']['1']['jd_text'] = '门店运营 外呼扫街'
        HS.save_state(self.cfg, st)
        _, data = self.replay('--alt-config', str(alt_path), '--near-miss-threshold', '1', '--pool-threshold', '0')
        self.assertEqual(data['jd']['current']['near_miss']['count'], 0)
        self.assertEqual(data['matrix']['current_to_alt']['C->A'], 1)
        self.assertEqual(data['diagnosis']['verdict'], 'rules_tight')

    def test_invalid_alt_regex_is_json_error(self):
        alt = self.fixture()
        for section in ('grading', 'filters'):
            cfg = json.loads(alt.read_text())
            if section == 'grading':
                cfg['grading']['roles'] = ['[']
            else:
                cfg['grading']['roles'] = ['城市经理']
                cfg['filters']['text_block'] = ['[']
            alt.write_text(json.dumps(cfg))
            code, data = self.replay('--alt-config', str(alt))
            self.assertEqual(code, 1)
            self.assertFalse(data['ok'])
            self.assertTrue(data['errors'])

    def test_missing_state_and_workdir_are_read_only(self):
        self.cfg['paths'] = {'workdir': str(self.root / 'absent')}
        _, data = self.replay()
        self.assertEqual(data['list']['current']['total'], 0)
        self.assertEqual(data['jd']['current']['grades'], {'A': 0, 'B': 0, 'C': 0})
        self.assertFalse((self.root / 'absent').exists())

    def test_cli_json_and_three_line_text(self):
        self.fixture()
        self.path.write_text(json.dumps(self.cfg))
        result = subprocess.run([sys.executable, str(Path(B.__file__)), '--config', str(self.path), 'regrade', '--json', '--top', '0'],
                                capture_output=True, text=True, env=dict(os.environ, PYTHONPYCACHEPREFIX='/tmp/pyc'))
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout)['items'], [])
        args = B.build_parser().parse_args(['--config', str(self.path), 'regrade'])
        stream = io.StringIO()
        with redirect_stdout(stream):
            self.assertEqual(args.func(args), 0)
        self.assertEqual(len(stream.getvalue().splitlines()), 3)
