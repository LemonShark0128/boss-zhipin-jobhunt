"""Attachment-card receipts using fictional data and offline browser doubles."""
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
import conv_actions as A
import send_safety as S
import test_conversation_run as fixtures


CARD = '虚构简历.pdf\n点击预览附件简历'


class ResumeCardReceiptTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.cfg = {'paths': {'workdir': tmp.name, 'state': tmp.name + '/state.json'},
                    'conversation': {}, 'sending': {'min_gap_seconds': 0}}
        self.before = {'conv_key': 'id:fiction', 'messages': [
            {'id': 'old', 'who': 'friend', 'text': '虚构简历请求'}]}
        self.card = {'id': 'card', 'who': 'unknown', 'text': CARD}
        self.after_key = 'id:fiction'
        self.br = Mock()
        self.br.json.return_value = {'ok': True}
        self.br.click.return_value = {}

    def deliver(self):
        def active(*args):
            confirmed = any(c.args[1] == '#bh-conv-resume-confirm'
                            for c in self.br.click.call_args_list)
            if not confirmed:
                return self.before
            return {'conv_key': self.after_key, 'messages': self.before['messages'] + [
                {'id': 'empty', 'who': 'unknown', 'text': ''}, self.card]}

        with patch.object(A.chat_dom, 'active_conversation', side_effect=active), \
             patch.object(A, '_resume_dialog', return_value={'confirm': True}), \
             patch.object(A, '_resume_close_button', return_value={'present': False}), \
             patch.object(A.time, 'sleep'), patch.object(A.HC, 'trace'):
            result = A.send_resume(self.br, 1, self.cfg, 'id:fiction', 'fiction-job')
        self.assertEqual([c.args[1] for c in self.br.click.call_args_list],
                         ['#bh-conv-resume', '#bh-conv-resume-confirm'])
        trace = Path(self.cfg['paths']['workdir']) / 'resume_trace.jsonl'
        self.rows = [json.loads(line) for line in trace.read_text().splitlines()]
        return result

    def assert_pending(self):
        self.assertEqual(self.deliver(), 'pending_verification')
        self.assertEqual([r['status'] for r in S.ledger_latest(self.cfg)], ['pending'])
        self.assertEqual(self.rows[-1]['result'], 'pending_verification')

    def test_new_unknown_card_after_confirmation_commits_sent(self):
        self.assertEqual(self.deliver(), 'sent')
        self.assertEqual([r['status'] for r in S.ledger_latest(self.cfg)], ['sent'])
        self.assertEqual(self.rows[-1]['event'], 'end')
        self.assertEqual(self.rows[-1]['result'], 'sent')
        polls = [r for r in self.rows if r['event'] == 'poll' and r['phase'] == 'confirmed']
        self.assertEqual(len(polls), 1)
        self.assertEqual(polls[0]['receipts'], 1)
        self.assertEqual(polls[0]['messages'], [
            {'who': 'unknown', 'text': ''}, {'who': 'unknown', 'text': CARD}])

    def test_friend_card_is_not_a_receipt(self):
        self.card['who'] = 'friend'
        self.assert_pending()

    def test_card_id_in_before_snapshot_is_not_a_new_receipt(self):
        self.before['messages'].append(dict(self.card))
        self.assert_pending()

    def test_custom_pattern_excludes_card(self):
        self.cfg['conversation']['resume_sent_pattern'] = '虚构自定义发送成功'
        self.assert_pending()

    def test_card_in_another_conversation_is_not_a_receipt(self):
        self.after_key = 'id:fiction-other'
        self.assert_pending()


class ResumeCardHistoryTests(unittest.TestCase):
    def setUp(self):
        self.fixture = fixtures.ConversationRunTests()
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)

    def test_historical_attachment_card_prevents_resume_resend(self):
        f = self.fixture
        history = f.history()
        history.insert(1, {'who': 'unknown', 'id': 'fiction-card',
                           'classes': 'message-item item-system',
                           'text': CARD, 'ts': f.ts - 15})
        _, _, calls, _, _ = f.run_hop(history=history)
        self.assertNotIn('resume', [c[0] for c in calls])
        self.assertEqual(f.saved()['resume_sent_ts'], f.ts - 15)
        self.assertTrue(any(a['type'] == 'resume_seen' and a['result'] == 'observed'
                            for a in f.saved()['actions']))


if __name__ == '__main__':
    unittest.main()
