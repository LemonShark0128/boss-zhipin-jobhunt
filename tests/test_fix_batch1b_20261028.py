"""Offline receipt recovery and pre-click safety regressions."""
from pathlib import Path
from unittest.mock import Mock, patch

from test_phase_a import Base, B
import outbound as O
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

    def test_greeting_precheck_failure_records_failed_without_click(self):
        cfg = self.config_at(str(self.root))
        page = Page(buttons=2)
        self.assertEqual(self.greet(page, cfg), 'send_aborted:button_not_unique')
        self.assertEqual(S.ledger_latest(cfg)[0]['status'], 'failed')
        self.assertEqual(page.clicks, 0)
