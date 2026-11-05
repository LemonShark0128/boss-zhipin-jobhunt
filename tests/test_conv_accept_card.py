"""Fictional resume request cards; only temporary ledgers and browser doubles."""
import sys
from itertools import chain, repeat
import json
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
import conv_actions as A
import send_safety as S


class AcceptCardTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.cfg = {'paths': {'workdir': self.tmp.name, 'state': self.tmp.name+'/state.json'},
                    'conversation': {}, 'sending': {'min_gap_seconds': 0}}
        self.br = Mock()
        self.br.json.return_value = {'ok': True}
        self.br.click.return_value = {}
        self.before = {'conv_key': 'id:fiction', 'messages': []}

    def deliver(self, count=0, key='id:fiction'):
        after = {'conv_key': key, 'messages': [
            {'id': 'fiction-sys%d' % n, 'who': 'unknown', 'text': '附件简历已发送'} for n in range(count)]}
        with patch.object(A.chat_dom, 'active_conversation', side_effect=chain([self.before], repeat(after))), \
             patch.object(A, '_resume_button', side_effect=AssertionError('toolbar forbidden')), \
             patch.object(A, '_resume_dialog', return_value={}), \
             patch.object(A.time, 'sleep'), patch.object(A.HC, 'trace'):
            return A.accept_resume_request(self.br, 1, self.cfg, 'id:fiction', 'fiction-job', '虚构请求卡')

    def test_action_exists(self):
        self.assertTrue(callable(getattr(A, 'accept_resume_request', None)), 'missing accept card action')

    def test_dry_and_missing_button_do_not_click(self):
        for dry in (False, True):
            for found in (False, True):
                if not dry and found:
                    continue
                self.cfg['conversation']['dry'] = dry
                self.br.json.return_value = {'ok': found, 'reason': 'accept_button'}
                receipt = self.deliver()
                self.assertEqual(receipt, 'dry_ok' if found else 'send_aborted:accept_button')
                self.br.click.assert_not_called()
                self.assertEqual(S.ledger_latest(self.cfg), [])

    def test_success_and_duplicate_share_resume_ledger(self):
        self.assertEqual(self.deliver(1), 'sent')
        self.assertEqual(A.LAST_RESUME_BUBBLES, 1)
        self.br.click.assert_called_once_with(1, '#bh-conv-accept')
        self.assertEqual(S.ledger_latest(self.cfg)[0]['status'], 'sent')
        self.br.reset_mock()
        self.assertEqual(self.deliver(), 'duplicate')
        self.br.click.assert_not_called()
        self.assertEqual(A.LAST_RESUME_BUBBLES, 0)

    def test_no_new_receipt_is_pending_in_shared_ledger(self):
        self.assertEqual(self.deliver(), 'pending_verification')
        self.assertEqual(S.ledger_latest(self.cfg)[0]['status'], 'pending')
        self.assertEqual(A.LAST_RESUME_BUBBLES, 0)

    def test_confirmation_dialog_is_clicked_only_once(self):
        with patch.object(A, '_resume_dialog', return_value={'confirm': True}):
            # deliver normally supplies an empty dialog; drive the shared action here.
            after = {'conv_key': 'id:fiction', 'messages': [
                {'id': 'fiction-receipt', 'who': 'unknown', 'text': '附件简历已发送'}]}
            with patch.object(A.chat_dom, 'active_conversation', side_effect=[self.before] + [self.before] * 8 + [after]), \
                 patch.object(A.time, 'sleep'):
                receipt = A.accept_resume_request(self.br, 1, self.cfg, 'id:fiction', 'fiction-job', '虚构请求卡')
        self.assertEqual(receipt, 'sent')
        self.assertEqual([call.args[1] for call in self.br.click.call_args_list],
                         ['#bh-conv-accept', '#bh-conv-resume-confirm'])

    def test_receipt_with_confirm_popup_never_confirms_and_cancels_once(self):
        after = {'conv_key': 'id:fiction', 'messages': [
            {'id': 'fiction-receipt', 'who': 'unknown', 'text': '附件简历已发送'}]}
        self.br.json.return_value = {'ok': True, 'present': True, 'close': True}
        with patch.object(A.chat_dom, 'active_conversation',
                          side_effect=chain([self.before], repeat(after))), \
             patch.object(A, '_resume_dialog', return_value={'confirm': True, 'html': '简历 确定 取消'}), \
             patch.object(A.time, 'sleep'), patch.object(A.HC, 'trace'):
            result = A.accept_resume_request(self.br, 1, self.cfg, 'id:fiction', 'fiction-job', '虚构请求卡')
        self.assertEqual(result, 'sent')
        self.assertEqual([call.args[1] for call in self.br.click.call_args_list],
                         ['#bh-conv-accept', '#bh-conv-resume-close'])
        self.assertEqual(S.ledger_latest(self.cfg)[0]['status'], 'sent')

    def test_two_system_bubbles_are_counted(self):
        self.assertEqual(self.deliver(2), 'sent')
        self.assertEqual(A.LAST_RESUME_BUBBLES, 2)

    def test_wrong_identity_never_clicks(self):
        self.before['conv_key'] = 'id:other'
        self.assertEqual(self.deliver(), 'error: unknown_conversation')
        self.br.click.assert_not_called()

    def test_selector_uses_friend_card_and_exact_agree_text(self):
        A._accept_button(self.br, 1, 'id:fiction', '虚构请求卡')
        js = self.br.json.call_args.args[1]
        self.assertIn('item-friend', js)
        self.assertIn("=== '同意'", js)
        self.assertNotIn('发简历', js)

    def test_selector_runs_offline_with_nested_ambiguous_hidden_and_latest_cards(self):
        A._accept_button(self.br, 1, 'id:fiction', '虚构请求卡')
        expression = self.br.json.call_args.args[1]
        fixture = r"""
const input = JSON.parse(process.argv[1]);
const getComputedStyle = e => ({display:e.hidden ? 'none' : 'block'});
const make = (text, children=[], hidden=false) => ({innerText:text, id:'',
  offsetParent:hidden ? null : {}, children,
  contains(other) {return this.children.some(c => c === other || c.contains(other));},
  querySelectorAll() {return this.children.flatMap(c => [c,...c.querySelectorAll('*')]);}});
const oldButton = make('同意');
const oldCard = make('虚构请求卡', [oldButton]);
const leaf = make(input.label || ' 同意 ', [], input.hidden);
const nested = make(input.label || '同意', [leaf], input.hidden);
const current = make('虚构请求卡', input.missing ? [] : input.ambiguous ? [nested,make('同意')] : [nested]);
const header = {getAttribute:a => a === 'data-conversation-id' ? input.key : ''};
const panel = {offsetParent:{}, querySelector:()=>header, querySelectorAll:()=>[oldCard,current]};
const document = {querySelectorAll:s => s === '.chat-conversation' ? [panel] : []};
const result = eval(input.expr);
process.stdout.write(JSON.stringify({result,old:oldButton.id,outer:nested.id,inner:leaf.id}));
"""
        for state, ok, reason in (({}, True, None), ({'ambiguous': True}, False, 'accept_button'),
                                  ({'missing': True}, False, 'accept_button'),
                                  ({'hidden': True}, False, 'accept_button'),
                                  ({'label': '不同意'}, False, 'accept_button'),
                                  ({'key': 'other'}, False, 'unknown_conversation')):
            with self.subTest(state=state):
                payload = dict(expr=expression, key='fiction', **{k:v for k,v in state.items() if k != 'key'})
                payload['key'] = state.get('key', 'fiction')
                process = subprocess.run(['node', '-e', fixture, json.dumps(payload)],
                                         capture_output=True, text=True, check=True)
                data = json.loads(process.stdout)
                self.assertEqual(data['result']['ok'], ok)
                self.assertEqual(data['result'].get('reason'), reason)
                self.assertEqual(data['old'], '')
                self.assertEqual(data['outer'], '')
                self.assertEqual(data['inner'], 'bh-conv-accept' if ok else '')
