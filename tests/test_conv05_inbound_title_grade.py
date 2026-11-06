"""conv-05: fictional inbound data, offline browser/LLM/send boundaries."""
import json
from pathlib import Path
import sys
import unittest
from contextlib import ExitStack
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
import chrome_mcp as CM
import conversation as C
import conv_store
import test_conv_title as fixtures


class PayloadTests(unittest.TestCase):
    def test_plain_json_string_survives_wrapper(self):
        self.assertEqual(CM.parse_json_payload(
            '<page-content untrusted="true">\n"商家bd"\n</page-content>'), '商家bd')

    def test_existing_payload_shapes(self):
        for raw, expected in [('{"a":1}', {'a': 1}), ('[1,2]', [1, 2]),
                              (json.dumps('{"a":1}'), {'a': 1}),
                              (json.dumps('[1,2]'), [1, 2]),
                              ('true', True), ('false', False), ('42', 42),
                              ('', 'fallback'), ('""', 'fallback'),
                              ('garbage', 'fallback')]:
            with self.subTest(raw=raw):
                self.assertEqual(CM.parse_json_payload(raw, 'fallback'), expected)


class InboundTitleTests(unittest.TestCase):
    setUp = fixtures.ConversationTitleTests.setUp
    saved = fixtures.ConversationTitleTests.saved

    def hop(self, title='', dry=False):
        self.br.new_tab.side_effect = [10, 11]
        self.br.json.side_effect = lambda *a, **k: CM.parse_json_payload(json.dumps(title))
        self.br.network.side_effect = lambda tab, url, index, **kw: {'text': json.dumps(
            {'code': 0, 'zpData': {'result': [self.item] if index == 0 else []}})}
        history = [{'who': 'friend', 'id': 'fiction-message', 'text': '您好', 'ts': self.ts-10}]
        with ExitStack() as stack:
            def p(obj, name, **kw):
                return stack.enter_context(patch.object(obj, name, **kw))
            p(C.chat_dom, 'conversation_rows', return_value=[{'conv_key': self.item['conv_key']}])
            p(C.chat_dom, 'open_by_index', return_value=('ok', '', history))
            p(C.jd_page, 'read_jd', return_value='虚构商户洽谈签约职责')
            p(C.CM, 'cleanup_browser', return_value=2)
            p(C.hunt_filter, 'load_jobs', return_value=[])
            p(C.hunt_filter, 'judge', return_value=('pass', '虚构匹配'))
            grade = p(C.hunt_filter, 'grade_with_jd', return_value={'grade': 'B'})
            for name, value in [('opener', '虚构开场，'), ('lead', ''), ('answer', ''), ('extract', {})]:
                p(C.conv_llm, name, return_value=value)
            send = p(C.conv_actions, 'send_text', return_value='sent')
            resume = p(C.conv_actions, 'send_resume', return_value='sent')
            result = C.run(self.cfg, self.br, self.out, now=self.now, dry=dry)
        return result, grade, send, resume

    def test_page_title_reaches_grade_and_record(self):
        _, grade, _, _ = self.hop('虚构商家bd')
        self.assertEqual(grade.call_args.args[0].get('jobName'), '虚构商家bd')
        self.assertEqual(self.saved()['title'], '虚构商家bd')
        self.assertTrue(self.saved()['job_ok'])

    def test_missing_title_retries_then_notifies_once_without_sending(self):
        notices = []
        for attempt in range(1, 5):
            result, grade, send, resume = self.hop()
            row = self.saved()
            self.assertIsNone(row.get('job_ok'))
            self.assertNotIn('job_ok', row)
            self.assertEqual(row['title_failures'], min(attempt, 3))
            grade.assert_not_called()
            send.assert_not_called()
            resume.assert_not_called()
            notices.extend(result['notifications'])
            if attempt >= 3:
                self.assertEqual(row['status'], 'manual')
        self.assertEqual(len(notices), 1)
        self.assertIn('岗位名读不到', notices[0])

    def test_item_title_is_not_overwritten(self):
        self.item['jobName'] = '虚构原岗位'
        _, grade, _, _ = self.hop('虚构页面岗位')
        self.assertEqual(grade.call_args.args[0]['jobName'], '虚构原岗位')
        self.assertEqual(self.saved()['title'], '虚构原岗位')

    def test_missing_title_dry_does_not_persist_or_send(self):
        result, grade, send, resume = self.hop(dry=True)
        self.assertFalse(self.path.exists())
        grade.assert_not_called()
        send.assert_not_called()
        resume.assert_not_called()
        self.assertEqual(result['plans'][0]['reason'], 'title_unavailable')


if __name__ == '__main__':
    unittest.main()
