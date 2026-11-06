"""Broadcast/tick regressions: temporary storage, no live browser or model."""
import copy
import importlib.util
import json
import os
import subprocess
import sys
import tempfile
import unittest
from contextlib import ExitStack
from datetime import datetime
from pathlib import Path
from unittest.mock import Mock, patch

SCRIPTS = Path(__file__).resolve().parents[1] / 'scripts'
sys.path.insert(0, str(SCRIPTS))
import boss_hunt as B
import hunt_config as HC
import hunt_schedule as HQ
import hunt_state as HS
import outbound as O
spec = importlib.util.spec_from_file_location('tick_fmt', SCRIPTS / 'boss-hunt-tick-fmt.py')
F = importlib.util.module_from_spec(spec)
spec.loader.exec_module(F)


class BroadcastTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix='broadcast-test-')
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.cfg = {'paths': {'workdir': str(self.root), 'state': str(self.root / 'state.json'),
                              'log': str(self.root / 'runs.log')},
                    'schedule': {'timezone': 'Asia/Shanghai', 'windows': ['20:00'],
                                 'scan_windows': ['20:00']}}
        self.now = datetime(2026, 10, 29, 20, 0, tzinfo=HC.cfg_tz(self.cfg))
        self.st = {'day': '2026-10-29', 'sent': {}, 'ticks_done': []}
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        self.stack.enter_context(patch.object(B.CM, "wake_extension"))
        self.stack.enter_context(patch.object(B.time, "sleep"))
        self.stack.enter_context(patch.object(HC, 'now_local', return_value=self.now))
        self.browser = Mock()
        self.browser.opened = []
        self.browser.json.return_value = {}
        self.browser.connect.return_value = 'bridge_ok'
        self.browser.close_own_tabs.return_value = 0
        self.stack.enter_context(patch.object(B.CM, 'Browser', return_value=self.browser))
        self.stack.enter_context(patch.object(O, 'check_login', return_value=(1, {'avatar': True})))
        self.stack.enter_context(patch.object(B, 'ledger_guard'))
        self.stack.enter_context(patch.object(HS, 'migrate_ledger'))
        self.stack.enter_context(patch.object(O, 'scan', return_value=(1, [])))
        self.sender = self.stack.enter_context(patch.object(O, 'do_send', return_value={'sent': 0}))
        self.save()

    def save(self):
        HS.save_state(self.cfg, self.st)

    def tick(self, now=None):
        self.out = []
        return B.run_tick(self.cfg, self.st, now or self.now, self.out)

    def test_T1_today_sent_seven(self):
        items = [{'brand': '品牌%d' % n, 'name': '职位%d' % n} for n in range(9)]
        text = '\n'.join(F.render({'window': '20:00', 'today_sent': {'count': 9, 'items': items}}))
        self.assertIn('📋 今日已发 9', text)
        self.assertIn('· 品牌0 ｜ 职位0', text)
        self.assertIn('· 品牌7 ｜ 职位7', text)
        self.assertIn('另有 1 个未列出', text)
        self.assertNotIn('品牌8', text)
        self.assertNotIn('(A)', text)
        self.assertEqual(F.render({'today_sent': {'count': 0, 'items': []}}), [])
        # 窗口外的补跑保持静默：没有 window 时即便今日已发也不出这一行
        self.assertEqual(F.render({'today_sent': {'count': 7, 'items': [
            {'brand': '品牌0', 'grade': 'A'}]}}), [])

    def test_T2_log_and_write_failure_preserve_stdout(self):
        payload = {'window': '20:00', 'send': {'sent': 1, 'day_count': 1, 'daily_cap': 20}}
        def run(path, data=payload):
            return subprocess.run([sys.executable, str(SCRIPTS / 'boss-hunt-tick-fmt.py')],
                                  input=json.dumps(data), text=True, capture_output=True,
                                  cwd=self.root, env=dict(os.environ, BOSS_BROADCAST_LOG=str(path)))
        log = self.root / 'nested' / 'broadcast.log'
        good = run(log)
        bad = run(self.root)  # directory cannot be opened as a file
        self.assertEqual(good.returncode, 0)
        self.assertEqual(bad.returncode, 0)
        self.assertEqual(good.stdout, bad.stdout)
        self.assertEqual(good.stdout, '\n'.join(F.render(payload)) + '\n')
        self.assertTrue(bad.stderr)
        stored = log.read_text()
        self.assertIn('window=20:00', stored)
        self.assertIn(good.stdout + '---\n', stored)
        run(log)
        self.assertEqual(log.read_text().count('window=20:00'), 2)
        empty_log = self.root / 'empty.log'
        self.assertEqual(run(empty_log, {}).stdout, '')
        self.assertFalse(empty_log.exists())

    def test_T3_inspect_cap_after_success(self):
        data = {'send': {'sent': 1, 'stop_reason': 'inspect_cap',
                         'quiet': {'JD读取上限，留到下一跳': 6}}}
        self.assertIn('⏳ JD读取到上限，还有 6 个候选留下跳', F.render(data))
        data['send']['quiet'] = {}
        self.assertIn('⏳ JD读取到上限，部分候选留下跳', F.render(data))

    def test_T4_missed_boundaries_and_dedup(self):
        for minute, expected in [(5, []), (10, []), (11, [{'window': '20:00', 'delay_min': 11}]),
                                 (41, [{'window': '20:00', 'delay_min': 41}])]:
            with self.subTest(minute=minute):
                self.assertEqual(HQ.missed_windows(self.cfg, self.st, self.now.replace(minute=minute)), expected)
        self.assertEqual(HQ.missed_windows(self.cfg, self.st, self.now.replace(hour=21, minute=30)),
                         [{'window': '20:00', 'delay_min': 90}])
        self.assertEqual(HQ.missed_windows(self.cfg, self.st, self.now.replace(hour=21, minute=31)), [])
        for key in ('ticks_done', 'missed_reported'):
            state = dict(self.st, **{key: ['2026-10-29 20:00']})
            self.assertEqual(HQ.missed_windows(self.cfg, state, self.now.replace(minute=41)), [])
        before = copy.deepcopy(self.st)
        HQ.missed_windows(self.cfg, self.st, self.now.replace(minute=41))
        self.assertEqual(self.st, before)

    def test_T5_failure_audit_original_error_survives_save_error(self):
        self.browser.connect.side_effect = RuntimeError('extension_offline')
        result = self.tick()
        self.assertEqual(result['error'], 'extension_offline')
        self.assertEqual(self.st['ticks_done'], [])
        self.assertEqual(len(self.st['ticks_failed']), 1)
        self.assertEqual(self.st['ticks_failed'][0]['key'], '2026-10-29 20:00')
        self.assertEqual(json.loads(Path(self.cfg['paths']['state']).read_text())['ticks_failed'], self.st['ticks_failed'])
        with patch.object(HS, 'save_state', side_effect=OSError('disk full')):
            result = self.tick()
        self.assertEqual(result['error'], 'extension_offline')
        self.assertTrue(any('disk full' in line for line in self.out))

    def test_T6_scan_network_degrades_and_continues(self):
        for scan_result in (RuntimeError('scan_network_failed'), RuntimeError(), (0, []), (2, ['network'])):
            with self.subTest(scan=scan_result), patch.object(O, 'scan') as scan:
                self.st['ticks_done'] = []
                self.save()
                if isinstance(scan_result, Exception):
                    scan.side_effect = scan_result
                else:
                    scan.return_value = scan_result
                self.sender.reset_mock()
                result = self.tick()
                self.assertTrue(result['ok'])
                self.assertTrue(result['scan_error'])
                self.assertEqual(result['scan']['pages_saved'], 0)
                self.sender.assert_called_once()
                self.assertEqual(self.st.get('ticks_failed', []), [])
                self.assertEqual(self.st['ticks_done'], ['2026-10-29 20:00'])
                self.assertIn('本跳用旧快照继续投', '\n'.join(F.render(result)))

    def test_T7_scan_risk_login_captcha_timeout_abort(self):
        for error in ('risk_code_37', 'not_logged_in', 'captcha', TimeoutError('budget')):
            with self.subTest(error=error), patch.object(O, 'scan') as scan:
                self.st.pop('ticks_failed', None)
                self.save()
                if isinstance(error, Exception):
                    scan.side_effect = error
                else:
                    scan.return_value = (0, [error])
                self.sender.reset_mock()
                result = self.tick()
                self.assertFalse(result['ok'])
                self.sender.assert_not_called()
                self.assertEqual(len(self.st['ticks_failed']), 1)
                self.assertEqual(self.st['ticks_done'], [])

    def test_T8_today_snapshot_after_send_and_outside_window(self):
        self.st['sent'] = {str(n): {'brand': '品牌%d' % n, 'grade': 'A',
                                  'applied_ts': '2026-10-29 19:%02d:00' % n} for n in range(10)}
        self.st['sent']['old'] = {'brand': '昨天', 'ts': '2026-10-28 20:00:00'}
        self.save()
        before = Path(self.cfg['paths']['state']).read_bytes()
        result = self.tick(self.now.replace(hour=23))
        self.assertEqual(result['today_sent']['count'], 10)
        self.assertEqual(len(result['today_sent']['items']), 8)
        self.assertEqual(result['today_sent']['items'][0]['brand'], '品牌9')
        self.assertEqual(result['today_sent']['items'][0]['grade'], 'A')
        self.assertEqual(Path(self.cfg['paths']['state']).read_bytes(), before)
        def send(*args, **kwargs):
            self.st['sent']['new'] = {'brand': '新品牌', 'grade': 'B', 'ts': '2026-10-29 20:00:00'}
            return {'sent': 1}
        self.sender.side_effect = send
        self.assertEqual(self.tick()['today_sent']['count'], 11)

    def test_T9_missed_saved_once_no_actions_or_browser(self):
        late = self.now.replace(minute=41)
        result = self.tick(late)
        self.assertEqual(result['missed'], [{'window': '20:00', 'delay_min': 41}])
        self.assertIn('⏰ 20:00 跳被吞了（晚 41 分钟，超出 ±10 分钟容差），额度已顺延', F.render(result))
        self.assertEqual(self.tick(late)['missed'], [])
        self.browser.connect.assert_not_called()
        self.sender.assert_not_called()
        self.assertEqual(self.st['missed_reported'], ['2026-10-29 20:00'])

    def test_T11_missed_in_active_window_and_save_failure(self):
        self.cfg['schedule']['windows'] = ['19:20', '20:00']
        self.cfg['schedule']['inbox_windows'] = ['20:00']
        self.cfg['schedule']['summary_window'] = '20:00'
        with patch.object(B, 'inbox', return_value={}) as inbox, \
                patch.object(B, 'daily_summary', return_value={'activity': False}) as summary, \
                patch.object(B, 'summary_lines', return_value=[]), \
                patch.object(O, 'scan', side_effect=RuntimeError('network')), \
                patch.object(HS, 'save_state', side_effect=[OSError('disk full'), None]):
            result = self.tick()
        self.assertTrue(result['ok'])
        self.assertEqual(result['missed'], [{'window': '19:20', 'delay_min': 40}])
        self.sender.assert_called_once()
        inbox.assert_called_once()
        summary.assert_called_once()
        self.assertTrue(any('disk full' in line for line in self.out))

    def test_T12_audit_and_report_history_capped(self):
        self.st['missed_reported'] = ['old%d' % n for n in range(64)]
        self.save()
        self.tick(self.now.replace(minute=41))
        self.assertEqual(len(self.st['missed_reported']), 64)
        self.assertEqual(self.st['missed_reported'][0], 'old1')
        self.st['ticks_failed'] = [{'key': 'old%d' % n} for n in range(64)]
        self.save()
        self.browser.connect.side_effect = RuntimeError('extension_offline')
        self.tick()
        self.assertEqual(len(self.st['ticks_failed']), 64)
        self.assertEqual(self.st['ticks_failed'][0], {'key': 'old1'})
        self.assertEqual(self.st['ticks_failed'][-1]['error'], 'extension_offline')

    def test_T13_pre_key_failure_preserves_error(self):
        with patch.object(HS, 'load_state', side_effect=RuntimeError('load_failed')):
            result = self.tick()
        self.assertEqual(result['error'], 'load_failed')
        self.assertIsNone(self.st['ticks_failed'][-1]['key'])

    def test_T14_invalid_state_not_overwritten_by_failure_audit(self):
        state = Path(self.cfg['paths']['state'])
        for raw in ('{broken', '{"sent": [], "day": "2026-10-29"}'):
            with self.subTest(raw=raw):
                state.write_text(raw)
                result = B.run_tick(self.cfg, {}, self.now, [])
                self.assertFalse(result['ok'])
                self.assertEqual(state.read_text(), raw)
        self.browser.connect.assert_not_called()

    def test_T15_parse_failure_broadcasts_also_logged(self):
        for raw, expected in [('no json', '输出里没有 JSON'), ('{bad', '输出无法解析')]:
            with self.subTest(raw=raw):
                log = self.root / 'parse.log'
                result = subprocess.run([sys.executable, str(SCRIPTS / 'boss-hunt-tick-fmt.py')],
                                        input=raw, text=True, capture_output=True, cwd=self.root,
                                        env=dict(os.environ, BOSS_BROADCAST_LOG=str(log)))
                self.assertEqual(result.returncode, 0)
                self.assertIn(expected, result.stdout)
                self.assertTrue(log.exists())
                self.assertIn(result.stdout + '---\n', log.read_text())
                self.assertIn('window=?', log.read_text())

    def test_T16_degraded_scan_still_marks_window_done(self):
        with patch.object(O, 'scan', side_effect=RuntimeError('scan_network_failed')):
            result = self.tick()
            self.assertTrue(result['ok'])
            self.assertEqual(self.st['ticks_done'], ['2026-10-29 20:00'])
            stored = json.loads(Path(self.cfg['paths']['state']).read_text())
            self.assertEqual(stored['ticks_done'], ['2026-10-29 20:00'])
            self.assertEqual(self.tick()['reason'], 'already_done')
        self.sender.assert_called_once()

    def test_T17_missed_excludes_failed_windows(self):
        self.st['ticks_failed'] = [{'key': None}, {'key': '2026-10-29 20:00'}]
        late = self.now.replace(minute=41)
        self.assertEqual(HQ.missed_windows(self.cfg, self.st, late), [])
        self.st['ticks_failed'][1]['key'] = '2026-10-28 20:00'
        self.assertEqual(HQ.missed_windows(self.cfg, self.st, late),
                         [{'window': '20:00', 'delay_min': 41}])

    def test_T18_captcha_after_degraded_scan_aborts(self):
        for verdict in ('captcha', 'not_logged_in', ''):
            with self.subTest(verdict=verdict):
                self.st['ticks_done'] = []
                self.st['ticks_failed'] = []
                self.save()
                self.sender.reset_mock()
                with patch.object(O, 'scan', side_effect=RuntimeError('scan_network_failed')), \
                        patch.object(O, 'page_guard', return_value=verdict, create=True) as guard:
                    result = self.tick()
                guard.assert_called_once_with(self.browser, 1)
                self.assertEqual(result['scan_error'], 'scan_network_failed')
                if verdict:
                    self.assertFalse(result['ok'])
                    self.assertEqual(result['error'], 'scan_' + verdict)
                    self.sender.assert_not_called()
                    self.assertEqual(len(self.st['ticks_failed']), 1)
                    self.assertEqual(self.st['ticks_done'], [])
                else:
                    self.assertTrue(result['ok'])
                    self.sender.assert_called_once()

    def test_T19_inspect_cap_zero_sent_only_one_reason(self):
        for quiet in ({}, {'JD读取上限，留到下一跳': 6}):
            with self.subTest(quiet=quiet):
                lines = F.render({'send': {'sent': 0, 'stop_reason': 'inspect_cap', 'quiet': quiet}})
                self.assertEqual(sum('JD读取到上限' in line for line in lines), 1)
                self.assertFalse(any(line.startswith('⏳') for line in lines))

    def test_T20_page_guard_verdicts_and_read_errors(self):
        for state, expected in [({'cap': True, 'login': True}, 'captcha'),
                                ({'login': True}, 'not_logged_in'), ({}, ''), (None, ''),
                                (RuntimeError('unavailable'), '')]:
            with self.subTest(state=state):
                self.browser.json.side_effect = state if isinstance(state, Exception) else None
                self.browser.json.return_value = state
                self.assertEqual(O.page_guard(self.browser, 42), expected)

    def test_T21_guard_only_after_degradation_and_before_send(self):
        with patch.object(O, 'page_guard', create=True) as guard:
            self.assertTrue(self.tick()['ok'])
            guard.assert_not_called()
        self.st['ticks_done'] = []
        self.save()
        self.sender.reset_mock()
        self.browser.opened = [1, 42]
        def guard_before_send(br, tab):
            self.sender.assert_not_called()
            self.assertEqual(tab, 42)  # scan opened a separate search tab
            return 'captcha'
        with patch.object(O, 'scan', side_effect=RuntimeError('scan_network_failed')), \
                patch.object(O, 'page_guard', side_effect=guard_before_send, create=True) as guard:
            self.assertFalse(self.tick()['ok'])
        guard.assert_called_once()
        self.sender.assert_not_called()

    def test_T10_state_field_types(self):
        for key in ('ticks_failed', 'missed_reported'):
            Path(self.cfg['paths']['state']).write_text(json.dumps({key: {}}))
            with self.subTest(key=key), self.assertRaisesRegex(ValueError, key):
                HS.load_state(self.cfg)


if __name__ == '__main__':
    unittest.main()
