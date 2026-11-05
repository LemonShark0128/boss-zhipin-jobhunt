"""发资料承诺与待办：全部状态和依赖均离线隔离。"""
import contextlib
import io
import json
import os
import sys
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..', 'scripts')))
import chat_dom as CD
import boss_hunt as B
import hunt_config as HC
import hunt_state as HS
import outbound as O
import reply as R


class ResumeFollowupTest(unittest.TestCase):
    def setUp(self):
        root = tempfile.TemporaryDirectory()
        self.addCleanup(root.cleanup)
        self.cfg = {
            'paths': {name: os.path.join(root.name, value) for name, value in (
                ('workdir', '.'), ('state', 'state.json'), ('raw_dir', 'raw'),
                ('human_replies', 'human.json'), ('log', 'runs.log'))},
            'inbox': {'auto_reply': {'enabled': True, 'daily_cap': 40},
                      'interview_auto_reply': False},
        }
        self.st = HS.load_state(self.cfg)

    def run_inbox(self, key, hr='发份简历', result='replied', resume_result='sent'):
        row = {'conv_key': key, 'brand': '示例公司', 'boss': '王女士',
               'title': '城市经理', 'text': '王女士 示例公司 城市经理'}
        browser = Mock()
        browser.network.return_value = {'text': json.dumps({'code': 0, 'zpData': {'result': []}})}
        scan = dict(checked=0, failed=0, skipped_failures=0, deadline=False)
        out = []
        with patch.object(B, 'scan_conv_states', return_value=scan), \
             patch.object(CD, 'goto_tab', return_value=True), \
             patch.object(CD, 'conversation_rows', return_value=[row]), \
             patch.object(CD, 'open_by_index', return_value=(
                 'ok', row['text'], [{'who': 'friend', 'text': hr}])), \
             patch.object(B.CA, 'send_resume', return_value=resume_result), \
             patch.object(B, 'send_in_chat', return_value=result), \
             patch.object(HS, 'remember_reply'):
            res = B.inbox(self.cfg, browser, 1, self.st, out)
        res['_out'] = out
        return res

    def test_llm_resume_promise_keeps_source_and_intent(self):
        self.cfg['inbox']['auto_reply']['use_llm'] = True
        text = '行，我整理一份发您，方便说下区域吗？'
        with patch.object(R.G, '_generate_raw', return_value=(text, '')):
            result = R.generate(self.cfg, {}, '发份简历')
        self.assertEqual((result['text'], result['source'], result['intent']),
                         (text, 'llm', 'resume'))

    def test_resume_request_sends_attachment_instead_of_promising(self):
        invite = self.run_inbox('id:invite', '方便聊聊吗')
        self.assertEqual(invite['auto_replied'][0]['intent'], 'invite')
        self.assertFalse(self.st.get('resume_followups'))
        # 要简历 → 直接点 BOSS 自带的「发附件简历」：不发文字、不留「待我发」待办
        sent = self.run_inbox('id:resume')
        self.assertFalse(sent.get('auto_replied'))
        self.assertTrue(any('📎 发简历' in line for line in sent['_out']))
        self.assertFalse(HS.load_state(self.cfg).get('resume_followups'))
        # 发简历失败才退回文字 + 待办，不能既没发出去又没吭声
        failed = self.run_inbox('id:resume_fail', resume_result='error:no_resume_button')
        self.assertEqual(failed['auto_replied'][0]['intent'], 'resume')
        saved = HS.load_state(self.cfg)['resume_followups']
        self.assertEqual([r['conv_key'] for r in saved], ['id:resume_fail'])
        self.assertRegex(saved[0]['ts'], r'^\d{4}-\d{2}-\d{2} \d{2}:\d{2}$')
        self.assertEqual(saved[0]['reply'], R.RESUME_TEMPLATE)

    def test_followups_keep_latest_thirty(self):
        for i in range(35):
            self.run_inbox('id:%d' % i, resume_result='error:no_resume_button')
        saved = HS.load_state(self.cfg)['resume_followups']
        self.assertEqual(len(saved), 30)
        self.assertEqual([r['conv_key'] for r in saved], ['id:%d' % i for i in range(5, 35)])

    def test_resume_or_attachment_word_still_rejected_and_falls_back(self):
        self.cfg['inbox']['auto_reply']['use_llm'] = True
        for word in ('简历', '附件'):
            text = '行，我整理一份%s发您，方便说下区域吗？' % word
            with self.subTest(word=word), patch.object(R.G, '_generate_raw', return_value=(text, '')):
                self.assertIn('承诺简历', R.check(text, self.cfg)[1])
                result = R.generate(self.cfg, {}, '发份简历')
                self.assertEqual((result['source'], result['intent']), ('template', 'resume'))
                self.assertEqual(result['text'], R.RESUME_TEMPLATE)
                self.assertIn('承诺简历', result['reasons'])
                self.assertTrue(R.check(result['text'], self.cfg)[0])


if __name__ == '__main__':
    unittest.main()
