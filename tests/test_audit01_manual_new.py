"""audit-01 U1：「需人工」会话又来新的 HR 消息时提醒一次并重新置顶；只看列表快照，不打开、不回复。

虚构数据、临时目录，不连浏览器、不调模型。
"""
import unittest
from unittest.mock import patch

import test_conversation_run as T
import conv_store


class ManualNewMessageTests(unittest.TestCase):
    setUp = T.ConversationRunTests.setUp
    module = T.ConversationRunTests.module
    item = T.ConversationRunTests.item
    history = T.ConversationRunTests.history
    run_hop = T.ConversationRunTests.run_hop
    record = T.ConversationRunTests.record

    HR = '您好，岗位还在招，方便明天电话沟通一下吗'

    def manual(self, item, seen_age=3600, status='manual'):
        row = self.record(item, status=status)
        row['last_seen'] = {'ts': self.ts - seen_age, 'text': '旧消息'}
        row['notified']['manual'] = True
        return row

    def hop(self, items, records, dry=False):
        conv_store.save(self.path, records)
        c = self.module()
        with patch.object(c.chat_dom, 'reveal_conversation', return_value={'ok': False, 'reason': 'not_found'}), \
             patch.object(c.chat_dom, 'reset_list_scroll'):
            return self.run_hop(items=items, dry=dry)

    def lines(self):
        return [l for l in self.out if '需人工·新消息' in l]

    def test_new_hr_message_pushed_once_and_pinned(self):
        it = self.item(1, text=self.HR, age=60)
        result, opened, calls, _, _ = self.hop([it], {it['conv_key']: self.manual(it)})
        opened.assert_not_called()
        self.assertFalse(calls)
        self.assertEqual(len(self.lines()), 1)
        self.assertIn('虚构公司1', self.lines()[0])
        self.assertIn(self.HR[:10], self.lines()[0])
        self.assertEqual(result['manual_new'], 1)
        saved = conv_store.load(self.path)[it['conv_key']]
        self.assertEqual(saved['status'], 'manual')
        self.assertEqual(saved['manual_new_snap']['text'], self.HR)
        self.assertEqual(saved['pin']['reason'], 'manual_new')
        self.assertIsNone(saved['pin']['done_ts'])
        self.assertTrue(any(l in result['notifications'] for l in self.lines()))

    def test_same_message_not_pushed_again(self):
        it = self.item(1, text=self.HR, age=60)
        self.hop([it], {it['conv_key']: self.manual(it)})
        self.out.clear()
        self.br.new_tab.side_effect = [20, 21, 22, 23]
        result, _, _, _, _ = self.hop([it], conv_store.load(self.path))
        self.assertEqual(self.lines(), [])
        self.assertEqual(result['manual_new'], 0)

    def test_unpinned_then_new_message_repins(self):
        it = self.item(1, text=self.HR, age=60)
        row = self.manual(it)
        row['pin'] = {'reason': 'manual', 'text': '旧', 'msg_ts': self.ts - 3600, 'want_ts': self.ts - 3600,
                      'pinned_ts': self.ts - 3500, 'done_ts': self.ts - 1000, 'done_by': 'user_unpinned',
                      'failures': 0, 'pin_migrated_13b': True}
        self.hop([it], {it['conv_key']: row})
        saved = conv_store.load(self.path)[it['conv_key']]
        self.assertIsNone(saved['pin']['done_ts'])
        self.assertIsNone(saved['pin']['pinned_ts'])

    def test_message_not_newer_than_last_seen_ignored(self):
        it = self.item(1, text=self.HR, age=7200)
        self.hop([it], {it['conv_key']: self.manual(it, seen_age=3600)})
        self.assertEqual(self.lines(), [])

    def test_override_record_without_last_seen_uses_updated_ts(self):
        it = self.item(1, text=self.HR, age=7200)
        row = self.manual(it)
        row['last_seen'] = {'ts': None, 'text': ''}
        row['updated_ts'] = self.ts - 3600
        self.hop([it], {it['conv_key']: row})
        self.assertEqual(self.lines(), [])

    def test_own_side_or_system_messages_ignored(self):
        cases = [self.item(1, text=self.HR, age=60, mine=True),
                 self.item(2, text='对方已同意，您的附件简历已发送给对方', age=60),
                 self.item(3, text='附件简历请求已发送', age=60)]
        self.hop(cases, {it['conv_key']: self.manual(it) for it in cases})
        self.assertEqual(self.lines(), [])

    def test_other_terminal_statuses_untouched(self):
        items = [self.item(n, text=self.HR, age=60) for n in (1, 2, 3)]
        records = {it['conv_key']: self.manual(it, status=s)
                   for it, s in zip(items, ('complete', 'closed', 'declined'))}
        self.hop(items, records)
        self.assertEqual(self.lines(), [])

    def test_at_most_three_per_hop(self):
        items = [self.item(n, text=self.HR + str(n), age=60 + n) for n in range(1, 6)]
        result, _, _, _, _ = self.hop(items, {it['conv_key']: self.manual(it) for it in items})
        self.assertEqual(len(self.lines()), 3)
        saved = conv_store.load(self.path)
        self.assertEqual(sum('manual_new_snap' in saved[it['conv_key']] for it in items), 3)

    def test_dry_run_reports_without_writing(self):
        it = self.item(1, text=self.HR, age=60)
        self.hop([it], {it['conv_key']: self.manual(it)}, dry=True)
        self.assertEqual(len(self.lines()), 1)
        self.assertNotIn('manual_new_snap', conv_store.load(self.path)[it['conv_key']])


    def test_message_before_user_unpinned_counts_as_seen(self):
        # 21:30 真机误报复现：消息 11:51 到，17:30 置顶，用户之后取消置顶（=看过），本跳不该再提醒
        it = self.item(1, text=self.HR, age=4 * 3600)
        row = self.manual(it, seen_age=30 * 3600)
        row['pin'] = {'reason': 'manual', 'text': '旧', 'msg_ts': self.ts - 30 * 3600, 'want_ts': self.ts - 30 * 3600,
                      'pinned_ts': self.ts - 2 * 3600, 'done_ts': None, 'done_by': None,
                      'failures': 0, 'pin_migrated_13b': True}
        it['isTop'] = 0  # 用户已取消置顶，本跳先登记 done，再判新消息
        self.hop([it], {it['conv_key']: row})
        self.assertEqual(self.lines(), [])
        self.assertEqual(conv_store.load(self.path)[it['conv_key']]['pin']['done_by'], 'user_unpinned')

    def test_message_after_user_unpinned_still_pushed(self):
        it = self.item(1, text=self.HR, age=60)
        row = self.manual(it, seen_age=30 * 3600)
        row['pin'] = {'reason': 'manual', 'text': '旧', 'msg_ts': self.ts - 30 * 3600, 'want_ts': self.ts - 30 * 3600,
                      'pinned_ts': self.ts - 5 * 3600, 'done_ts': self.ts - 3600, 'done_by': 'user_unpinned',
                      'failures': 0, 'pin_migrated_13b': True}
        self.hop([it], {it['conv_key']: row})
        self.assertEqual(len(self.lines()), 1)


if __name__ == '__main__':
    unittest.main()
