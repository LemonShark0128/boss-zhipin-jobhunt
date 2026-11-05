"""修法 3：离线替换 LLM，验证生成、兜底与原有闸门。"""
import os
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..', 'scripts')))
import reply as R


class ReplyLLMTest(unittest.TestCase):
    def setUp(self):
        root = tempfile.TemporaryDirectory()
        self.addCleanup(root.cleanup)
        self.cfg = {
            'paths': {'human_replies': os.path.join(root.name, 'human.json')},
            'inbox': {'auto_reply': {'use_llm': True}},
            'user': {'greeting': {'facts': ['带过5人团队']}},
        }
        self.job = {'encryptJobId': 'llm-test', 'jobName': '城市经理'}

    def test_qualified_reply_uses_llm_and_intent_prompt(self):
        text = '可以的，方便说下这个岗位负责哪个区域吗？'
        for hr, intent, directions in (
            ('方便聊聊吗', 'invite', ('详细介绍', '工作内容')),
            ('发份简历', 'resume', ('岗位重点', '区域')),
        ):
            with self.subTest(intent=intent), patch.object(R.G, '_generate_raw', return_value=(text, '')) as raw:
                history = [{'who': 'friend', 'text': hr}]
                result = R.generate(self.cfg, self.job, hr, history=history)
                self.assertEqual(result['source'], 'llm')
                self.assertEqual(result['text'], text)
                self.assertTrue(R.check(result['text'], self.cfg)[0])
                self.assertEqual(result['intent'], intent)
                prompt = raw.call_args.args[1]
                for token in ('带过5人团队', hr, '城市经理', '15-60', '您好', '感谢', '贵司',
                              '本人', '该岗位', '数字', '问句') + directions:
                    self.assertIn(token, prompt)

    def test_invalid_reply_falls_back_with_reasons(self):
        for text in (
            '带过5人团队，方便说下负责区域吗？',
            '可以的，贵司这个岗位负责哪个区域呢？',
            '可以的，我对这个工作机会很感兴趣。',
            '可以，负责哪个区域？',
            '我在火星公司负责销售，岗位负责哪个区域？',
            '可以的，我能到岗，岗位负责哪个区域？',
        ):
            for hr in ('方便聊聊吗', '发份简历'):
                with self.subTest(text=text, hr=hr), patch.object(R.G, '_generate_raw', return_value=(text, '')):
                    result = R.generate(self.cfg, self.job, hr)
                    self.assertEqual(result['source'], 'template')
                    self.assertTrue(result['reasons'])
                    self.assertTrue(R.check(result['text'], self.cfg)[0])
                    if hr == '发份简历':
                        self.assertIn('整理一下发您', result['text'])

    def test_failure_or_timeout_falls_back(self):
        for error in (RuntimeError('failed'), TimeoutError('timeout')):
            with self.subTest(error=error), patch.object(R.G, '_generate_raw', side_effect=error):
                result = R.generate(self.cfg, self.job, '方便聊聊吗')
                self.assertEqual(result['source'], 'template')
                self.assertTrue(result['text'])
                self.assertIn('llm_error', result['reasons'])
        with patch.object(R.G, '_generate_raw', return_value=(None, 'llm_unreachable:timeout')):
            result = R.generate(self.cfg, self.job, '方便聊聊吗')
            self.assertEqual(result['source'], 'template')
            self.assertTrue(result['reasons'])

    def test_disabled_or_missing_switch_never_calls_llm(self):
        for switch in ({'use_llm': False}, {}, {'use_llm': 'false'}):
            self.cfg['inbox']['auto_reply'] = switch
            with self.subTest(switch=switch), patch.object(R.G, '_generate_raw') as raw:
                result = R.generate(self.cfg, self.job, '方便聊聊吗')
                self.assertEqual(result['source'], 'template')
                self.assertTrue(result['text'])
                raw.assert_not_called()

    def test_manual_gate_never_calls_llm(self):
        for hr in ('聊聊？base 在成都', '方便聊聊吗？单休能接受吗',
                   '期望薪资多少', '明天方便面试吗'):
            for history in (None, [{'who': 'friend', 'text': hr}]):
                with self.subTest(hr=hr, history=history), patch.object(R.G, '_generate_raw') as raw:
                    self.assertFalse(R.self_contained(hr)[0])
                    self.assertFalse(R.judge([{'who': 'friend', 'text': hr}])[0])
                    result = R.generate(self.cfg, self.job, hr, history=history)
                    self.assertEqual(result['source'], 'manual_required')
                    self.assertEqual(result['text'], '')
                    raw.assert_not_called()


if __name__ == '__main__':
    unittest.main()
