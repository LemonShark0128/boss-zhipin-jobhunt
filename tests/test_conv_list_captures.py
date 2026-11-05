"""Offline list capture regressions; all contacts and responses are fictional."""
import json
import unittest

import test_conversation_run as fixtures


class ListCaptureTests(unittest.TestCase):
    setUp = fixtures.ConversationRunTests.setUp
    module = fixtures.ConversationRunTests.module
    item = fixtures.ConversationRunTests.item
    history = fixtures.ConversationRunTests.history
    run_hop = fixtures.ConversationRunTests.run_hop

    @staticmethod
    def response(items):
        return {'text': json.dumps({'code': 0, 'zpData': {'result': items}})}

    def capture(self, responses, items=None):
        def network(tab, url, index, **kwargs):
            return responses[index] if index < len(responses) else {'text': '[NO_MATCH] 虚构结束'}
        return self.run_hop(items=items, network=network, dry=True)[0]

    def indices(self):
        return [call.kwargs['index'] for call in self.br.network.call_args_list]

    def test_recent_fifth_batch_is_candidate(self):
        recent = self.item(5)
        responses = [self.response([self.item(i, age=80*3600)]) for i in range(1, 5)]
        result = self.capture(responses + [self.response([recent])], items=[recent])
        self.assertEqual(result['candidates'], 1)
        self.assertEqual(result['captured'], 5)
        self.assertEqual(result.get('captures'), 5)
        self.assertEqual(self.indices(), [0, 1, 2, 3, 4, 5])

    def test_duplicate_keeps_newer_message_in_either_capture(self):
        for ages in ((80*3600, 10), (10, 80*3600)):
            with self.subTest(ages=ages):
                self.br.network.reset_mock()
                result = self.capture([self.response([self.item(age=ages[0])]),
                                       self.response([]), self.response([self.item(age=ages[1])])])
                self.assertEqual(result['candidates'], 1)
                self.assertEqual(result['captured'], 1)
                self.assertEqual(result.get('captures'), 3)

    def test_equal_or_missing_time_keeps_first_capture(self):
        for first_time, later_time in ((1000, 1000), (None, 1000), (1000, None), (None, None)):
            with self.subTest(times=(first_time, later_time)):
                first, later = self.item(mine=True), self.item(mine=False)
                for item, value in ((first, first_time), (later, later_time)):
                    if value is None:
                        item['lastMessageInfo'].pop('msgTime')
                    else:
                        item['lastMessageInfo']['msgTime'] = (self.ts-10)*1000
                result = self.capture([self.response([first]), self.response([later])])
                self.assertEqual(result['candidates'], 0)

    def test_no_match_and_is_error_stop_immediately(self):
        for terminal in ({'text': '[NO_MATCH] 虚构结束'},
                         dict(self.response([self.item(2)]), isError=True)):
            with self.subTest(terminal=terminal):
                self.br.network.reset_mock()
                result = self.capture([self.response([self.item()]), terminal,
                                       self.response([self.item(3)])])
                self.assertEqual(self.indices(), [0, 1])
                self.assertEqual(result['captured'], 1)
                self.assertEqual(result.get('captures'), 1)

    def test_thirteen_batches_read_only_twelve(self):
        result = self.capture([self.response([self.item(i)]) for i in range(13)])
        self.assertEqual(self.indices(), list(range(12)))
        self.assertEqual(result['captured'], 12)
        self.assertEqual(result.get('captures'), 12)

    def test_bad_json_and_nonzero_code_are_skipped(self):
        result = self.capture([self.response([self.item(1)]), {'text': '{broken fiction'},
                               {'text': json.dumps({'code': 1})},
                               self.response([self.item(2)])])
        self.assertEqual(result['captured'], 2)
        self.assertEqual(result.get('captures'), 2)
        self.assertEqual(self.indices(), [0, 1, 2, 3, 4])

    def test_overview_reports_valid_capture_count_including_empty(self):
        result = self.capture([self.response([self.item()]), self.response([])])
        self.assertIn('列表 1（抓包 2）｜候选 1', result['overview'])
        self.assertIn(result['overview'], self.out)

    def test_empty_and_invalid_responses_keep_original_errors_after_retry(self):
        for response, error in ((self.response([]), 'chat_empty_list'),
                                ({'text': '{fiction broken'}, 'chat_no_valid_response')):
            with self.subTest(error=error):
                self.br.network.reset_mock()
                with self.assertRaisesRegex(RuntimeError, '^' + error + '$'):
                    self.capture([response])
                self.assertEqual(self.indices(), [0, 1, 0, 1])
                self.cleanup.assert_called_once_with(self.br)
