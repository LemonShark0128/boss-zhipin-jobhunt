"""Offline stuck-new and notification regressions; all data is fictional."""
import unittest

import test_conversation_run as fixtures


class Conv03tTests(unittest.TestCase):
    setUp = fixtures.ConversationRunTests.setUp
    module = fixtures.ConversationRunTests.module
    item = fixtures.ConversationRunTests.item
    record = fixtures.ConversationRunTests.record
    history = fixtures.ConversationRunTests.history
    run_hop = fixtures.ConversationRunTests.run_hop
    saved = fixtures.ConversationRunTests.saved

    def seed(self, asked=True, cursor=None, resume_sent=False, job_ok=True):
        row = self.record()
        row.update(origin='inbound', job_ok=job_ok)
        if resume_sent:
            row['resume_sent_ts'] = self.ts - 50
        if asked:
            row['asked_round1_ts'] = self.ts - 100
        if cursor:
            row['last_msg_id'] = cursor
        fixtures.conv_store.save(self.path, {row['conv_key']: row})
        return row

    def test_stuck_new_extracts_answer_and_sends_round2(self):
        row = self.seed(resume_sent=True)
        text = '主要做商户拓展，底薪加提成'
        result, _, calls, _, _ = self.run_hop(
            items=[self.item(text=text)], history=self.history(text=text),
            extracted={'requirements': '商户拓展', 'salary_structure': '底薪加提成'})
        saved = self.saved()
        self.assertEqual(saved['status'], 'round2')
        self.assertEqual(saved['answers']['requirements'], '商户拓展')
        self.assertEqual(saved['answers']['salary_structure'], '底薪加提成')
        self.assertEqual(saved['asked_round1_ts'], row['asked_round1_ts'])
        self.assertEqual(result['manual'], 0)
        self.assertEqual(result['round2'], 1)
        self.assertEqual([c[0] for c in calls], ['text'])
        self.assertEqual(calls[0][1][5], '面试大概有几轮？可以安排线上面试吗？')
        self.assertIn(('status_repair', 'new_to_engaged'),
                      [(a['type'], a['result']) for a in saved['actions']])

    def test_pending_resume_keeps_new_and_retries_resume_without_question(self):
        row = self.seed()
        result, _, calls, _, _ = self.run_hop(resume_result='failed')
        saved = self.saved()
        self.assertEqual(saved['status'], 'new')
        self.assertIsNone(saved['resume_sent_ts'])
        self.assertEqual(saved['asked_round1_ts'], row['asked_round1_ts'])
        self.assertEqual([c[0] for c in calls], ['resume'])
        self.assertEqual(result['round1'], 0)
        self.assertEqual(result['manual'], 0)
        self.assertFalse(any(a['type'] == 'status_repair' for a in saved['actions']))

    def test_job_not_ok_repairs_without_resume(self):
        self.seed(job_ok=False)
        text = '主要做商户拓展，底薪加提成'
        result, _, calls, _, _ = self.run_hop(
            items=[self.item(text=text)], history=self.history(text=text),
            extracted={'requirements': '商户拓展', 'salary_structure': '底薪加提成'})
        self.assertEqual(self.saved()['status'], 'round2')
        self.assertIsNone(self.saved()['resume_sent_ts'])
        self.assertEqual(result['manual'], 0)
        self.assertEqual([c[0] for c in calls], ['text'])
        self.assertIn(('status_repair', 'new_to_engaged'),
                      [(a['type'], a['result']) for a in self.saved()['actions']])

    def test_unknown_question_notifies_last_hr_text_not_system_snapshot(self):
        self.seed()
        text = '您喜欢哪种虚构颜色？'
        snapshot = '您的附件简历 虚构 已发送给Boss'
        history = self.history(text=text)
        history.append({'who': 'unknown', 'classes': 'item-system',
                        'id': 'fiction-system', 'text': snapshot})
        result, _, calls, _, _ = self.run_hop(items=[self.item(text=snapshot)], history=history)
        self.assertEqual(self.saved()['status'], 'manual')
        self.assertEqual(result['manual'], 1)
        self.assertFalse(calls)
        line = '🙋 需人工 虚构公司1｜虚构经理｜虚构HR1：' + text
        self.assertEqual(result['notifications'], [line])
        self.assertIn(line, self.out)
        self.assertEqual(self.saved()['last_seen']['text'], snapshot)
        self.assertTrue(self.saved()['notified']['manual'])
        again, _, calls, _, _ = self.run_hop(items=[self.item(text=snapshot)], history=history)
        self.assertFalse(again['notifications'])
        self.assertFalse(calls)

    def test_cursor_lost_masks_system_snapshot_without_new_messages(self):
        for snapshot in ('您的附件简历 虚构 已发送给Boss', '你与该职位竞争者PK情况',
                         '你与该职位竞争者PK情况 共8人投递 查看详细分析', '虚构系统提示',
                         '虚构系统特征：完成', '虚构投递回执'):
            with self.subTest(snapshot=snapshot):
                self.seed(cursor='fiction-missing')
                self.cfg['conversation'].update(
                    system_texts=['你与该职位竞争者PK情况', '虚构系统提示'],
                    system_patterns=['你与该职位竞争者PK情况.*查看详细分析', '虚构系统特征'],
                    resume_sent_pattern='附件简历.*已发送|虚构投递回执')
                self.br.new_tab.side_effect = [10]
                result, _, calls, _, _ = self.run_hop(
                    items=[self.item(text=snapshot)], history=self.history(text='虚构旧回复'))
                self.assertFalse(calls)
                self.assertEqual(result['manual'], 1)
                line = '🙋 需人工 虚构公司1｜虚构经理｜虚构HR1：（系统消息）'
                self.assertEqual(result['notifications'], [line])
                self.assertIn(line, self.out)
                self.assertEqual(self.saved()['last_seen']['text'], snapshot)

    def test_never_asked_new_keeps_first_round_behavior(self):
        self.seed(asked=False)
        result, _, calls, _, _ = self.run_hop()
        self.assertEqual(result['round1'], 1)
        self.assertEqual(result['round2'], 0)
        self.assertEqual(result['manual'], 0)
        self.assertEqual([c[0] for c in calls], ['text', 'resume'])
        self.assertEqual(self.saved()['status'], 'engaged')
        self.assertFalse(any(a['type'] == 'status_repair' for a in self.saved()['actions']))
