"""方案 1 第 1 步：只用临时配置与本地快照。"""
import copy
import io
import json
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import patch

from test_phase_a import Base, B
import hunt_config as HC
import hunt_filter as HF
import hunt_schedule as HQ
import hunt_state as HS
import outbound as O


class DiagnosticsTests(Base):
    def invoke(self, command, *options):
        self.path.write_text(json.dumps(self.cfg))
        args = B.build_parser().parse_args(['--config', str(self.path), command, *options])
        stream = io.StringIO()
        with redirect_stdout(stream):
            code = args.func(args)
        return code, stream.getvalue()

    def test_open_pool_and_regrade_exclude_all_terminal_states(self):
        jobs = [self.job(str(i)) for i in range(10)]
        st = HS.load_state(self.cfg)
        st['sent']['1'] = {}
        st['contacts'] = [jobs[2]['brandName'] + '|' + jobs[2]['bossName']]
        st['checked_jobs']['3'] = HF.grade_fingerprint(jobs[3], self.cfg)
        st['platform_chatted']['4'] = 'today'
        st['opened_no_greet']['5'] = {}
        jobs[6]['jobName'] = '其它'
        jobs[7]['jobValidStatus'] = 0
        st['checked_jobs']['8'] = 'old-rules'
        st['grade_cache']['9'] = {'jd_text': '普通职责'}
        before = copy.deepcopy(st)
        self.assertTrue(callable(getattr(B, 'open_pool', None)), 'missing common open_pool')
        self.assertEqual([j['encryptJobId'] for j in B.open_pool(st, jobs, self.cfg)], ['0', '8', '9'])
        funnel = B.regrade_report(self.cfg, st, jobs)['list']['current']
        self.assertEqual(funnel['open_role_matched'], 3)
        self.assertEqual(funnel['fresh_pool'], 2)
        self.assertEqual(st, before)

    def sources_fixture(self):
        jobs = [self.job(str(i)) for i in range(7)]
        for name, group in [('示例城市_城市经理_0.json', jobs[:6]),
                            ('示例城市_其它_1.json', [jobs[2], jobs[6]])]:
            Path(self.cfg['paths']['raw_dir'], name).write_text(json.dumps({'code': 0, 'zpData': {'jobList': group}}))
        st = HS.load_state(self.cfg)
        self.cfg['filters']['text_block'] = ['外呼']
        for job, jd in zip(jobs, ['商户拓展开城', '门店运营', '普通职责', '门店运营 外呼', '', '门店运营', '']):
            st['grade_cache'][job['encryptJobId']] = {'jd_text': jd, 'grade': 'A', 'rule_fp': 'stale'}
        st['sent']['5'] = {}
        st['platform_chatted']['6'] = 'today'
        HS.save_state(self.cfg, st)
        Path(self.cfg['paths']['log']).write_text('existing log\n')

    def snapshot(self):
        return {str(p.relative_to(self.root)): (p.read_bytes() if p.is_file() else None, p.stat().st_mtime_ns)
                for p in self.root.rglob('*') if p != self.path}

    def test_by_src_json_counts_sort_ratio_and_strict_read_only(self):
        self.sources_fixture()
        before = self.snapshot()
        code, output = self.invoke('grades', '--by-src', '--json')
        self.assertEqual(code, 0)
        data = json.loads(output)
        self.assertEqual((data['cmd'], data['ok'], data['unique_total']), ('grades', True, 7))
        rows = data['by_src']
        self.assertEqual(rows[0], {'src': '示例城市_城市经理_0.json', 'open': 5,
                                 'final_A': 1, 'final_B': 1, 'final_C': 2,
                                 'no_signal': 1, 'redline': 1, 'no_jd': 1, 'sent': 1,
                                 'b_pool': 0, 'open_with_bpool': 5, 'sr': 0.5})
        self.assertEqual(rows[1]['open'], 1)
        self.assertEqual(rows[1]['final_C'], 1)
        self.assertEqual(rows[1]['sr'], 0)
        self.assertEqual(self.snapshot(), before)
        self.assertFalse(Path(self.cfg['paths']['workdir'], '.lock').exists())
        code, output = self.invoke('grades', '--by-src')
        self.assertEqual(code, 0)
        self.assertIn('final_A', output)
        self.assertEqual(self.snapshot(), before)

    def test_by_src_does_not_create_missing_workdir(self):
        self.cfg['paths'] = {'workdir': str(self.root / 'absent')}
        code, output = self.invoke('grades', '--by-src', '--json')
        self.assertEqual(code, 0)
        self.assertEqual(json.loads(output)['by_src'], [])
        self.assertFalse((self.root / 'absent').exists())

    def test_zero_open_source_ratio_is_zero(self):
        self.raw([self.job()])
        st = HS.load_state(self.cfg)
        st['sent']['j1'] = {}
        HS.save_state(self.cfg, st)
        _, output = self.invoke('grades', '--by-src', '--json')
        row = json.loads(output)['by_src'][0]
        self.assertEqual((row['open'], row['sr'], row['final_A'], row['final_B']), (0, 0, 0, 0))

    def test_validate_warns_without_failing_and_ignores_disabled_targets(self):
        self.sources_fixture()
        self.cfg['targets'][0]['keywords'] += ['其它', '未扫描']
        self.cfg['targets'].append(dict(self.cfg['targets'][0], enabled=False, keywords=['禁用词']))
        code, output = self.invoke('validate', '--json')
        self.assertEqual(code, 0)
        warnings = json.loads(output)['warnings']
        self.assertIn('关键词「其它」与 roles 无交集，扫回来的大概率判 C', warnings)
        self.assertTrue(any('零产出关键词' in w and '其它' in w for w in warnings))
        self.assertFalse(any('城市经理' in w or '禁用词' in w for w in warnings))
        self.assertFalse(any('零产出关键词' in w and '未扫描' in w for w in warnings))

    def test_track_only_role_warns_and_does_not_hide_role_miss(self):
        self.cfg['grading']['tracks']['A']['roles'] = ['商务(拓展|开发)']
        self.cfg['targets'][0]['keywords'] = ['商务拓展 / 上海', '城市经理']
        errors, warnings = HQ.validate_cfg(self.cfg)
        self.assertEqual(errors, [])
        self.assertTrue(any('商务拓展 / 上海' in w and '与 roles 无交集' in w for w in warnings))
        self.assertTrue(any('档位 roles 与 grading.roles 不一致' in w for w in warnings))
        self.cfg['grading']['roles'].append('商务(拓展|开发)')
        self.assertFalse(any('与 roles 无交集' in w for w in HQ.validate_cfg(self.cfg)[1]))
        self.cfg['targets'][0]['keywords'] = ['财务']
        self.assertTrue(any('与 roles 无交集' in w for w in HQ.validate_cfg(self.cfg)[1]))

    def test_local_b_to_c_regrade_removes_only_downgraded_pool_entry(self):
        job = self.job()
        st = HS.load_state(self.cfg)
        st['grade_cache']['j1'] = dict(HF.grade_with_jd(job, self.cfg, '门店运营'),
            jd_text='门店运营', jd_fp=HF.jd_fingerprint(job), jd_ts=HC.stamp(self.cfg),
            rule_fp=HF.rule_fingerprint(self.cfg))
        st['b_pool'] = {'j1': {'job': job}, 'other': {'job': self.job('other')}}
        self.cfg['grading']['tracks']['B']['min_hits'] = 2
        self.assertEqual(HS.cached_grade(self.cfg, job, st)['grade'], 'C')
        self.assertEqual(set(st['b_pool']), {'other'})

    def test_invalid_track_role_is_validation_error_instead_of_crash(self):
        self.cfg['grading']['tracks']['A']['roles'] = ['[']
        code, output = self.invoke('validate', '--json')
        self.assertEqual(code, 1)
        self.assertTrue(any('grading.tracks.A.roles' in e for e in json.loads(output)['errors']))

    def test_zero_output_combines_pages_and_cities_and_sanitized_keyword(self):
        self.cfg['targets'][0]['keywords'] = ['BD / KA', '城市经理']
        self.cfg['targets'].append(dict(self.cfg['targets'][0], city='另一城市'))
        rows = [dict(src='示例城市_BD_KA_0.json', open=1, final_A=0, final_B=0, final_C=1),
                dict(src='另一城市_BD_KA_1.json', open=1, final_A=0, final_B=1, final_C=0),
                dict(src='示例城市_城市经理_0.json', open=1, final_A=0, final_B=0, final_C=1)]
        self.assertEqual(B.zero_output_keywords(self.cfg, rows), ['城市经理'])

    def test_optional_diagnostics_failure_keeps_validate_exit_zero(self):
        Path(self.cfg['paths']['state']).write_text('{broken')
        code, output = self.invoke('validate', '--json')
        self.assertEqual(code, 0)
        data = json.loads(output)
        self.assertEqual(data['errors'], [])
        self.assertTrue(any('零产出关键词诊断不可用' in w for w in data['warnings']))

    def test_open_without_jd_is_unknown_not_zero_output(self):
        job = dict(self.job(), _src=['示例城市_城市经理_0.json'])
        rows = B.grades_by_src(self.cfg, HS.load_state(self.cfg), [job])
        self.assertEqual(B.zero_output_keywords(self.cfg, rows), [])
        self.assertEqual(rows[0]['no_jd'], 1)

    def test_sent_and_bpool_preserve_source_output(self):
        for state_key in ('sent', 'b_pool'):
            with self.subTest(state_key=state_key):
                jobs = [dict(self.job(str(i)), _src=['示例城市_城市经理_0.json']) for i in range(2)]
                st = HS.load_state(self.cfg)
                st['grade_cache']['0'] = {'jd_text': '普通职责'}
                st[state_key]['1'] = {'job': jobs[1], 'grade': 'B'}
                rows = B.grades_by_src(self.cfg, st, jobs)
                self.assertEqual(B.zero_output_keywords(self.cfg, rows), [])
                self.assertEqual(rows[0][state_key], 1)

    def test_open_unique_and_min_score(self):
        self.sources_fixture()
        _, output = self.invoke('grades', '--by-src', '--json')
        data = json.loads(output)
        self.assertEqual(data['open_unique'], 5)
        self.assertEqual(sum(row['open'] for row in data['by_src']), 6)
        _, output = self.invoke('grades', '--by-src')
        self.assertIn('行内 open 不去重', output)
        jobs = [self.job('low'), dict(self.job('high'), bossOnline=True)]
        self.cfg['scoring'] = {'min_score': 3, 'keyword_in_title': 2, 'boss_online': 1}
        expected = [x['job']['encryptJobId'] for x in HS.candidates(self.cfg, jobs)]
        self.assertEqual(expected, ['high'])
        self.assertEqual([j['encryptJobId'] for j in B.open_pool(HS.load_state(self.cfg), jobs, self.cfg)], expected)

    def test_pool_fallback_matches_tick_without_changing_snapshot_pool(self):
        st = HS.load_state(self.cfg)
        snapshot = dict(self.job('snapshot'), _src=['示例城市_城市经理_0.json'])
        for jid in ('extra', 'role-miss', 'blocked', 'sent', 'checked', 'low'):
            job = dict(self.job(jid), _src=['示例城市_城市经理_0.json'], bossOnline=True)
            if jid == 'role-miss':
                job['jobName'] = '其它'
            if jid == 'blocked':
                job['jobValidStatus'] = 0
            if jid == 'low':
                job['bossOnline'] = False
                job['jobName'] = '其它'
            st['b_pool'][jid] = {'job': job, 'src': ['示例城市_城市经理_0.json']}
        st['sent']['sent'] = {}
        st['checked_jobs']['checked'] = HF.grade_fingerprint(st['b_pool']['checked']['job'], self.cfg)
        self.cfg['scoring'] = {'min_score': 1, 'keyword_in_title': 2, 'boss_online': 1}
        before = copy.deepcopy(st)
        HS.save_state(self.cfg, st)
        Path(self.cfg['paths']['raw_dir'], '示例城市_城市经理_0.json').write_text(
            json.dumps({'code': 0, 'zpData': {'jobList': [snapshot]}}))
        _, output = self.invoke('grades', '--by-src', '--json')
        data = json.loads(output)
        self.assertEqual(data['open_unique'], 1)
        self.assertEqual(data['open_with_bpool'], 3)
        self.assertEqual(data['by_src'][0]['open_with_bpool'], 3)
        self.assertEqual(len(B.open_pool(st, [snapshot], self.cfg)), 1)
        self.assertEqual(st, before)

    def test_historical_source_priority_and_legacy_unknown(self):
        src = '示例城市_城市经理_0.json'
        jobs = [dict(self.job('current'), _src=[src])]
        st = HS.load_state(self.cfg)
        st['grade_cache']['current'] = {'jd_text': '普通职责'}
        st['sent']['old'] = {'src': src}
        st['grade_cache']['old'] = {'job': self.job('old')}
        st['b_pool']['old'] = {'src': 'pool.json', 'job': self.job('old')}
        st['sent']['current'] = {'src': 'sent.json'}
        st['b_pool']['current'] = {'src': 'pool.json', 'job': self.job('current')}
        st['b_pool']['extra'] = {'src': ['pool.json'], 'job': self.job('extra')}
        st['sent']['fallback'] = {}
        st['b_pool']['fallback'] = {'src': 'fallback.json', 'job': self.job('fallback')}
        st['grade_cache']['cache-only'] = {'job': dict(self.job('cache-only'), _src=['ignored.json'])}
        st['sent']['legacy'] = '2020-01-01 00:00'
        before = copy.deepcopy(st)
        rows = {r['src']: r for r in B.grades_by_src(self.cfg, st, jobs)}
        self.assertEqual(rows[src]['sent'], 2)
        self.assertEqual(rows['pool.json']['b_pool'], 1)
        self.assertEqual(rows['unknown']['sent'], 1)
        self.assertEqual(rows['fallback.json']['sent'], 1)
        self.assertNotIn('ignored.json', rows)
        self.assertNotIn('sent.json', rows)
        self.assertEqual(st, before)

    def test_sent_source_without_snapshot_prevents_zero_output(self):
        src = '示例城市_城市经理_0.json'
        jobs = [dict(self.job('current'), _src=[src])]
        st = HS.load_state(self.cfg)
        st['grade_cache']['current'] = {'jd_text': '普通职责'}
        st['sent']['old'] = {'src': src}
        self.assertEqual(B.zero_output_keywords(self.cfg, B.grades_by_src(self.cfg, st, jobs)), [])
        st['sent']['old'] = {}
        self.assertEqual(B.zero_output_keywords(self.cfg, B.grades_by_src(self.cfg, st, jobs)), ['城市经理'])

    def test_load_legacy_sent_string_and_default_source(self):
        st = HS.load_state(self.cfg)
        st['sent'] = {'legacy': '2020-01-01 00:00', 'dict': {'grade': 'A'}}
        HS.save_state(self.cfg, st)
        loaded = HS.load_state(self.cfg)
        self.assertEqual(loaded['sent']['legacy'], {'ts': '2020-01-01 00:00'})
        self.assertEqual(loaded['sent']['dict'], {'grade': 'A'})
        rows = B.grades_by_src(self.cfg, loaded, [])
        self.assertEqual((rows[0]['src'], rows[0]['sent']), ('unknown', 2))
        for invalid in (1, [], None):
            st['sent']['legacy'] = invalid
            HS.save_state(self.cfg, st)
            with self.assertRaises(ValueError):
                HS.load_state(self.cfg)

    def test_mixed_no_jd_suppresses_zero_output_and_warning_has_counts(self):
        src = '示例城市_城市经理_0.json'
        jobs = [dict(self.job(str(i)), _src=[src]) for i in range(21)]
        st = HS.load_state(self.cfg)
        st['grade_cache']['0'] = {'jd_text': '普通职责'}
        rows = B.grades_by_src(self.cfg, st, jobs)
        self.assertEqual(rows[0]['no_jd'], 20)
        self.assertEqual(B.zero_output_keywords(self.cfg, rows), [])
        Path(self.cfg['paths']['raw_dir'], src).write_text(json.dumps({'code': 0, 'zpData': {'jobList': jobs}}))
        HS.save_state(self.cfg, st)
        _, output = self.invoke('validate', '--json')
        self.assertFalse(any('零产出关键词「' in w for w in json.loads(output)['warnings']))
        for j in jobs:
            st['grade_cache'][j['encryptJobId']] = {'jd_text': '普通职责'}
        HS.save_state(self.cfg, st)
        _, output = self.invoke('validate', '--json')
        warning = next(w for w in json.loads(output)['warnings'] if '零产出关键词「' in w)
        self.assertIn('no_jd=0', warning)
        self.assertIn('已判数=21', warning)

    def test_send_records_sources_without_reading_diagnostic_src(self):
        class DiagnosticOnly(dict):
            def get(self, key, *args):
                if key == 'src':
                    raise AssertionError('sending read diagnostic src')
                return super().get(key, *args)

            def __getitem__(self, key):
                if key == 'src':
                    raise AssertionError('sending read diagnostic src')
                return super().__getitem__(key)

        for source in ('source.json', ['source.json', 'page2.json'], None):
            with self.subTest(source=source):
                st = HS.load_state(self.cfg)
                st['sent'] = {'past': DiagnosticOnly(src='forbidden')}
                a, b = self.job('a'), self.job('b', '示例品牌行业')
                if source is not None:
                    a['_src'] = b['_src'] = source
                st['b_pool'] = {'b': DiagnosticOnly(src='forbidden', job=b)}
                def save(cfg, state):
                    # Install the access guard on newly written records too.
                    for key in ('sent', 'b_pool'):
                        for jid, row in list(state[key].items()):
                            state[key][jid] = DiagnosticOnly(row)
                def prepare(cfg, br, tab, job, state):
                    return dict(grade='A' if job['encryptJobId'] == 'a' else 'B',
                                final=True, status='ready', reason='test', cached=True)
                with patch.object(HF, 'load_jobs', return_value=[a, b]), \
                     patch.object(HS, 'save_state', side_effect=save), \
                     patch.object(O, 'prepare_grade', side_effect=prepare), \
                     patch.object(HQ, 'quotas_for_now', side_effect=[{'A': 1, 'B': 1}, {'A': 0, 'B': 1}]), \
                     patch.object(O, 'send_one', side_effect=['sent', 'skip_min_gap:60', 'sent']), patch.object(B.time, 'sleep'):
                    result = O.do_send(self.cfg, None, None, st, 2, [])
                    self.assertEqual(result['sent'], 1)
                    expected = source if source is not None else 'unknown'
                    self.assertEqual(dict(st['sent']['a'])['src'], expected)
                    self.assertEqual(dict(st['b_pool']['b'])['src'], expected)
                    result = O.do_send(self.cfg, None, None, st, 2, [])
                    self.assertEqual(result['sent'], 1)
                    self.assertEqual(dict(st['sent']['b'])['src'], expected)

    def test_regrade_adds_pool_counts_without_changing_existing_fields(self):
        jobs = [self.job('snapshot'), self.job('c')]
        st = HS.load_state(self.cfg)
        st['grade_cache']['c'] = {'jd_text': '普通职责'}
        baseline = B.regrade_report(self.cfg, st, jobs)
        for jid in ('extra', 'downgraded', 'sent'):
            st['b_pool'][jid] = {'job': self.job(jid)}
        st['b_pool']['role-miss'] = {'job': dict(self.job('role-miss'), jobName='其它')}
        st['sent']['sent'] = {}
        st['grade_cache']['downgraded'] = {'jd_text': '普通职责'}
        before = copy.deepcopy(st)
        data = B.regrade_report(self.cfg, st, jobs, alt=copy.deepcopy(self.cfg))
        for label in ('current', 'alt'):
            funnel = data['list'][label]
            old = {k: v for k, v in baseline['list']['current'].items() if not k.endswith('_with_bpool')}
            self.assertEqual({k: funnel[k] for k in old}, old)
            self.assertEqual(funnel['open_role_matched_with_bpool'], 5)
            self.assertEqual(funnel['fresh_pool_with_bpool'], 3)
            self.assertEqual((funnel['open_role_matched'], funnel['fresh_pool']), (2, 1))
        self.assertEqual(st, before)
        self.raw(jobs)
        HS.save_state(self.cfg, st)
        code, output = self.invoke('regrade', '--json')
        self.assertEqual(code, 0)
        self.assertEqual(json.loads(output)['list']['current'], data['list']['current'])
