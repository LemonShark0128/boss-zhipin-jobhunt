"""conv-13c：推送原话里 BOSS 的 <copy> 等标记（含 HTML 转义形式）要去掉，保留里面的字。虚构数据。"""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
import test_conv13_pin as P

RAW = '虚构HR的微信号：<copy>fiction_wx01</copy>'
ESCAPED = '虚构HR的微信号：&lt;copy&gt;fiction_wx01&lt;/copy&gt;'


class PushMarkupTests(unittest.TestCase):
    def c(self):
        import conversation
        return conversation

    def test_raw_and_escaped_copy_tags_removed(self):
        for text in (RAW, ESCAPED):
            with self.subTest(text=text):
                self.assertEqual(self.c()._push_text(text), '虚构HR的微信号：fiction_wx01')

    def test_pin_text_cleaned_before_cut(self):
        long = '虚构' * 15 + ESCAPED
        cleaned = self.c()._pin_text(long)
        self.assertNotIn('&lt;', cleaned)
        self.assertNotIn('copy', cleaned)
        self.assertEqual(len(cleaned), 40)

    def test_plain_angle_brackets_and_entities_kept_readable(self):
        self.assertEqual(self.c()._push_text('底薪 <5k 也行，提成 >10%'), '底薪 <5k 也行，提成 >10%')
        self.assertEqual(self.c()._push_text('A&amp;B 公司'), 'A&B 公司')


class StoredEscapedTextRenderedCleanTests(P.PinBase):
    def test_block_cleans_text_saved_by_older_version(self):
        row = self.pinrec(1, self.ts - 100)
        row['pin']['text'] = ESCAPED[:40]
        self.store(row)
        result, _ = self.pin_hop(items=[self.item(1)])
        block = [n for n in result['notifications'] if n.startswith('📌')][0]
        self.assertNotIn('&lt;', block)
        self.assertNotIn('copy', block)
        self.assertIn('fiction_wx01', block)

    def test_manual_line_cleaned(self):
        self.cfg['conversation']['pin_manual'] = False
        result, _ = self.pin_hop(items=[self.item(text=ESCAPED)], history=self.history(text=ESCAPED),
                                 text_result='manual_required')
        line = [n for n in result['notifications'] if n.startswith('🙋')][0]
        self.assertTrue(line.endswith('：虚构HR的微信号：fiction_wx01'), line)


if __name__ == '__main__':
    unittest.main()
