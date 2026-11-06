"""conv-08：对话模块遵守 filters.brand_block。虚构公司与临时目录，不连浏览器。"""
import json
import unittest
from unittest.mock import patch
import test_conversation_run as T
from conv_store import load, new_record


class BrandBlockTests(unittest.TestCase):
    setUp = T.ConversationRunTests.setUp
    module = T.ConversationRunTests.module
    item = T.ConversationRunTests.item
    history = T.ConversationRunTests.history
    run_hop = T.ConversationRunTests.run_hop

    def blocked_item(self, n=1, text='方便发一份你的简历过来吗？', age=10):
        it = self.item(n, text=text, age=age)
        it['brandName'] = '示例云图%d' % n
        return it

    def block(self, patterns=('云图',)):
        self.cfg['filters'] = {'brand_block': list(patterns)}

    def pushes(self):
        return [line for line in self.out if line.startswith('🔒')]

    def test_visible_blocked_not_opened_and_marked_manual(self):
        self.block()
        it = self.blocked_item()
        result, opened, calls, send_text, _ = self.run_hop(items=[it])
        opened.assert_not_called()
        send_text.assert_not_called()
        self.assertFalse(calls)
        self.assertEqual(result['brand_blocked'], 1)
        self.assertEqual(result['processed'], 0)
        self.assertEqual(result['candidates'], 0)
        row = load(self.path)[it['conv_key']]
        self.assertEqual(row['status'], 'manual')
        self.assertTrue(row['notified']['manual'])
        self.assertEqual(row['actions'][-1]['type'], 'brand_block')
        self.assertEqual(self.pushes(), [
            '🔒 屏蔽公司 示例云图1｜虚构HR1：方便发一份你的简历过来吗？（不自动处理，需你手动看）'])
        self.assertTrue(any('屏蔽 1' in line for line in self.out if line.startswith('- 💬') or '💬' in line))

    def test_out_of_view_blocked_not_revealed(self):
        self.block()
        it = self.blocked_item()
        c = self.module()
        with patch.object(c.chat_dom, 'reveal_conversation') as reveal, \
             patch.object(c.chat_dom, 'reset_list_scroll') as reset:
            result, opened, calls, _, _ = self.run_hop(items=[it], row_snapshots=lambda *a: [])
        reveal.assert_not_called()
        reset.assert_not_called()
        opened.assert_not_called()
        self.assertFalse(any('📭' in line for line in self.out))
        self.assertEqual(len(self.pushes()), 1)
        self.assertEqual(result['out_of_view'], 0)
        self.assertEqual(result['brand_blocked'], 1)

    def test_second_hop_new_message_not_pushed_again(self):
        self.block()
        self.run_hop(items=[self.blocked_item()])
        self.out.clear()
        self.br.new_tab.side_effect = [20, 21]
        result, opened, _, _, _ = self.run_hop(items=[self.blocked_item(text='在吗？', age=5)])
        opened.assert_not_called()
        self.assertEqual(self.pushes(), [])
        self.assertEqual(result['brand_blocked'], 0)

    def test_existing_engaged_record_becomes_manual_keeping_fields(self):
        self.block()
        it = self.blocked_item()
        row = new_record(it['conv_key'], 'reply', it['encryptJobId'], it['brandName'],
                         it['name'], '虚构岗位', self.ts - 999)
        row['status'] = 'engaged'
        row['asked_round1_ts'] = self.ts - 500
        self.path.write_text(json.dumps({it['conv_key']: row}, ensure_ascii=False))
        result, opened, _, _, _ = self.run_hop(items=[it])
        opened.assert_not_called()
        saved = load(self.path)[it['conv_key']]
        self.assertEqual(saved['status'], 'manual')
        self.assertEqual(saved['origin'], 'reply')
        self.assertEqual(saved['title'], '虚构岗位')
        self.assertEqual(saved['asked_round1_ts'], self.ts - 500)
        self.assertEqual(len(self.pushes()), 1)

    def test_already_notified_record_not_pushed_again(self):
        self.block()
        it = self.blocked_item()
        row = new_record(it['conv_key'], 'inbound', it['encryptJobId'], it['brandName'],
                         it['name'], '', self.ts - 999)
        row['notified']['manual'] = True
        self.path.write_text(json.dumps({it['conv_key']: row}, ensure_ascii=False))
        result, opened, _, _, _ = self.run_hop(items=[it])
        opened.assert_not_called()
        self.assertEqual(self.pushes(), [])
        self.assertEqual(result['brand_blocked'], 1)
        self.assertEqual(load(self.path)[it['conv_key']]['status'], 'manual')

    def test_falls_back_to_record_brand_when_item_brand_empty(self):
        self.block()
        it = self.item()
        it['brandName'] = ''
        row = new_record(it['conv_key'], 'reply', it['encryptJobId'], '示例云图',
                         it['name'], '', self.ts - 999)
        self.path.write_text(json.dumps({it['conv_key']: row}, ensure_ascii=False))
        result, opened, _, _, _ = self.run_hop(items=[it])
        opened.assert_not_called()
        self.assertEqual(result['brand_blocked'], 1)

    def test_empty_or_missing_block_behaves_as_before(self):
        for cfg_filters in (None, {}, {'brand_block': []}, {'brand_block': ['']}):
            with self.subTest(filters=cfg_filters):
                self.setUp()
                if cfg_filters is not None:
                    self.cfg['filters'] = cfg_filters
                result, opened, _, _, _ = self.run_hop(items=[self.blocked_item()])
                opened.assert_called_once()
                self.assertEqual(result['processed'], 1)
                self.assertEqual(result['brand_blocked'], 0)
                self.assertEqual(self.pushes(), [])
                self.assertFalse(any('屏蔽' in line for line in self.out))

    def test_other_conversations_still_processed_and_caps_unaffected(self):
        self.block()
        self.cfg['conversation']['per_hop'] = 1
        items = [self.blocked_item(1, age=1), self.item(2, age=5)]
        result, opened, _, _, _ = self.run_hop(items=items)
        self.assertEqual([call.kwargs['conv_key'] for call in opened.call_args_list], ['id:fiction2'])
        self.assertEqual(result['processed'], 1)
        self.assertEqual(result['brand_blocked'], 1)

    def test_daily_cap_reached_still_blocks(self):
        self.block()
        self.cfg['conversation']['daily_cap'] = 0
        result, opened, _, _, _ = self.run_hop(items=[self.blocked_item()])
        opened.assert_not_called()
        self.assertEqual(result['brand_blocked'], 1)
        self.assertEqual(load(self.path)[self.blocked_item()['conv_key']]['status'], 'manual')

    def test_dry_reports_without_writing(self):
        self.block()
        result, opened, calls, _, _ = self.run_hop(items=[self.blocked_item()], dry=True)
        opened.assert_not_called()
        self.assertFalse(calls)
        self.assertEqual(result['brand_blocked'], 1)
        self.assertEqual(len(self.pushes()), 1)
        self.assertFalse(self.path.exists())


if __name__ == '__main__':
    unittest.main()
