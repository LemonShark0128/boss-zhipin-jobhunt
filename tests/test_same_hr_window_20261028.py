"""Offline contact cooldown regression fixtures; no real runtime data."""
import io
import json
from contextlib import redirect_stdout
from unittest.mock import patch

from test_phase_a import Base, B
import hunt_config as HC
import hunt_schedule as HQ
import hunt_state as HS
import outbound as O
import send_safety as SS


class SameHRWindowTests(Base):
    def setUp(self):
        super().setUp()
        self.now = HC.now_local(self.cfg).replace(microsecond=0)
        self.st = HS.load_state(self.cfg)
        self.jobs = [self.job(str(i)) for i in range(4)]
        for job in self.jobs:
            job.update(brandName='品牌', bossName='HR')
        self.st['contacts'] = ['品牌|HR']

    def ledger(self, rows):
        with open(SS.ledger_path(self.cfg), 'w') as f:
            for row in rows:
                f.write(json.dumps(row) + '\n')

    def row(self, days=8, status='sent', **extra):
        row = dict(event_id='fixture', cmd='send', brand='品牌', boss='HR', status=status,
                    ts_epoch=int((self.now.timestamp() - days * 86400) * 1000), **extra)
        row.setdefault('ts', B.datetime.fromtimestamp(self.now.timestamp() - days * 86400, HC.cfg_tz(self.cfg)).isoformat())
        return row

    def check_pool(self, expected):
        with patch.object(HS, 'load_state', return_value=self.st), \
             patch.object(HC, 'now_local', return_value=self.now):
            cs = HS.candidates(self.cfg, self.jobs, st=self.st)
            pool = B.open_pool(self.st, self.jobs, self.cfg)
        self.assertEqual({x['job']['encryptJobId'] for x in cs}, set(expected))
        self.assertEqual({x['encryptJobId'] for x in pool}, set(expected))
        for item in cs:
            self.assertIn('same_hr_window_pass', item['flags'])

    def test_missing_and_null_configuration_preserve_permanent_skip(self):
        self.ledger([self.row()])
        self.check_pool([])
        self.cfg['filters']['same_hr_window_days'] = None
        self.check_pool([])

    def test_seven_days_old_recent_and_exact_boundary(self):
        for days, expected in [(8, ['0', '1', '2', '3']), (3, []), (7, ['0', '1', '2', '3'])]:
            with self.subTest(days=days):
                self.cfg.pop('_contact_last_sent_cache', None)
                self.cfg['filters']['same_hr_window_days'] = 7
                self.ledger([self.row(days)])
                self.check_pool(expected)

    def test_zero_disables_contact_cooldown(self):
        self.cfg['filters']['same_hr_window_days'] = 0
        self.ledger([self.row(0)])
        self.check_pool(['0', '1', '2', '3'])

    def test_only_pending_is_not_sent(self):
        self.cfg['filters']['same_hr_window_days'] = 7
        self.ledger([self.row(0, 'pending')])
        self.check_pool([])

    def test_missing_ledger_preserves_legacy_except_zero(self):
        self.cfg['filters']['same_hr_window_days'] = 7
        self.check_pool([])
        self.cfg['filters']['same_hr_window_days'] = 0
        self.check_pool(['0', '1', '2', '3'])

    def test_other_guards_still_apply(self):
        self.cfg['filters']['same_hr_window_days'] = 7
        self.ledger([self.row()])
        self.st['sent']['0'] = {'ts': 'old'}
        self.jobs[1]['salaryDesc'] = '1-2K'
        self.cfg['filters']['salary_min_k'] = 10
        self.check_pool(['2', '3'])

    def test_timestamp_fallback_latest_sent_and_bad_lines_cached(self):
        self.cfg['filters']['same_hr_window_days'] = 7
        old = self.now.timestamp() - 8 * 86400
        self.ledger([self.row(10), dict(brand='品牌', boss='HR', status='sent',
                     ts_epoch='invalid', ts=B.datetime.fromtimestamp(old, HC.cfg_tz(self.cfg)).isoformat()),
                     self.row(0, 'pending'), self.row(0, 'failed')])
        with open(SS.ledger_path(self.cfg), 'a') as f:
            f.write('broken\n[]\n{"status":"sent"}\n')
        with patch('builtins.open', wraps=open) as reader:
            self.assertEqual(HS.contact_last_sent(self.st, self.cfg), {'品牌|HR': old})
            self.check_pool(['0', '1', '2', '3'])
        self.assertEqual(reader.call_count, 1)
        self.assertNotIn('_contact_last_sent_cache', self.st)

    def test_tick_budget_window_dry_and_outside_window(self):
        self.cfg['filters']['same_hr_window_days'] = 7
        self.path.write_text(json.dumps(self.cfg))
        args = B.build_parser().parse_args(['--config', str(self.path), 'tick', '--dry-run', '--json'])
        output = io.StringIO()
        with redirect_stdout(output):
            self.assertEqual(args.func(args), 0)
        self.assertEqual(json.loads(output.getvalue())['budget']['same_hr_window_days'], 7)
        outside = self.now.replace(hour=1, minute=0)
        self.assertEqual(B.run_tick(self.cfg, self.st, outside, [])['budget']['same_hr_window_days'], 7)

    def test_historical_seconds_are_supported(self):
        self.cfg['filters']['same_hr_window_days'] = 7
        row = self.row(8)
        row['ts_epoch'] = self.now.timestamp() - 8 * 86400
        self.ledger([row])
        self.check_pool(['0', '1', '2', '3'])

    def test_unknown_timestamp_blocks_with_specific_reason_except_zero(self):
        self.ledger([])
        self.cfg['filters']['same_hr_window_days'] = 7
        self.check_pool([])
        self.assertEqual(HS.same_hr_filter(self.st, self.cfg, self.jobs[0]),
                         'skip_same_hr_unknown_ts')
        self.raw(self.jobs[:2])
        with patch.object(HC, 'now_local', return_value=self.now):
            result = O.do_send(self.cfg, None, None, self.st, 2, [])
        self.assertEqual([x['result'] for x in result['skipped']],
                         ['skip_same_hr_unknown_ts'] * 2)
        self.cfg['filters']['same_hr_window_days'] = 0
        self.check_pool(['0', '1', '2', '3'])

    def test_same_tick_sends_only_one_and_updates_contact_cache(self):
        for history, days in ((False, 7), (True, 7), (True, 0)):
            with self.subTest(history=history, days=days):
                self.cfg.pop('_contact_last_sent_cache', None)
                self.cfg['filters']['same_hr_window_days'] = days
                self.cfg['sending']['grade_quota'] = {'A': 30, 'B': 0}
                self.st = HS.load_state(self.cfg)
                self.st.update(sent={}, contacts=['品牌|HR'] if history else [], day_count=0,
                               day_by_grade={}, day_by_target={})
                self.ledger([self.row(8)] if history else [])
                self.raw(self.jobs[:2])
                # Offline page/send doubles only; state/cache/selection remain real.
                with patch.object(HC, 'now_local', return_value=self.now.replace(hour=22)), \
                     patch.object(O, 'inspect_job', return_value=('ready', '商户拓展与开城')), \
                     patch.object(O, 'send_one', return_value='sent'), \
                     patch.object(B.time, 'sleep'):
                    result = O.do_send(self.cfg, None, None, self.st, 2, [])
                    self.assertEqual(result['sent'], 1)
                    self.assertEqual([x['result'] for x in result['skipped']], ['skip_same_hr'])
                    self.assertEqual(HS.contact_last_sent(self.st, self.cfg)['品牌|HR'],
                                     self.now.replace(hour=22).timestamp())
                self.assertEqual(len(self.st['sent']), 1)

    def test_invalid_windows_rejected_by_validate_and_load_config(self):
        for value in ('7', True, -1, 7.0):
            with self.subTest(value=value):
                self.cfg['filters']['same_hr_window_days'] = value
                self.assertTrue(any('same_hr_window_days' in e for e in HQ.validate_cfg(self.cfg)[0]))
                self.path.write_text(json.dumps(self.cfg))
                with self.assertRaisesRegex(ValueError, 'same_hr_window_days'):
                    HC.load_config(str(self.path))
                args = B.build_parser().parse_args(['--config', str(self.path), 'validate', '--json'])
                output = io.StringIO()
                with redirect_stdout(output):
                    self.assertEqual(args.func(args), 1)
                data = json.loads(output.getvalue())
                self.assertFalse(data['ok'])
                self.assertIn('same_hr_window_days', ' '.join(data['errors']))

    def test_regrade_same_hr_matches_window_filter(self):
        self.cfg['filters']['same_hr_window_days'] = 7
        self.ledger([self.row(8)])
        with patch.object(HC, 'now_local', return_value=self.now):
            report = B.regrade_report(self.cfg, self.st, self.jobs)
        self.assertEqual(report['list']['current']['same_hr'], 0)

    def test_valid_window_values_load(self):
        for value in (None, 0, 7):
            with self.subTest(value=value):
                self.cfg['filters']['same_hr_window_days'] = value
                self.assertEqual(HQ.validate_cfg(self.cfg)[0], [])
                self.path.write_text(json.dumps(self.cfg))
                self.assertEqual(HC.load_config(str(self.path))['filters']['same_hr_window_days'], value)

    def test_send_uses_in_memory_contacts_including_b_pool(self):
        self.cfg['filters']['same_hr_window_days'] = 7
        self.ledger([])
        self.st['b_pool'] = {'0': {'job': self.jobs[0]}}
        # Disk state has no contact; authoritative in-memory state does.
        with patch.object(O, 'inspect_job', side_effect=AssertionError('must not inspect')):
            result = O.do_send(self.cfg, None, None, self.st, 2, [])
        self.assertEqual(result['sent'], 0)
        self.assertEqual(result['skipped'][0]['result'], 'skip_same_hr_unknown_ts')
