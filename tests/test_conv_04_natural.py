"""conv-04 acceptance; fictional facts and offline generation/delivery only."""
import copy
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
import conv_actions as A
import conv_llm as L
import conv_rules as R
import conv_store as S
import conversation as C


class NaturalReplyTests(unittest.TestCase):
    def setUp(self):
        self.cfg = {'user': {'greeting': {'facts': ['做过五年示例行业BD']}}}
        self.row = S.new_record('id:fiction', 'reply', 'fiction-job',
                                '虚构公司', '虚构HR', '虚构岗位', 1)
        self.ctx = dict(cfg=self.cfg, now_ts=30, job_ok=True, history=[],
                        lead=lambda kind: '了解，', answer=lambda text: '做过五年示例行业BD，',
                        opener=lambda: '对这个岗位挺感兴趣，',
                        extract=lambda h: {'requirements': '虚构客户维护'})

    def decide(self, text, row=None):
        return R.decide(row or self.row, [{'who': 'friend', 'ts': 20, 'text': text}], self.ctx)

    def texts(self, actions):
        return [a['text'] for a in actions if a['type'] == 'send_text']

    def execute(self, before, actions, planned, receipt='sent', real_gate=False):
        result = dict(round1=0, round2=0, resume_sent=0, resume_duplicates=0,
                      manual=0, complete=0, notifications=[])
        if real_gate:
            # Exercise the unchanged template/safety gate, stopping at mocked DOM identity.
            with patch.object(A.chat_dom, 'active_conversation', return_value={}) as dom:
                row = C._execute(self.cfg, Mock(), 1, {}, before, actions, planned,
                                 result, [], 30)
                self.assertTrue(dom.called, 'sender rejected before reaching DOM boundary')
                return row
        with patch.object(A, 'send_text', return_value=receipt) as send:
            row = C._execute(self.cfg, Mock(), 1, {}, before, actions, planned,
                             result, [], 30)
        return row, send

    def engaged(self):
        self.row.update(status='engaged', resume_sent_ts=2, asked_round1_ts=2)

    def test_round2_lead_reaches_existing_gate_and_delivery(self):
        self.engaged()
        actions, planned = self.decide('主要负责虚构客户维护')
        template = R.DEFAULT_TEMPLATES['round2']
        self.assertEqual(self.texts(actions), ['了解，' + template])
        self.execute(self.row, actions, planned, real_gate=True)
        row, send = self.execute(self.row, actions, planned)
        self.assertEqual(send.call_args.args[6:], (template, '了解，'))
        self.assertEqual(row['sent_templates'], [template])
        self.assertEqual(self.row.get('sent_templates', []), [])

    def test_invalid_leads_are_dropped(self):
        self.engaged()
        for text in ('了解2点，', '整理一下发您，', '了解吗？', '明天再聊，', '加微信，'):
            with self.subTest(text=text):
                self.ctx['lead'] = lambda kind: text
                actions, _ = self.decide('主要负责虚构客户维护')
                self.assertEqual(self.texts(actions), [R.DEFAULT_TEMPLATES['round2']])

    def test_lead_generation_checks_output_and_failure(self):
        self.assertTrue(callable(getattr(L, 'lead', None)), 'lead helper missing')
        for text, expected in [('了解，', '了解，'), ('整理一下发您，', ''),
                               ('了解3项，', ''), ('方便吗？', ''), ('甲' * 41, '')]:
            with patch.object(L.G, '_generate_raw', return_value=(text, '')):
                self.assertEqual(L.lead(self.cfg, 'round2', '虚构介绍', [], L.G.facts(self.cfg)), expected)
        with patch.object(L.G, '_generate_raw', side_effect=RuntimeError('fiction offline')):
            self.assertEqual(L.lead(self.cfg, 'round1', '虚构介绍', [], L.G.facts(self.cfg)), '')

    def test_experience_answer_and_partial_question(self):
        self.assertEqual(R.classify('请问有相关工作经验吗？'), 'experience')
        self.engaged()
        self.row['answers']['requirements'] = '虚构客户维护'
        actions, planned = self.decide('请问有相关工作经验吗？')
        template = R.DEFAULT_TEMPLATES['salary_structure']
        self.assertEqual(self.texts(actions), ['做过五年示例行业BD，' + template])
        self.execute(self.row, actions, planned, real_gate=True)
        row, send = self.execute(self.row, actions, planned)
        self.assertEqual(send.call_args.args[6:], (template, '做过五年示例行业BD，'))
        self.assertEqual(row['experience_answered_ts'], 30)

    def test_answer_generation_only_uses_confirmed_facts(self):
        self.assertTrue(callable(getattr(L, 'answer', None)), 'answer helper missing')
        for text, expected in [('做过五年示例行业BD，', '做过五年示例行业BD，'),
                               ('做过10年示例行业BD，', ''), ('带过火箭研发团队，', '')]:
            with patch.object(L.G, '_generate_raw', return_value=(text, '')):
                self.assertEqual(L.answer(self.cfg, '有经验吗？', [], L.G.facts(self.cfg)), expected)

    def test_invalid_or_second_experience_is_manual(self):
        self.engaged()
        for answer in ('做过10年示例行业BD，', '', '带过火箭研发团队，'):
            self.ctx['answer'] = lambda text: answer
            actions, planned = self.decide('请问有相关工作经验吗？')
            self.assertEqual(self.texts(actions), [])
            self.assertEqual(planned['status'], 'manual')
        self.row['experience_answered_ts'] = 5
        self.ctx['answer'] = Mock(side_effect=AssertionError('must not answer twice'))
        actions, planned = self.decide('请问有相关工作经验吗？')
        self.assertEqual(planned['status'], 'manual')
        self.assertEqual(self.texts(actions), [])
        self.ctx['answer'].assert_not_called()

    def test_manual_intent_has_priority(self):
        for text in ('有相关经验吗？期望薪资多少？', '有经验吗？加个微信吧',
                     '有经验吗？什么时候到岗？', '有经验吗？需要前置收费'):
            self.assertEqual(R.classify(text), 'manual')

    def test_review2_experience_yes_no_questions(self):
        for text in ('做过零售平台的BD吗？', '带过团队吗？', '负责过连锁餐饮吗？',
                     '你之前带过团队吗？', '以前做过BD吗？', '干过销售吗？',
                     '管过团队没有？', '跑过客户没？', '签过连锁客户?'):
            with self.subTest(text=text):
                self.assertEqual(R.classify(text), 'experience')

    def test_review2_manual_questions_keep_priority(self):
        for text in ('管过多少人？', '签过几家？', '带过几个团队吗？', '团队有几人？',
                     '说说你之前做什么的', '介绍下以前负责的业务',
                     '之前负责过哪些项目？', '做过BD吗？期望薪资多少？',
                     '你之前带过团队吗？什么时候到岗？', '以前做过BD吗？加个微信吧'):
            with self.subTest(text=text):
                self.assertEqual(R.classify(text), 'manual')

    def test_review2_fact_formatting_is_not_a_new_claim(self):
        cases = [
            ('5 年示例行业行业 BD 与团队管理经验',
             '有的，5年示例行业行业BD与团队管理经验。'),
            ('行业（虚构）：示例行业／零售 BD', '有的，行业(虚构):示例行业/零售BD。'),
            ('行业(虚构):示例行业/零售BD', '有的，行业（虚构）：示例行业／零售 BD。'),
            ('示例\t行业\u3000BD\u00a0经验', '有的，示例行业BD经验。'),
        ]
        for fact, answer in cases:
            with self.subTest(fact=fact):
                self.cfg['user']['greeting']['facts'] = [fact]
                self.assertTrue(R.safe_answer(answer, self.cfg))

    def test_review2_normalization_does_not_relax_grounding(self):
        self.cfg['user']['greeting']['facts'] = ['5 年示例行业行业 BD 与团队管理经验']
        for answer in ('有的，6年示例行业行业BD与团队管理经验。',
                       '有的，5年全国示例行业行业BD与团队管理经验。',
                       '有的，有的，5年示例行业行业BD与团队管理经验。'):
            with self.subTest(answer=answer):
                self.assertFalse(R.safe_answer(answer, self.cfg))

    def test_review2_compact_answer_reaches_round2_gate_unchanged(self):
        self.engaged()
        self.row['answers'].update(requirements='虚构要求', salary_structure='虚构结构')
        self.cfg['user']['greeting']['facts'] = ['5 年示例行业行业 BD 与团队管理经验']
        answer = '有的，5年示例行业行业BD与团队管理经验。'
        self.ctx['answer'] = lambda text: answer
        actions, planned = self.decide('请问有相关工作经验吗？')
        template = '面试大概有几轮？可以安排线上面试吗？'
        self.assertEqual(self.texts(actions), [answer + template])
        self.execute(self.row, actions, planned, real_gate=True)
        row, send = self.execute(self.row, actions, planned)
        self.assertEqual(send.call_args.args[5:], (answer + template, template, answer))
        self.assertEqual(row['sent_templates'], [template])
        self.assertEqual(row['experience_answered_ts'], 30)

    def test_ack_whitelist_requires_one_following_fact(self):
        for ack in ('有的', '做过', '是的', '有'):
            with self.subTest(ack=ack):
                text = ack + '，做过五年示例行业BD。'
                self.assertTrue(R.safe_answer(text, self.cfg))
                with patch.object(L.G, '_generate_raw', return_value=(text, '')):
                    self.assertEqual(L.answer(self.cfg, '有经验吗？', [], []), text)
                for invalid in (ack, ack + '，' + ack + '，做过五年示例行业BD。',
                                ack + '，带过虚构研发团队。'):
                    self.assertFalse(R.safe_answer(invalid, self.cfg))

    def test_answer_punctuation_length_boundary(self):
        self.engaged()
        for length in (59, 60):
            fact = '虚' * length
            self.cfg['user']['greeting']['facts'] = [fact]
            self.ctx['answer'] = lambda text: fact
            actions, planned = self.decide('有相关经验吗？')
            if length == 59:
                self.assertEqual(self.texts(actions), [fact + '。' + R.DEFAULT_TEMPLATES['round1']])
            else:
                self.assertEqual(self.texts(actions), [])
                self.assertEqual(planned['status'], 'manual')

    def test_experience_preserves_batch_resume_state(self):
        self.row['origin'] = 'inbound'
        for answer in ('做过五年示例行业BD，', ''):
            self.ctx['answer'] = lambda text: answer
            actions, planned = R.decide(self.row, [
                {'who': 'friend', 'text': '你好', 'ts': 10},
                {'who': 'friend', 'text': '有相关经验吗？', 'ts': 20}], self.ctx)
            self.assertEqual(sum(a['type'] == 'send_resume' for a in actions), 1)
            self.assertEqual(planned['resume_sent_ts'], 30)
            if not answer:
                self.assertIsNone(planned['asked_round1_ts'])

    def test_fact_excerpt_cannot_drop_negation_or_subject(self):
        for fact in ('未做过五年示例行业BD', '同事做过五年示例行业BD'):
            self.cfg['user']['greeting']['facts'] = [fact]
            actions, planned = self.decide('有相关经验吗？')
            self.assertEqual(self.texts(actions), [])
            self.assertEqual(planned['status'], 'manual')

    def test_batch_greeting_does_not_swallow_experience_question(self):
        for answer in ('做过五年示例行业BD，', ''):
            self.ctx['answer'] = lambda text: answer
            actions, planned = R.decide(self.row, [
                {'who': 'friend', 'text': '方便聊聊吗？', 'ts': 10},
                {'who': 'friend', 'text': '有相关经验吗？', 'ts': 20}], self.ctx)
            self.assertEqual(self.texts(actions),
                             [answer + R.DEFAULT_TEMPLATES['round1']] if answer else [])
            if not answer:
                self.assertEqual(planned['status'], 'manual')

    def test_lead_rejects_relative_time_and_promises(self):
        for text in ('稍后再聊，', '待会回复，', '随后联系您，', '我会发你，'):
            self.ctx['lead'] = lambda kind: text
            actions, _ = self.decide('方便聊聊吗？')
            self.assertEqual(self.texts(actions), [R.DEFAULT_TEMPLATES['round1']])

    def test_experience_advances_or_uses_tail(self):
        self.engaged()
        self.row['answers'].update(requirements='虚构要求', salary_structure='虚构结构')
        actions, planned = self.decide('请问有相关工作经验吗？')
        self.assertEqual(self.texts(actions), ['做过五年示例行业BD，' + R.DEFAULT_TEMPLATES['round2']])
        self.row.update(status='round2', asked_round2_ts=4)
        self.row['answers'].update(interview_rounds='两轮', online_interview='线上')
        actions, planned = self.decide('请问有相关工作经验吗？')
        tail = '您这边对这个岗位还有什么想了解的吗？'
        self.assertEqual(self.texts(actions), ['做过五年示例行业BD，' + tail])
        self.execute(self.row, actions, planned, real_gate=True)

    def test_template_repeat_guard(self):
        template = R.DEFAULT_TEMPLATES['round1']
        for count, lead, allowed in ((2, '了解，', False), (1, '', False), (1, '了解，', True)):
            self.row['sent_templates'] = [template] * count
            self.ctx['lead'] = lambda kind: lead
            actions, planned = self.decide('方便聊聊吗？')
            self.assertEqual(bool(self.texts(actions)), allowed)
            if not allowed:
                self.assertEqual(planned['status'], 'manual')

    def test_delivery_only_updates_new_fields_on_sent(self):
        self.engaged()
        self.row['sent_templates'] = ['虚构模板%d' % i for i in range(5)]
        actions, planned = self.decide('请问有相关工作经验吗？')
        self.assertEqual(planned['sent_templates'], self.row['sent_templates'])
        for receipt in ('pending_verification', 'fill_err', 'sent'):
            row, _ = self.execute(self.row, actions, planned, receipt)
            self.assertEqual(row.get('experience_answered_ts'), 30 if receipt == 'sent' else None)
            expected = self.row['sent_templates'][1:] + [R.DEFAULT_TEMPLATES['round1']]
            self.assertEqual(row['sent_templates'], expected if receipt == 'sent' else self.row['sent_templates'])

    def test_old_record_loads_and_runs(self):
        old = copy.deepcopy(self.row)
        old.pop('sent_templates', None)
        old.pop('experience_answered_ts', None)
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'fiction.json'
            path.write_text(json.dumps({'id:fiction': old}), encoding='utf-8')
            row = S.load(path)['id:fiction']
        self.assertEqual(row.get('sent_templates'), [])
        self.assertIn('experience_answered_ts', row)
        actions, _ = self.decide('方便聊聊吗？', row)
        self.assertEqual(self.texts(actions), ['了解，' + R.DEFAULT_TEMPLATES['round1']])

    def test_fixed_replies_and_inbound_opener_unchanged(self):
        actions, _ = self.decide('明天来面试可以吗？')
        self.assertEqual(self.texts(actions), [R.INTERVIEW_REPLY])
        self.ctx['declined'] = True
        actions, _ = self.decide('方便聊聊吗？')
        self.assertEqual(self.texts(actions), [R.DECLINE_TEXT])
        self.ctx['declined'] = False
        self.row['origin'] = 'inbound'
        actions, _ = self.decide('方便聊聊吗？')
        self.assertEqual(self.texts(actions), ['对这个岗位挺感兴趣，' + R.DEFAULT_TEMPLATES['round1']])
