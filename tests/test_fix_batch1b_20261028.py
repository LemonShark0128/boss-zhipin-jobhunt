"""Offline receipt recovery and pre-click safety regressions."""
from contextlib import ExitStack
import tempfile
from pathlib import Path
from unittest.mock import Mock, patch

from test_phase_a import Base, B
import chat_dom as CD
import outbound as O
from test_round7 import Page as ReplyPage, TEXT as REPLY_TEXT
from test_round8 import Page, JOB, TEXT
import chat_send as CS
import send_safety as S


class Batch1b(Base):
    def greet(self, page, cfg):
        with patch.object(O, 'inspect_job', return_value=('ready', '')), \
             patch.object(O, 'job_identity_matches', return_value=True), \
             patch.object(O, 'greeting_switch', return_value='auto_greeting_off'), \
             patch.object(B.G, 'generate', return_value={'text': TEXT}), \
             patch.object(B.time, 'sleep'):
            return O.send_one(page, 1, dict(JOB), cfg)

    def config_at(self, root):
        return dict(self.cfg, grading={'enabled': False},
                    paths={'workdir': root, 'state': str(Path(root) / 'state.json')})

    def test_only_known_preclick_failures_can_abort(self):
        reasons = ('identity_changed', 'identity_mismatch', 'owner_unknown', 'stale_draft',
                   'button_disabled', 'button_not_unique', 'panel_hidden', 'panel_not_visible',
                   'panel_not_ready', 'panel_not_unique', 'input_mismatch', 'fill_mismatch')
        for reason in reasons + ('trusted_click_failed', 'enter_failed', 'future_failure'):
            for attempted in (None, False, True):
                with self.subTest(reason=reason, attempted=attempted):
                    receipt = {'ok': False, 'clicked': False, 'reason': reason}
                    if attempted is not None:
                        receipt['attempted'] = attempted
                    expected = 'aborted' if reason in reasons and attempted is not True else 'pending'
                    self.assertEqual(CS.click_outcome(receipt), expected)

    def test_js_fallback_marks_only_explicit_precheck_failure_unattempted(self):
        for receipt, attempted in (({}, True), (None, True), (RuntimeError('transport lost'), True),
                ({'ok': False, 'clicked': False, 'reason': 'identity_changed'}, False),
                ({'ok': False, 'clicked': False, 'reason': 'lost'}, True)):
            with self.subTest(receipt=receipt):
                br = Mock(click=None)
                br.json.side_effect = [{'ok': True, 'ready': True}, receipt]
                result = CS.click_send_scoped(br, 1, TEXT, job=JOB)
                self.assertIs(result.get('attempted'), attempted)
                self.assertEqual(CS.click_outcome(result), 'aborted' if not attempted else 'pending')

    def test_enter_expression_preserves_both_attempt_markers(self):
        br = Mock()
        br.json.return_value = {}
        CS.press_enter_scoped(br, 1, TEXT, job=JOB)
        expr = br.json.call_args.args[1]
        self.assertIn('attempted:false', expr)
        self.assertIn('attempted:true', expr)

    def test_fullwidth_codes_are_retained_in_python_and_js(self):
        for name in ('区域经理(１２３４５)', '区域经理(A１２)', '区域经理（A1２）'):
            with self.subTest(name=name):
                self.assertEqual(CS.norm_job_name(name), name)
                job = dict(JOB, jobName=name)
                page = Page(panelText=JOB['brandName'] + ' 区域经理', text=TEXT, model=TEXT)
                self.assertEqual(CS.click_send_scoped(page, 1, TEXT, job=job)['reason'], 'identity_mismatch')
                self.assertEqual(page.clicks, 0)

    def test_pending_click_checks_new_bubble_for_greeting_and_all_reply_commands(self):
        for cmd in ('send', 'auto_reply', 'interview_reply', 'followup'):
            for receipt in ({'isError': True}, {'ok': False}, RuntimeError('lost receipt')):
                for bubble in (False, True):
                    with self.subTest(cmd=cmd, receipt=receipt, bubble=bubble), tempfile.TemporaryDirectory() as root:
                        cfg = self.config_at(root)
                        page = Page() if cmd == 'send' else ReplyPage()
                        msg = TEXT if cmd == 'send' else B.INTERVIEW_DEFAULT if cmd == 'interview_reply' else REPLY_TEXT
                        original_click = page.click
                        def click(tab, selector):
                            result = original_click(tab, selector)
                            if selector != '#bh-send-btn':
                                return result
                            if not bubble:
                                page.state['messages'].pop()
                            if isinstance(receipt, Exception):
                                raise receipt
                            return receipt
                        page.click = click
                        def run():
                            if cmd == 'send':
                                return self.greet(page, cfg)
                            return B.send_in_chat(page, 1, msg, '', 'id:1', cfg, cmd=cmd, job_id='j')
                        with patch.object(B.time, 'sleep'):
                            if bubble:
                                self.assertEqual(run(), 'sent' if cmd == 'send' else 'replied')
                                self.assertEqual(S.ledger_latest(cfg)[0]['status'], 'sent')
                                if cmd == 'send':
                                    self.assertEqual(O.LAST_GREET['via'], 'confirm_only')
                            else:
                                for _ in range(2):
                                    with self.assertRaisesRegex(RuntimeError, 'unresolved_send'):
                                        run()
                                    self.assertEqual(S.ledger_latest(cfg)[0]['status'], 'unknown')
                            self.assertEqual(page.clicks, 1)

    def test_pending_confirmation_rejects_changed_identity_or_unproven_bubble(self):
        for cmd in ('send', 'auto_reply', 'interview_reply', 'followup'):
            for defect in ('identity', 'old_id', 'wrong_owner', 'wrong_text'):
                with self.subTest(cmd=cmd, defect=defect), tempfile.TemporaryDirectory() as root:
                    cfg = self.config_at(root)
                    page = Page() if cmd == 'send' else ReplyPage()
                    original_click = page.click
                    def click(tab, selector):
                        result = original_click(tab, selector)
                        if selector != '#bh-send-btn':
                            return result
                        if defect == 'identity':
                            page.state.update(key='2', panelText='另一家公司 另一岗位')
                        elif defect == 'old_id':
                            page.state['messages'][-1]['id'] = 'h1'
                        elif defect == 'wrong_owner':
                            page.state['messages'][-1]['who'] = 'friend'
                        else:
                            page.state['messages'][-1]['text'] += '改写'
                        return {'isError': True}
                    page.click = click
                    with patch.object(B.time, 'sleep'), self.assertRaisesRegex(RuntimeError, 'unresolved_send'):
                        if cmd == 'send':
                            self.greet(page, cfg)
                        else:
                            msg = B.INTERVIEW_DEFAULT if cmd == 'interview_reply' else REPLY_TEXT
                            B.send_in_chat(page, 1, msg, '', 'id:1', cfg, cmd=cmd)
                    self.assertEqual(S.ledger_latest(cfg)[0]['status'], 'unknown')
                    self.assertEqual(page.clicks, 1)

    def test_greeting_precheck_failure_records_failed_without_click(self):
        cfg = self.config_at(str(self.root))
        page = Page(buttons=2)
        self.assertEqual(self.greet(page, cfg), 'send_aborted:button_not_unique')
        self.assertEqual(S.ledger_latest(cfg)[0]['status'], 'failed')
        self.assertEqual(page.clicks, 0)

    def test_pending_confirmation_errors_remain_unknown(self):
        for cmd in ('send', 'auto_reply', 'interview_reply', 'followup'):
            for failure_stage in ('sleep', 'snapshot', 'confirm'):
                with self.subTest(cmd=cmd, stage=failure_stage), tempfile.TemporaryDirectory() as root, ExitStack() as stack:
                    cfg = self.config_at(root)
                    page = Page() if cmd == 'send' else ReplyPage()
                    clicked = []
                    def click(tab, selector):
                        if selector == '#bh-send-btn':
                            clicked.append(True)
                            page.clicks += 1
                            return {'isError': True}
                        return {}
                    page.click = click
                    snapshot_name = 'chat_snapshot' if cmd == 'send' else 'active_conversation'
                    owner = CS if cmd == 'send' else CD
                    snapshot = getattr(owner, snapshot_name)
                    def read(*args, **kwargs):
                        if clicked and failure_stage == 'snapshot':
                            raise RuntimeError('snapshot lost')
                        return snapshot(*args, **kwargs)
                    def sleep(_):
                        if clicked and failure_stage == 'sleep':
                            raise RuntimeError('wait interrupted')
                    stack.enter_context(patch.object(owner, snapshot_name, side_effect=read))
                    if failure_stage == 'confirm':
                        stack.enter_context(patch.object(CS, 'confirm_new_bubble', side_effect=RuntimeError('confirm lost')))
                    if cmd == 'send':
                        stack.enter_context(patch.object(O, 'inspect_job', return_value=('ready', '')))
                        stack.enter_context(patch.object(O, 'job_identity_matches', return_value=True))
                        stack.enter_context(patch.object(O, 'greeting_switch', return_value='auto_greeting_off'))
                        stack.enter_context(patch.object(B.G, 'generate', return_value={'text': TEXT}))
                    stack.enter_context(patch.object(B.time, 'sleep', side_effect=sleep))
                    with self.assertRaisesRegex(RuntimeError, 'unresolved_send'):
                        if cmd == 'send':
                            O.send_one(page, 1, dict(JOB), cfg)
                        else:
                            msg = B.INTERVIEW_DEFAULT if cmd == 'interview_reply' else REPLY_TEXT
                            B.send_in_chat(page, 1, msg, '', 'id:1', cfg, cmd=cmd)
                    self.assertEqual(S.ledger_latest(cfg)[0]['status'], 'unknown')
                    with self.assertRaisesRegex(RuntimeError, 'unresolved_send'):
                        S.ledger_guard(cfg)
                    self.assertEqual(page.clicks, 1)
