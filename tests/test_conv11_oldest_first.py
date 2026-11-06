"""conv-11: fictional conversations, offline boundaries, cleaned system temp storage."""
import unittest
from unittest.mock import patch
import test_conversation_run as T


class OldestFirstTests(unittest.TestCase):
    setUp = T.ConversationRunTests.setUp
    module = T.ConversationRunTests.module
    item = T.ConversationRunTests.item
    history = T.ConversationRunTests.history
    run_hop = T.ConversationRunTests.run_hop

    def hop(self, items, visible, failures=(), receipts=(), displace=False, dry=False,
            history=None):
        c = self.module()
        rows = [{'conv_key': it['conv_key'], 'receipt': False} for it in visible]

        def reveal(br, tab, item):
            key = item['conv_key']
            if key in failures:
                return {'ok': False, 'reason': 'not_found'}
            if displace:
                rows.clear()
            rows.append({'conv_key': key, 'receipt': key in receipts})
            return {'ok': True, 'index': 70}

        self.br.new_tab.side_effect = range(10, 40)
        with patch.object(c.chat_dom, 'wait_rows_ready', return_value=None), \
             patch.object(c.chat_dom, 'reveal_conversation', side_effect=reveal) as rev, \
             patch.object(c.chat_dom, 'reset_list_scroll') as reset:
            result, opened, calls, _, _ = self.run_hop(
                items=items, row_snapshots=lambda *a: list(rows), dry=dry, history=history)
        return result, [call.kwargs['conv_key'] for call in opened.call_args_list], \
            [call.args[2]['conv_key'] for call in rev.call_args_list], reset, calls

    def test_mixed_ten_only_reveals_oldest_four_and_counts_backlog(self):
        items = [self.item(i, age=i) for i in range(1, 11)]
        self.cfg['conversation']['per_hop'] = 4
        result, opened, revealed, reset, _ = self.hop(items, [items[0], items[4], items[8]])
        self.assertEqual(opened, ['id:fiction10', 'id:fiction9', 'id:fiction8', 'id:fiction7'])
        self.assertEqual(revealed, ['id:fiction10', 'id:fiction8', 'id:fiction7'])
        self.assertEqual({k: result.get(k) for k in (
            'candidates', 'out_of_view', 'processed', 'revealed', 'revealed_processed', 'reveal_failed')},
            dict(candidates=10, out_of_view=7, processed=4, revealed=3,
                 revealed_processed=3, reveal_failed=0))
        self.assertIn('💬 滚动定位｜revealed 3｜揭示后处理 3｜reveal_failed 0', self.out)
        reset.assert_called_once_with(self.br, 10)

    def test_failed_oldest_fills_slots_and_notifies_once_across_hops(self):
        items = [self.item(i, age=i) for i in range(1, 5)]
        self.cfg['conversation']['per_hop'] = 2
        result, opened, revealed, _, _ = self.hop(items, [items[0]], failures=['id:fiction4'])
        self.assertEqual(opened, ['id:fiction3', 'id:fiction2'])
        self.assertEqual(revealed, ['id:fiction4', 'id:fiction3', 'id:fiction2'])
        self.assertEqual(result['reveal_failed'], 1)
        self.assertEqual(result['out_of_view'], 3)
        self.assertEqual(result['reveal_reasons'], {'not_found': 1})
        self.assertIn('💬 滚动定位｜revealed 2｜揭示后处理 2｜reveal_failed 1（not_found 1）', self.out)
        self.hop(items, [items[0]], failures=['id:fiction4'])
        self.assertEqual(sum('📭' in line for line in self.out), 1)

    def test_revealed_receipt_does_not_take_slot(self):
        items = [self.item(i, age=i) for i in range(1, 4)]
        self.cfg['conversation']['per_hop'] = 1
        result, opened, revealed, _, _ = self.hop(items, [items[0]], receipts=['id:fiction3'])
        self.assertEqual(opened, ['id:fiction2'])
        self.assertEqual(revealed, ['id:fiction3', 'id:fiction2'])
        self.assertEqual(result['revealed'], 2)
        self.assertEqual(result.get('revealed_processed'), 1)
        self.assertEqual(result['candidates'], 3)

    def test_displaced_initial_row_is_revealed_when_its_turn_arrives(self):
        items = [self.item(1, age=1), self.item(2, age=2)]
        result, opened, revealed, _, _ = self.hop(items, [items[0]], displace=True)
        self.assertEqual(opened, ['id:fiction2', 'id:fiction1'])
        self.assertEqual(revealed, ['id:fiction2', 'id:fiction1'])
        self.assertEqual(result['out_of_view'], 1)
        self.assertEqual(result.get('revealed_processed'), 2)

    def test_daily_cap_stops_reveals_live_and_dry(self):
        for dry in (False, True):
            for cap in (0, 1):
                with self.subTest(dry=dry, cap=cap):
                    self.setUp()
                    self.cfg['conversation']['daily_cap'] = cap
                    items = [self.item(1, age=1), self.item(2, age=2)]
                    result, opened, revealed, reset, calls = self.hop(items, [], dry=dry)
                    expected = ['id:fiction2'] if cap else []
                    self.assertEqual(opened, expected)
                    self.assertEqual(revealed, expected)
                    self.assertEqual(result['processed'], cap)
                    self.assertEqual(reset.call_count, cap)
                    if dry:
                        self.assertFalse(calls)
                        self.assertEqual(list(self.path.parent.iterdir()), [])

    def test_successful_reveal_without_history_counts_but_uses_slot(self):
        items = [self.item(1, age=1), self.item(2, age=2)]
        self.cfg['conversation']['per_hop'] = 1
        result, opened, revealed, _, _ = self.hop(items, [], history=[])
        self.assertEqual(opened, ['id:fiction2'])
        self.assertEqual(revealed, ['id:fiction2'])
        self.assertEqual(result['revealed'], 1)
        self.assertEqual(result.get('revealed_processed'), 0)
        self.assertEqual(result['processed'], 0)

    def test_all_visible_stable_ties_no_reveal_or_reset(self):
        items = [self.item(3, age=5), self.item(1, age=5), self.item(2, age=1)]
        result, opened, revealed, reset, _ = self.hop(items, items)
        self.assertEqual(opened, ['id:fiction3', 'id:fiction1', 'id:fiction2'])
        self.assertEqual(revealed, [])
        reset.assert_not_called()
        self.assertEqual(result['out_of_view'], 0)
