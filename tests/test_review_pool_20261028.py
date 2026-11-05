"""P2-2b only: cache fingerprints/TTL, no review/approve workflow."""
import copy
import json
import os
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import patch

from test_phase_a import Base, B
import hunt_config as HC
import hunt_filter as HF
import hunt_schedule as HQ
import hunt_state as HS
import outbound as O


class FingerprintTests(Base):
    def setUp(self):
        super().setUp()
        self.now = datetime(2026, 10, 28, 22, tzinfo=HC.cfg_tz(self.cfg))
        clock = patch.object(HC, 'now_local', return_value=self.now)
        clock.start(); self.addCleanup(clock.stop)
        sleep = patch.object(B.time, 'sleep')
        sleep.start(); self.addCleanup(sleep.stop)

    def cache(self, jd='商户拓展开城'):
        self.raw([self.job()])
        st = HS.load_state(self.cfg)
        job, = HF.load_jobs(self.cfg)
        with patch.object(O, 'inspect_job', return_value=('ready', jd)):
            O.prepare_grade(self.cfg, None, None, job, st)
        return job, st

    def test_fingerprints_ignore_snapshot_and_volatile_fields(self):
        job, st = self.cache()
        fp = HF.jd_fingerprint(job)
        composite = HF.grade_fingerprint(job, self.cfg)
        raw = Path(self.cfg['paths']['raw_dir'], 'sample.json')
        ns = raw.stat().st_mtime_ns + 1000000000
        os.utime(raw, ns=(ns, ns))
        changed = dict(job, bossOnline=True, _src=['other.json'])
        self.assertEqual(HF.jd_fingerprint(changed), fp)
        self.assertEqual(HF.grade_fingerprint(changed, self.cfg), composite)
        for field, value in [('jobName', '其它经理'), ('salaryDesc', '25-30K')]:
            self.assertNotEqual(HF.jd_fingerprint(dict(job, **{field: value})), fp)
        cfg = copy.deepcopy(self.cfg)
        cfg['grading']['tracks']['A']['min_hits'] = 1
        self.assertNotEqual(HF.rule_fingerprint(cfg), HF.rule_fingerprint(self.cfg))
        self.assertNotEqual(HF.grade_fingerprint(job, cfg), composite)
        self.assertEqual(st['grade_cache']['j1']['job']['jobName'], '城市经理')
        self.assertEqual(set(st['grade_cache']['j1']['job']), set(HF.JD_FIELDS))

    def test_rule_change_regrades_without_inspection_or_probe(self):
        job, st = self.cache('商户拓展')
        self.assertEqual(st['grade_cache']['j1']['grade'], 'C')
        HS.record_outcome(self.cfg, st, job, 'skip_grade', 'grade', 'w1')
        self.cfg['grading']['tracks']['A']['min_hits'] = 1
        self.cfg['sending']['grade_quota'] = {'A': 0, 'B': 8}
        self.cfg['sending']['hidden_b_probe'] = 0
        with patch.object(O, 'inspect_job', side_effect=AssertionError('no network')):
            data = O.do_send(self.cfg, None, None, st, 1, [], window_key='w2')
        self.assertEqual(data['inspects'], 0)
        self.assertEqual(st['grade_cache']['j1']['grade'], 'A')
        self.assertEqual(st['grade_cache']['j1']['rule_fp'], HF.rule_fingerprint(self.cfg))
        self.assertTrue(st['grade_cache']['j1']['regraded'])
        self.assertNotIn('本跳A配额已满，A候选留到下跳', data['quiet'])

    def test_local_regrade_keeps_jd_timestamp_and_updates_all_metadata(self):
        job, st = self.cache('门店运营 外呼')
        ts = st['grade_cache']['j1']['jd_ts']
        self.cfg['filters']['text_block'] = ['外呼']
        gr = HS.cached_grade(self.cfg, job, st)
        self.assertEqual((gr['grade'], gr['cause'], gr['redlines']), ('C', 'redline', ['外呼']))
        self.assertEqual(gr['pre_redline_grade'], 'B')
        self.assertEqual(gr['jd_ts'], ts)
        self.assertTrue(gr['cached']); self.assertTrue(gr['regraded'])
        self.assertEqual(gr['fingerprint'], HF.grade_fingerprint(job, self.cfg))
        self.cfg['filters']['text_block'] = []
        self.assertEqual(HS.cached_grade(self.cfg, job, st)['grade'], 'B')

    def test_expired_disabled_and_changed_job_read_again(self):
        for mode in ('expired', 'disabled', 'changed'):
            with self.subTest(mode=mode):
                self.cfg['grading'].pop('jd_cache_ttl_days', None)
                job, st = self.cache()
                if mode == 'expired':
                    st['grade_cache']['j1']['jd_ts'] = (self.now - timedelta(days=8)).strftime('%Y-%m-%d %H:%M')
                elif mode == 'disabled':
                    self.cfg['grading']['jd_cache_ttl_days'] = 0
                else:
                    job['salaryDesc'] = '25-30K'
                with patch.object(O, 'inspect_job', return_value=('ready', '门店运营')) as reader:
                    gr = O.prepare_grade(self.cfg, None, None, job, st)
                    self.assertEqual(gr['grade'], 'B')
                    reader.assert_called_once()
                    if mode == 'disabled':
                        O.prepare_grade(self.cfg, None, None, job, st)
                        self.assertEqual(reader.call_count, 2)

    def test_ttl_boundary_and_invalid_timestamp(self):
        job, st = self.cache()
        st['grade_cache']['j1']['jd_ts'] = (self.now - timedelta(days=7)).strftime('%Y-%m-%d %H:%M')
        self.assertIsNotNone(HS.cached_jd(self.cfg, job, st))
        st['grade_cache']['j1']['jd_ts'] = 'bad'
        self.assertIsNone(HS.cached_jd(self.cfg, job, st))

    def test_legacy_migration_requires_old_fingerprint_match(self):
        for valid in (True, False):
            with self.subTest(valid=valid):
                job, st = self.cache()
                row = st['grade_cache']['j1']
                for key in ('jd_fp', 'jd_ts', 'rule_fp', 'job'):
                    row.pop(key)
                row['fingerprint'] = HF.legacy_grade_fingerprint(job, self.cfg) if valid else 'stale'
                with patch.object(O, 'inspect_job', return_value=('ready', '门店运营')) as reader:
                    gr = O.prepare_grade(self.cfg, None, None, job, st)
                self.assertEqual(reader.call_count, 0 if valid else 1)
                self.assertEqual(gr['grade'], 'A' if valid else 'B')
                for key in ('jd_fp', 'jd_ts', 'rule_fp', 'job'):
                    self.assertIn(key, st['grade_cache']['j1'])

    def test_validate_jd_ttl_and_reserved_state_maps(self):
        for value in (-1, 61, True, 1.5, '7'):
            self.cfg['grading']['jd_cache_ttl_days'] = value
            self.assertTrue(any('jd_cache_ttl_days' in e for e in HQ.validate_cfg(self.cfg)[0]))
        for value in (0, 7, 60):
            self.cfg['grading']['jd_cache_ttl_days'] = value
            self.assertFalse(HQ.validate_cfg(self.cfg)[0])
        for key in ('review_pool', 'manual_grade'):
            for value in ([], 'bad', {'j1': []}):
                Path(self.cfg['paths']['state']).write_text(json.dumps({key: value}))
                with self.assertRaisesRegex(ValueError, key):
                    HS.load_state(self.cfg)

    def test_chat_not_opened_retries_and_never_marks_opened(self):
        for limit in (0, 1, 2):
            with self.subTest(limit=limit):
                self.cfg['sending']['transient_retry_max'] = limit
                self.raw([self.job()]); st = HS.load_state(self.cfg)
                st['checked_jobs'] = {}; st['retry_jobs'] = {}; st['opened_no_greet'] = {}
                with patch.object(O, 'inspect_job', return_value=('ready', '商户拓展开城')), \
                     patch.object(O, 'send_one', return_value='error: chat_not_opened') as sender:
                    for i in range(limit + 2):
                        O.do_send(self.cfg, None, None, st, 1, [], window_key=str(i))
                self.assertEqual(sender.call_count, limit + 1)
                self.assertIn('j1', st['checked_jobs'])
                self.assertNotIn('j1', st['opened_no_greet'])
                self.assertEqual(st['day_count'], 0)
                self.assertEqual(st['contacts'], [])
