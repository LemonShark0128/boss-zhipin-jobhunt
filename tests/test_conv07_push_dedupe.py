"""conv-07：同一会话同一跳不同时推 🙋 和 📎；推送去掉卡片按钮字。临时目录、虚构数据。"""
import unittest
import test_conversation_run as T

CARD = '我想要一份您的附件简历，期望薪资多少\n拒绝\n同意\n\n或者发送\n\n电话号码\n微信号码'


class PushTextTests(unittest.TestCase):
    def test_strips_only_standalone_button_lines(self):
        import conversation as c
        self.assertEqual(c._push_text(CARD), '我想要一份您的附件简历，期望薪资多少')
        self.assertEqual(c._push_text('我同意线上面试\n同意'), '我同意线上面试')
        self.assertEqual(c._push_text('拒绝'), '')
        self.assertEqual(c._push_text(None), '')
        self.assertEqual(c._push_text('  第一行 \n\n 第二行 '), '第一行 第二行')


class PushDedupeTests(unittest.TestCase):
    setUp = T.ConversationRunTests.setUp
    module = T.ConversationRunTests.module
    item = T.ConversationRunTests.item
    history = T.ConversationRunTests.history
    run_hop = T.ConversationRunTests.run_hop
    saved = T.ConversationRunTests.saved

    def lines(self, result):
        return [n for n in result['notifications'] if '虚构公司1' in n]

    def test_unqualified_resume_manual_pushes_single_paperclip(self):
        result, _, calls, _, _ = self.run_hop(items=[self.item(text=CARD)],
            history=self.history(inbound=True, text=CARD), job_ok=False)
        lines = self.lines(result)
        self.assertEqual(len(lines), 1, lines)
        self.assertTrue(lines[0].startswith('📎 不过筛但要简历 '))
        self.assertTrue(lines[0].endswith('：我想要一份您的附件简历，期望薪资多少'))
        for word in ('拒绝', '电话号码', '微信号码', '或者发送'):
            self.assertNotIn(word, lines[0])
        row = self.saved()
        self.assertEqual(row['status'], 'manual')
        self.assertTrue(row['notified']['manual'])
        self.assertTrue(row['resume_asked'])
        self.assertEqual(result['manual'], 1)
        self.assertEqual(result['ignored_resume_asked'], 1)
        self.assertFalse(any(c[0] == 'resume' for c in calls))

    def test_qualified_manual_strips_buttons(self):
        result, _, _, _, _ = self.run_hop(items=[self.item(text=CARD)],
            history=self.history(inbound=True, text=CARD), job_ok=True)
        lines = self.lines(result)
        self.assertEqual(len(lines), 1, lines)
        self.assertTrue(lines[0].startswith('🙋 需人工 '))
        self.assertTrue(lines[0].endswith('：我想要一份您的附件简历，期望薪资多少'))
        self.assertNotIn('微信号码', lines[0])

    def test_unqualified_manual_without_resume_still_pushes_manual(self):
        text = '期望薪资多少'
        result, _, _, _, _ = self.run_hop(items=[self.item(text=text)],
            history=self.history(inbound=True, text=text), job_ok=False)
        lines = self.lines(result)
        self.assertEqual(len(lines), 1, lines)
        self.assertTrue(lines[0].startswith('🙋 需人工 '))
        self.assertEqual(result['ignored_resume_asked'], 0)

    def test_already_flagged_resume_asked_falls_back_to_manual_push(self):
        # 上一跳已推过 📎（resume_asked=True），本跳不会再推 📎 → 转人工必须照常推 🙋，不能两头都不推
        first = '发份简历看看'
        self.run_hop(items=[self.item(text=first, age=30)],
                     history=self.history(inbound=True, text=first), job_ok=False)
        self.assertTrue(self.saved()['resume_asked'])
        self.br.new_tab.side_effect = [10, 11]
        history = self.history(inbound=True, text=first) + [
            {'who': 'friend', 'id': 'm2', 'text': CARD, 'ts': self.ts - 5}]
        history[0]['ts'] = self.ts - 30
        self.out.clear()
        result, _, _, _, _ = self.run_hop(items=[self.item(text=CARD, age=5)], history=history, job_ok=False)
        lines = self.lines(result)
        self.assertEqual(len(lines), 1, lines)
        self.assertTrue(lines[0].startswith('🙋 需人工 '))


if __name__ == '__main__':
    unittest.main()
