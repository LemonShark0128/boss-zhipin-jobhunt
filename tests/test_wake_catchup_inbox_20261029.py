"""Round 3: temporary state and browser/model substitutes only."""
import unittest
from unittest.mock import Mock, patch

import test_broadcast_visibility_20261029 as V

B, F = V.B, V.F
import hunt_state as HS
import outbound as O
import outbound as O


class TickRecoveryTests(unittest.TestCase):
    setUp = V.BroadcastTests.setUp
    save = V.BroadcastTests.save
    tick = V.BroadcastTests.tick

    def test_T20_wake_on_offline(self):
        for error in ('extension_offline', 'bridge_offline'):
            for recovered in (True, False):
                with self.subTest(error=error, recovered=recovered):
                    self.st['ticks_done'] = []
                    self.st['ticks_failed'] = []
                    self.save()
                    self.browser.connect.side_effect = [RuntimeError(error), 'bridge_ok' if recovered else RuntimeError(error)]
                    with patch.object(B.sys, 'platform', 'darwin'), patch.object(B.CM, 'wake_extension') as wake:
                        data = self.tick()
                    wake.assert_called_once_with(self.cfg)
                    self.assertEqual(data['ok'], recovered)
                    self.assertEqual(len(self.st['ticks_failed']), 0 if recovered else 1)
                    self.assertEqual(self.st['ticks_done'], ['2026-10-29 20:00'] if recovered else [])
                    if recovered:
                        self.assertIn('桥离线已唤醒 Chrome 并恢复', self.out)

    def test_wake_only_on_mac_and_known_errors(self):
        for platform, error in [('linux', 'extension_offline'), ('darwin', 'other_error')]:
            self.browser.connect.reset_mock()
            self.browser.connect.side_effect = RuntimeError(error)
            with self.subTest(platform=platform), patch.object(B.sys, 'platform', platform), patch.object(B.CM, 'wake_extension') as wake:
                self.assertFalse(self.tick()['ok'])
                wake.assert_not_called()
                self.browser.connect.assert_called_once_with(fast=True)

    def test_T21_catch_up_once(self):
        self.cfg['schedule']['catch_up'] = True
        self.sender.return_value = {'sent': 2}
        data = self.tick(self.now.replace(minute=41))
        self.sender.assert_called_once_with(self.cfg, self.browser, 1, self.st, None, self.out,
                                            window_key='2026-10-29 20:00')
        self.assertEqual(data['catch_up'], {'window': '20:00', 'sent': 2})
        self.assertEqual(self.st['ticks_done'], ['2026-10-29 20:00'])
        self.assertIn('🔁 补跑 20:00 跳：发出 2', self.out)
        self.assertIn('🔁 补跑 20:00 跳：发出 2', F.render(data))
        self.assertNotIn('📋 今日已发', '\n'.join(F.render(data)))
        self.assertTrue(self.tick(self.now.replace(minute=41))['ok'])
        self.assertEqual(self.sender.call_count, 1)

    def test_catch_up_defaults_off_and_only_nearest_send(self):
        for setting in ({}, {'catch_up': False}):
            self.cfg['schedule'].update(setting)
            self.st['missed_reported'] = []
            self.save()
            self.tick(self.now.replace(minute=41))
        self.sender.assert_not_called()
        self.cfg['schedule'].update(catch_up=True, windows=['19:30', '20:00'],
                                    inbox_windows=['20:00'], summary_window='20:00')
        self.st['missed_reported'] = []
        self.save()
        with patch.object(O, 'scan') as scan, patch.object(B, 'inbox') as inbox, patch.object(B, 'daily_summary') as summary:
            data = self.tick(self.now.replace(minute=41))
            scan.assert_not_called()
            inbox.assert_not_called()
            summary.assert_not_called()
        self.assertEqual(data['catch_up']['window'], '20:00')
        self.assertEqual(self.st['ticks_done'], ['2026-10-29 20:00'])
        self.sender.assert_called_once()

    def test_catch_up_failure_and_guards(self):
        self.cfg['schedule']['catch_up'] = True
        for dependency in ('ledger_guard', 'migrate_ledger', 'check_login', 'do_send'):
            with self.subTest(dependency=dependency):
                self.st['missed_reported'] = []
                self.st['ticks_failed'] = []
                self.save()
                with patch.object({'ledger_guard': B, 'migrate_ledger': HS, 'check_login': O, 'do_send': O}[dependency], dependency, side_effect=RuntimeError('blocked')):
                    data = self.tick(self.now.replace(minute=41))
                self.assertFalse(data['ok'])
                self.assertEqual(self.st['ticks_done'], [])
                self.assertEqual(self.st['ticks_failed'][0]['key'], '2026-10-29 20:00')

    def test_active_window_does_not_catch_up(self):
        self.cfg['schedule'].update(catch_up=True, windows=['19:20', '20:00'])
        data = self.tick()
        self.assertNotIn('catch_up', data)
        self.assertEqual(self.st['ticks_done'], ['2026-10-29 20:00'])
        self.sender.assert_called_once()


if __name__ == '__main__':
    unittest.main()
