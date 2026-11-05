"""离线回归：邀约/要简历意图、安全余句、模板质量和跨进程稳定性。"""
import json
import contextlib
import io
import os
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

HERE = os.path.dirname(os.path.abspath(__file__))
SCRIPTS = os.path.abspath(os.path.join(HERE, os.pardir, 'scripts'))
sys.path.insert(0, SCRIPTS)

import reply as R
import reply_eval as E


class ReplyGateTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.cfg = {'paths': {'human_replies': os.path.join(self.tmp.name, 'human.json')}}

    def test_fixture_gates_and_intents(self):
        for case in E.load_cases():
            with self.subTest(id=case['id']):
                history = [{'who': 'friend', 'text': case['hr']}]
                want = case['expect'] == 'auto'
                for result in (R.self_contained(case['hr']), R.judge(history)):
                    self.assertEqual(result[0], want)
                    if want:
                        self.assertEqual(result[1], 'resume' if case['id'].startswith('resume') else 'invite')

    def test_mixed_conditions_remain_manual(self):
        for text in (
            '这个岗位聊聊薪资？', '方便聊聊，单休能接受吗',
            '欢迎聊聊，需要自备车辆', '有兴趣吗？试用期有考核',
            '聊聊？需要外呼', '方便沟通，需要陌拜',
            '有兴趣吗，工作地点在上海', '方便聊聊，时间确定了吗',
            '聊聊，需要带团队5人', '薪资可以聊聊',
            '你好，上海能来吗？', '你好，我们在招城市经理',
        ):
            with self.subTest(text=text):
                self.assertFalse(R.self_contained(text)[0])
                self.assertFalse(R.judge([{'who': 'friend', 'text': text}])[0])
                self.assertEqual(R.generate(self.cfg, {}, text)['source'], 'manual_required')

    def test_prior_context_still_requires_manual(self):
        for text in ('期望薪资多少', '能接受单休吗', '介绍一下经历'):
            self.assertFalse(R.judge([{'who': 'friend', 'text': text},
                                      {'who': 'friend', 'text': '方便聊聊吗'}])[0])

    def test_resume_template_answers_request_and_passes_check(self):
        with patch.object(R.G, '_generate_raw', side_effect=AssertionError('unexpected LLM')):
            for text in ('方便的话发份简历', '简历发我一下', '发我一份简历'):
                with self.subTest(text=text):
                    result = R.generate(self.cfg, {}, text)
                    self.assertEqual(result['source'], 'template')
                    self.assertEqual(result.get('intent'), 'resume')
                    self.assertIn('整理', result['text'])
                    self.assertIn('发您', result['text'])
                    self.assertTrue(R.check(result['text'], self.cfg)[0])
                    self.assertNotRegex(result['text'], r'简历|附件|\d')

    def test_invite_template_stable_in_and_across_processes(self):
        job = {'encryptJobId': '稳定-job-42'}
        replies = [R.generate(self.cfg, job, '方便聊聊吗') for _ in range(5)]
        self.assertTrue(all(r['source'] == 'template' and r.get('intent') == 'invite' for r in replies))
        self.assertEqual(len({r['text'] for r in replies}), 1)
        code = ("import json,sys; sys.path.insert(0,sys.argv[1]); import reply as R; "
                "print(json.dumps([R.generate(json.loads(sys.argv[2]), {'encryptJobId':j}, "
                "'方便聊聊吗')['text'] for j in ['稳定-job-42','job-1','job-2']], ensure_ascii=False))")
        outputs = []
        for seed in ('1', '2', '123'):
            env = dict(os.environ, PYTHONHASHSEED=seed, PYTHONPYCACHEPREFIX='/tmp/pyc')
            outputs.append(json.loads(subprocess.check_output(
                [sys.executable, '-c', code, SCRIPTS, json.dumps(self.cfg)], env=env, text=True)))
        self.assertEqual(outputs[0][0], replies[0]['text'])
        self.assertTrue(all(o == outputs[0] for o in outputs))

    def test_eval_checks_template_relevance_offline(self):
        cases = E.load_cases()
        rows = E.run(cases, self.cfg)
        self.assertEqual(E.summarize(rows), {
            'total': len(E.load_cases()), 'pass': len(E.load_cases()), 'miss': 0,
            'false_auto': 0, 'false_manual': 0,
            'flagged': 0, 'false_auto_ids': [], 'false_manual_ids': [], 'flagged_ids': []})
        self.assertTrue(all(r['source'] == 'template' and r['text'] for r in rows if r['gate'] == 'auto'))
        resume = next(c for c in cases if c['id'] == 'resume-01')
        self.assertTrue(E.template_mismatch(resume, '方便，岗位负责哪个区域？', 'template'))

    def test_default_eval_cli_needs_no_real_config_or_llm(self):
        output = io.StringIO()
        with patch.object(sys, 'argv', ['reply_eval.py']), \
                patch.object(E.HC, 'load_config', side_effect=AssertionError('real config read')), \
                patch.object(R.G, '_generate_raw', side_effect=AssertionError('unexpected LLM')), \
                contextlib.redirect_stdout(output):
            self.assertEqual(E.main(), 0)
        self.assertIn('通过 %d' % len(E.load_cases()), output.getvalue())
        self.assertIn('答非所问 0', output.getvalue())


if __name__ == '__main__':
    unittest.main()
