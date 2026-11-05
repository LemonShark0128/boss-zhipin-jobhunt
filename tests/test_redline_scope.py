"""JD 红线范围回归：内联 JD，发送入口在身份校验处停止。"""
from pathlib import Path
import sys
import unittest
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
import boss_hunt as B
import hunt_filter as HF
import outbound as O


class RedlineScopeTests(unittest.TestCase):
    def setUp(self):
        self.job = {'encryptJobId': 'inline-job', 'jobName': '区域经理'}
        self.cfg = {'grading': {'enabled': True, 'roles': ['区域经理'],
                                'tracks': {'A': {'jd_signals': ['.']}}},
                    'filters': {'text_block': ['合伙人', '外呼', '无底薪', '创业']}}

    def grade(self, jd):
        return HF.grade_with_jd(self.job, self.cfg, jd)

    def test_benefits_redline_does_not_block(self):
        gr = self.grade('岗位职责 负责区域门店 任职要求 3年以上经验\n我们能为你提供 从区域经理到合伙人')
        self.assertEqual(gr['redlines'], [])
        self.assertEqual(gr['grade'], 'A')

    def test_duties_redline_blocks(self):
        gr = self.grade('岗位职责 每天需外呼 200 通 任职要求 ...')
        self.assertEqual(gr['redlines'], ['外呼'])
        self.assertEqual((gr['grade'], gr['cause']), ('C', 'redline'))

    def test_requirements_redline_blocks(self):
        gr = self.grade('任职要求 无底薪 高提成')
        self.assertEqual(gr['redlines'], ['无底薪'])
        self.assertEqual((gr['grade'], gr['cause']), ('C', 'redline'))

    def test_company_introduction_is_excluded(self):
        self.assertEqual(self.grade('公司简介 我们是一家创业公司 岗位职责 拓展商户 任职要求 5年经验')['redlines'], [])

    def test_unheaded_preamble_is_excluded(self):
        self.assertEqual(self.grade('创业公司期待你加入 岗位职责 拓展商户')['redlines'], [])

    def test_no_included_heading_keeps_original(self):
        for jd in ('每天外呼，无底薪', '公司简介 创业公司 福利待遇 合伙人', '', '  每天外呼\n'):
            with self.subTest(jd=jd):
                self.assertTrue(callable(getattr(HF, 'jd_redline_scope', None)))
                self.assertEqual(HF.jd_redline_scope(jd), jd)
        self.assertEqual(self.grade('每天外呼，无底薪')['redlines'], ['外呼', '无底薪'])

    def test_colon_headings_on_single_line(self):
        self.assertEqual(self.grade('岗位职责：负责区域门店 任职要求：经验 四、我们提供：合伙人')['redlines'], [])

    def test_ascii_colon_headings(self):
        self.assertEqual(self.grade('工作职责:负责区域门店\n福利待遇:无底薪 任职资格:外呼')['redlines'], ['外呼'])

    def test_bracket_headings(self):
        self.assertEqual(self.grade('【公司介绍】创业 【岗位职责】负责区域门店 【岗位福利】合伙人')['redlines'], [])

    def test_all_included_headings_resume_scope(self):
        for heading in ('岗位职责', '工作职责', '职位描述', '工作内容', '岗位定位', '任职要求',
                        '任职资格', '岗位要求', '职位要求', '加分项', '优先条件'):
            with self.subTest(heading=heading):
                gr = self.grade('公司简介 创业 %s 外呼' % heading)
                self.assertEqual(gr['redlines'], ['外呼'])

    def test_all_excluded_headings_stop_scope(self):
        for heading in ('公司介绍', '公司简介', '关于我们', '企业介绍', '团队介绍', '我们可以为你提供',
                        '我们能为你提供', '我们提供', '福利待遇', '薪资福利', '岗位福利', '公司福利',
                        '职业发展', '晋升路径', '发展空间'):
            with self.subTest(heading=heading):
                jd = '岗位职责 负责门店\n%s 合伙人\n公司简介 创业 任职要求 外呼' % heading
                self.assertEqual(self.grade(jd)['redlines'], ['外呼'])

    def test_heading_words_inside_prose_are_not_sections(self):
        self.assertEqual(self.grade('岗位职责 制定公司福利政策并外呼')['redlines'], ['外呼'])

    def test_excluded_heading_phrases_in_prose_keep_redlines(self):
        for jd, redline in (
            ('岗位职责 1、拓展商户 2、提供广阔的发展空间 3、每天需外呼100通 任职要求 3年经验', '外呼'),
            ('任职要求 关注个人职业发展 接受无底薪高提成', '无底薪'),
            ('工作内容 我们提供 客户线索，你需要每天外呼 任职要求 无', '外呼'),
        ):
            with self.subTest(jd=jd):
                gr = self.grade(jd)
                self.assertEqual(gr['redlines'], [redline])
                self.assertEqual((gr['grade'], gr['cause']), ('C', 'redline'))

    def test_numbered_benefits_section_is_excluded(self):
        jd = '岗位职责 负责门店 任职要求 3年 四、我们可以为你提供 我们能为你提供 从区域经理到合伙人'
        self.assertEqual(self.grade(jd)['redlines'], [])

    def test_adjacent_bracket_sections_preserve_duties_only(self):
        self.assertEqual(self.grade('【岗位职责】外呼客户【福利待遇】合伙人路径')['redlines'], ['外呼'])

    def test_company_heading_at_document_start_is_excluded(self):
        self.assertEqual(self.grade('公司简介：创业氛围 岗位职责 拓展 任职要求 5年')['redlines'], [])

    def test_excluded_section_number_markers_allow_whitespace(self):
        for marker in ('一、', '十一、 ', '1、', '12. ', '3．\t'):
            with self.subTest(marker=marker):
                self.assertEqual(self.grade('岗位职责 负责门店 ' + marker + '福利待遇 合伙人')['redlines'], [])

    def test_excluded_heading_still_requires_trailing_delimiter(self):
        self.assertEqual(self.grade('岗位职责 负责门店\n公司福利政策要求外呼')['redlines'], ['外呼'])

    def test_send_and_grade_use_same_redlines(self):
        for jd, redlines, result in (
            ('岗位职责 负责门店\n我们提供 合伙人', [], 'error: unknown_conversation'),
            ('岗位职责 外呼\n我们提供 合伙人', ['外呼'], 'skip_jd_redline'),
            ('任职要求 无底薪', ['无底薪'], 'skip_jd_redline'),
            ('每天外呼', ['外呼'], 'skip_jd_redline'),
        ):
            with self.subTest(jd=jd), patch.object(O.SS, 'ledger_guard'), \
                    patch.object(O, 'inspect_job', return_value=('ready', jd)), \
                    patch.object(O, 'job_identity_matches', return_value=False):
                self.assertEqual(self.grade(jd)['redlines'], redlines)
                self.assertEqual(O.send_one(Mock(spec=[]), 1, dict(self.job), self.cfg), result)

    def test_no_direct_full_jd_text_block_match(self):
        source = Path(B.__file__).read_text(encoding='utf-8')
        self.assertNotIn('text_block"), jd)', source)

    def test_rules_version_changes_fingerprint(self):
        original = HF.rule_fingerprint(self.cfg)
        self.assertTrue(hasattr(HF, 'GRADE_RULES_VERSION'))
        with patch.object(HF, 'GRADE_RULES_VERSION', HF.GRADE_RULES_VERSION + 1):
            self.assertNotEqual(HF.rule_fingerprint(self.cfg), original)
        self.assertEqual(HF.rule_fingerprint(self.cfg), original)


if __name__ == '__main__':
    unittest.main()
