"""Offline conv-03u acceptance using fictional captures and browser substitutes."""
import tempfile
import unittest
from itertools import repeat
from pathlib import Path

import test_conversation_run as fixtures

conv_store = fixtures.conv_store


class OutOfViewTests(unittest.TestCase):
    module = fixtures.ConversationRunTests.module
    item = fixtures.ConversationRunTests.item
    record = fixtures.ConversationRunTests.record
    history = fixtures.ConversationRunTests.history
    run_hop = fixtures.ConversationRunTests.run_hop

    def setUp(self):
        # Reuse the offline boundaries, but keep even temporary data in this repo.
        with fixtures.patch.object(tempfile, 'tempdir', str(Path(__file__).parent)):
            fixtures.ConversationRunTests.setUp(self)

    def outside_hop(self, items):
        self.br.new_tab.side_effect = [10]
        return self.run_hop(items=items, row_snapshots=repeat([]))[0]

    def notices(self, result):
        return [line for line in result['notifications'] if line.startswith('📭')]

    def test_outside_items_do_not_consume_per_hop(self):
        self.cfg['conversation']['per_hop'] = 2
        items = [self.item(n, age=n) for n in range(1, 5)]
        rows = [{'conv_key': 'id:fiction%d' % n} for n in (3, 4, 5)]
        result, opened, _, _, _ = self.run_hop(
            items=items, row_snapshots=[rows, rows, rows], dry=True)
        self.assertEqual(result['processed'], 2)
        self.assertEqual([c.kwargs['conv_key'] for c in opened.call_args_list],
                         ['id:fiction3', 'id:fiction4'])
        self.assertEqual(result.get('out_of_view'), 2)
        self.assertEqual(result['row_missing'], 0)

    def test_system_snapshots_only_count(self):
        for text in ('您的附件简历 某某 已发送给Boss点击查看附件',
                     '你与该职位竞争者PK情况', '虚构自定义回执'):
            with self.subTest(text=text):
                self.cfg['conversation']['resume_sent_pattern'] = (
                    '附件简历.*已发送|虚构自定义回执')
                result = self.outside_hop([self.item(text=text)])
                self.assertEqual(result.get('out_of_view'), 1)
                self.assertEqual(self.notices(result), [])
                self.assertFalse(self.path.exists())

    def test_hr_snapshot_creates_nonterminal_record_and_exact_notice(self):
        text = '虚构岗位邀请您了解工作内容以及团队情况，请问近期是否方便进一步交流呢'
        result = self.outside_hop([self.item(text=text)])
        self.assertEqual(self.notices(result), [
            '📭 列表 40 行外有未处理消息 虚构公司1｜虚构HR1：' + text[:30] + '（需你手动看）'])
        record = conv_store.load(self.path)['id:fiction1']
        self.assertEqual(record['status'], 'new')
        self.assertEqual(record['out_of_view_snap'], {'ts': self.ts-10, 'text': text})
        self.assertEqual(record['last_seen'], {'ts': None, 'text': ''})
        self.assertEqual(record['actions'], [])
        self.assertIsNone(record['resume_sent_ts'])

    def test_same_snapshot_not_repeated_but_changed_snapshot_notified(self):
        item = self.item()
        self.assertEqual(len(self.notices(self.outside_hop([item]))), 1)
        self.assertEqual(self.notices(self.outside_hop([item])), [])
        changed = self.item(text='虚构HR补充了岗位说明', age=5)
        self.assertEqual(len(self.notices(self.outside_hop([changed]))), 1)
        self.assertEqual(conv_store.load(self.path)['id:fiction1']['out_of_view_snap'],
                         {'ts': self.ts-5, 'text': '虚构HR补充了岗位说明'})

    def test_manual_terminal_not_notified(self):
        record = self.record(status='manual')
        conv_store.save(self.path, {record['conv_key']: record})
        self.assertEqual(self.notices(self.outside_hop([self.item()])), [])
        self.assertEqual(conv_store.load(self.path)[record['conv_key']], record)

    def test_at_most_three_newest_notices_and_deferred_items_retry(self):
        items = [self.item(n, age=n) for n in (5, 2, 4, 1, 3)]
        first = self.outside_hop(items)
        self.assertEqual(first.get('out_of_view'), 5)
        self.assertEqual(len(self.notices(first)), 3)
        for notice, n in zip(self.notices(first), (1, 2, 3)):
            self.assertIn('虚构公司%d｜虚构HR%d' % (n, n), notice)
        self.assertEqual(set(conv_store.load(self.path)),
                         {'id:fiction1', 'id:fiction2', 'id:fiction3'})
        self.assertEqual(len(self.notices(self.outside_hop(items))), 2)

    def test_overview_preserves_zero_wording_and_adds_nonzero_count(self):
        expected = ('💬 对话｜列表 1（抓包 12）｜候选 0｜处理 0｜发简历 0｜'
                    '问第一轮 0｜问第二轮 0｜问齐 0｜需人工 0｜'
                    '不过筛 0｜回绝 0｜row_missing 0｜今日 0/20')
        zero = self.outside_hop([self.item(mine=True)])
        self.assertEqual(zero.get('out_of_view'), 0)
        self.assertEqual(zero['overview'], expected)
        outside = self.outside_hop([self.item()])
        self.assertEqual(outside['overview'], expected.replace(
            'row_missing 0', 'row_missing 0｜40行外 1'))

    def test_row_disappearing_on_refresh_still_counts_missing(self):
        result, opened, calls, _, _ = self.run_hop(
            row_snapshots=[[{'conv_key': 'id:fiction1'}], []])
        self.assertEqual(result['row_missing'], 1)
        self.assertEqual(result.get('out_of_view'), 0)
        opened.assert_not_called()
        self.assertFalse(calls)
        self.assertFalse(self.path.exists())
