"""Offline regressions for round-two P0 A/B/C."""
from pathlib import Path
import shutil
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
import chat_dom as CD


class Round2Tests(unittest.TestCase):
    def setUp(self):
        self.root = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.root)
        self.cfg = {'paths': {'workdir': str(self.root)}, 'user': {}}

    def test_substring_company_is_unknown(self):
        for fields in ({}, {'brand': '甲科技供应链', 'boss': '王HR'}):
            rows = [dict(conv_key='id:wrong', text='甲科技供应链 王HR 城市经理', **fields)]
            with self.assertRaisesRegex(ValueError, 'unknown_conversation'):
                CD.select_conversation(rows, '甲科技', '王HR')

    def test_exact_company_hr_pair_and_ambiguity(self):
        rows = [{'conv_key': 'id:a', 'text': '甲科技 王HR 城市经理', 'brand': '甲科技', 'boss': '王HR'},
                {'conv_key': 'id:b', 'text': '乙科技 王HR 城市经理', 'brand': '乙科技', 'boss': '王HR'}]
        self.assertEqual(CD.select_conversation(rows, '甲科技 / 王HR')['conv_key'], 'id:a')
        self.assertEqual(CD.select_conversation(rows, ' 甲 科技 / 王ＨＲ ')['conv_key'], 'id:a')
        with self.assertRaisesRegex(ValueError, 'ambiguous.*甲科技.*甲科技'):
            CD.select_conversation([rows[0], dict(rows[0], conv_key='id:c')], '甲科技 / 王HR')

    def test_fallback_requires_whole_tokens(self):
        rows = [{'conv_key': 'id:a', 'text': '甲科技 王HR 城市经理'}]
        self.assertEqual(CD.select_conversation(rows, '甲科技 / 王HR')['conv_key'], 'id:a')
        with self.assertRaisesRegex(ValueError, 'unknown_conversation'):
            CD.select_conversation(rows, '甲科')
