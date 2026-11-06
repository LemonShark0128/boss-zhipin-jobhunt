"""Conversation browser and model pieces, using fictional data and substitutes."""
import ast
from itertools import chain, repeat
import json
import sys
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
import boss_hunt as B
import chat_dom as CD
import conv_actions as A
import conv_llm as L
import conv_rules as R
import send_safety as S


class ConversationPieces(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.cfg = {'paths': {'workdir': self.tmp.name, 'state': self.tmp.name + '/state.json'},
                    'user': {'greeting': {'facts': ['我做过虚构行业的客户维护。']},
                             'interview_reply': R.INTERVIEW_REPLY}, 'conversation': {},
                    'schedule': {'timezone': 'UTC'}, 'sending': {'min_gap_seconds': 0}}
        self.job = {'encryptJobId': 'fictional-job', 'jobName': '虚构客户经理'}
        self.before = {'conv_key': 'id:fiction', 'messages': [{'id': 'f1', 'who': 'friend', 'text': '你好'}]}
        self.br = Mock()
        self.br.click.return_value = {}
        self.sleep = patch.object(A.time, 'sleep').start()
        self.addCleanup(patch.stopall)

    def send_text(self, template=None, opener='', text=None, active=None):
        template = R.DEFAULT_TEMPLATES['round2'] if template is None else template
        msg = opener + template if text is None else text
        after = {'conv_key': 'id:fiction', 'messages': self.before['messages'] + [{'id': 'm2', 'who': 'mine', 'text': msg}]}
        with patch.object(CD, 'active_conversation', side_effect=active or [self.before, after]), \
             patch.object(A.CS, 'type_into_chat', return_value={'ok': True}) as fill, \
             patch.object(CD, 'atomic_chat_click', return_value={'ok': True, 'clicked': True, 'conv_key': 'id:fiction'}) as click:
            result = A.send_text(self.br, 1, self.cfg, 'id:fiction', self.job, msg, template, opener)
            return result, fill.call_count, click.call_count

    def test_text_templates_and_ledger(self):
        self.assertEqual(self.send_text()[0], 'sent')
        self.assertEqual(S.ledger_latest(self.cfg)[0]['cmd'], 'conv_text')
        self.assertEqual(self.send_text(R.DEFAULT_TEMPLATES['round2'] + '呀'), ('send_aborted:template_mismatch', 0, 0))
        self.assertEqual(self.send_text(text='篡改')[0], 'send_aborted:text_mismatch')
        singles = ''.join(R.DEFAULT_TEMPLATES[k] for k in R.ANSWER_KEYS)
        self.assertEqual(self.send_text(singles)[0], 'sent')
        self.cfg['conversation']['templates'] = {'round2': '虚构定制问题？'}
        self.assertEqual(self.send_text('虚构定制问题？')[0], 'sent')
        self.cfg['conversation']['templates']['round1'] = '虚构第一轮？'
        self.assertEqual(self.send_text(R.DEFAULT_TEMPLATES['round1'])[0], 'send_aborted:template_mismatch')

    def test_unsafe_opener_identity_and_last_turn(self):
        for opener in ['我做过9999万元业绩。', '接受出差', '字' * 61]:
            self.assertEqual(self.send_text(opener=opener), ('manual_required', 0, 0))
        result = self.send_text(active=[{'conv_key': 'id:other', 'messages': []}])
        self.assertIn('unknown_conversation', result[0]); self.assertEqual(result[1:], (0, 0))
        self.before['messages'][-1]['who'] = 'mine'
        self.assertEqual(self.send_text()[0], 'last_turn_is_ours')

    def resume(self, located=None, dialog=None, after=None, receipt_after_confirm=False):
        self.br.json.side_effect = chain([located or {'ok': True}], repeat(dialog or {}))
        initial = [self.before] * (9 if receipt_after_confirm else 1)
        with patch.object(CD, 'active_conversation', side_effect=chain(initial, repeat(after or self.before))), \
             patch.object(A.HC, 'trace') as trace:
            result = A.send_resume(self.br, 1, self.cfg, 'id:fiction', 'fictional-job')
            return result, trace

    def test_resume_button_count_and_dry(self):
        for count in [0, 2]:
            result, _ = self.resume({'ok': False, 'reason': 'resume_button', 'count': count})
            self.assertEqual(result, 'send_aborted:resume_button')
        self.br.click.assert_not_called()
        self.cfg['conversation']['dry'] = True
        self.assertEqual(self.resume()[0], 'dry_ok')
        self.br.click.assert_not_called()
        self.assertFalse(S.ledger_latest(self.cfg))

    def test_resume_dry_only_locates_even_with_unresolved_ledger(self):
        self.cfg['conversation']['dry'] = True
        with patch.object(S, 'ledger_guard', side_effect=AssertionError('dry must only locate')), \
             patch.object(S, 'ledger_latest', side_effect=AssertionError('dry must only locate')):
            self.assertEqual(self.resume()[0], 'dry_ok')
        self.br.click.assert_not_called()
        self.assertFalse((Path(self.tmp.name) / '.lock').exists())

    def test_resume_confirmation_trace_and_duplicate(self):
        after = {'conv_key': 'id:fiction', 'messages': self.before['messages'] + [{'id': 'r2', 'who': 'mine', 'text': '附件简历'}]}
        result, trace = self.resume(dialog={'html': '<div>虚构简历确认</div>', 'confirm': True}, after=after)
        self.assertEqual(result, 'sent')
        self.assertEqual([call.args[1] for call in self.br.click.call_args_list], ['#bh-conv-resume'])
        self.assertIn('<div>虚构简历确认</div>', str(trace.call_args_list))
        self.assertEqual(A.send_resume(self.br, 1, self.cfg, 'id:fiction', 'fictional-job'), 'duplicate')
        self.assertEqual(self.br.click.call_count, 1)
        self.assertEqual(S.ledger_latest(self.cfg)[0]['cmd'], 'conv_resume')

    def test_resume_confirms_once_only_after_waiting_without_receipt(self):
        after = {'conv_key': 'id:fiction', 'messages': self.before['messages'] + [
            {'id': 'r2', 'who': 'unknown', 'text': '简历已发送'}]}
        result, trace = self.resume(dialog={'html': '<div>虚构简历确认</div>', 'confirm': True},
                                    after=after, receipt_after_confirm=True)
        self.assertEqual(result, 'sent')
        self.assertEqual([call.args[1] for call in self.br.click.call_args_list],
                         ['#bh-conv-resume', '#bh-conv-resume-confirm'])
        self.assertIn('<div>虚构简历确认</div>', str(trace.call_args_list))
        self.assertEqual(S.ledger_latest(self.cfg)[0]['status'], 'sent')

    def test_resume_without_dialog(self):
        after = {'conv_key': 'id:fiction', 'messages': self.before['messages'] + [{'id': 'r2', 'who': 'mine', 'text': '简历已发送'}]}
        self.assertEqual(self.resume(after=after)[0], 'sent')
        self.assertEqual(self.br.click.call_count, 1)

    def test_resume_pending_never_reclicks(self):
        self.assertEqual(self.resume()[0], 'pending_verification')
        self.assertEqual(S.ledger_latest(self.cfg)[0]['status'], 'pending')
        try:
            result = A.send_resume(self.br, 1, self.cfg, 'id:fiction', 'fictional-job')
            self.assertIn(result, {'duplicate', 'pending_verification'})
        except RuntimeError as exc:
            self.assertIn('unresolved_send', str(exc))
        self.assertEqual(self.br.click.call_count, 1)

    def test_resume_identity_mismatch(self):
        with patch.object(CD, 'active_conversation', return_value={'conv_key': 'other'}):
            self.assertIn('unknown_conversation', A.send_resume(self.br, 1, self.cfg, 'id:fiction', 'fictional-job'))
        self.br.click.assert_not_called()

    def test_extract_json_and_prompt_limit(self):
        history = [{'who': 'friend', 'text': '虚构消息%d' % i} for i in range(5)]
        data = dict.fromkeys(R.ANSWER_KEYS); data['requirements'] = '甲' * 45
        for raw in [json.dumps(data), '```json\n' + json.dumps(data) + '\n```']:
            with patch.object(L.G, '_generate_raw', return_value=(raw, '')) as model:
                result = L.extract(self.cfg, history)
            self.assertEqual(result['requirements'], '甲' * 40)
            self.assertIsNone(result['online_interview'])
            prompt = model.call_args.args[1]
            self.assertNotIn('虚构消息0', prompt); self.assertIn('虚构消息4', prompt)
        for raw in ['not json', '[]', '{"requirements": {}}']:
            with patch.object(L.G, '_generate_raw', return_value=(raw, '')):
                with self.assertRaises(ValueError): L.extract(self.cfg, history)

    def test_opener_retries_and_cache(self):
        with patch.object(L.G, '_generate_raw', return_value=('我做过9999万元业绩。', '')) as model:
            self.assertEqual(L.opener(self.cfg, self.job, '虚构JD'), '')
            self.assertEqual(model.call_count, 2)
        with patch.object(L.G, '_generate_raw', return_value=('我做过虚构行业的客户维护。', '')) as model:
            self.assertEqual(L.opener(self.cfg, self.job, '虚构JD'), '我做过虚构行业的客户维护。')
            self.assertEqual(L.opener(self.cfg, self.job, '虚构JD'), '我做过虚构行业的客户维护。')
            self.assertEqual(model.call_count, 1)
            L.opener(self.cfg, self.job, '另一份虚构JD')
            self.assertEqual(model.call_count, 2)
        self.assertTrue((Path(self.tmp.name) / 'conv_opener_cache.json').exists())

    def test_resume_dom_selectors_execute_offline(self):
        # A tiny DOM fixture runs the actual emitted JavaScript, with no browser.
        fixture = r"""
const input = JSON.parse(process.argv[1]);
const getComputedStyle = e => ({display:e.hidden ? 'none' : 'block'});
const button = text => ({innerText:text,offsetParent:{},id:'',getAttribute:()=>null,closest:()=>null});
const resumes = Array.from({length:input.count},()=>button('发简历'));
const confirms = Array.from({length:input.confirms || 0},()=>button('确认'));
const header = {getAttribute:a => a === 'data-conversation-id' ? input.key : '',querySelector:()=>null};
const panel = {offsetParent:{},querySelector:()=>header,
  querySelectorAll:s=>s === '.chat-controls .toolbar-btn' ? resumes : []};
const dialog = {innerText:'虚构简历',offsetParent:input.hidden ? null : {},
  outerHTML:'<div>虚构简历弹层</div>',contains:()=>false,closest:()=>null,querySelectorAll:()=>confirms};
const document = {querySelectorAll:s=>s === '.chat-conversation' ? [panel] :
  s.startsWith('#') ? [] : input.dialog ? [dialog] : []};
const result=eval(input.expr);
process.stdout.write(JSON.stringify({result,resumes:resumes.map(b=>b.id),confirms:confirms.map(b=>b.id)}));
"""
        def execute(expr, **state):
            run = subprocess.run(['node', '-e', fixture, json.dumps(dict(expr=expr, key='fiction', count=1, **state))],
                                 capture_output=True, text=True, check=True)
            return json.loads(run.stdout)
        class Capture:
            def json(self, tab, expr, default=None):
                self.expr = expr
                return {}
        capture = Capture()
        A._resume_button(capture, 1, 'id:fiction')
        expr = capture.expr
        for count in (0, 2):
            payload = dict(expr=expr, key='fiction', count=count)
            run = subprocess.run(['node', '-e', fixture, json.dumps(payload)], capture_output=True, text=True, check=True)
            self.assertEqual(json.loads(run.stdout)['result']['reason'], 'resume_button')
        self.assertEqual(execute(expr)['resumes'], ['bh-conv-resume'])
        A._resume_dialog(capture, 1, 'id:fiction')
        self.assertTrue(execute(capture.expr, dialog=True, confirms=1)['result']['confirm'])
        self.assertFalse(execute(capture.expr, dialog=True, confirms=2)['result']['confirm'])
        self.assertEqual(execute(capture.expr, dialog=True, confirms=1, hidden=True)['result'], {})

    def test_dom_boundaries(self):
        names = 'CHAT_URL CONVERSATION_JS conversation_rows conversation_norm source_conversation_key select_conversation active_conversation open_by_index normalized_history atomic_chat_click'.split()
        for name in names:
            self.assertTrue(hasattr(CD, name)); self.assertFalse(hasattr(B, name))
        tree = ast.parse(Path(CD.__file__).read_text())
        imports = {a.name.split('.')[0] for n in ast.walk(tree) if isinstance(n, ast.Import) for a in n.names}
        imports |= {n.module.split('.')[0] for n in ast.walk(tree) if isinstance(n, ast.ImportFrom) and n.module}
        self.assertFalse(imports & {'reply', 'human', 'boss_hunt'})
