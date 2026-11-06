"""conv-06b：列表 DOM 渲染好再读行；揭示失败按原因计数。假浏览器、临时目录、虚构数据。"""
import json
import subprocess
import unittest
from unittest.mock import Mock, patch
import test_conversation_run as T


class WaitRowsReadyTests(unittest.TestCase):
    def dom(self):
        import chat_dom
        return chat_dom

    def test_ready_after_retries(self):
        br, sleep = Mock(), Mock()
        br.json.side_effect = [{'rows': 0, 'vl': True, 'ds': 0}, {'rows': 0, 'vl': True, 'ds': 99},
                               {'rows': 40, 'vl': True, 'ds': 99}]
        self.assertIs(self.dom().wait_rows_ready(br, 10, attempts=5, delay=2.0, sleep=sleep), True)
        self.assertEqual(sleep.call_count, 2)

    def test_rows_without_datasources_is_not_ready(self):
        br, sleep = Mock(), Mock()
        br.json.return_value = {'rows': 40, 'vl': True, 'ds': 0}
        self.assertIs(self.dom().wait_rows_ready(br, 10, attempts=3, sleep=sleep), False)

    def test_rows_without_virtual_list_is_ready(self):
        br = Mock()
        br.json.return_value = {'rows': 12, 'vl': False, 'ds': 0}
        self.assertIs(self.dom().wait_rows_ready(br, 10, sleep=Mock()), True)

    def test_never_ready_sleeps_between_attempts_only(self):
        br, sleep = Mock(), Mock()
        br.json.return_value = {'rows': 0, 'vl': True, 'ds': 0}
        self.assertIs(self.dom().wait_rows_ready(br, 10, attempts=4, sleep=sleep), False)
        self.assertEqual(br.json.call_count, 4)
        self.assertEqual(sleep.call_count, 3)

    def test_unjudgeable_returns_none_without_waiting(self):
        for value in (None, [], 'x', True, {'rows': '40'}, {}):
            with self.subTest(value=value):
                br, sleep = Mock(), Mock()
                br.json.return_value = value
                self.assertIsNone(self.dom().wait_rows_ready(br, 10, sleep=sleep))
                sleep.assert_not_called()
        br, sleep = Mock(), Mock()
        br.json.side_effect = RuntimeError('虚构桥接失败')
        self.assertIsNone(self.dom().wait_rows_ready(br, 10, sleep=sleep))
        sleep.assert_not_called()

    def test_script_reads_rows_and_datasources(self):
        br = Mock()
        br.json.return_value = {'rows': 1, 'vl': False, 'ds': 0}
        self.dom().wait_rows_ready(br, 10, sleep=Mock())
        script = br.json.call_args.args[1]
        harness = r"""
const {script, rows, ds} = JSON.parse(process.argv[1]);
const v = {$options:{name:'virtual-list'}, dataSources: new Array(ds).fill({})};
global.document = {querySelector(){return {__vue__:v};},
  querySelectorAll(sel){return sel === '.friend-content' ? new Array(rows).fill({}) : [];}};
console.log(JSON.stringify(eval(script)));
"""
        run = subprocess.run(['node', '-e', harness, json.dumps({'script': script, 'rows': 40, 'ds': 99})],
                             capture_output=True, text=True, check=True)
        self.assertEqual(json.loads(run.stdout), {'rows': 40, 'vl': True, 'ds': 99})


class RowsReadyRunTests(unittest.TestCase):
    setUp = T.ConversationRunTests.setUp
    module = T.ConversationRunTests.module
    item = T.ConversationRunTests.item
    history = T.ConversationRunTests.history
    run_hop = T.ConversationRunTests.run_hop

    def rows(self, *items):
        return [{'conv_key': it['conv_key'], 'receipt': False} for it in items]

    def test_empty_first_read_waits_and_rereads(self):
        c = self.module()
        items = [self.item(i + 1, age=i + 1) for i in range(3)]
        with patch.object(c.chat_dom, 'wait_rows_ready', return_value=True) as wait, \
             patch.object(c.chat_dom, 'reveal_conversation') as reveal, \
             patch.object(c.chat_dom, 'reset_list_scroll') as reset:
            result, opened, _, _, _ = self.run_hop(
                items=items, row_snapshots=[[]] + [self.rows(*items)] * 10)
        wait.assert_called_once()
        reveal.assert_not_called()
        reset.assert_not_called()
        self.assertEqual(result['candidates'], 3)
        self.assertEqual(result['reveal_failed'], 0)
        self.assertFalse(result.get('rows_not_ready'))
        self.assertEqual(opened.call_count, 3)

    def test_newest_missing_waits_and_rereads(self):
        c = self.module()
        newest, older = self.item(1, age=1), self.item(2, age=50)
        with patch.object(c.chat_dom, 'wait_rows_ready', return_value=True) as wait, \
             patch.object(c.chat_dom, 'reveal_conversation') as reveal, \
             patch.object(c.chat_dom, 'reset_list_scroll'):
            result, _, _, _, _ = self.run_hop(
                items=[newest, older], row_snapshots=[self.rows(older)] + [self.rows(newest, older)] * 10)
        wait.assert_called_once()
        reveal.assert_not_called()
        self.assertEqual(result['candidates'], 2)

    def test_newest_present_does_not_wait(self):
        c = self.module()
        newest, older = self.item(1, age=1), self.item(2, age=50)
        with patch.object(c.chat_dom, 'wait_rows_ready') as wait, \
             patch.object(c.chat_dom, 'reveal_conversation', return_value={'ok': False, 'reason': 'not_found'}), \
             patch.object(c.chat_dom, 'reset_list_scroll'):
            result, _, _, _, _ = self.run_hop(items=[newest, older],
                                              row_snapshots=lambda *a: self.rows(newest))
        wait.assert_not_called()
        self.assertEqual(result['reveal_reasons'], {'not_found': 1})

    def test_not_ready_flags_and_continues(self):
        c = self.module()
        item = self.item()
        with patch.object(c.chat_dom, 'wait_rows_ready', return_value=False), \
             patch.object(c.chat_dom, 'reveal_conversation', return_value={'ok': False, 'reason': 'no_virtual_list'}) as reveal, \
             patch.object(c.chat_dom, 'reset_list_scroll'):
            result, opened, _, _, _ = self.run_hop(items=[item], row_snapshots=lambda *a: [])
        self.assertTrue(result['rows_not_ready'])
        reveal.assert_called()
        opened.assert_not_called()
        line = [l for l in self.out if l.startswith('💬 滚动定位')][0]
        self.assertIn('rows_not_ready', line)
        self.assertIn('no_virtual_list', line)

    def test_unjudgeable_wait_keeps_first_read(self):
        c = self.module()
        item = self.item()
        with patch.object(c.chat_dom, 'wait_rows_ready', return_value=None), \
             patch.object(c.chat_dom, 'reveal_conversation', return_value={'ok': False, 'reason': 'not_found'}), \
             patch.object(c.chat_dom, 'reset_list_scroll'):
            result, _, _, _, _ = self.run_hop(items=[item], row_snapshots=[[]] + [self.rows(item)] * 10)
        self.assertFalse(result.get('rows_not_ready'))
        # conv-11：处理时重读当前 DOM，行已出现，无需揭示。
        self.assertEqual(result['reveal_failed'], 0)

    def test_reveal_empty_reread_waits_then_succeeds(self):
        c = self.module()
        newest, far = self.item(1, age=1), self.item(2, age=5)
        snaps = [self.rows(newest), [], self.rows(far)] + [self.rows(newest, far)] * 10
        with patch.object(c.chat_dom, 'wait_rows_ready', return_value=True) as wait, \
             patch.object(c.chat_dom, 'reveal_conversation', return_value={'ok': True, 'index': 70}), \
             patch.object(c.chat_dom, 'reset_list_scroll'):
            result, opened, _, _, _ = self.run_hop(items=[newest, far], row_snapshots=snaps)
        # conv-11：空快照由处理前读行消费，揭示后行已就绪，不再等待。
        wait.assert_not_called()
        self.assertEqual(result['revealed'], 1)
        self.assertEqual(result['reveal_failed'], 0)
        self.assertEqual(result['processed'], 2)

    def test_reasons_counted_and_shown(self):
        c = self.module()
        newest = self.item(1, age=1)
        a, b, d = self.item(2, age=2), self.item(3, age=3), self.item(4, age=4)
        receipts = {a['conv_key']: {'ok': False, 'reason': 'not_found'},
                    b['conv_key']: {'ok': True}, d['conv_key']: {'ok': True}}
        with patch.object(c.chat_dom, 'wait_rows_ready', return_value=False), \
             patch.object(c.chat_dom, 'reveal_conversation', side_effect=lambda br, tab, it: receipts[it['conv_key']]), \
             patch.object(c.chat_dom, 'reset_list_scroll'):
            snaps = [self.rows(newest), [], [], [], []] + [self.rows(newest)] * 10
            result, opened, _, _, _ = self.run_hop(items=[newest, a, b, d], row_snapshots=snaps)
        self.assertEqual(result['reveal_reasons'], {'not_found': 1, 'rows_empty': 2})
        self.assertEqual(result['reveal_failed'], 3)
        line = [l for l in self.out if l.startswith('💬 滚动定位')][0]
        self.assertIn('not_found 1', line)
        self.assertIn('rows_empty 2', line)

    def test_duplicate_and_missing_reasons(self):
        c = self.module()
        newest, a, b = self.item(1, age=1), self.item(2, age=2), self.item(3, age=3)
        dup = self.rows(newest) + self.rows(a, a)
        with patch.object(c.chat_dom, 'wait_rows_ready'), \
             patch.object(c.chat_dom, 'reveal_conversation', return_value={'ok': True}), \
             patch.object(c.chat_dom, 'reset_list_scroll'):
            snaps = [self.rows(newest), dup, self.rows(newest)] + [self.rows(newest)] * 10
            result, _, _, _, _ = self.run_hop(items=[newest, a, b], row_snapshots=snaps)
        # conv-11：最旧优先且揭示前重读；重复行快照在揭示前消费，两次揭示均缺目标行。
        self.assertEqual(result['reveal_reasons'], {'rows_missing': 2})


if __name__ == '__main__':
    unittest.main()
