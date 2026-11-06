"""conv-10：减少需人工。虚构公司与人物；离线状态机 + 小结计数。"""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
sys.path.insert(0, str(Path(__file__).resolve().parent))
import conv_rules as R
import conv_store as S

ROUND1 = R.DEFAULT_TEMPLATES['round1']
ROUND2 = R.DEFAULT_TEMPLATES['round2']
TAIL = R.DEFAULT_TEMPLATES['experience_tail']
CFG = {'targets': [{'name': '上海', 'city': '上海'}, {'name': '杭州', 'city': '杭州'}]}


def record(origin='reply', status='new', title='测试岗位'):
    row = S.new_record('fiction:1', origin, 'fiction-job', '虚构星港', '虚构小岚', title, 1)
    row['status'] = status
    if status in ('engaged', 'round2'):
        row['resume_sent_ts'] = 10
        row['asked_round1_ts'] = 10
    if status == 'round2':
        row['asked_round2_ts'] = 20
    return row


def ctx(**kw):
    c = {'cfg': CFG, 'now_ts': 30, 'job_ok': True, 'extract': lambda m: {},
         'opener': lambda: '对这个岗位挺感兴趣，', 'history': [], 'job_city': ''}
    c.update(kw)
    return c


def step(row, text, **kw):
    return R.decide(row, [{'text': text, 'ts': 25}], ctx(**kw))


class ClassifyTests(unittest.TestCase):
    def test_labels(self):
        samples = {
            'greet': ['请问还在看机会吗？', '请问在考虑新的工作机会吗？',
                      '看了你的简历，觉得你的工作经验和我们需求很吻合，有兴趣进一步沟通一下吗？',
                      '你好，对你之前的经历非常感兴趣，我们这边正在招聘虚构代表',
                      '刚看到你在求职地是上海，我们是南京呢'],
            'manual': ['我觉得你很适合我们的岗位，你可以直接跟我互换电话联系我',
                       '换个微信聊吧', '在职吗？期望薪资多少'],
            'ack': ['好的', '收到', '好的，谢谢', '收到，谢谢。我这边同步给用人经理，三个工作日内给您反馈面试安排。'],
            'employment': ['你现在是在职状态吗', '您目前是离职状态吗', '在职还是离职？', '是否在职'],
            'location': ['您是否接受此工作地点?', '岗位需要接受调动您看能接受吗？', '工作地点在浦东可以吗'],
            'resume': ['你好，看了你的简历，适合这个岗位，方便发一份简历过来', '好的，那您先发份简历'],
            'experience': ['请问有相关工作经验吗？可以行业专业岗位开始吗？'],
            'other': ['[捂脸]那给不到', '你在上海哪个区'],
        }
        for label, texts in samples.items():
            for text in texts:
                with self.subTest(text=text):
                    self.assertEqual(R.classify(text), label)


class DecideTests(unittest.TestCase):
    def test_cold_outreach_new_asks_round1_not_manual(self):
        actions, row = step(record('inbound'), '请问在考虑新的工作机会吗？')
        self.assertNotEqual(row['status'], 'manual')
        self.assertTrue(any(a['type'] == 'send_text' and a.get('template_part') == ROUND1 for a in actions))

    def test_ack_on_new_waits(self):
        for text in ('好的', '收到，谢谢。我这边同步给用人经理，三个工作日内给您反馈面试安排。'):
            with self.subTest(text=text):
                actions, row = step(record(), text)
                self.assertEqual(actions, [])
                self.assertEqual(row['status'], 'new')
                self.assertEqual(row['last_seen']['text'], text)

    def test_ack_in_round2_goes_to_extraction(self):
        seen = []
        actions, row = step(record(status='round2'), '好的',
                            extract=lambda m: seen.append(1) or {'online_interview': '可以'})
        self.assertEqual(seen, [1])
        self.assertEqual(row['answers']['online_interview'], '可以')

    def test_employment_answer_with_round1(self):
        actions, row = step(record('reply'), '你现在是在职状态吗')
        texts = [a for a in actions if a['type'] == 'send_text']
        self.assertEqual(texts, [{'type': 'send_text', 'text': R.EMPLOYMENT_ANSWER + ROUND1,
                                  'template_part': ROUND1, 'opener_part': R.EMPLOYMENT_ANSWER}])
        self.assertEqual(row['employment_answered_ts'], 30)
        self.assertEqual(row['asked_round1_ts'], 30)
        self.assertEqual(row['status'], 'engaged')
        self.assertIn({'type': 'send_resume'}, actions)

    def test_employment_follows_experience_round_choice(self):
        # 与经验答句同口径：第一轮没答齐重带第一轮；已问第二轮带第二轮；都答齐带收尾句
        actions, row = step(record(status='engaged'), '目前在职吗？')
        self.assertEqual(actions[0]['template_part'], ROUND1)
        actions, row = step(record(status='round2'), '目前在职吗？')
        self.assertEqual(actions[0]['template_part'], ROUND2)
        done = record(status='round2')
        done['answers'] = {k: '虚构' for k in done['answers']}
        actions, row = step(done, '目前在职吗？')
        self.assertEqual(actions[0]['template_part'], TAIL)

    def test_employment_second_time_manual(self):
        row = record(status='engaged')
        row['employment_answered_ts'] = 5
        actions, row = step(row, '你现在是在职状态吗')
        self.assertEqual(row['status'], 'manual')
        self.assertFalse(any(a['type'] == 'send_text' for a in actions))

    def test_location_job_city_in_targets(self):
        actions, row = step(record(status='engaged'), '您是否接受此工作地点?', job_city='上海')
        self.assertEqual(actions[0]['opener_part'], R.LOCATION_ANSWER)
        self.assertEqual(row['location_answered_ts'], 30)

    def test_location_job_city_outside_targets_manual(self):
        actions, row = step(record(status='engaged'), '您是否接受此工作地点?', job_city='南京')
        self.assertEqual(row['status'], 'manual')
        self.assertFalse(any(a['type'] == 'send_text' for a in actions))

    def test_location_city_from_title_only_when_single(self):
        _, row = step(record(status='engaged', title='BDM-杭州-虚构'), '您是否接受此工作地点?')
        self.assertNotEqual(row['status'], 'manual')
        _, row = step(record(status='engaged', title='上海/杭州 虚构经理'), '您是否接受此工作地点?')
        self.assertEqual(row['status'], 'manual')
        _, row = step(record(status='engaged', title='虚构经理'), '您是否接受此工作地点?')
        self.assertEqual(row['status'], 'manual')

    def test_transfer_or_travel_always_manual(self):
        for text in ('岗位需要接受调动您看能接受吗？', '能接受出差吗', '可以接受外派吗'):
            with self.subTest(text=text):
                _, row = step(record(status='engaged'), text, job_city='上海')
                self.assertEqual(row['status'], 'manual')

    def test_no_targets_location_manual(self):
        _, row = R.decide(record(status='engaged'), [{'text': '您是否接受此工作地点?', 'ts': 25}],
                          ctx(cfg={}, job_city='上海'))
        self.assertEqual(row['status'], 'manual')

    def test_contact_swap_is_manual(self):
        _, row = step(record('inbound'), '我觉得你很适合我们的岗位，你可以直接跟我互换电话联系我')
        self.assertEqual(row['status'], 'manual')

    def test_unqualified_inbound_city_statement_follows_normal_flow(self):
        actions, row = step(record('inbound'), '刚看到你在求职地是上海，我们是南京呢', job_ok=False)
        self.assertNotEqual(row['status'], 'manual')
        self.assertFalse(any(a['type'] == 'send_resume' for a in actions))


class SummaryCountTests(unittest.TestCase):
    def test_manual_count_only_recent(self):
        import test_conversation_run as T
        case = T.ConversationRunTests('run_hop')
        case.setUp()
        try:
            case.module()
            old = S.new_record('id:old', 'reply', '', '虚构甲', '虚构HR', '', case.ts - 4 * 86400)
            old['status'] = 'manual'
            new = S.new_record('id:new', 'reply', '', '虚构乙', '虚构HR', '', case.ts - 3600)
            new['status'] = 'manual'
            S.save(case.path, {'id:old': old, 'id:new': new})
            case.cfg['conversation']['hours'] = [case.now.hour]
            result, _, _, _, _ = case.run_hop()
            self.assertEqual(result['summary']['manual'], 1)
        finally:
            case.doCleanups()


if __name__ == '__main__':
    unittest.main()


class JobCityContextTests(unittest.TestCase):
    def test_run_passes_job_city_to_rules(self):
        import test_conversation_run as T
        from unittest.mock import patch
        case = T.ConversationRunTests('run_hop')
        case.setUp()
        try:
            c = case.module()
            item = case.item()
            item['cityName'] = '杭州'
            seen = []
            real = c.conv_rules.decide
            def spy(record, msgs, ctx):
                seen.append(ctx.get('job_city'))
                return real(record, msgs, ctx)
            with patch.object(c.conv_rules, 'decide', side_effect=spy):
                case.run_hop(items=[item])
            self.assertEqual(seen, ['杭州'])
        finally:
            case.doCleanups()
