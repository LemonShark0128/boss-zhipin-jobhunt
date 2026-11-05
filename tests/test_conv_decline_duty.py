"""Duty-only decline matching, using fictional jobs and pure functions."""
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
import conversation as C
import hunt_filter as HF


class DeclineDutyTests(unittest.TestCase):
    def match(self, jd, **job):
        return C._decline_match({'decline_industries': ['金融', '助贷']},
                                job, {}, {}, jd)

    def test_degree_requirement_does_not_decline_data_sales(self):
        jd = '岗位职责：\n负责虚构金属数据产品销售\n任职要求：\n全日制本科，金融学相关专业'
        self.assertEqual(self.match(jd, brandName='虚构金属数据公司',
                                    jobName='KA大客户经理'), '')

    def test_loan_promotion_duty_still_declines(self):
        self.assertEqual(self.match('岗位职责：\n负责虚构助贷产品推广\n任职要求：本科'), '助贷')

    def test_description_stops_at_inline_requirement(self):
        self.assertEqual(self.match('职位描述：负责虚构数据销售 任职要求：金融学相关专业'), '')

    def test_unheaded_jd_does_not_decline(self):
        self.assertEqual(self.match('虚构金融产品销售'), '')

    def test_industry_still_declines_without_duty_evidence(self):
        self.assertEqual(self.match('虚构数据产品销售', brandIndustry='互联网金融'), '金融')

    def test_requirement_variants_need_section_evidence_for_short_heading(self):
        for heading in ('要求', '任职条件', '资格要求', '应聘要求', '我们希望你',
                        '你需要具备', '岗位任职要求'):
            for prefix in ('\n', '\n二、', '\n2. ', '【'):
                with self.subTest(heading=heading, prefix=prefix):
                    marker = prefix + heading + ('】' if prefix == '【' else '：')
                    result = HF.jd_duty_scope('岗位职责：虚构数据销售' + marker + '金融学相关专业')
                    self.assertNotIn('金融', result)
                    self.assertIn('虚构数据销售', result)
        for body in ('根据客户要求：开展虚构金融数据服务',
                     '配合虚构业务，要求：提供金融数据服务'):
            with self.subTest(body=body):
                self.assertEqual(HF.jd_duty_scope('岗位职责：' + body), body)

    def test_heading_wrappers_are_not_duty_content(self):
        for jd in ('【岗位职责】虚构数据销售【任职资格】金融学相关专业',
                   '【岗位职责】：\n虚构数据销售\n【任职资格】：金融学相关专业',
                   '岗位职责：虚构数据销售\n任职要求：金融学相关专业'):
            with self.subTest(jd=jd):
                self.assertEqual(HF.jd_duty_scope(jd), '虚构数据销售')

    def test_duty_scope_sections_and_boundaries(self):
        scope = getattr(HF, 'jd_duty_scope', None)
        self.assertTrue(callable(scope), 'jd_duty_scope must be available')
        for heading in ('岗位职责', '工作职责', '工作内容', '岗位定位', '职位描述'):
            for excluded in ('任职要求', '任职资格', '岗位要求', '职位要求', '加分项',
                             '优先条件', '福利', '福利待遇', '公司介绍', '公司简介',
                             '关于我们', '企业介绍', '团队介绍', '薪资福利', '岗位福利',
                             '公司福利', '职业发展', '晋升路径', '发展空间', '我们提供'):
                with self.subTest(heading=heading, excluded=excluded):
                    jd = '%s：虚构数据销售\n%s：金融\n【工作内容】虚构数据服务' % (heading, excluded)
                    result = scope(jd)
                    self.assertIn('虚构数据销售', result)
                    self.assertIn('虚构数据服务', result)
                    self.assertNotIn('金融', result)
        for jd in ('', '虚构金融销售', '任职要求：金融', '公司介绍：金融'):
            with self.subTest(jd=jd):
                self.assertEqual(scope(jd), '')


if __name__ == '__main__':
    unittest.main()
