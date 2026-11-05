"""Offline rejection wording regressions using fictional conversation records."""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
import conv_rules as R
import conv_store as S


FIRST_REJECTION = (
    '很高兴收到您的简历，但是我们这次更倾向于有不同经验范围的候选人。'
    '祝您在BOSS直聘找到理想的工作！'
)


class RejectWordingTests(unittest.TestCase):
    def test_regret_missed_call_can_call_later(self):
        self.assertNotEqual(R.classify(
            '很遗憾刚才没接到电话，不太方便的话我晚点再打'), 'reject')

    def test_experience_coincides_with_position(self):
        self.assertNotEqual(R.classify('您的经验与这个岗位不谋而合'), 'reject')

    def test_matching_background_invites_discussion(self):
        self.assertNotEqual(R.classify(
            '您的背景跟岗位很对口，不知道您是否方便聊聊'), 'reject')

    def test_not_considering_other_candidates(self):
        self.assertNotEqual(R.classify('我们不考虑其他候选人了，就等您'), 'reject')

    def test_will_not_consider_other_candidates(self):
        self.assertNotEqual(R.classify('我们不会考虑其他候选人'), 'reject')

    def test_prioritize_other_candidates(self):
        self.assertEqual(R.classify('我们会优先考虑其他候选人'), 'reject')

    def test_consider_other_candidates_after_evaluation(self):
        self.assertEqual(R.classify(
            '收到您的简历，经过评估，我们会优先考虑其他候选人'), 'reject')

    def test_preferred_experience_matches_candidate(self):
        self.assertNotEqual(R.classify(
            '我们倾向于有餐饮经验的人选，你正好合适'), 'reject')

    def test_unrelated_experience_can_be_omitted(self):
        self.assertNotEqual(R.classify('不相关的经历可以不用写'), 'reject')

    def test_regret_missed_call_reschedule_position_discussion(self):
        self.assertNotEqual(R.classify(
            '很遗憾刚才没接到您电话，岗位的事我们再约时间'), 'reject')

    def test_prefer_other_candidates(self):
        self.assertEqual(R.classify(FIRST_REJECTION), 'reject')

    def test_background_does_not_correspond(self):
        self.assertEqual(R.classify(
            '感谢您的关注和投递，但当前职位需要与您背景不太相符'), 'reject')

    def test_regret_position_requirements(self):
        self.assertEqual(R.classify(
            '我们非常感谢您的投递，但是很遗憾，我们的职位需要其他领域的经验。'), 'reject')

    def test_low_match(self):
        self.assertEqual(R.classify(
            '抱歉，简历给到区域总看了 匹配度不是很高 期待您找到合适的工作。'), 'reject')

    def test_skills_do_not_match_position(self):
        self.assertEqual(R.classify(
            '我们感谢您的投递，但您的专业技能与我们目前的职位不太匹配'), 'reject')

    def test_related_experience_question(self):
        self.assertNotEqual(R.classify('你有相关经验吗？'), 'reject')

    def test_relevant_industry_question(self):
        self.assertNotEqual(R.classify('之前做过对口的行业吗？'), 'reject')

    def test_regret_missed_call(self):
        self.assertNotEqual(R.classify('很遗憾刚才没接到您电话，方便再约个时间吗'), 'reject')

    def test_prefer_online_interview(self):
        self.assertNotEqual(R.classify('我们倾向于线上面试，可以吗？'), 'reject')

    def test_high_match(self):
        self.assertNotEqual(R.classify('匹配度很高，想约您聊聊'), 'reject')

    def test_resume_receipt_is_not_request(self):
        self.assertIn(R.classify('收到您的简历了，我们看一下'), ('other', 'greet'))

    def test_send_resume_request(self):
        self.assertEqual(R.classify('能发一份您的简历吗'), 'resume')

    def test_ask_for_resume(self):
        self.assertEqual(R.classify('求一份你的简历'), 'resume')

    def test_first_rejection_closes_without_sending(self):
        record = S.new_record('fiction:reject', 'reply', 'fiction-job',
                              '虚构公司', '虚构招聘者', '虚构岗位', 1)
        self.assertEqual(record['status'], 'new')
        actions, planned = R.decide(record, [{'text': FIRST_REJECTION, 'ts': 2}],
                                    {'cfg': {}, 'now_ts': 3, 'job_ok': True})
        self.assertFalse(any(a['type'] in ('send_text', 'send_resume') for a in actions))
        self.assertEqual(planned['status'], 'closed')
        self.assertIsNone(planned['resume_sent_ts'])


if __name__ == '__main__':
    unittest.main()
