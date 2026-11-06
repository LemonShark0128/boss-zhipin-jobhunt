"""heal-02：虚构开关响应，验证重试顺序及发送闸门；不连接浏览器。"""
import json
from pathlib import Path
import sys
import unittest
from unittest.mock import Mock, call, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
import outbound as O

URL = 'https://www.zhipin.com/web/geek/notify-set?type=greetSet'


def response(on):
    rows = [] if on is None else [{'t': '启用招呼语', 'on': on}]
    return {'text': json.dumps(rows)}


class GreetingSwitchRetryTests(unittest.TestCase):
    def setUp(self):
        self.br = Mock(spec=['nav', 'js', 'click'])
        sleeper = patch.object(O.time, 'sleep')
        self.sleep = sleeper.start()
        self.addCleanup(sleeper.stop)
        self.events = Mock()
        self.events.attach_mock(self.br.nav, 'nav')
        self.events.attach_mock(self.br.js, 'read')
        self.events.attach_mock(self.sleep, 'sleep')

    def assert_reads(self, count, renavigate=False):
        expr = self.br.js.call_args_list[0].args[1]
        expected = [call.nav(7, URL, wait=3.5), call.read(7, expr)]
        if count >= 2:
            expected += [call.sleep(4), call.read(7, expr)]
        if renavigate:
            expected += [call.nav(7, URL, wait=3.5), call.read(7, expr)]
        self.assertEqual(self.events.mock_calls, expected)

    def test_unknown_then_off_reads_same_page_after_four_seconds(self):
        self.br.js.side_effect = [response(None), response(False)]
        self.assertEqual(O.greeting_switch(self.br, 7), 'auto_greeting_off')
        self.assert_reads(2)

    def test_two_unknown_then_off_renavigates_before_third_read(self):
        self.br.js.side_effect = [response(None), response(None), response(False)]
        self.assertEqual(O.greeting_switch(self.br, 7), 'auto_greeting_off')
        self.assert_reads(3, renavigate=True)

    def test_three_unknown_stop_without_click_even_with_fix(self):
        self.br.js.side_effect = [response(None)] * 3
        self.assertEqual(O.greeting_switch(self.br, 7, fix=True), 'auto_greeting_unknown')
        self.assert_reads(3, renavigate=True)

    def test_send_one_still_stops_after_three_unknown(self):
        self.br.js.side_effect = [response(None)] * 3
        job = {'encryptJobId': 'fiction-job', 'jobName': '虚构岗位',
               'brandName': '虚构公司'}
        with patch.object(O.SS, 'ledger_guard'), \
             patch.object(O, 'inspect_job', return_value=('ready', '虚构JD')), \
             patch.object(O, 'job_identity_matches', return_value=True), \
             patch.object(O.G, 'generate') as generate:
            with self.assertRaisesRegex(RuntimeError, '^platform_greeting_unknown: auto_greeting_unknown'):
                O.send_one(self.br, 7, job, {})
        self.assert_reads(3, renavigate=True)
        generate.assert_not_called()
        self.br.click.assert_not_called()

    def test_first_on_does_not_retry_or_fix_by_default(self):
        self.br.js.side_effect = [response(True)]
        self.assertEqual(O.greeting_switch(self.br, 7, fix=False), 'auto_greeting_on')
        self.assert_reads(1)

    def test_fix_on_preserves_click_wait_and_single_verification(self):
        for after, expected in [(False, 'auto_greeting_off'),
                                (True, 'auto_greeting_on_still'),
                                (None, 'auto_greeting_unknown')]:
            with self.subTest(after=after):
                self.events.reset_mock()
                self.br.js.side_effect = [response(True), {'text': 'clicked'}, response(after)]
                self.assertEqual(O.greeting_switch(self.br, 7, fix=True), expected)
                self.br.nav.assert_called_once_with(7, URL, wait=3.5)
                self.assertEqual(self.br.js.call_count, 3)
                self.assertIn('hit.click()', self.br.js.call_args_list[1].args[1])
                self.assertEqual(self.br.js.call_args_list[0], self.br.js.call_args_list[2])
                self.sleep.assert_called_once_with(2)
                self.assertEqual([c[0] for c in self.events.mock_calls],
                                 ['nav', 'read', 'read', 'sleep', 'read'])

    def test_unknown_then_on_is_not_off(self):
        self.br.js.side_effect = [response(None), response(True)]
        self.assertEqual(O.greeting_switch(self.br, 7), 'auto_greeting_on')
        self.assert_reads(2)

    def test_first_off_never_waits_or_rereads_even_with_fix(self):
        self.br.js.side_effect = [response(False)]
        self.assertEqual(O.greeting_switch(self.br, 7, fix=True), 'auto_greeting_off')
        self.assert_reads(1)


if __name__ == '__main__':
    unittest.main()
