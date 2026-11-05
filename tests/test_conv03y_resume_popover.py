"""conv-03y: fictional resume popovers, offline browser doubles only."""
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
import conv_actions as A
import conv_store
import send_safety as S
import test_conversation_run as fixtures


class ResumePopoverTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.cfg = {'paths': {'workdir': tmp.name, 'state': tmp.name + '/state.json'},
                    'conversation': {}, 'sending': {'min_gap_seconds': 0}}
        self.br = Mock()
        self.br.click.return_value = {}
        self.before = {'conv_key': 'id:fiction', 'messages': [
            {'id': 'old', 'who': 'friend', 'text': '虚构简历请求'}]}

    def test_dialog_script_recognizes_resume_popover_and_excludes_contacts(self):
        self.br.json.return_value = {}
        A._resume_dialog(self.br, 1, 'id:fiction')
        script = self.br.json.call_args.args[1]
        self.assertIn('.panel-resume.sentence-popover', script)
        self.assertIn('.chat-controls', script)
        self.assertIn('.btn-sure-v2', script)
        self.assertIn('span.btn-v2', script)
        self.assertRegex(script, r"!\w+\.closest\(['\"]\.panel-contact['\"]\)")

    def test_button_script_checks_vue_conversation_preconditions(self):
        self.br.json.return_value = {}
        A._resume_button(self.br, 1, 'id:fiction')
        script = self.br.json.call_args.args[1]
        for token in ('__vue__', 'parentElement', 'conversation$', 'bothTalked',
                      'exchangeOptGray', 'requestResume', 'resume_not_both_talked',
                      'resume_requested'):
            self.assertIn(token, script)

    def test_precondition_aborts_without_click_or_ledger_entry(self):
        for reason in ('resume_not_both_talked', 'resume_requested'):
            with self.subTest(reason=reason):
                self.br.json.return_value = {'ok': False, 'reason': reason}
                with patch.object(A.chat_dom, 'active_conversation', return_value=self.before):
                    result = A.send_resume(self.br, 1, self.cfg, 'id:fiction', 'fiction-job')
                self.assertEqual(result, 'send_aborted:' + reason)
                self.br.click.assert_not_called()
                self.assertEqual(S.ledger_latest(self.cfg), [])

    def test_popover_confirmation_once_then_receipt_commits_sent(self):
        self.br.json.return_value = {'ok': True}
        def active(*args):
            confirmed = any(c.args[1] == '#bh-conv-resume-confirm'
                            for c in self.br.click.call_args_list)
            messages = self.before['messages'] + ([{
                'id': 'receipt', 'who': 'unknown', 'text': '附件简历已发送'}] if confirmed else [])
            return dict(self.before, messages=messages)
        dialog = {'html': '<div class="panel-resume sentence-popover">虚构简历</div>',
                  'confirm': True}
        with patch.object(A.chat_dom, 'active_conversation', side_effect=active), \
             patch.object(A, '_resume_dialog', return_value=dialog), \
             patch.object(A, '_resume_close_button', return_value={'present': False}), \
             patch.object(A.time, 'sleep'):
            result = A.send_resume(self.br, 1, self.cfg, 'id:fiction', 'fiction-job')
        self.assertEqual(result, 'sent')
        self.assertEqual([c.args[1] for c in self.br.click.call_args_list],
                         ['#bh-conv-resume', '#bh-conv-resume-confirm'])
        self.assertEqual([r['status'] for r in S.ledger_latest(self.cfg)], ['sent'])


class ResumePopoverOrchestrationTests(unittest.TestCase):
    def setUp(self):
        self.fixture = fixtures.ConversationRunTests()
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)

    def test_requested_goes_manual_with_one_notification(self):
        f = self.fixture
        result, _, _, _, sender = f.run_hop(resume_result='send_aborted:resume_requested')
        sender.assert_called_once()
        self.assertEqual(f.saved()['status'], 'manual')
        self.assertIsNone(f.saved()['resume_sent_ts'])
        self.assertEqual(result['manual'], 1)
        self.assertEqual(len(result['notifications']), 1)
        self.assertIn('请手动点「同意」', result['notifications'][0])

    def test_not_both_talked_restores_last_seen_without_notification(self):
        f = self.fixture
        row = f.record()
        row['last_seen'] = {'ts': 1, 'text': '虚构旧消息'}
        row['last_msg_id'] = 'm0'
        conv_store.save(f.path, {row['conv_key']: row})
        result, _, _, _, sender = f.run_hop(resume_result='send_aborted:resume_not_both_talked')
        sender.assert_called_once()
        self.assertEqual(f.saved()['last_seen'], row['last_seen'])
        self.assertEqual(f.saved()['last_msg_id'], 'm0')
        self.assertIsNone(f.saved()['resume_sent_ts'])
        self.assertEqual(result['notifications'], [])
        self.assertEqual(result['manual'], 0)


if __name__ == '__main__':
    unittest.main()
