"""Offline acceptance cases; all employers and candidate facts are fictional."""
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
import greet as G


class GreetGateConflictsTests(unittest.TestCase):
    def setUp(self):
        self.cfg = {'user': {'greeting': {'facts': [
            '在甲平台做城市经理，新签商户 200+ 家',
            '在乙零售带团队，业绩 +30%',
        ]}}}

    def greeting(self, name, extra=''):
        return ('您好，看到你们在招' + name + '。'
                '我在甲平台做城市经理，新签商户 200+ 家。'
                '我在乙零售带团队，业绩 +30%。你们这个岗位看重商户开拓。' + extra + G.CLOSINGS[1])

    def assert_no_reason(self, reasons, *prefixes):
        self.assertFalse([r for r in reasons if r.startswith(prefixes)], reasons)

    def test_display_names_strip_annotations_salary_and_marketing(self):
        display = getattr(G, 'display_job_name', None)
        self.assertTrue(callable(display), 'display_job_name must be available')
        cases = [
            ('甲生鲜正编-鲜果BDM主管-高提成·全国出差', '甲生鲜正编-鲜果BDM主管'),
            ('示例城餐饮区域经理15~25k', '示例城餐饮区域经理'),
            ('区域零售高级经理（示例大区）(J13893)', '区域零售高级经理'),
            ('高奢渠道拓展经理-甲品牌 （A227996）', '高奢渠道拓展经理-甲品牌'),
            ('月3w+甲平台/乙平台专项/丙示例储备BDM', '甲平台/乙平台专项/丙示例储备BDM'),
            ('城市经理15-25K', '城市经理'),
            ('城市经理 年薪30-50万', '城市经理'),
            ('城市经理－编号123', '城市经理'),
            ('城市经理|急招', '城市经理'),
            ('城市经理｜双休', '城市经理'),
            ('城市经理【五险包住】', '城市经理'),
            ('渠道经理-甲事业部', '渠道经理-甲事业部'),
            ('（J123）', '（J123）'),
        ]
        for raw, expected in cases:
            with self.subTest(raw=raw):
                self.assertEqual(display({'jobName': raw}), expected)
        self.assertEqual(display({}), '')

    def test_raw_job_numbers_are_ignored_but_name_is_recognized(self):
        for name in ('城市经理（J13893）', '城市经理15~25k',
                     '城市经理（J11）(A22)15~25k-编号33'):
            with self.subTest(name=name):
                ok, reasons = G.check(self.greeting(name), self.cfg, {'jobName': name})
                self.assertTrue(ok, reasons)

    def test_name_variants_and_brand_are_recognized_and_excluded(self):
        job = {'jobName': '城市经理（J13893）-全国出差', 'brandName': '甲示例公司2026'}
        for name in ('城市经理', '城市经理-全国出差', '甲示例公司2026'):
            with self.subTest(name=name):
                reasons = G.check(self.greeting(name), self.cfg, job)[1]
                self.assert_no_reason(reasons, 'no_job_or_brand')
        for name in ('城市经理', job['brandName']):
            text = self.greeting(name)
            self.assertTrue(G.check(text, self.cfg, job)[0], G.check(text, self.cfg, job)[1])
            self.assertTrue(G.basic_safety(text, self.cfg, job)[0])
            self.assertTrue(G.fact_binding(text, self.cfg, job)[0])

    def test_zero_to_one_idioms_do_not_count_as_metrics(self):
        for idiom in ('从0到1', '从 0 到 1', '0-1', '0 到 1', '0~1'):
            with self.subTest(idiom=idiom):
                text = self.greeting('城市经理', idiom + ' 开拓。')
                reasons = G.check(text, self.cfg, {'jobName': '城市经理'})[1]
                self.assert_no_reason(reasons, 'numbers_not_in_facts', 'too_many_numbers',
                                      'number_metric_not_in_facts')
                self.assertTrue(G.fact_binding(idiom + ' 开拓', self.cfg)[0])
                sparse = '看到你们在招城市经理。' + idiom + ' 开拓。' + G.CLOSINGS[1]
                reasons = G.check(sparse, self.cfg, {'jobName': '城市经理'})[1]
                self.assertIn('too_few_numbers:0', reasons)

    def test_title_conditions_are_not_candidate_commitments(self):
        job = {'jobName': '甲生鲜正编-鲜果BDM主管-高提成·全国出差'}
        text = self.greeting(job['jobName'])
        self.assertTrue(G.check(text, self.cfg, job)[0], G.check(text, self.cfg, job)[1])
        self.assertTrue(G.basic_safety(text, self.cfg, job)[0])
        self.assertIn('manual_required', G.check(text + '可以接受全国出差。', self.cfg, job)[1])
        self.assertIn('manual_required', G.basic_safety(text + '可以接受全国出差。', self.cfg, job)[1])

    def test_unbound_summary_stays_rejected_with_actionable_feedback(self):
        clause = '我在甲平台和乙零售都跑过这套'
        reasons = G.check(self.greeting('城市经理', clause + '。'), self.cfg,
                          {'jobName': '城市经理'})[1]
        self.assertIn('fact_not_bound:' + clause, reasons)
        feedback = G._feedback(clause, reasons, G.block(self.cfg))
        self.assertIn('找不到对应条目', feedback)
        self.assertIn(clause, feedback)
        self.assertIn('不要写出差、薪资、到岗、面试时间相关的意愿或承诺',
                      G._feedback('', ['manual_required'], G.block(self.cfg)))

    def test_fabricated_numbers_stay_rejected(self):
        text = self.greeting('城市经理').replace('200+', '999')
        reasons = G.check(text, self.cfg, {'jobName': '城市经理'})[1]
        self.assertIn('numbers_not_in_facts:999', reasons)
        self.assertFalse(G.fact_binding(text, self.cfg)[0])

    def test_prompt_uses_display_name_and_forbids_unbound_summaries(self):
        raw = '城市经理（J13893）15~25k-全国出差'
        prompt = G.build_prompt(self.cfg, {'jobName': raw}, closing=G.CLOSINGS[1])
        self.assertIn('看到你们在招城市经理，', prompt)
        self.assertNotIn(raw, prompt)
        self.assertIn('不要写『我在A和B都做过/跑过…』这类概括句', prompt)

    def test_unknown_employer_claims_rejected_with_or_without_job(self):
        job = {'jobName': '城市经理', 'brandName': '丁示例平台'}
        claims = [
            '我在丁示例平台带过城市团队，负责过区域拓展',
            '我在丁示例平台做过城市经理，新签商户 200+ 家',
            '我在丙超市带团队，业绩 +30%',
            '在丙超市做城市经理，新签商户 200+ 家',
            '我负责丁示例平台的区域拓展',
        ]
        for text in claims:
            for context in (None, job):
                if text == claims[-1] and context is None:
                    continue  # 无「在X做」结构时，靠 job 提供待核对的品牌名。
                for gate in (G.fact_binding, G.basic_safety):
                    with self.subTest(text=text, job=context, gate=gate.__name__):
                        ok, reasons = gate(text, self.cfg, context)
                        self.assertFalse(ok, reasons)
                        self.assertTrue(any(r.startswith(('org_not_in_facts:',
                                                         'fact_not_bound:'))
                                            for r in reasons), reasons)

    def test_original_employers_and_recruiter_references_remain_safe(self):
        job = {'jobName': '城市经理', 'brandName': '丁示例平台'}
        for text in (
            '在甲平台做城市经理，新签商户 200+ 家',
            '在乙零售带团队，业绩 +30%',
            '看到你们在招城市经理，这行我做了7年',
            '看到你们在招丁示例平台城市经理，你们这个岗位看重区域拓展',
        ):
            for gate in (G.fact_binding, G.basic_safety):
                with self.subTest(text=text, gate=gate.__name__):
                    ok, reasons = gate(text, self.cfg, job)
                    self.assertTrue(ok, reasons)
        known_job = {'jobName': '城市经理', 'brandName': '甲平台'}
        self.assertTrue(G.basic_safety('我在甲平台做城市经理', self.cfg, known_job)[0])

    def test_recruiter_brand_anywhere_in_my_experience_is_rejected(self):
        job = {'jobName': '城市经理', 'brandName': '丁示例'}
        for text in (
            '丁示例那边我也带过团队',
            '我之前也在丁示例干过',
            '丁示例的活我都做过',
            '丁示例那边我也当过经理',
            '丁示例那边我也待过',
            '丁 示例那边我也带过团队',
        ):
            for gate in (G.fact_binding, G.basic_safety):
                with self.subTest(text=text, gate=gate.__name__):
                    ok, reasons = gate(text, self.cfg, job)
                    self.assertFalse(ok, reasons)
                    self.assertIn('org_not_in_facts:' + text.replace(' ', ''), reasons)

    def test_recruiter_brand_without_my_experience_remains_safe(self):
        job = {'jobName': '城市经理', 'brandName': '丁 示例'}
        for text in (
            '看到你们在招城市经理，这行我做了7年',
            '你们丁示例这个岗位看重区域拓展',
            '我关注丁示例这个岗位',
            '丁示例在招城市经理，我在乙零售带团队',
        ):
            for gate in (G.fact_binding, G.basic_safety):
                with self.subTest(text=text, gate=gate.__name__):
                    ok, reasons = gate(text, self.cfg, job)
                    self.assertTrue(ok, reasons)

    def test_recruiter_brand_in_fictional_facts_remains_safe(self):
        cfg = {'user': {'greeting': {'facts': ['在丁 示例做BD']}}}
        job = {'jobName': '城市经理', 'brandName': '丁 示例'}
        for gate in (G.fact_binding, G.basic_safety):
            with self.subTest(gate=gate.__name__):
                ok, reasons = gate('我在丁示例做BD', cfg, job)
                self.assertTrue(ok, reasons)

    def test_numeric_ranges_keep_both_endpoints_for_provenance(self):
        for text in ('团队规模10-15人', '团队规模10到15人', '团队规模10~15人',
                     '团队规模从0到15人', '团队规模从10到1人'):
            with self.subTest(text=text):
                reasons = G.check(self.greeting('城市经理', text + '。'), self.cfg,
                                  {'jobName': '城市经理'})[1]
                missing = next(r for r in reasons if r.startswith('numbers_not_in_facts:'))
                endpoints = missing.split(':', 1)[1].split(',')
                if '10' in text:
                    self.assertIn('10', endpoints)
                if '15' in text:
                    self.assertIn('15', endpoints)
                self.assertFalse(G.fact_binding(text, self.cfg)[0])
        for text in ('从0到1开拓', '0-1 开拓'):
            with self.subTest(text=text):
                self.assertTrue(G.fact_binding(text, self.cfg)[0])


class GreetTermBindingTests(unittest.TestCase):
    """greet-01d: category-prefixed, fictional candidate facts only."""

    def setUp(self):
        self.cfg = {'user': {'greeting': {'facts': [
            '现任甲出行 BDM（示例类目）：承接区域 GMV 与新签指标，'
            '任内累计新签商户 90+ 家，核心 KA 覆盖率 60%',
            '乙零售城市经理（丙城/丁城）：一年内新增合作商家 70+ 家，'
            '团队业绩 +12%',
            '核心能力：KA 商务谈判、商户分层运营与渠道拓展',
        ]}}}
        self.job = {'jobName': '城市经理', 'brandName': '己平台'}

    def test_known_places_and_terms_pass_both_gates(self):
        for text in (
            '在乙零售丙城做城市经理',
            '我在甲出行跑过KA商务谈判',
            '我在甲出行做过KA商务谈判和商户分层运营',
            '我在丙城做过城市经理',
            '我在乙零售的丙城那边做过城市经理',
            '我在甲出行接过ka 商务谈判',
            '我在甲出行主导过KA商务谈判与商户分层运营',
        ):
            for gate in (G.fact_binding, G.basic_safety):
                with self.subTest(text=text, gate=gate.__name__):
                    self.assertEqual(gate(text, self.cfg, self.job), (True, []))

    def test_unknown_terms_places_and_cross_experience_summary_stay_rejected(self):
        for text, reason in (
            ('我在甲出行做过直播带货', 'fact_not_bound:'),
            ('我在戊超市做过KA商务谈判', 'org_not_in_facts:'),
            ('我在甲出行戊城做过KA商务谈判', 'org_not_in_facts:'),
            ('我在戊超市跑过KA商务谈判', 'org_not_in_facts:'),
            ('我在甲出行做过KA商务谈判和直播带货', 'fact_not_bound:'),
            ('我在甲出行和乙零售都跑过这套', 'fact_not_bound:'),
            ('我在甲出行主导过己平台的KA商务谈判', 'org_not_in_facts:'),
        ):
            for gate in (G.fact_binding, G.basic_safety):
                with self.subTest(text=text, gate=gate.__name__):
                    ok, reasons = gate(text, self.cfg, self.job)
                    self.assertFalse(ok, reasons)
                    self.assertIn(reason + text, reasons)

    def test_keyword_binding_does_not_relax_numeric_alignment(self):
        for text in ('我在甲出行新签商户 300+ 家',
                     '我在甲出行做过KA商务谈判 300 次',
                     '我在甲出行做过KA商务谈判 200 次'):
            for gate in (G.fact_binding, G.basic_safety):
                with self.subTest(text=text, gate=gate.__name__):
                    ok, reasons = gate(text, self.cfg, self.job)
                    self.assertFalse(ok, reasons)
                    self.assertTrue(any(r.startswith('number_metric_not_in_facts:')
                                        for r in reasons), reasons)


if __name__ == '__main__':
    unittest.main()
