"""OUT-02: fictional jobs, temporary state, and offline browser responses only."""
from unittest.mock import Mock, call, patch

from test_phase_a import Base
import chrome_mcp as CM
import hunt_state as HS
import outbound as O


class DetailUnavailableTests(Base):
    def setUp(self):
        super().setUp()
        self.cfg['_deadline'] = float('inf')
        self.sleep = patch.object(O.time, 'sleep').start()
        self.addCleanup(patch.stopall)
        self.job_data = dict(self.job(), _jd='虚构岗位职责')

    def browser(self, payloads, diagnostic=''):
        browser = Mock(spec=['nav', 'json', 'js'])
        browser.nav.return_value = {}
        # Model the real JSON parser while keeping probes separate from diagnostics.
        browser.json.side_effect = [CM.parse_json_payload(p, {}) for p in payloads]
        browser.js.return_value = {'text': diagnostic}
        return browser

    def inspect(self, browser):
        return O.inspect_job(browser, 17, self.job_data, self.cfg)

    def assert_reopened(self, browser):
        self.assertEqual(browser.nav.call_args_list, [
            call(17, 'https://www.zhipin.com/job_detail/j1.html', wait=4.0),
            call(17, 'https://www.zhipin.com/job_detail/j1.html', wait=4.0)])

    def test_reopen_recovers_after_eight_non_json_probes(self):
        browser = self.browser(['not JSON'] * 8 + ['{"s":true}'])
        self.assertEqual(self.inspect(browser), ('ready', '虚构岗位职责'))
        self.assert_reopened(browser)
        self.assertEqual(browser.json.call_count, 9)
        self.assertEqual(self.sleep.call_args_list, [call(1.2)] * 8)
        browser.js.assert_not_called()

    def test_final_failure_uses_one_unwrapped_truncated_diagnostic(self):
        raw = 'Execution context was destroyed ' + 'x' * 100
        browser = self.browser(['not JSON'] * 16,
                               '<page-content>"  ' + raw + '  "</page-content>')
        with self.assertRaises(RuntimeError) as caught:
            self.inspect(browser)
        self.assertEqual(str(caught.exception), 'detail_unavailable: ' + raw[:80])
        self.assert_reopened(browser)
        self.assertEqual(browser.json.call_count, 16)
        self.assertEqual(self.sleep.call_args_list, [call(1.2)] * 16)
        browser.js.assert_called_once_with(17, browser.json.call_args.args[1])

    def test_empty_diagnostic(self):
        browser = self.browser([''] * 16, ' \n\t ')
        with self.assertRaisesRegex(RuntimeError, '^detail_unavailable: empty$'):
            self.inspect(browser)
        browser.js.assert_called_once()

    def test_diagnostic_exception_becomes_detail_unavailable(self):
        browser = self.browser([''] * 16)
        browser.js.side_effect = ValueError('fictional failure')
        with self.assertRaisesRegex(RuntimeError, '^detail_unavailable: exc:ValueError$'):
            self.inspect(browser)
        browser.js.assert_called_once()

    def test_reopen_navigation_error_skips_network(self):
        browser = self.browser([''] * 8)
        # heal-01：重开导航出错会再等一下重试一次，两次都错才 skip_network
        browser.nav.side_effect = [{}, {'isError': True}, {'isError': True}]
        self.assertEqual(self.inspect(browser), ('skip_network', ''))
        self.assertEqual(browser.nav.call_count, 3)
        self.assertEqual(browser.json.call_count, 8)
        browser.js.assert_not_called()

    def test_recognized_states_do_not_reopen_or_diagnose(self):
        for key, expected in [('login', 'skip_not_logged_in'), ('cap', 'skip_captcha'),
                              ('app', 'skip_need_app'), ('c', 'skip_already_chatted'),
                              ('s', 'ready')]:
            with self.subTest(key=key):
                browser = self.browser(['{"%s":true}' % key])
                self.assertEqual(self.inspect(browser)[0], expected)
                browser.nav.assert_called_once()
                browser.json.assert_called_once()
                browser.js.assert_not_called()

    def test_reopened_states_keep_existing_classification(self):
        for key, expected in [('login', 'skip_not_logged_in'), ('cap', 'skip_captcha'),
                              ('app', 'skip_need_app'), ('c', 'skip_already_chatted')]:
            with self.subTest(key=key):
                browser = self.browser([''] * 8 + ['{"%s":true}' % key])
                self.assertEqual(self.inspect(browser)[0], expected)
                self.assert_reopened(browser)
                browser.js.assert_not_called()

    def test_non_scheduled_empty_probes_keep_skip_no_button(self):
        self.cfg.pop('_deadline')
        browser = self.browser([''] * 8)
        self.assertEqual(self.inspect(browser), ('skip_no_button', ''))
        browser.nav.assert_called_once()
        self.assertEqual(browser.json.call_count, 8)
        browser.js.assert_not_called()

    def test_valid_state_without_button_does_not_reopen(self):
        browser = self.browser(['{"s":false,"title":"虚构岗位"}'] * 8)
        self.assertEqual(self.inspect(browser), ('skip_no_button', ''))
        browser.nav.assert_called_once()
        browser.js.assert_not_called()

    def test_suffixed_outcome_halts_without_consuming_retry_or_quota(self):
        status = 'detail_unavailable: Execution context was destroyed'
        st = HS.load_state(self.cfg)
        for phase in ('grade', 'send'):
            self.assertEqual(HS.record_outcome(self.cfg, st, self.job(), status, phase, ''), 'halt')
        self.cfg.pop('_deadline')
        self.raw([self.job()])
        with patch.object(O, 'inspect_job', return_value=(status, '')), \
             patch.object(O, 'send_one') as sender:
            result = O.do_send(self.cfg, None, None, st, 1, [])
        self.assertEqual(result['stop_reason'], 'network')
        sender.assert_not_called()
        self.assertEqual(st['day_count'], 0)
        self.assertEqual(st['retry_jobs'], {})
        self.assertEqual(st['checked_jobs'], {})

    def test_multiline_diagnostic_collapses_whitespace_before_truncation(self):
        raw = '  Execution\n\tcontext   was\r\ndestroyed  ' + 'x' * 100
        browser = self.browser(['not JSON'] * 16, raw)
        with self.assertRaises(RuntimeError) as caught:
            self.inspect(browser)
        expected = ('Execution context was destroyed ' + 'x' * 100)[:80]
        self.assertEqual(str(caught.exception), 'detail_unavailable: ' + expected)
        self.assertEqual(len(str(caught.exception).splitlines()), 1)
