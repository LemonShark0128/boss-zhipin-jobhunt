"""BOSS「PK 情况」系统气泡识别（2026-11-01 真机取证）。

真机事实：这类气泡的 class 是 `message-item item-friend`，与 HR 真人消息同 class，
所以只能靠文案特征识别；而「系统文案 + 真人指令」必须留给真人流程，不能当前缀吞掉。
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'scripts'))

import conv_parse as CP

PK_BUBBLE = ('你与该职位竞争者PK情况\n\n共人投递，你超过竞争者\n\n'
             '优秀竞争者会，建议你\n\n查看详细分析')
FRIEND_CLASS = 'message-item item-friend'


class SystemPatternTests(unittest.TestCase):

    def test_pk_bubble_is_system_despite_friend_class(self):
        self.assertTrue(CP.is_system_message(
            {'classes': FRIEND_CLASS, 'text': PK_BUBBLE}))

    def test_system_copy_plus_real_instruction_stays_human(self):
        msg = {'classes': FRIEND_CLASS, 'text': '你与该职位竞争者PK情况，请发简历'}
        self.assertFalse(CP.is_system_message(msg))

    def test_normal_greeting_is_not_system(self):
        msg = {'classes': FRIEND_CLASS,
               'text': '你好，看了你的简历，方便聊聊吗？我们主要做虚构品牌线下广告置换投放的，周末双休，不加班'}
        self.assertFalse(CP.is_system_message(msg))

    def test_config_can_extend_patterns(self):
        cfg = {'conversation': {'system_patterns': [r'^系统提示：']}}
        pats = CP.system_patterns(cfg)
        self.assertTrue(CP.is_system_message(
            {'classes': FRIEND_CLASS, 'text': '系统提示：请勿线下交易'},
            system_texts=CP.system_message_texts(cfg), system_pats=pats))

    def test_js_parser_emits_regex_guard(self):
        js = CP.system_message_parser_js()
        self.assertIn('new RegExp(p)', js)
        self.assertIn('PK', js)


if __name__ == '__main__':
    unittest.main()
