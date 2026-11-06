"""Offline reveal acceptance; temporary storage and fictional conversations only."""
import json
import subprocess
import unittest
from unittest.mock import Mock, patch
import test_conversation_run as T


class RevealTests(unittest.TestCase):
    setUp = T.ConversationRunTests.setUp
    module = T.ConversationRunTests.module
    item = T.ConversationRunTests.item
    history = T.ConversationRunTests.history
    run_hop = T.ConversationRunTests.run_hop

    def hop(self, count=1, outcome=None, copies=1, dry=False, retry_fail=False):
        c = self.module()
        items = [self.item(i + 1, age=i + 1) for i in range(count)]
        visible = []
        attempts = {}
        def reveal(br, tab, item):
            key = item['conv_key']
            attempts[key] = attempts.get(key, 0) + 1
            visible[:] = [{'conv_key': key, 'receipt': False}] * copies
            return ({'ok': False, 'reason': 'not_found'} if retry_fail and attempts[key] > 1
                    else outcome if outcome is not None else {'ok': True, 'index': 70})
        with patch.object(c.chat_dom, 'reveal_conversation', side_effect=reveal, create=True) as rev, \
             patch.object(c.chat_dom, 'reset_list_scroll', create=True) as reset:
            result = self.run_hop(items=items, row_snapshots=lambda *a: list(visible), dry=dry)
        return result, rev, reset

    def test_reveal_processes_and_resets(self):
        (result, opened, calls, _, _), rev, reset = self.hop()
        self.assertEqual(result['processed'], 1)
        self.assertEqual(result['revealed'], 1)
        self.assertEqual(result['reveal_failed'], 0)
        # conv-11：out_of_view 是初始积压，揭示成功不扣减。
        self.assertEqual(result['out_of_view'], 1)
        self.assertEqual(sum(call[0] == 'text' for call in calls), 1)
        self.assertFalse(any('📭' in line for line in self.out))
        # conv-11：按需揭示一次，不再先批量揭示。
        self.assertEqual(rev.call_count, 1)
        reset.assert_called_once_with(self.br, 10)

    def test_receipt_on_first_reveal_skips_candidate(self):
        c = self.module()
        item = self.item()
        own = [{'conv_key': item['conv_key'], 'receipt': True}]
        other = [{'conv_key': item['conv_key'], 'receipt': False}]
        with patch.object(c.chat_dom, 'reveal_conversation', return_value={'ok': True}) as reveal, \
             patch.object(c.chat_dom, 'reset_list_scroll') as reset:
            result, opened, calls, send_text, _ = self.run_hop(
                items=[item], row_snapshots=[[], own, other, other])
        opened.assert_not_called()
        send_text.assert_not_called()
        self.assertFalse(calls)
        self.assertFalse(any('📭' in line for line in self.out))
        # conv-11：候选先合并计数，当前 DOM 回执行随后跳过。
        self.assertEqual(result['candidates'], 1)
        self.assertEqual(result['revealed'], 0)
        self.assertEqual(result['reveal_failed'], 0)
        # conv-11：处理时已在当前 DOM，无需揭示或复位。
        reveal.assert_not_called()
        reset.assert_not_called()

    def test_receipt_on_second_reveal_skips_open(self):
        c = self.module()
        item = self.item()
        other = [{'conv_key': item['conv_key'], 'receipt': False}]
        own = [{'conv_key': item['conv_key'], 'receipt': True}]
        with patch.object(c.chat_dom, 'reveal_conversation', return_value={'ok': True}) as reveal, \
             patch.object(c.chat_dom, 'reset_list_scroll') as reset:
            result, opened, calls, send_text, _ = self.run_hop(
                items=[item], row_snapshots=[[], other, other, own])
        # conv-11：按需读到正常行即处理，不再批量揭示或读取第二次揭示回执。
        opened.assert_called_once()
        send_text.assert_called_once()
        self.assertEqual([call[0] for call in calls], ['text', 'resume'])
        self.assertFalse(any('📭' in line for line in self.out))
        self.assertEqual(result['revealed'], 0)
        self.assertEqual(result['reveal_failed'], 0)
        # conv-11：当前行存在，无需揭示或复位。
        reveal.assert_not_called()
        reset.assert_not_called()

    def test_failures_push_at_most_three_and_do_not_open(self):
        for reason in ('ambiguous', 'not_found', 'no_virtual_list'):
            with self.subTest(reason=reason):
                self.setUp()
                (result, opened, calls, _, _), _, reset = self.hop(5, {'ok': False, 'reason': reason})
                reset.assert_called_once_with(self.br, 10)
                opened.assert_not_called()
                self.assertFalse(calls)
                self.assertEqual(result['reveal_failed'], 5)
                self.assertEqual(sum('📭' in line for line in self.out), 3)

    def test_missing_or_duplicate_rows_fail_closed(self):
        for copies in (0, 2):
            with self.subTest(copies=copies):
                self.setUp()
                (result, opened, calls, _, _), _, _ = self.hop(copies=copies)
                opened.assert_not_called()
                self.assertFalse(calls)
                self.assertEqual(result['reveal_failed'], 1)
                self.assertEqual(sum('📭' in line for line in self.out), 1)

    def test_second_reveal_failure_pushes(self):
        (result, opened, _, _, _), _, _ = self.hop(retry_fail=True)
        # conv-11：仅按需揭示一次，不触发替身的第二次揭示失败。
        opened.assert_called_once()
        self.assertEqual(result['revealed'], 1)
        self.assertEqual(result['reveal_failed'], 0)
        self.assertEqual(sum('📭' in line for line in self.out), 0)

    def test_caps_and_timestamp_order(self):
        self.cfg['conversation']['per_hop'] = 8
        (result, opened, calls, _, _), _, _ = self.hop(10)
        self.assertEqual(result['processed'], 8)
        self.assertEqual(result['revealed'], 8)
        self.assertEqual([call.kwargs['conv_key'] for call in opened.call_args_list],
                         # conv-11：最旧优先处理 age=10 到 age=3。
                         ['id:fiction%d' % i for i in range(10, 2, -1)])
        self.setUp()
        self.cfg['conversation']['daily_cap'] = 0
        (result, opened, calls, _, _), _, _ = self.hop(10)
        self.assertEqual(result['processed'], 0)
        opened.assert_not_called()
        self.assertFalse(calls)

    def test_dry_reveals_without_writing_or_sending(self):
        (result, _, calls, _, _), rev, reset = self.hop(dry=True)
        self.assertTrue(rev.called)
        self.assertEqual(result['revealed'], 1)
        self.assertFalse(calls)
        self.assertFalse(self.path.exists())
        reset.assert_called_once()

    def test_no_out_of_view_candidates_does_not_reset(self):
        c = self.module()
        with patch.object(c.chat_dom, 'reveal_conversation') as reveal, \
             patch.object(c.chat_dom, 'reset_list_scroll') as reset:
            result, _, _, _, _ = self.run_hop()
        self.assertEqual(result['processed'], 1)
        reveal.assert_not_called()
        reset.assert_not_called()

    def test_processing_exception_after_reveal_resets(self):
        c = self.module()
        item = self.item()
        visible = [{'conv_key': item['conv_key'], 'receipt': False}]
        with patch.object(c.chat_dom, 'reveal_conversation', return_value={'ok': True}) as reveal, \
             patch.object(c.chat_dom, 'reset_list_scroll', create=True) as reset, \
             patch.object(c, '_execute', side_effect=RuntimeError('fictional failure')):
            with self.assertRaisesRegex(RuntimeError, 'fictional failure'):
                self.run_hop(items=[item], row_snapshots=[[], visible, visible, visible])
        # conv-11：异常前当前行已出现，没有揭示，因此也无需复位。
        reveal.assert_not_called()
        reset.assert_not_called()

    def test_js_and_malformed_bridge_returns(self):
        dom = self.module().chat_dom
        br = Mock()
        br.json.return_value = {'ok': True, 'index': 70}
        self.assertTrue(dom.reveal_conversation(br, 10, self.item())['ok'])
        script = br.json.call_args.args[1]
        for token in ('virtual-list', 'scrollToIndex', 'onScroll', 'encryptJobId',
                      'brandName', 'name', 'friendId', 'securityId', '$nextTick', 'matches.length'):
            self.assertIn(token, script)
        for value in (None, [], 'invalid', True):
            br.json.return_value = value
            self.assertFalse(dom.reveal_conversation(br, 10, self.item())['ok'])
            dom.reset_list_scroll(br, 10)
        br.json.side_effect = RuntimeError('fictional bridge failure')
        self.assertFalse(dom.reveal_conversation(br, 10, self.item())['ok'])
        dom.reset_list_scroll(br, 10)

    def test_script_matches_exact_identity_and_updates_background_window(self):
        dom = self.module().chat_dom
        item = dict(self.item(), friendId=42, securityId='fiction-security')
        harness = r"""
const {script, rows, missing} = JSON.parse(process.argv[1]);
const events = [];
const v = {$options:{name:'virtual-list'}, dataSources:rows,
  scrollToIndex(i){events.push(['index', i]);},
  onScroll(e){events.push(['event', e.type]);},
  async $nextTick(){events.push(['tick']);}};
global.document = {querySelector(){return missing ? null : {__vue__:v};}};
global.Event = class {constructor(type){this.type=type;}};
(async () => {const result = await eval(script); console.log(JSON.stringify({result, events}));})();
"""
        cases = [([item], False, {'ok': True, 'index': 0}),
                 ([], False, {'ok': False, 'reason': 'not_found'}),
                 ([item, item], False, {'ok': False, 'reason': 'ambiguous'}),
                 ([item], True, {'ok': False, 'reason': 'no_virtual_list'})]
        for field in ('encryptJobId', 'name', 'brandName', 'friendId', 'securityId'):
            cases.append(([dict(item, **{field: 'different'})], False,
                          {'ok': False, 'reason': 'not_found'}))
        cases.append(([{k: v for k, v in item.items() if k not in ('friendId', 'securityId')}],
                      False, {'ok': True, 'index': 0}))
        br = Mock()
        br.json.return_value = {}
        dom.reveal_conversation(br, 10, item)
        script = br.json.call_args.args[1]
        for rows, missing, expected in cases:
            with self.subTest(rows=rows, missing=missing):
                run = subprocess.run(['node', '-e', harness, json.dumps(
                    {'script': script, 'rows': rows, 'missing': missing})],
                    capture_output=True, text=True, check=True)
                actual = json.loads(run.stdout)
                self.assertEqual(actual['result'], expected)
                self.assertEqual(actual['events'],
                                 [['index', 0], ['event', 'scroll'], ['tick']] if expected['ok'] else [])
        dom.reset_list_scroll(br, 10)
        run = subprocess.run(['node', '-e', harness, json.dumps(
            {'script': br.json.call_args.args[1], 'rows': [], 'missing': False})],
            capture_output=True, text=True, check=True)
        self.assertEqual(json.loads(run.stdout)['events'],
                         [['index', 0], ['event', 'scroll'], ['tick']])
