"""heal-01：瞬时失败先重试一次。替身浏览器，不连真实 Chrome。"""
import sys
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
import outbound as O


class CheckLoginRetryTests(unittest.TestCase):
    def setUp(self):
        p = patch.object(O.time, 'sleep')
        self.sleep = p.start()
        self.addCleanup(p.stop)
        self.br = Mock()

    def run_check(self, tabs, states):
        self.br.new_tab.side_effect = list(tabs)
        with patch.object(O, 'login_state', side_effect=list(states)) as ls:
            tab, st = O.check_login(self.br, {})
        return tab, st, ls

    def test_tab_retry_succeeds(self):
        tab, st, ls = self.run_check([None, 123], [{'avatar': True}])
        self.assertEqual(tab, 123)
        self.assertTrue(st['avatar'])
        self.assertEqual(self.br.new_tab.call_count, 2)

    def test_tab_fails_twice(self):
        tab, st, ls = self.run_check([None, None], [])
        self.assertIsNone(tab)
        self.assertEqual(st, {})
        ls.assert_not_called()
        self.assertEqual(self.br.new_tab.call_count, 2)

    def test_login_wall_not_retried(self):
        tab, st, ls = self.run_check([7], [{'login': True}, {'avatar': True}])
        self.assertTrue(st['login'])
        self.assertEqual(ls.call_count, 1)

    def test_avatar_appears_later(self):
        for n in (2, 3):
            with self.subTest(n=n):
                states = [{}] * (n - 1) + [{'avatar': True}]
                tab, st, ls = self.run_check([7], states)
                self.assertTrue(st.get('avatar'))
                self.assertEqual(ls.call_count, n)

    def test_unconfirmed_after_three_reads(self):
        tab, st, ls = self.run_check([7], [{}, {'raw': 'eval_failed'}, {}, {'avatar': True}])
        self.assertFalse(st.get('avatar'))
        self.assertEqual(ls.call_count, 3)

    def test_first_read_ok_no_extra_wait(self):
        tab, st, ls = self.run_check([7], [{'avatar': True}])
        self.assertEqual(ls.call_count, 1)
        self.assertEqual(self.br.new_tab.call_count, 1)
        self.assertEqual(self.sleep.call_count, 1)  # 原有的开页后 3 秒


class InspectNavRetryTests(unittest.TestCase):
    def setUp(self):
        p = patch.object(O.time, 'sleep')
        self.sleep = p.start()
        self.addCleanup(p.stop)
        self.br = Mock()
        self.br.json.return_value = {'s': True}
        self.job = {'encryptJobId': 'fiction-job'}

    def test_nav_error_then_ok_continues(self):
        self.br.nav.side_effect = [{'isError': True}, {}]
        with patch.object(O, 'read_jd', return_value='虚构JD'):
            status, _ = O.inspect_job(self.br, 1, self.job, {})
        self.assertNotEqual(status, 'skip_network')
        self.assertEqual(self.br.nav.call_count, 2)
        self.assertIn(O.NAV_RETRY_WAIT, [c.args[0] for c in self.sleep.call_args_list if c.args])

    def test_nav_error_twice_skip_network(self):
        self.br.nav.side_effect = [{'isError': True}, {'isError': True}]
        status, _ = O.inspect_job(self.br, 1, self.job, {})
        self.assertEqual(status, 'skip_network')
        self.assertEqual(self.br.nav.call_count, 2)
        self.br.json.assert_not_called()

    def test_normal_nav_no_retry_wait(self):
        self.br.nav.return_value = {}
        with patch.object(O, 'read_jd', return_value='虚构JD'):
            O.inspect_job(self.br, 1, self.job, {})
        self.assertEqual(self.br.nav.call_count, 1)
        self.assertNotIn(O.NAV_RETRY_WAIT, [c.args[0] for c in self.sleep.call_args_list if c.args])

    def test_deadline_reopen_also_retries(self):
        # 定时模式第一轮读不到状态 → 重开；重开导航先错一次再好
        self.br.nav.side_effect = [{}, {'isError': True}, {}]
        self.br.json.side_effect = [{}] * 8 + [{'s': True}]
        with patch.object(O, 'read_jd', return_value='虚构JD'):
            status, _ = O.inspect_job(self.br, 1, self.job, {'_deadline': 1})
        self.assertNotEqual(status, 'skip_network')
        self.assertEqual(self.br.nav.call_count, 3)


if __name__ == '__main__':
    unittest.main()
