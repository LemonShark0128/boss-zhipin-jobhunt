"""conv-12：开场白/承接句/答句/闸门拒绝自我否定；「揭示后处理」按初始 40 行外计。虚构数据。"""
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
sys.path.insert(0, str(ROOT / 'tests'))
import message_policy as P
import conv_llm as L
import conv_rules as R
import conv_actions as A
import chat_dom as CD
import test_conversation_run as T

NEGATIVE = ['我5年示例行业BD团队管理经验，区域GMV达成率90%+，但无员工关系专项经验。',
            '没有带过团队的经验', '缺乏ToB背景', '暂无相关资源', '不具备虚构行业能力',
            '对虚构行业不太熟悉相关业务基础', '尚未接触过相关经验']
POSITIVE = ['现任虚构公司BDM，累计新签优质商户200+家', '5年示例行业BD经验', '无责底薪加提成',
            '无底薪也能接受吗那就算了', '我做过虚构行业的客户维护。', '无论渠道还是KA都做过']


class SelfNegationTests(unittest.TestCase):
    def test_detector(self):
        for text in NEGATIVE:
            with self.subTest(text=text):
                self.assertTrue(P.self_negation(text))
        for text in POSITIVE:
            with self.subTest(text=text):
                self.assertFalse(P.self_negation(text))
        self.assertFalse(P.self_negation(None))


class OpenerTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.cfg = {'paths': {'workdir': self.tmp.name},
                    'user': {'greeting': {'facts': ['我做过虚构行业的客户维护。']}}}
        self.job = {'encryptJobId': 'fictional-job', 'jobName': '虚构客户经理'}

    def test_safe_opener_rejects_negation(self):
        self.assertFalse(L._safe_opener('我做过虚构行业的客户维护，但无相关专项经验。', self.cfg, self.job))
        self.assertTrue(L._safe_opener('我做过虚构行业的客户维护。', self.cfg, self.job))

    def test_cached_negative_opener_regenerated(self):
        key = json.dumps(['fictional-job', __import__('hashlib').sha256('虚构JD'.encode()).hexdigest()],
                         ensure_ascii=False)
        (Path(self.tmp.name) / 'conv_opener_cache.json').write_text(
            json.dumps({key: '我做过虚构行业的客户维护，但无相关专项经验。'}, ensure_ascii=False))
        with patch.object(L.G, '_generate_raw', return_value=('我做过虚构行业的客户维护。', '')) as model:
            self.assertEqual(L.opener(self.cfg, self.job, '虚构JD'), '我做过虚构行业的客户维护。')
        model.assert_called_once()

    def test_two_negative_generations_return_empty(self):
        with patch.object(L.G, '_generate_raw', return_value=('缺乏虚构行业背景。', '')) as model:
            self.assertEqual(L.opener(self.cfg, self.job, '虚构JD'), '')
        self.assertEqual(model.call_count, 2)


class RulesGateTests(unittest.TestCase):
    def test_safe_lead_rejects_negation(self):
        self.assertFalse(R.safe_lead('了解，没有相关背景，', {}))
        self.assertTrue(R.safe_lead('了解，', {}))

    def test_safe_answer_rejects_negation_even_if_in_facts(self):
        cfg = {'user': {'greeting': {'facts': ['做过五年示例行业BD', '无相关专项经验']}}}
        self.assertTrue(R.safe_answer('做过五年示例行业BD。', cfg))
        self.assertFalse(R.safe_answer('无相关专项经验。', cfg))


class SendTextGateTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.cfg = {'paths': {'workdir': self.tmp.name, 'state': self.tmp.name + '/state.json'},
                    'user': {'greeting': {'facts': ['我做过虚构行业的客户维护。']}}, 'conversation': {},
                    'schedule': {'timezone': 'UTC'}, 'sending': {'min_gap_seconds': 0}}
        self.before = {'conv_key': 'id:fiction', 'messages': [{'id': 'f1', 'who': 'friend', 'text': '你好'}]}

    def test_negative_opener_part_manual_required_without_typing(self):
        template = R.DEFAULT_TEMPLATES['round1']
        opener = '我做过客户维护，但缺乏虚构行业经验。'
        with patch.object(CD, 'active_conversation', return_value=self.before), \
             patch.object(A.CS, 'type_into_chat') as fill:
            result = A.send_text(Mock(), 1, self.cfg, 'id:fiction', {}, opener + template, template, opener)
        self.assertEqual(result, 'manual_required')
        fill.assert_not_called()


class RevealProcessedCountTests(unittest.TestCase):
    setUp = T.ConversationRunTests.setUp
    module = T.ConversationRunTests.module
    item = T.ConversationRunTests.item
    history = T.ConversationRunTests.history
    run_hop = T.ConversationRunTests.run_hop

    def test_neighbours_brought_in_by_one_reveal_count_as_processed(self):
        c = self.module()
        visible_item = self.item(1, age=1)
        far_a, far_b = self.item(2, age=50), self.item(3, age=40)
        rows = [{'conv_key': visible_item['conv_key'], 'receipt': False}]
        def reveal(br, tab, item):
            # 一次揭示把相邻两个老会话都带进视窗
            rows[:] = [{'conv_key': far_a['conv_key'], 'receipt': False},
                       {'conv_key': far_b['conv_key'], 'receipt': False},
                       {'conv_key': visible_item['conv_key'], 'receipt': False}]
            return {'ok': True, 'index': 60}
        with patch.object(c.chat_dom, 'wait_rows_ready', return_value=None), \
             patch.object(c.chat_dom, 'reveal_conversation', side_effect=reveal) as rev, \
             patch.object(c.chat_dom, 'reset_list_scroll'):
            result, opened, _, _, _ = self.run_hop(items=[visible_item, far_a, far_b],
                                                   row_snapshots=lambda *a: list(rows))
        self.assertEqual(rev.call_count, 1)
        self.assertEqual(result['processed'], 3)
        self.assertEqual(result['revealed'], 1)
        self.assertEqual(result['revealed_processed'], 2)
        self.assertIn('💬 滚动定位｜revealed 1｜揭示后处理 2｜reveal_failed 0', self.out)


if __name__ == '__main__':
    unittest.main()
