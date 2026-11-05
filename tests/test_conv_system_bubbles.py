"""conv-03o: fictional bubbles, offline send boundaries and real ledgers."""
import hashlib
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
import conv_actions as A
import conv_rules as R
import send_safety as S


def receipt(n=1):
    return dict(id='fiction-system%d' % n, who='unknown', classes='message-item item-system',
                text='您的附件简历 虚构小禾 已发送给Boss', ts='100')


class SystemBubbleTests(unittest.TestCase):
    def deliver(self, messages, options=None):
        with tempfile.TemporaryDirectory() as root:
            cfg = dict(paths=dict(workdir=root, state=root+'/state.json'),
                       sending=dict(min_gap_seconds=0), conversation=options or {})
            text = R.DEFAULT_TEMPLATES['round2']
            before = dict(conv_key='id:fiction', messages=messages)
            after = dict(before, messages=messages + [dict(id='fiction-new', who='mine', text=text)])
            with patch.object(A.chat_dom, 'active_conversation', side_effect=[before, after]), \
                 patch.object(A.CS, 'type_into_chat', return_value=dict(ok=True)) as fill, \
                 patch.object(A.chat_dom, 'atomic_chat_click', return_value=dict(
                     ok=True, clicked=True, conv_key='id:fiction')) as click, patch.object(A.time, 'sleep'):
                result = A.send_text(Mock(), 1, cfg, 'id:fiction', {}, text, text)
            return result, S.ledger_latest(cfg), fill.call_count, click.call_args

    def test_two_system_receipts_allow_send_and_keep_raw_ledger_baseline(self):
        messages = [receipt(), receipt(2), dict(id='fiction-hr', who='friend', text='薪资底薪八千')]
        result, ledger, _, click = self.deliver(messages)
        self.assertEqual(result, 'sent')
        self.assertEqual(ledger[0]['status'], 'sent')
        self.assertEqual(ledger[0]['msg_count'], 3)
        self.assertEqual(ledger[0]['last_message_id'], 'fiction-hr')
        self.assertEqual([m['id'] for m in click.args[4]], ['fiction-system1', 'fiction-system2', 'fiction-hr'])

    def test_unknown_non_system_still_aborts_before_fill(self):
        result, ledger, fills, _ = self.deliver([dict(id='fiction-unknown', who='unknown', text='虚构未明发言')])
        self.assertEqual(result, 'send_aborted:owner_unknown')
        self.assertEqual((ledger, fills), ([], 0))

    def test_trailing_system_receipt_does_not_create_peer_turn(self):
        result, ledger, fills, _ = self.deliver([dict(id='fiction-mine', who='mine', text='虚构已问问题'), receipt()])
        self.assertEqual(result, 'last_turn_is_ours')
        self.assertEqual((ledger, fills), ([], 0))

    def test_configured_system_text_is_whitespace_normalized(self):
        result, _, _, _ = self.deliver([
            dict(id='fiction-tip', who='unknown', text='虚构 系统\n提示'),
            dict(id='fiction-hr', who='friend', text='底薪八千')], dict(system_texts=['虚构系统提示']))
        self.assertEqual(result, 'sent')

    def test_atomic_guard_uses_system_policy_but_still_checks_raw_history(self):
        fixture = r'''
const input=JSON.parse(process.argv[1]);
const header={getAttribute:a=>a==='data-conversation-id'?'fiction':'',querySelector:()=>null};
const nodes=input.messages.map(m=>({className:m.classes||'',getAttribute:a=>
 a==='data-mid'?m.id:a==='data-timestamp'?(m.ts||''):'',querySelector:()=>({innerText:m.text})}));
const panel={querySelector:()=>header,querySelectorAll:()=>nodes};
const fail=reason=>({reason});
const result=eval(input.expr);process.stdout.write(JSON.stringify(result));
'''
        cases = [([receipt(), dict(id='f', who='friend', classes='item-friend', text='底薪八千')], False, 'ok'),
                 ([dict(id='u', who='unknown', text='未知')], False, 'owner_unknown'),
                 ([receipt()], True, 'stale_draft'),
                 ([dict(id='t', who='unknown', text='你与该职位竞争者 PK 情况')], False, 'ok')]
        for messages, stale, expected in cases:
            with self.subTest(expected=expected, messages=messages):
                with patch.object(A.chat_dom.CS, 'click_send_scoped', return_value={}) as scoped:
                    A.chat_dom.atomic_chat_click(Mock(), 1, 'id:fiction', '虚构问题',
                        [] if stale else A.chat_dom.normalized_history(messages))
                args = scoped.call_args.kwargs
                expr = '(()=>{' + args['identity_js'] + args['guard_js'] + ';return {reason:"ok"};})()'
                run = subprocess.run(['node', '-e', fixture, json.dumps(dict(expr=expr, messages=messages))],
                                     capture_output=True, text=True, check=True)
                self.assertEqual(json.loads(run.stdout)['reason'], expected)

    def test_raw_system_baseline_can_confirm_pending_bubble(self):
        text = '虚构第二轮问题'
        row = dict(conv_key='id:fiction', msg_count=2, last_message_id='fiction-system2',
                   click_ts_epoch=200000, text_sha256=hashlib.sha256(text.encode()).hexdigest())
        panel = dict(conv_key='id:fiction', messages=[receipt(), receipt(2),
                     dict(id='fiction-new', who='mine', text=text, ts=201), receipt(3)])
        self.assertTrue(S.confirms_new_bubble(row, panel))
        panel['messages'][2]['who'] = 'unknown'
        self.assertFalse(S.confirms_new_bubble(row, panel))

    def test_rejections_close_without_manual_and_question_does_not_reject(self):
        for text in ('感谢您投递我司虚构经理岗位。本轮初筛已结束，经综合评估，有更适配该岗位的候选人',
                     '初筛未通过', '综合评估后不予录用', '有更匹配这个职位的人选'):
            with self.subTest(text=text):
                import conv_store
                row = conv_store.new_record('id:fiction', 'reply', 'fiction-job', '虚构公司', '虚构HR', '虚构岗位', 1)
                self.assertEqual(R.classify(text), 'reject')
                actions, planned = R.decide(row, [dict(text=text, ts=2)], dict(cfg={}, now_ts=2))
                self.assertEqual((actions, planned['status']), ([], 'closed'))
        for text in ('方便说下更适合哪类候选人吗', '方便说下更合适的候选人吗？'):
            self.assertNotEqual(R.classify(text), 'reject')
