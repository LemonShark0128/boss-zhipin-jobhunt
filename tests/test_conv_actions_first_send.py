"""Offline resume receipts: fictional messages, real temporary ledger, no browser."""
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


class FirstSendReceipts(unittest.TestCase):
    def deliver(self, added, before_messages=None, after_key='id:fiction', pattern=None):
        with tempfile.TemporaryDirectory() as tmp:
            cfg = {'paths': {'workdir': tmp, 'state': tmp + '/state.json'},
                   'sending': {'min_gap_seconds': 0}, 'conversation': {}}
            if pattern is not None:
                cfg['conversation']['resume_sent_pattern'] = pattern
            before = {'conv_key': 'id:fiction', 'messages': before_messages or []}
            after = {'conv_key': after_key, 'messages': before['messages'] + added}
            br = Mock()
            br.click.return_value = {}
            with patch.object(A.chat_dom, 'active_conversation', side_effect=chain([before], repeat(after))), \
                 patch.object(A, '_resume_button', return_value={'ok': True}), \
                 patch.object(A, '_resume_dialog', return_value={}), \
                 patch.object(A.time, 'sleep'), patch.object(A.HC, 'trace') as trace:
                receipt = A.send_resume(br, 1, cfg, 'id:fiction', 'fiction-job')
            return receipt, getattr(A, 'LAST_RESUME_BUBBLES', None), S.ledger_latest(cfg), trace, br

    def bubble(self, ident='fiction-new', who='unknown', text='您的附件简历 虚构小林 已发送给Boss'):
        return {'id': ident, 'who': who, 'text': text, 'classes': 'message-item item-system'}

    def test_new_system_resume_bubble_marks_ledger_sent(self):
        receipt, count, rows, _, br = self.deliver([self.bubble()])
        self.assertEqual(receipt, 'sent')
        self.assertEqual(count, 1)
        self.assertEqual(rows[0]['status'], 'sent')
        self.assertEqual(br.click.call_count, 1)

    def test_two_distinct_system_bubbles_are_traced_and_counted_once_each(self):
        receipt, count, rows, trace, br = self.deliver([
            self.bubble('fiction-a'), self.bubble('fiction-b'), self.bubble('fiction-b')])
        self.assertEqual(receipt, 'sent')
        self.assertEqual(count, 2)
        self.assertTrue(any('2' in str(call) for call in trace.call_args_list))
        self.assertEqual(rows[0]['status'], 'sent')
        self.assertEqual(br.click.call_count, 1)

    def test_old_friend_missing_id_and_wrong_conversation_do_not_confirm(self):
        for added, old, key in (([], [self.bubble()], 'id:fiction'),
                                 ([self.bubble(who='friend')], [], 'id:fiction'),
                                 ([self.bubble(ident=None)], [], 'id:fiction'),
                                 ([self.bubble()], [], 'id:other')):
            with self.subTest(added=added, key=key):
                receipt, count, rows, _, _ = self.deliver(added, old, key)
                self.assertEqual(receipt, 'pending_verification')
                self.assertEqual(count, 0)
                self.assertEqual(rows[0]['status'], 'pending')

    def test_custom_pattern_and_legacy_mine_bubble_remain_supported(self):
        for message, pattern, count in ((self.bubble(text='虚构附件交付完成'), '虚构附件交付完成', 1),
                                        (self.bubble(who='mine', text='附件简历'), None, 0)):
            with self.subTest(message=message):
                receipt, actual, rows, _, _ = self.deliver([message], pattern=pattern)
                self.assertEqual(receipt, 'sent')
                self.assertEqual(actual, count)
                self.assertEqual(rows[0]['status'], 'sent')

    def test_count_resets_after_previous_success(self):
        self.deliver([self.bubble('fiction-a'), self.bubble('fiction-b')])
        receipt, count, _, _, _ = self.deliver([])
        self.assertEqual(receipt, 'pending_verification')
        self.assertEqual(count, 0)

    def test_mine_receipts_confirm_but_do_not_count_as_duplicate_system_bubbles(self):
        messages = [{'id': 'fiction-mine-%d' % n, 'who': 'mine', 'text': '简历已发送'}
                    for n in (1, 2)]
        receipt, count, rows, _, _ = self.deliver(messages)
        self.assertEqual(receipt, 'sent')
        self.assertEqual(count, 0)
        self.assertEqual(rows[0]['status'], 'sent')


class ResumeOncePollingTests(unittest.TestCase):
    def run_scenario(self, receipt_poll=None, confirm=False, changed_poll=None, close=False):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        cfg = {'paths': {'workdir': tmp.name, 'state': tmp.name + '/state.json'},
               'sending': {'min_gap_seconds': 0}, 'conversation': {}}
        before = {'conv_key': 'id:fiction', 'messages': []}
        br = Mock()
        br.click.return_value = {}
        br.json.return_value = {'present': True, 'close': close}
        polls = []
        def active(*args):
            if not polls:
                polls.append('before')
                return before
            number = len(polls)
            polls.append(number)
            return {'conv_key': 'id:other' if changed_poll == number else 'id:fiction',
                    'messages': [dict(id='fiction-receipt', who='unknown', text='附件简历已发送')]
                    if receipt_poll is not None and number >= receipt_poll else []}
        with patch.object(A.chat_dom, 'active_conversation', side_effect=active), \
             patch.object(A, '_resume_button', return_value={'ok': True}), \
             patch.object(A, '_resume_dialog', return_value={'confirm': True, 'html': '简历 确定'} if confirm else {}), \
             patch.object(A.time, 'sleep') as sleep, patch.object(A.HC, 'trace') as trace:
            result = A.send_resume(br, 1, cfg, 'id:fiction', 'fiction-job')
        clicks = [call.args[1] for call in br.click.call_args_list]
        return result, clicks, S.ledger_latest(cfg), polls[1:], sleep, trace

    def test_second_poll_receipt_prevents_confirmation(self):
        result, clicks, rows, polls, sleep, _ = self.run_scenario(receipt_poll=2, confirm=True)
        self.assertEqual(result, 'sent')
        self.assertEqual(clicks, ['#bh-conv-resume'])
        self.assertEqual(polls, [1, 2])
        self.assertEqual(sleep.call_count, 2)
        self.assertEqual(rows[0]['status'], 'sent')
        self.assertEqual(A.LAST_RESUME_BUBBLES, 1)

    def test_receipt_after_confirmation_confirms_exactly_once(self):
        result, clicks, rows, polls, _, _ = self.run_scenario(receipt_poll=10, confirm=True)
        self.assertEqual(result, 'sent')
        self.assertEqual(clicks, ['#bh-conv-resume', '#bh-conv-resume-confirm'])
        self.assertEqual(len(polls), 10)
        self.assertEqual(rows[0]['status'], 'sent')

    def test_confirmation_without_receipt_remains_pending(self):
        result, clicks, rows, polls, sleep, _ = self.run_scenario(confirm=True)
        self.assertEqual(result, 'pending_verification')
        self.assertEqual(clicks, ['#bh-conv-resume', '#bh-conv-resume-confirm'])
        self.assertEqual(len(polls), 20)
        self.assertEqual(sleep.call_count, 20)
        self.assertEqual(rows[0]['status'], 'pending')
        self.assertEqual(A.LAST_RESUME_BUBBLES, 0)

    def test_no_receipt_or_dialog_never_clicks_again(self):
        result, clicks, rows, polls, _, _ = self.run_scenario()
        self.assertEqual(result, 'pending_verification')
        self.assertEqual(clicks, ['#bh-conv-resume'])
        self.assertEqual(len(polls), 16)  # initial 8 + late 8（03m 审查退回）
        self.assertEqual(rows[0]['status'], 'pending')

    def test_conversation_change_during_poll_never_confirms(self):
        result, clicks, rows, polls, _, _ = self.run_scenario(changed_poll=2, confirm=True)
        self.assertEqual(result, 'pending_verification')
        self.assertEqual(clicks, ['#bh-conv-resume'])
        self.assertEqual(polls, [1, 2])
        self.assertEqual(rows[0]['status'], 'pending')

    def test_ambiguous_cancel_is_left_alone_after_receipt(self):
        result, clicks, rows, _, _, trace = self.run_scenario(receipt_poll=1, confirm=True)
        self.assertEqual(result, 'sent')
        self.assertEqual(clicks, ['#bh-conv-resume'])
        self.assertEqual(rows[0]['status'], 'sent')
        self.assertTrue(trace.called)

    def test_dialog_selectors_only_mark_unique_enabled_buttons_offline(self):
        fixture = r"""
const input = JSON.parse(process.argv[1]);
const getComputedStyle = e => ({display:'block'});
const buttons = input.buttons.map(b => ({innerText:b.text, offsetParent:b.hidden ? null : {},
  disabled:b.disabled, getAttribute:()=>b.ariaDisabled || null, closest:()=>null, id:''}));
const header = {getAttribute:a=>a === 'data-conversation-id' ? 'fiction' : ''};
const panel = {offsetParent:{},querySelector:()=>header,querySelectorAll:()=>[]};
const dialog = {innerText:'虚构简历 确定 取消',offsetParent:{},outerHTML:'<div>虚构简历弹窗</div>',
  contains:()=>false,closest:()=>null,querySelectorAll:()=>buttons};
const document = {querySelectorAll:s=>s === '.chat-conversation' ? [panel] :
  s.startsWith('#') ? [] : [dialog]};
const result = eval(input.expr);
process.stdout.write(JSON.stringify({result,ids:buttons.map(b=>b.id)}));
"""
        for helper, flag, selector, states in (
            (A._resume_close_button, 'close', 'bh-conv-resume-close', [
                ([{'text': '取消'}], True),
                ([{'text': '取消'}, {'text': '关闭'}], False),
                ([{'text': '知道了'}], True),
                ([{'text': '确定'}], False),
                ([{'text': '取消', 'disabled': True}], False),
                ([{'text': '关闭', 'ariaDisabled': 'true'}], False),
                ([{'text': '取消', 'hidden': True}], False)]),
            (A._resume_dialog, 'confirm', 'bh-conv-resume-confirm', [
                ([{'text': '确定'}], True),
                ([{'text': '确定'}, {'text': '发送'}], False),
                ([{'text': '确认', 'disabled': True}], False),
                ([{'text': '发送', 'ariaDisabled': 'true'}], False)])):
            br = Mock()
            br.json.return_value = {}
            helper(br, 1, 'id:fiction')
            expression = br.json.call_args.args[1]
            for buttons, expected in states:
                with self.subTest(helper=helper.__name__, buttons=buttons):
                    run = subprocess.run(['node', '-e', fixture, json.dumps(
                        {'expr': expression, 'buttons': buttons})],
                        capture_output=True, text=True, check=True)
                    data = json.loads(run.stdout)
                    self.assertEqual(data['result'][flag], expected)
                    self.assertEqual(data['ids'].count(selector), int(expected))
                    self.assertIn('虚构简历弹窗', data['result']['html'])
