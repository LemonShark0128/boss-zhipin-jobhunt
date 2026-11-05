"""Resume diagnostics with fictional snapshots and temporary local ledgers."""
import json
import sys
import tempfile
import unittest
from itertools import chain, repeat
from pathlib import Path
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
import conv_actions as A
import send_safety as S


class ResumeTraceTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.path = Path(tmp.name) / 'resume_trace.jsonl'
        self.cfg = {'paths': {'workdir': tmp.name, 'state': tmp.name + '/state.json'},
                    'conversation': {}, 'sending': {'min_gap_seconds': 0}}
        self.before = {'conv_key': 'id:fiction', 'messages': [
            {'id': 'old%d' % i, 'who': 'friend', 'text': '虚构' * 100} for i in range(4)]}
        self.receipt = {'conv_key': 'id:fiction', 'messages': self.before['messages'] + [
            {'id': 'receipt', 'who': 'unknown', 'text': '附件简历已发送'}]}
        self.br = Mock()
        self.br.json.return_value = {'ok': True}
        self.br.click.return_value = {}

    def deliver(self, polls=None, dialog=None, close=None, accept=False):
        with patch.object(A.chat_dom, 'active_conversation', side_effect=chain(
                [self.before], polls if polls is not None else repeat(self.before))), \
             patch.object(A, '_resume_dialog', return_value=dialog or {}), \
             patch.object(A, '_resume_close_button', return_value=close or {'present': False, 'close': False}), \
             patch.object(A.time, 'sleep'), patch.object(A.HC, 'trace'):
            if accept:
                return A.accept_resume_request(self.br, 1, self.cfg, 'id:fiction', 'fiction-job', '虚构卡片')
            return A.send_resume(self.br, 1, self.cfg, 'id:fiction', 'fiction-job')

    def rows(self):
        self.assertTrue(self.path.exists(), 'resume diagnostic file is missing')
        return [json.loads(line) for line in self.path.read_text().splitlines()]

    def test_second_poll_receipt_has_ordered_trace(self):
        self.assertEqual(self.deliver([self.before, self.receipt]), 'sent')
        rows = self.rows()
        self.assertEqual([r['event'] for r in rows], ['start', 'click', 'poll', 'poll', 'close_dialog', 'end'])
        self.assertEqual(rows[-1]['result'], 'sent')
        self.assertEqual(len({r['run_id'] for r in rows}), 1)
        self.assertTrue(rows[0]['run_id'])
        self.assertTrue(all(r['kind'] == 'send' and r['ts'] for r in rows))
        self.assertEqual([m['id'] for m in rows[0]['before']], ['old1', 'old2', 'old3'])
        self.assertEqual(len(rows[0]['before'][0]['text']), 40)
        self.assertEqual(rows[3]['receipts'], 1)

    def test_missing_receipt_records_initial_and_late_polls(self):
        self.assertEqual(self.deliver(), 'pending_verification')
        rows = self.rows()
        self.assertEqual([r['event'] for r in rows], ['start', 'click'] + ['poll'] * 8 + ['dialog'] + ['poll'] * 8 + ['end'])
        self.assertEqual([(r['phase'], r['attempt']) for r in rows if r['event'] == 'poll'],
                         [(phase, n) for phase in ('initial', 'late') for n in range(1, 9)])
        self.assertEqual(rows[-1]['result'], 'pending_verification')
        self.assertEqual(S.ledger_latest(self.cfg)[0]['status'], 'pending')

    def test_confirm_and_payload_limits(self):
        noisy = {'conv_key': 'id:fiction', 'messages': self.before['messages'] + [
            {'id': 'new%d' % i, 'who': 'friend', 'text': '虚' * 400} for i in range(7)]}
        self.assertEqual(self.deliver([noisy] * 8 + [self.receipt],
                         dialog={'confirm': True, 'reason': '虚' * 400, 'html': '弹' * 2000},
                         close={'present': True, 'close': False, 'html': '窗' * 2000}), 'sent')
        rows = self.rows()
        dialog = next(r for r in rows if r['event'] == 'dialog')
        self.assertEqual(len(dialog['html']), 1500)
        self.assertEqual(len(dialog['reason']), 300)
        poll = next(r for r in rows if r['event'] == 'poll')
        self.assertEqual(len(poll['messages']), 5)
        self.assertEqual(len(poll['messages'][0]['text']), 60)
        self.assertTrue(next(r for r in rows if r['event'] == 'confirm_click')['result'])
        self.assertEqual(len(next(r for r in rows if r['event'] == 'close_dialog')['html']), 800)

    def test_write_failure_does_not_change_delivery(self):
        with patch.object(A, 'open', side_effect=OSError('fictional denied'), create=True):
            result = self.deliver([self.before, self.receipt])
        self.assertEqual(result, 'sent')
        self.assertFalse(self.path.exists())
        self.assertEqual(S.ledger_latest(self.cfg)[0]['status'], 'sent')

    def test_rotation_preserves_tail(self):
        self.path.write_text(''.join(json.dumps({'old': i}) + '\n' for i in range(520)))
        self.assertEqual(self.deliver([self.receipt]), 'sent')
        rows = self.rows()
        self.assertLessEqual(len(rows), 305)
        self.assertEqual([r['old'] for r in rows if 'old' in r][-1], 519)
        self.assertEqual(rows[-1]['result'], 'sent')

    def test_dry_never_creates_trace(self):
        self.cfg['conversation']['dry'] = True
        self.assertEqual(self.deliver(), 'dry_ok')
        self.assertFalse(self.path.exists())

    def test_accept_and_duplicate_have_separate_runs(self):
        self.assertEqual(self.deliver([self.receipt], accept=True), 'sent')
        first = self.rows()
        self.assertEqual(first[0]['selector'], '#bh-conv-accept')
        self.assertTrue(all(r['kind'] == 'accept' for r in first))
        self.assertEqual(self.deliver(), 'duplicate')
        last = self.rows()[-1]
        self.assertEqual(last['result'], 'duplicate')
        self.assertNotEqual(last['run_id'], first[0]['run_id'])

    def test_aborted_and_changed_conversation_are_logged(self):
        self.br.json.return_value = {'ok': False, 'reason': 'resume_button'}
        self.assertEqual(self.deliver(), 'send_aborted:resume_button')
        self.assertEqual(self.rows()[-1]['result'], 'send_aborted:resume_button')
        self.br.json.return_value = {'ok': True}
        self.assertEqual(self.deliver([dict(self.receipt, conv_key='id:other')]), 'pending_verification')
        self.assertFalse(next(r for r in self.rows() if r['event'] == 'poll')['conv_key_matches'])
        poll = next(r for r in self.rows() if r['event'] == 'poll')
        self.assertEqual(poll['messages'], [{'who': 'unknown', 'text': '附件简历已发送'}])
        self.assertEqual(poll['receipts'], 1)
