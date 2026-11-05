"""Non-object browser payloads follow the existing unreadable-result paths."""
from pathlib import Path
import sys
import unittest
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
import chat_dom
import conv_actions


class DictGuardTests(unittest.TestCase):
    payloads = ('虚构文本', 42, ['x'])
    key = 'id:fiction-guard'

    def test_conversation_rows_unreadable(self):
        for payload in self.payloads:
            with self.subTest(payload=payload):
                br = Mock()
                br.json.return_value = payload
                self.assertEqual(chat_dom.conversation_rows(br, 1), [])
                self.assertEqual(chat_dom.open_by_index(br, 1, 0),
                                 ('error: unknown_conversation', '', []))
                br.click.assert_not_called()

    def test_action_readers_return_empty_mapping(self):
        readers = (
            (conv_actions._resume_button, ()),
            (conv_actions._accept_button, ('虚构请求',)),
            (conv_actions._resume_dialog, ()),
            (conv_actions._resume_close_button, ()),
            (conv_actions._not_interested_target, ('dry',)),
        )
        for reader, args in readers:
            for payload in self.payloads:
                with self.subTest(reader=reader.__name__, payload=payload):
                    br = Mock()
                    br.json.return_value = payload
                    self.assertEqual(reader(br, 1, self.key, *args), {})

    def test_missing_resume_buttons_abort_without_click(self):
        for payload in self.payloads:
            for accept in (False, True):
                with self.subTest(payload=payload, accept=accept):
                    br = Mock()
                    br.json.side_effect = [{'conv_key': self.key, 'messages': []}, payload]
                    cfg = {'conversation': {'dry': True}}
                    if accept:
                        result = conv_actions.accept_resume_request(
                            br, 1, cfg, self.key, 'fiction-job', '虚构请求')
                    else:
                        result = conv_actions.send_resume(br, 1, cfg, self.key, 'fiction-job')
                    self.assertEqual(result, 'send_aborted:' +
                                     ('accept_button' if accept else 'resume_button'))
                    br.click.assert_not_called()

    def test_missing_dialog_remains_pending(self):
        for payload in self.payloads:
            with self.subTest(payload=payload), patch.object(conv_actions.time, 'sleep'), \
                    patch.object(conv_actions.HC, 'trace'):
                br = Mock()
                before = {'conv_key': self.key, 'messages': []}
                br.json.side_effect = [before] * 8 + [payload] + [before] * 8
                br.click.return_value = {'ok': True}
                self.assertEqual(conv_actions._send_resume_and_confirm(
                    br, 1, {}, self.key, before, '#fiction-resume'), 'pending_verification')
                br.click.assert_called_once_with(1, '#fiction-resume')

    def test_missing_close_button_preserves_verified_send(self):
        for payload in self.payloads:
            with self.subTest(payload=payload), patch.object(conv_actions.time, 'sleep'), \
                    patch.object(conv_actions.HC, 'trace'):
                br = Mock()
                before = {'conv_key': self.key, 'messages': []}
                br.json.side_effect = [
                    {'conv_key': self.key, 'messages': [
                        {'id': 'fiction-receipt', 'who': 'mine', 'text': '简历已发送'}]}, payload]
                br.click.return_value = {'ok': True}
                self.assertEqual(conv_actions._send_resume_and_confirm(
                    br, 1, {}, self.key, before, '#fiction-resume'), 'sent')
                br.click.assert_called_once_with(1, '#fiction-resume')

    def test_missing_decline_target_uses_original_abort_reason(self):
        for payload in self.payloads:
            for dry, reason in ((True, 'dry'), (False, 'menu')):
                with self.subTest(payload=payload, dry=dry), patch.object(conv_actions.HC, 'trace'):
                    br = Mock()
                    br.json.return_value = payload
                    self.assertEqual(conv_actions.mark_not_interested_company(
                        br, 1, {'conversation': {'dry': dry}}, self.key), 'aborted:' + reason)
                    br.click.assert_not_called()


if __name__ == '__main__':
    unittest.main()
