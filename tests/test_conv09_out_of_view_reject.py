"""conv-09：40 行外快照是拒信 → 直接结束，不推 📭。虚构数据、临时目录。"""
import unittest
from unittest.mock import patch
import test_conversation_run as T
import conv_rules
import conv_store


class OutOfViewRejectTests(unittest.TestCase):
    setUp = T.ConversationRunTests.setUp
    module = T.ConversationRunTests.module
    item = T.ConversationRunTests.item
    history = T.ConversationRunTests.history
    run_hop = T.ConversationRunTests.run_hop

    REJECT = '感谢您的投递，但是很抱歉我们正在寻找其他背景的候选人。祝您在'
    REAL = '你好，看了你的简历，适合这个岗位，方便发一份简历过来'

    def hop(self, items, dry=False):
        c = self.module()
        with patch.object(c.chat_dom, 'reveal_conversation', return_value={'ok': False, 'reason': 'not_found'}), \
             patch.object(c.chat_dom, 'reset_list_scroll'):
            return self.run_hop(items=items, row_snapshots=lambda *a: [], dry=dry)

    def test_reject_snapshot_closed_not_pushed(self):
        it = self.item(1, text=self.REJECT)
        result, opened, calls, _, _ = self.hop([it])
        opened.assert_not_called()
        self.assertFalse(calls)
        self.assertFalse(any('📭' in l for l in self.out))
        self.assertEqual(result['out_of_view_rejected'], 1)
        row = conv_store.load(self.path)[it['conv_key']]
        self.assertEqual(row['status'], 'closed')
        self.assertEqual(row['actions'][-1]['type'], 'out_of_view_reject')
        self.assertTrue(any('40行外拒信 1' in l for l in self.out))

    def test_real_message_still_pushed(self):
        result, _, _, _, _ = self.hop([self.item(1, text=self.REAL)])
        self.assertEqual(sum('📭' in l for l in self.out), 1)
        self.assertEqual(result['out_of_view_rejected'], 0)

    def test_rejects_do_not_use_push_slots(self):
        items = [self.item(i, text='不好意思，不太合适哦', age=i) for i in range(1, 5)]
        items += [self.item(i, text=self.REAL + str(i), age=i) for i in range(5, 9)]
        result, _, _, _, _ = self.hop(items)
        self.assertEqual(result['out_of_view_rejected'], 4)
        self.assertEqual(sum('📭' in l for l in self.out), 3)

    def test_rejects_closed_even_after_push_cap(self):
        items = [self.item(i, text=self.REAL + str(i), age=i) for i in range(1, 5)]
        items.append(self.item(9, text=self.REJECT, age=20))
        result, _, _, _, _ = self.hop(items)
        self.assertEqual(result['out_of_view_rejected'], 1)
        self.assertEqual(conv_store.load(self.path)['id:fiction9']['status'], 'closed')

    def test_existing_record_closed_keeps_fields(self):
        it = self.item(1, text=self.REJECT)
        rec = conv_store.new_record(it['conv_key'], 'reply', 'j', '虚构公司1', '虚构HR1', '虚构岗位', self.ts - 99)
        rec['status'] = 'engaged'
        rec['asked_round1_ts'] = self.ts - 50
        conv_store.save(self.path, {it['conv_key']: rec})
        self.hop([it])
        saved = conv_store.load(self.path)[it['conv_key']]
        self.assertEqual(saved['status'], 'closed')
        self.assertEqual(saved['title'], '虚构岗位')
        self.assertEqual(saved['asked_round1_ts'], self.ts - 50)

    def test_dry_counts_without_writing(self):
        result, _, _, _, _ = self.hop([self.item(1, text=self.REJECT)], dry=True)
        self.assertEqual(result['out_of_view_rejected'], 1)
        self.assertFalse(self.path.exists())


class RejectPatternTests(unittest.TestCase):
    def test_new_template_rejections(self):
        for text in ('感谢您的投递，但是很抱歉我们正在寻找其他背景的候选人。',
                     '很抱歉，我们在找别的方向的人选',
                     '抱歉，目前在物色其他候选人'):
            with self.subTest(text=text):
                self.assertEqual(conv_rules.classify(text), 'reject')

    def test_not_rejections(self):
        for text in ('我们正在寻找其他城市的候选人，你能去杭州吗？',
                     '抱歉回复晚了，方便发一份简历吗',
                     '不好意思打扰了，我们在寻找优秀的BD'):
            with self.subTest(text=text):
                self.assertNotEqual(conv_rules.classify(text), 'reject')


if __name__ == '__main__':
    unittest.main()
