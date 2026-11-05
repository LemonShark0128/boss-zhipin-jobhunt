"""Batch 2b: offline regressions using temporary snapshots and state only."""
import copy
import json
import os
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import patch

from test_phase_a import Base, B
import hunt_config as HC
import hunt_filter as HF
import hunt_state as HS
import outbound as O


class Batch2bTests(Base):
    def setUp(self):
        super().setUp()
        self.now = datetime(2026, 10, 28, 22, tzinfo=HC.cfg_tz(self.cfg))
        clock = patch.object(HC, 'now_local', return_value=self.now)
        clock.start()
        self.addCleanup(clock.stop)
        sleep = patch.object(B.time, 'sleep')
        sleep.start()
        self.addCleanup(sleep.stop)

    def test_opened_jobs_stay_filtered_after_rescan_and_rollover(self):
        for rollover in (False, True):
            with self.subTest(rollover=rollover):
                self.raw([self.job()])
                st = HS.load_state(self.cfg)
                with patch.object(O, 'inspect_job', return_value=('ready', '商户拓展开城')), \
                     patch.object(O, 'send_one', return_value='fill_err') as sender:
                    O.do_send(self.cfg, None, None, st, 1, [], window_key='w1')
                self.assertEqual(sender.call_count, 1)
                if rollover:
                    st['day'] = '2026-10-27'
                HS.save_state(self.cfg, st)
                self.raw([dict(self.job(), salaryDesc='25-30K')])
                with patch.object(O, 'inspect_job', return_value=('ready', '商户拓展开城')) as reader, \
                     patch.object(O, 'send_one', return_value='fill_err') as sender:
                    data = O.do_send(self.cfg, None, None, HS.load_state(self.cfg), 1, [], window_key='w2')
                reader.assert_not_called()
                sender.assert_not_called()
                self.assertEqual(data['items'], [])
                self.assertEqual(data['skipped'], [])
                self.assertEqual(data['inspects'], 0)
                # Reset fixture for the next independent scenario.
                Path(self.cfg['paths']['state']).unlink()

    def test_retry_max_counts_retries_after_first_failure(self):
        for limit in (0, 1, 2):
            with self.subTest(limit=limit):
                self.cfg['sending']['transient_retry_max'] = limit
                self.raw([self.job()])
                st = HS.load_state(self.cfg)
                st['checked_jobs'] = {}; st['retry_jobs'] = {}
                with patch.object(O, 'inspect_job', return_value=('skip_no_button', '')) as reader:
                    for attempt in range(limit + 2):
                        O.do_send(self.cfg, None, None, st, 1, [], window_key=str(attempt))
                        if attempt < limit:
                            self.assertNotIn('j1', st['checked_jobs'])
                            self.assertEqual(st['retry_jobs']['j1']['n'], attempt + 1)
                self.assertEqual(reader.call_count, 1 + limit)
                self.assertIn('j1', st['checked_jobs'])
                self.assertEqual(st['retry_jobs'], {})

    def test_rescan_preserves_retry_count_window_and_new_first_order(self):
        self.raw([self.job('old')])
        st = HS.load_state(self.cfg)
        with patch.object(O, 'inspect_job', return_value=('skip_no_button', '')) as reader:
            O.do_send(self.cfg, None, None, st, 1, [], window_key='w1')
            old_fp = st['retry_jobs']['old']['fp']
            self.raw([self.job('old'), self.job('new')])
            snapshot = Path(self.cfg['paths']['raw_dir'], 'sample.json')
            ns = snapshot.stat().st_mtime_ns + 1000000000
            os.utime(snapshot, ns=(ns, ns))
            st = HS.load_state(self.cfg)
            O.do_send(self.cfg, None, None, st, 1, [], window_key='w1')
            self.assertEqual([c.args[2]['encryptJobId'] for c in reader.call_args_list], ['old', 'new'])
            O.do_send(self.cfg, None, None, st, 1, [], window_key='w2')
        self.assertEqual(st['retry_jobs']['old']['n'], 2)
        self.assertEqual(st['retry_jobs']['old']['fp'], old_fp)
        self.assertEqual([c.args[2]['encryptJobId'] for c in reader.call_args_list], ['old', 'new', 'old', 'new'])

    def test_stable_fingerprint_tracks_content_and_rules_but_not_snapshot(self):
        job = self.job()
        fp = HF.stable_fingerprint(job, self.cfg)
        self.assertEqual(HF.stable_fingerprint(dict(job, _src=['other.json'], _jd='runtime'), self.cfg), fp)
        self.assertNotEqual(HF.stable_fingerprint(dict(job, salaryDesc='25-30K'), self.cfg), fp)
        for key in ('grading', 'filters'):
            cfg = copy.deepcopy(self.cfg)
            cfg[key]['extra'] = True
            self.assertNotEqual(HF.stable_fingerprint(job, cfg), fp)

    def test_summary_ignores_historical_opened_jobs(self):
        st = HS.load_state(self.cfg)
        st['opened_no_greet'] = {'old': {'ts': (self.now - timedelta(days=1)).isoformat(), 'reason': 'fill_err'}}
        data = B.daily_summary(self.cfg, st)
        self.assertEqual(data['opened_no_greet'], 0)
        self.assertFalse(data['activity'])
        self.assertEqual(B.summary_lines(data), ['今天没有活动'])
        st['opened_no_greet']['today'] = {'ts': self.now.isoformat(), 'reason': 'fill_err'}
        data = B.daily_summary(self.cfg, st)
        self.assertEqual(data['opened_no_greet'], 1)
        self.assertTrue(data['activity'])

    def test_load_rejects_malformed_retry_entries(self):
        for row in (None, [], 'bad', {}, {'fp': 1, 'n': 0}, {'fp': 'fp'},
                    {'fp': 'fp', 'n': -1}, {'fp': 'fp', 'n': True},
                    {'fp': 'fp', 'n': 1.5}, {'fp': 'fp', 'n': '1'}):
            with self.subTest(row=row):
                Path(self.cfg['paths']['state']).write_text(json.dumps({'retry_jobs': {'j1': row}}))
                with self.assertRaisesRegex(ValueError, 'invalid retry_jobs entry'):
                    HS.load_state(self.cfg)
        Path(self.cfg['paths']['state']).write_text(json.dumps({'day': '2026-10-28',
            'retry_jobs': {'j1': {'fp': 'fp', 'n': 0}}}))
        self.assertEqual(HS.load_state(self.cfg)['retry_jobs']['j1']['n'], 0)
