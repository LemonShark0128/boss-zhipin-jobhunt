"""iter-06：要置顶的会话在网页会话列表里找不到（揭示 not_found）时，当跳就推带原话的提醒，不再空等 3 跳。

2026-11-07 真机：某公司 HR 14:30 来新消息 → 需人工·新消息（当时微信通道断了，用户没收到）→
登记置顶 → 14:30 / 15:30 / 16:30 三跳揭示都是 not_found → 第 3 次才推「⚠️ 置顶失败 …，需你手动看」，
不带原话、不说原因。只读查看真实页面：BOSS 网页「全部」列表前端不显示 relationType=5 的会话
（服务端名单里有、页面 dataSources 里没有），重试多少次都找不到。

改后：
- 揭示 not_found → 当跳推一次「📵 公司｜职位｜HR：原话（网页会话列表里找不到这条，没法自动置顶，请在 App 里看）」，
  不再重试；同跳已登记的「🙋 需人工·新消息」行被这条替换（同一段原话不推两遍）。
- 其他失败原因照旧重试，第 3 次的「⚠️ 置顶失败」也带上原话。

虚构数据、临时目录、sleep 打桩，不连浏览器。
"""
import unittest
from unittest.mock import Mock, patch

import test_conv13_pin as P  # 先导入：它把 scripts/ 加进 sys.path
import conv_store  # noqa: E402


class PinUnreachableTests(P.PinBase):
    def visible_none(self, *a):
        return [{'conv_key': 'id:other', 'receipt': False}]

    def test_backlog_not_found_pushes_once_with_words_and_stops(self):
        self.store(self.pinrec(1, self.ts - 3600))
        reveal = Mock(return_value={'ok': False, 'reason': 'not_found'})
        result, pin = self.pin_hop(reveal=reveal, row_snapshots=self.visible_none)
        pin.assert_not_called()
        lines = [n for n in result['notifications'] if n.startswith('📵')]
        self.assertEqual(len(lines), 1, result['notifications'])
        self.assertIn('虚构公司1', lines[0])
        self.assertIn('虚构原话1', lines[0])
        self.assertIn('App', lines[0])
        self.assertIn('网页会话列表里找不到', lines[0])
        self.assertFalse(any('置顶失败' in n for n in result['notifications']))
        self.assertIn(lines[0], self.out)
        saved = self.saved()['pin']
        self.assertEqual(saved['failures'], 3)
        self.assertIsNone(saved['pinned_ts'])
        self.assertIsNone(saved['done_ts'])  # 仍算「要人看」，只是不再自动置顶
        self.assertEqual(result['pin_failed'], 1)
        self.assertEqual(self.saved()['actions'][-1]['result'], 'reveal_not_found')

        self.br.new_tab.side_effect = [20]
        self.out.clear()
        reveal2 = Mock(return_value={'ok': False, 'reason': 'not_found'})
        again, pin2 = self.pin_hop(reveal=reveal2, row_snapshots=self.visible_none)
        reveal2.assert_not_called()
        pin2.assert_not_called()
        self.assertFalse(any(n.startswith('📵') for n in again['notifications']))

    def test_other_reveal_failures_still_retry(self):
        self.store(self.pinrec(1, self.ts - 3600))
        reveal = Mock(return_value={'ok': False, 'reason': 'ambiguous'})
        result, _ = self.pin_hop(reveal=reveal, row_snapshots=self.visible_none)
        self.assertFalse(any(n.startswith('📵') for n in result['notifications']))
        self.assertEqual(self.saved()['pin']['failures'], 1)

    def test_third_failure_warning_carries_words(self):
        self.store(self.pinrec(1, self.ts - 7200, failures=2))
        result, _ = self.pin_hop(pin=Mock(return_value={'ok': False, 'reason': 'not_confirmed'}))
        warn = [n for n in result['notifications'] if n.startswith('⚠️ 置顶失败')]
        self.assertEqual(len(warn), 1)
        self.assertIn('虚构原话1', warn[0])
        self.assertIn('需你手动看', warn[0])

    def test_manual_new_line_replaced_not_duplicated(self):
        hr = '你好，我们急招经销专员，能求一份你的简历吗？'
        it = self.item(1, text=hr, age=60)
        row = self.record(it, status='manual')
        row['last_seen'] = {'ts': self.ts - 3600, 'text': '旧消息'}
        row['notified']['manual'] = True
        conv_store.save(self.path, {it['conv_key']: row})
        c = self.module()
        with patch.object(c.chat_dom, 'reveal_conversation', return_value={'ok': False, 'reason': 'not_found'}), \
             patch.object(c.chat_dom, 'pin_conversation') as pin, \
             patch.object(c.chat_dom, 'reset_list_scroll'), patch.object(c.time, 'sleep'):
            result = self.run_hop(items=[it], row_snapshots=self.visible_none)[0]
        pin.assert_not_called()
        self.assertEqual(result['manual_new'], 1)
        self.assertFalse(any('需人工·新消息' in n for n in result['notifications']), result['notifications'])
        lines = [n for n in result['notifications'] if n.startswith('📵')]
        self.assertEqual(len(lines), 1, result['notifications'])
        self.assertIn(hr[:12], lines[0])
        self.assertEqual(sum(hr[:12] in n for n in result['notifications']), 1)


if __name__ == '__main__':
    unittest.main()
