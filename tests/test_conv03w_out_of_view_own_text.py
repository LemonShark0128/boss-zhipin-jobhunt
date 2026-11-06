"""Offline own-text snapshot regressions, using fictional temporary ledgers."""
import json
import unittest
from itertools import repeat
from pathlib import Path

import test_conversation_run as fixtures


class OutOfViewOwnTextTests(unittest.TestCase):
    setUp = fixtures.ConversationRunTests.setUp
    module = fixtures.ConversationRunTests.module
    item = fixtures.ConversationRunTests.item
    history = fixtures.ConversationRunTests.history
    run_hop = fixtures.ConversationRunTests.run_hop

    def ledger(self, *entries):
        path = Path(self.tmp.name) / 'send_ledger.jsonl'
        path.write_text(''.join(json.dumps(dict(
            event_id='fiction-event%d' % i, status='sent', cmd=cmd, text=text),
            ensure_ascii=False) + '\n' for i, (cmd, text) in enumerate(entries)),
            encoding='utf-8')
        return path

    def outside_hop(self, *texts, dry=False):
        self.br.new_tab.side_effect = [10]
        return self.run_hop(items=[self.item(i, text=text, mine=False)
                                   for i, text in enumerate(texts, 1)],
                            row_snapshots=repeat([]), dry=dry)[0]

    def notices(self, result):
        return [line for line in result['notifications'] if line.startswith('📭')]

    def assert_own(self, cmd, original, snapshot):
        ledger = self.ledger((cmd, original))
        before = ledger.read_bytes()
        result = self.outside_hop(snapshot)
        self.assertEqual(self.notices(result), [])
        self.assertEqual(result.get('out_of_view_own'), 1)
        self.assertFalse(self.path.exists())
        self.assertEqual(ledger.read_bytes(), before)

    def test_send_prefix_is_own_without_new_record(self):
        original = '看到你们在招虚构经理，这行我干了5年。在虚构公司做示例行业'
        self.assert_own('send', original, original[:25])

    def test_html_entity_prefix_is_own(self):
        self.assert_own('send', '虚构产品研发&运营团队经验丰富，方便交流吗',
                        '虚构产品研发&amp;运营团队经验丰富')

    def test_conversation_reply_prefix_is_own(self):
        self.assert_own('conv_text', '虚构语言考试七分，虚构城市有交流经历，欢迎了解',
                        '虚构语言考试七分，虚构城市有交流经历')

    def test_whitespace_and_nfkc_are_normalized(self):
        self.assert_own('send', '虚构ＡＢＣ\t团队\n运营经验丰富，欢迎了解',
                        '虚构ABC 团队\u3000运营经验丰富')

    def test_hr_text_is_not_own(self):
        self.ledger(('send', '虚构团队运营经验丰富，欢迎了解'))
        result = self.outside_hop('你好，我们虚构公司正在招聘商户拓展经理')
        self.assertEqual(len(self.notices(result)), 1)
        self.assertEqual(result.get('out_of_view_own'), 0)
        self.assertTrue(self.path.exists())

    def test_short_snapshot_is_not_matched(self):
        self.ledger(('conv_text', '好的，我这边有虚构团队的相关经历'))
        result = self.outside_hop('好的')
        self.assertEqual(len(self.notices(result)), 1)
        self.assertEqual(result.get('out_of_view_own'), 0)

    def test_resume_placeholder_is_excluded(self):
        self.ledger(('conv_resume', '<resume>'))
        result = self.outside_hop('<resume>')
        self.assertEqual(len(self.notices(result)), 1)
        self.assertEqual(result.get('out_of_view_own'), 0)

    def test_own_text_does_not_use_notification_budget(self):
        self.ledger(('send', '虚构团队运营经历欢迎了解更多'))
        result = self.outside_hop('虚构团队运营经历', '虚构HR第一条新消息',
                                  '虚构HR第二条新消息', '虚构HR第三条新消息')
        self.assertEqual(result.get('out_of_view_own'), 1)
        self.assertEqual(len(self.notices(result)), 3)
        self.assertNotIn('id:fiction1', fixtures.conv_store.load(self.path))

    def test_dry_does_not_write_files(self):
        self.ledger(('send', '虚构团队运营经历欢迎了解更多'))
        before = {p.name: p.read_bytes() for p in Path(self.tmp.name).iterdir()}
        result = self.outside_hop('虚构团队运营经历', '虚构HR新的岗位邀请', dry=True)
        self.assertEqual(result.get('out_of_view_own'), 1)
        self.assertEqual(len(self.notices(result)), 1)
        self.assertEqual({p.name: p.read_bytes() for p in Path(self.tmp.name).iterdir()}, before)
