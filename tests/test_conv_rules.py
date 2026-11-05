"""Offline state-machine contracts; all companies and people are fictional."""
import copy
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
import conv_rules as R
import conv_store as S

ROUND1 = '方便说下这个岗位主要的工作要求，和薪资结构（底薪/提成/奖金）吗？'
ROUND2 = '面试大概有几轮？可以安排线上面试吗？'
INTERVIEW = '希望线上，请先给时段，我确认后答复'
ALL = {'requirements': '负责虚构产品测试', 'salary_structure': '底薪加奖金',
       'interview_rounds': '两轮', 'online_interview': '可以线上'}


class ClassifyTests(unittest.TestCase):
    def test_modified_resume_requests_exclude_sent_statements(self):
        for text in ('可以投递的～辛苦发一份最新版本的附件简历吗',
                     '辛苦发一份最新版本的附件简历吗',
                     '麻烦把简历发我一下', '方便发下最新简历吗'):
            with self.subTest(text=text):
                self.assertEqual(R.classify(text), 'resume')
        for text in ('我已发简历给您了', '简历已发送给Boss', '发过简历',
                     '已经发过简历了', '已发送附件简历'):
            with self.subTest(text=text):
                self.assertNotEqual(R.classify(text), 'resume')

    def test_chinese_examples_and_precedence(self):
        samples = {
            'reject': ['不好意思，岗位已招满', '暂不考虑', '不太合适', '岗位已经关闭',
                       '不匹配，简历不用发了', '已经招到人了，方便加微信吗'],
            'manual': ['你期望薪资多少', '薪资要求是多少', '你的期望工资是？',
                       '什么时候可以到岗', '入职时间是什么时候', '请自我介绍一下',
                       '之前负责过哪些项目？', '能详细说说你的工作经历吗',
                       '方便加个微信吗', '交换一下电话号码',
                       '以上条件能否接受', '上述方案可以接受吗', '这是什么意思？',
                       '你希望薪资多少', '多久能入职', '到岗需要多久',
                       '方便介绍一下自己吗', '上一份工作为什么离职',
                       '你的上一份工作具体做什么',
                       '你期望薪资多少，明天来面试吧'],
            'experience': ['有没有带团队的经验'],
            'interview': ['我们约个面试吧，周三下午可以吗', '明天下午方便面谈吗',
                          '邀请你参加线上面试', '请来公司参加复试'],
            'resume': ['可以发份简历吗', '简历发我一下', '麻烦发送一份简历',
                       '你好，请发一下简历'],
            'greet': ['你好', '聊聊？', '您好', '对你很感兴趣', '方便沟通一下吗'],
            'other': ['薪资结构怎么算', '主要负责客户维护', '薪资是底薪加提成',
                      '面试有两轮，可以线上', '可以安排线上面试', '线下面试，共两轮', '',
                      '面试有两轮，可以安排线上面试',
                      '我们要求有项目经验', '我们预计下周入职，您看一下'],
        }
        for label, texts in samples.items():
            for text in texts:
                with self.subTest(text=text):
                    self.assertEqual(R.classify(text), label)

    def test_dryrun_rejections_and_positive_matches(self):
        for text in ('虚构星桥感谢您的投递，但您的专业技能与职位需求并不完全吻合。',
                     '虚构云帆感谢您的应聘，但项目需要与您的经验不完全匹配。',
                     '虚构岗位与您的背景不吻合', '虚构职位要求并不完全符合',
                     '虚构岗位与您的经验不符合'):
            with self.subTest(text=text):
                self.assertEqual(R.classify(text), 'reject')
        self.assertNotEqual(R.classify('这个虚构岗位和你的经验挺匹配的'), 'reject')
        self.assertNotEqual(R.classify('薪资符合你的预期吗'), 'reject')
        for text in ('底薪按城市不一致，提成另算', '各区域提成比例不一致'):
            with self.subTest(text=text):
                self.assertNotEqual(R.classify(text), 'reject')

    def test_meeting_invitations_precede_greetings_and_resume_requests(self):
        for text in ('后天上午九点左右，见面聊聊？', '方便面聊吗', '想面谈',
                     '当面沟通', '来公司聊聊', '到公司看看', '来我们公司',
                     '过来公司', '线下聊聊', '今晚聊一下', '周四见一下',
                     '星期五过来', '下午来一趟', '晚上聊聊', '09:30聊一下',
                     '10：30见一下', '九点见面，带份简历'):
            with self.subTest(text=text):
                self.assertEqual(R.classify(text), 'interview')
        for text in ('方便聊聊吗', '可以先线上聊聊这个岗位'):
            self.assertEqual(R.classify(text), 'greet')
        self.assertEqual(R.classify('面试大概两轮，可以线上'), 'other')

    def test_additional_resume_request_forms(self):
        for text in ('能求一份你的简历吗', '要一份您的附件简历', '来份简历',
                     '看看简历', '看下你的简历', '看一下您的简历',
                     '拿一下简历'):
            with self.subTest(text=text):
                self.assertEqual(R.classify(text), 'resume')

        self.assertNotEqual(R.classify('收到简历'), 'resume')

    def test_patterns_are_configurable_without_changing_defaults(self):
        cfg = {'conversation': {'patterns': {'reject': '虚构拒绝口令'}}}
        self.assertEqual(R.classify('虚构拒绝口令', cfg), 'reject')
        self.assertEqual(R.classify('虚构拒绝口令'), 'other')
        self.assertEqual(R.classify('你好', cfg), 'greet')
        cfg['conversation']['patterns']['interview'] = '可以安排线上面试'
        self.assertEqual(R.classify('可以安排线上面试', cfg), 'interview')


class ConversationRulesTests(unittest.TestCase):
    def test_split_first_round_batch_waits_for_next_hop_before_complete(self):
        messages = [{'text': text, 'ts': 25 + i} for i, text in enumerate((
            '主要工作是测试虚构产品', '底薪六到十六', '职级底薪，加奖金'))]
        seen = []

        def extract(history):
            seen.append(history)
            return {'requirements': '测试虚构产品', 'salary_structure': '底薪六到十六，加奖金'}

        actions, row = R.decide(self.record(status='engaged'), messages,
                               self.ctx(extract=extract, history=messages))
        self.assertEqual(row['status'], 'round2')
        self.assertEqual(actions, [{'type': 'send_text', 'text': ROUND2}])
        self.assertFalse(row['notified']['complete'])
        self.assertEqual(seen, [messages])
        answer = {'text': '两轮，可以线上', 'ts': 35}
        actions, row = R.decide(row, [answer], self.ctx(
            {'interview_rounds': '两轮', 'online_interview': '可以线上'},
            now_ts=40, history=messages + [answer]))
        self.assertEqual(row['status'], 'complete')
        self.assertEqual(actions[0]['kind'], 'complete')
        self.assertEqual(actions[0]['card']['interview_rounds'], '两轮')
        self.assertEqual(actions[0]['card']['online_interview'], '可以线上')
        self.assertEqual(actions[0]['card']['salary_structure'], '底薪六到十六，加奖金')

    def test_batch_with_all_answers_completes_without_second_question(self):
        messages = [{'text': text, 'ts': 25 + i} for i, text in enumerate((
            '负责虚构产品测试', '底薪加奖金', '两轮，可以线上'))]

        def extract(history):
            # A per-message context would miss the later interview answers.
            self.assertEqual(history, messages)
            return ALL

        actions, row = R.decide(self.record(status='engaged'), messages,
                               self.ctx(extract=extract, history=messages))
        self.assertEqual(row['status'], 'complete')
        self.assertEqual([a['type'] for a in actions], ['notify'])
        self.assertEqual(actions[0]['card']['interview_rounds'], '两轮')
        self.assertIsNone(row['asked_round2_ts'])

    def test_batch_before_first_question_cannot_supply_first_round_answers(self):
        messages = [{'text': text, 'ts': 25 + i} for i, text in enumerate((
            '你好', '主要负责虚构产品测试', '底薪加奖金'))]
        actions, row = R.decide(self.record(), messages, self.ctx(ALL, history=messages))
        self.assertEqual(row['status'], 'engaged')
        self.assertEqual(actions, [{'type': 'send_resume'}, {'type': 'send_text', 'text': ROUND1}])
        self.assertTrue(all(value is None for value in row['answers'].values()))
        self.assertIsNone(row['asked_round2_ts'])

    def test_long_resume_request_starts_reply_first_round(self):
        actions, row = self.run_step(self.record(), '可以投递的～辛苦发一份最新版本的附件简历吗')
        self.assertEqual(actions, [{'type': 'send_resume'}, {'type': 'send_text', 'text': ROUND1}])
        self.assertEqual(row['status'], 'engaged')

    def record(self, origin='reply', status='new'):
        row = S.new_record('fiction:1', origin, 'fiction-job', '虚构星港', '虚构小岚', '测试岗位', 1)
        row['status'] = status
        if status in ('engaged', 'round2'):
            row['resume_sent_ts'] = 10
            row['asked_round1_ts'] = 10
        if status == 'round2':
            row['asked_round2_ts'] = 20
        return row

    def ctx(self, answers=None, **overrides):
        context = {'cfg': {}, 'now_ts': 30, 'job_ok': True,
                   'extract': lambda messages: answers or {},
                   'opener': lambda: '我有虚构产品测试经验。', 'history': []}
        context.update(overrides)
        return context

    def run_step(self, record, text, ctx=None):
        return R.decide(record, [{'text': text, 'ts': 25}], ctx or self.ctx())

    def test_salary_expectation_non_rejection_still_requires_manual(self):
        actions, row = self.run_step(self.record(), '薪资符合你的预期吗')
        self.assertEqual(row['status'], 'manual')
        self.assertEqual([a['type'] for a in actions], ['notify'])

    def test_both_origins_complete_full_two_round_path(self):
        for origin in ('inbound', 'reply'):
            with self.subTest(origin=origin):
                actions, row = self.run_step(self.record(origin), '你好')
                text = {'type': 'send_text', 'text': ROUND1}
                expected = [{'type': 'send_resume'}, text]
                if origin == 'inbound':
                    expected = [{'type': 'send_text', 'text': '我有虚构产品测试经验。' + ROUND1,
                                 'template_part': ROUND1, 'opener_part': '我有虚构产品测试经验。'},
                                {'type': 'send_resume'}]
                self.assertEqual(actions, expected)
                self.assertEqual(row['status'], 'engaged')
                self.assertEqual((row['resume_sent_ts'], row['asked_round1_ts']), (30, 30))
                actions, row = self.run_step(row, '主要做测试，底薪加奖金', self.ctx({
                    'requirements': ALL['requirements'], 'salary_structure': ALL['salary_structure']}))
                self.assertEqual(actions, [{'type': 'send_text', 'text': ROUND2}])
                self.assertEqual(row['status'], 'round2')
                self.assertEqual(row['asked_round2_ts'], 30)
                actions, row = self.run_step(row, '面试两轮，可以线上', self.ctx(ALL))
                self.assertEqual(row['status'], 'complete')
                self.assertEqual(actions, [{'type': 'notify', 'kind': 'complete', 'card': {
                    'brand': '虚构星港', 'title': '测试岗位', 'boss': '虚构小岚', **ALL}}])
                self.assertTrue(row['notified']['complete'])

    def test_four_answers_in_first_round_skip_second_round(self):
        actions, row = self.run_step(self.record(status='engaged'), '测试，底薪加奖金，面试两轮，可以线上', self.ctx(ALL))
        self.assertEqual(row['status'], 'complete')
        self.assertEqual([a['type'] for a in actions], ['notify'])
        self.assertIsNone(row['asked_round2_ts'])

    def test_salary_only_still_asks_only_interview_questions_then_missing_card(self):
        actions, row = self.run_step(self.record(status='engaged'), '底薪加奖金', self.ctx({'salary_structure': '底薪加奖金'}))
        self.assertEqual(actions, [{'type': 'send_text', 'text': ROUND2}])
        actions, row = self.run_step(row, '暂时还没确定', self.ctx())
        self.assertEqual(row['status'], 'complete')
        self.assertEqual(actions[0]['card']['requirements'], '未答')
        self.assertEqual(actions[0]['card']['interview_rounds'], '未答')

    def test_inbound_ungraded_never_calls_opener(self):
        def forbidden(*args):
            self.fail('callback must not be invoked')
        for job_ok, status in ((None, 'new'),):
            for text in ('你好', '可以发份简历吗'):
                with self.subTest(job_ok=job_ok, text=text):
                    actions, row = self.run_step(self.record('inbound'), text, self.ctx(job_ok=job_ok, opener=forbidden))
                    self.assertEqual(actions, [])
                    self.assertEqual(row['status'], status)
        actions, row = self.run_step(self.record('reply'), '你好', self.ctx(job_ok=None, opener=forbidden))
        self.assertEqual(row['status'], 'engaged')
        self.assertEqual(len(actions), 2)

    def test_resume_request_runs_first_actions_but_never_resends(self):
        actions, row = self.run_step(self.record(), '可以发份简历吗')
        self.assertEqual(actions, [{'type': 'send_resume'}, {'type': 'send_text', 'text': ROUND1}])
        actions, row = self.run_step(row, '简历发我一下')
        self.assertEqual(actions, [])
        self.assertEqual(row['resume_sent_ts'], 30)

    def test_resume_sent_but_round1_missing_only_sends_questions(self):
        row = self.record()
        row['resume_sent_ts'] = 0
        actions, row = self.run_step(row, '简历发我一下')
        self.assertEqual(actions, [{'type': 'send_text', 'text': ROUND1}])
        self.assertEqual(row['status'], 'engaged')

    def test_interview_uses_fixed_reply_and_manual_notification(self):
        for cfg, expected in (({}, INTERVIEW), ({'user': {'interview_reply': '希望线上，请先给时段。'}}, '希望线上，请先给时段。')):
            actions, row = self.run_step(self.record(status='engaged'), '我们约个面试吧，周三下午可以吗', self.ctx(cfg=cfg))
            self.assertEqual(actions[0], {'type': 'send_text', 'text': expected})
            self.assertEqual(actions[1]['kind'], 'manual')
            self.assertEqual(row['status'], 'manual')
            self.assertTrue(row['notified']['manual'])
            actions, after = self.run_step(row, '周四可以吗')
            self.assertEqual(actions, [])
            self.assertEqual(after['answers'], row['answers'])

    def test_terminal_records_only_update_last_seen_even_in_batch(self):
        for status in ('manual', 'complete', 'closed', 'declined'):
            row = self.record(status=status)
            actions, after = R.decide(row, [{'text': '你好', 'ts': 31}, {'text': '请发简历', 'ts': 32}], self.ctx())
            self.assertEqual(actions, [])
            expected = copy.deepcopy(row)
            expected['last_seen'] = {'ts': 32, 'text': '请发简历'}
            self.assertEqual(after, expected)

    def test_reject_and_manual_take_priority_without_extraction(self):
        def forbidden(*args):
            self.fail('must not extract or generate an opener')
        for text, status, kinds in (('不合适，明天面试不用来了', 'closed', []),
                                    ('你期望薪资多少，明天面试吧', 'manual', ['notify'])):
            actions, row = self.run_step(self.record(), text, self.ctx(extract=forbidden, opener=forbidden))
            self.assertEqual(row['status'], status)
            self.assertEqual([a['type'] for a in actions], kinds)

    def test_extract_uses_last_three_messages_without_overwriting_known_answers(self):
        row = self.record(status='engaged')
        row['answers']['salary_structure'] = '已确认底薪'
        history = [{'text': '虚构历史%d' % n, 'ts': n} for n in range(5)]
        seen = []

        def extract(messages):
            seen.extend(copy.deepcopy(messages))
            messages[0]['text'] = '替身修改'
            return {**ALL, 'salary_structure': '不应覆盖', 'unexpected': '忽略'}

        original = copy.deepcopy(history)
        actions, after = self.run_step(row, '聊聊？', self.ctx(extract=extract, history=history))
        self.assertEqual(seen, original[-3:])
        self.assertEqual(history, original)
        self.assertEqual(after['answers']['salary_structure'], '已确认底薪')
        self.assertNotIn('unexpected', after['answers'])
        self.assertEqual(actions[0]['kind'], 'complete')

    def test_extraction_failure_logs_failure_and_keeps_state(self):
        def failed(messages):
            raise RuntimeError('fictional extraction failure')
        for status in ('engaged', 'round2'):
            row = self.record(status=status)
            actions, after = self.run_step(row, '底薪加奖金', self.ctx(extract=failed))
            self.assertEqual(actions, [])
            self.assertEqual(after['status'], status)
            self.assertEqual(after['answers'], row['answers'])
            self.assertEqual(after['actions'][-1]['type'], 'extract')
            self.assertIn('fail', after['actions'][-1]['result'])

    def test_no_first_round_answer_waits_without_followup(self):
        actions, row = self.run_step(self.record(status='engaged'), '暂未确定', self.ctx({'online_interview': '可以'}))
        self.assertEqual(actions, [])
        self.assertEqual(row['status'], 'engaged')

    def test_opener_failure_empty_or_too_long_never_sends_resume(self):
        def failed():
            raise RuntimeError('fictional failure')
        for opener in (failed, lambda: '', lambda: '虚' * 61):
            actions, row = self.run_step(self.record('inbound'), '你好', self.ctx(opener=opener))
            self.assertEqual(actions, [])
            self.assertEqual(row['status'], 'new')
            self.assertEqual(row['actions'][-1], {'ts': 30, 'type': 'opener', 'result': 'failed'})
            self.assertIsNone(row['resume_sent_ts'])

    def test_extraction_never_runs_for_resume_or_terminal_and_malformed_result_fails_closed(self):
        def forbidden(*args):
            self.fail('extract is forbidden on this path')
        for status, text in (('engaged', '简历发我一下'), ('manual', '你好'),
                             ('round2', '不好意思，岗位已招满')):
            self.run_step(self.record(status=status), text, self.ctx(extract=forbidden))
        actions, row = self.run_step(self.record(status='round2'), '待定', self.ctx(extract=lambda messages: []))
        self.assertEqual(actions, [])
        self.assertEqual(row['status'], 'round2')
        self.assertEqual(row['actions'][-1]['result'], 'failed')

    def test_interview_after_greeting_honors_text_cap_and_notifies_manual(self):
        actions, row = R.decide(self.record(), [
            {'text': '你好', 'ts': 1}, {'text': '请来公司面试', 'ts': 2}], self.ctx())
        self.assertEqual(sum(a['type'] == 'send_text' for a in actions), 1)
        self.assertEqual(actions[-1]['kind'], 'manual')
        self.assertEqual(row['status'], 'manual')

    def test_partial_questions_also_skip_salary_and_online_answers(self):
        row = self.record(status='engaged')
        row['answers']['salary_structure'] = '底薪加奖金'
        self.assertEqual(R.round_questions(row, {}, 1), '方便说下这个岗位主要的工作要求吗？')
        actions, row = self.run_step(row, '主要负责测试，可以线上', self.ctx({
            'requirements': '负责测试', 'online_interview': '可以'}))
        self.assertEqual(actions, [{'type': 'send_text', 'text': '面试大概有几轮？'}])
        self.assertEqual(row['status'], 'round2')

    def test_deep_copy_also_holds_during_extraction_failure_and_terminal_paths(self):
        for status, text in (('engaged', '底薪加奖金'), ('round2', '待定'), ('manual', '你好')):
            original = self.record(status=status)
            snapshot = copy.deepcopy(original)
            self.run_step(original, text, self.ctx(ALL))
            self.assertEqual(original, snapshot)

    def test_batch_greeting_resume_salary_question_ends_manual_with_send_caps(self):
        messages = [{'text': text, 'ts': i} for i, text in enumerate(('你好', '简历发我一下', '你期望薪资多少'))]
        actions, row = R.decide(self.record(), messages, self.ctx())
        self.assertEqual(actions[:2], [{'type': 'send_resume'}, {'type': 'send_text', 'text': ROUND1}])
        self.assertEqual([a['type'] for a in actions], ['send_resume', 'send_text', 'notify'])
        self.assertEqual(row['status'], 'manual')
        self.assertEqual(row['last_seen'], {'ts': 2, 'text': '你期望薪资多少'})

    def test_batch_limit_does_not_mark_unsent_round2_as_asked(self):
        messages = [{'text': '你好', 'ts': 1}, {'text': '底薪加奖金', 'ts': 2}]
        actions, row = R.decide(self.record(), messages, self.ctx({'salary_structure': '底薪加奖金'}))
        self.assertEqual(sum(a['type'] == 'send_text' for a in actions), 1)
        self.assertIsNone(row['asked_round2_ts'])
        self.assertEqual(row['status'], 'engaged')
        actions, row = self.run_step(row, '底薪加奖金', self.ctx({'salary_structure': '底薪加奖金'}))
        self.assertEqual(actions, [{'type': 'send_text', 'text': ROUND2}])
        self.assertEqual(row['status'], 'round2')

    def test_entering_terminal_skips_remaining_callbacks_but_tracks_latest_seen(self):
        messages = [{'text': '你期望薪资多少', 'ts': 1}, {'text': '底薪加奖金', 'ts': 2}]
        def forbidden(*args):
            self.fail('terminal must not call extract')
        actions, row = R.decide(self.record(status='engaged'), messages, self.ctx(extract=forbidden))
        self.assertEqual(len(actions), 1)
        self.assertEqual(row['status'], 'manual')
        self.assertEqual(row['last_seen'], {'ts': 2, 'text': '底薪加奖金'})

    def test_decide_deep_copies_record_and_empty_jump_does_nothing(self):
        row = self.record()
        original = copy.deepcopy(row)
        actions, after = self.run_step(row, '你好')
        self.assertEqual(row, original)
        after['answers']['requirements'] = '修改返回值'
        after['actions'].append({'type': 'test'})
        self.assertEqual(row, original)
        actions, after = R.decide(row, [], self.ctx())
        self.assertEqual((actions, after), ([], original))
        self.assertIsNot(after['answers'], row['answers'])

    def test_round_questions_skip_answered_items_and_use_config(self):
        row = self.record()
        self.assertEqual(R.round_questions(row, {}, 1), ROUND1)
        self.assertEqual(R.round_questions(row, {}, 2), ROUND2)
        row['answers']['requirements'] = '测试'
        self.assertEqual(R.round_questions(row, {}, 1), '方便说下薪资结构（底薪/提成/奖金）吗？')
        row['answers']['salary_structure'] = '奖金'
        self.assertEqual(R.round_questions(row, {}, 1), '')
        for key, expected in (('interview_rounds', '可以安排线上面试吗？'),
                              ('online_interview', '面试大概有几轮？')):
            partial = self.record()
            partial['answers'][key] = '已答'
            self.assertEqual(R.round_questions(partial, {}, 2), expected)
        cfg = {'conversation': {'templates': {'round1': '自定义第一轮？', 'round2': '自定义第二轮？',
                                             'online_interview': '是否支持视频面试？'}}}
        row = self.record()
        self.assertEqual(R.round_questions(row, cfg, 1), '自定义第一轮？')
        self.assertEqual(R.round_questions(row, cfg, 2), '自定义第二轮？')
        row['answers']['interview_rounds'] = '两轮'
        self.assertEqual(R.round_questions(row, cfg, 2), '是否支持视频面试？')
        row['answers']['online_interview'] = False
        self.assertEqual(R.round_questions(row, cfg, 2), '')

    def test_card_line_keeps_order_and_false_answer(self):
        card = {'brand': '虚构星港', 'title': '测试岗位', 'boss': '虚构小岚', **ALL}
        self.assertEqual(R.card_line(card), '虚构星港｜测试岗位｜虚构小岚｜负责虚构产品测试｜底薪加奖金｜两轮｜可以线上')
        row = self.record(status='round2')
        actions, after = self.run_step(row, '不支持远程', self.ctx({'online_interview': False}))
        self.assertIs(actions[0]['card']['online_interview'], False)
        self.assertEqual(after['status'], 'complete')

    def test_round2_process_description_extracts_answers_and_completes(self):
        row = self.record(status='round2')
        row['answers'].update({key: ALL[key] for key in R.ROUND_KEYS[1]})
        message = {'text': '面试一般两轮，第一轮线上，第二轮来公司面试', 'ts': 25}
        seen = []

        def extract(messages):
            seen.append(messages)
            return {'interview_rounds': '两轮', 'online_interview': '第一轮线上'}

        actions, after = R.decide(row, [message], self.ctx(
            extract=extract, history=[message]))
        self.assertEqual(seen, [[message]])
        self.assertEqual(after['status'], 'complete')
        self.assertEqual(after['answers']['interview_rounds'], '两轮')
        self.assertEqual(after['answers']['online_interview'], '第一轮线上')
        self.assertEqual(actions, [{'type': 'notify', 'kind': 'complete', 'card': {
            'brand': '虚构星港', 'title': '测试岗位', 'boss': '虚构小岚',
            **ALL, 'online_interview': '第一轮线上'}}])

    def test_round2_time_invitation_keeps_fixed_reply_and_manual(self):
        def forbidden(messages):
            self.fail('invitation must not extract answers')

        actions, row = self.run_step(self.record(status='round2'),
                                     '那明天下午来公司面试可以吗',
                                     self.ctx(extract=forbidden))
        self.assertEqual(actions[0], {'type': 'send_text', 'text': INTERVIEW})
        self.assertEqual(actions[1]['kind'], 'manual')
        self.assertEqual(row['status'], 'manual')

    def test_engaged_interview_without_time_keeps_original_behavior(self):
        def forbidden(messages):
            self.fail('invitation must not extract answers')

        actions, row = self.run_step(self.record(status='engaged'),
                                     '第二轮来公司面试', self.ctx(extract=forbidden))
        self.assertEqual(actions[0], {'type': 'send_text', 'text': INTERVIEW})
        self.assertEqual(actions[1]['kind'], 'manual')
        self.assertEqual(row['status'], 'manual')
