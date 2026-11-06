"""Batch 1c: offline delayed confirmation, diagnostics and ledger metadata."""
from contextlib import ExitStack
import tempfile
from unittest.mock import Mock, patch

from test_phase_a import Base, B
import hunt_config as HC
import outbound as O
from test_round8 import JOB, TEXT, Page
import chat_send as CS
import send_safety as S


class Batch1c(Base):
    def exercise(self, cmd, snapshots, receipt=None, failure=None):
        cfg = dict(self.cfg, grading={'enabled': False})
        before = {'ok': True, 'conv_key': 'id:1', 'panel_text': '戊示例科技 虚构渠道经理',
                  'messages': [{'id': 'h1', 'who': 'friend', 'text': '您好'}]}
        msg = TEXT
        br = Mock()
        br.click.return_value = {}
        br.json.return_value = {'path': '/web/geek/chat'}
        clicked = False
        reads = []
        def click(*args, **kwargs):
            nonlocal clicked
            clicked = True
            return receipt or {'ok': False}
        def snapshot(*args, **kwargs):
            if not clicked:
                return before
            reads.append(True)
            item = snapshots[min(len(reads) - 1, len(snapshots) - 1)]
            if isinstance(item, Exception):
                if len(reads) == 1:
                    raise item
                return before
            return dict(before, messages=before['messages'] + ([
                {'id': 'm1', 'who': 'mine', 'text': msg}] if item else []))
        with ExitStack() as stack:
            for owner, name, kwargs in (
                (O, 'inspect_job', {'return_value': ('ready', '')}),
                (O, 'job_identity_matches', {'return_value': True}),
                (O, 'greeting_switch', {'return_value': 'auto_greeting_off'}),
                (B.G, 'generate', {'return_value': {'text': TEXT}}),
                (CS, 'type_into_chat', {'return_value': {'ok': True}}),
                (CS, 'chat_snapshot', {'side_effect': snapshot}),
                (CS, 'click_send_scoped', {'side_effect': click}),
            ):
                stack.enter_context(patch.object(owner, name, **kwargs))
            slept = stack.enter_context(patch.object(B.time, 'sleep'))
            traced = stack.enter_context(patch.object(HC, 'trace'))
            if failure:
                stack.enter_context(patch.object(CS, 'confirm_new_bubble', side_effect=failure))
            def run():
                return O.send_one(br, 1, dict(JOB), cfg)
            if snapshots[-1] is True and not failure:
                self.assertEqual(run(), 'sent')
            else:
                with self.assertRaisesRegex(RuntimeError, 'unresolved_send'):
                    run()
                first_reads = list(reads)
                with self.assertRaisesRegex(RuntimeError, 'unresolved_send'):
                    run()
            self.assertEqual(CS.click_send_scoped.call_count, 1)
            self.assertEqual(CS.type_into_chat.call_count, 1)
            return reads if snapshots[-1] is True and not failure else first_reads, slept.call_args_list, traced.call_args_list, S.ledger_latest(cfg)[0]

    def test_pending_reads_third_snapshot_without_resending(self):
        for cmd in ('send',):
            with self.subTest(cmd=cmd), tempfile.TemporaryDirectory() as root:
                self.cfg['paths'].update(workdir=root, state=root + '/state.json')
                reads, sleeps, traces, row = self.exercise(cmd, [False, False, True])
                self.assertEqual(len(reads), 3)
                self.assertEqual([c.args[0] for c in sleeps], [2.0, 2.0, 2.0])
                self.assertEqual(row['meta']['via'], 'confirm_only')
                self.assertEqual(sum('after snapshot' in c.args[0] for c in traces), 1)

    def test_three_unconfirmed_reads_keep_ledger_guard(self):
        for cmd in ('send',):
            with self.subTest(cmd=cmd), tempfile.TemporaryDirectory() as root:
                self.cfg['paths'].update(workdir=root, state=root + '/state.json')
                reads, _, _, row = self.exercise(cmd, [False] * 3)
                self.assertEqual(len(reads), 3)
                self.assertEqual(row['status'], 'unknown')

    def test_confirmation_errors_are_traced_and_remain_unknown(self):
        for cmd in ('send',):
            for stage in ('snapshot', 'confirm'):
                with self.subTest(cmd=cmd, stage=stage), tempfile.TemporaryDirectory() as root:
                    self.cfg['paths'].update(workdir=root, state=root + '/state.json')
                    error = RuntimeError('confirmation lost')
                    _, _, traces, row = self.exercise(cmd, [error] if stage == 'snapshot' else [False],
                                                     failure=error if stage == 'confirm' else None)
                    self.assertIn("confirm error: RuntimeError('confirmation lost')", [c.args[0] for c in traces])
                    self.assertEqual(row['status'], 'unknown')

    def test_ledger_records_confirmed_receipt_path(self):
        for cmd in ('send',):
            for via in ('trusted_click', 'enter', 'js_fallback'):
                with self.subTest(cmd=cmd, via=via), tempfile.TemporaryDirectory() as root:
                    self.cfg['paths'].update(workdir=root, state=root + '/state.json')
                    receipt = {'ok': True, 'clicked': True, 'via': via,
                               'conv_key': 'job:j' if cmd == 'send' else 'id:1'}
                    reads, _, _, row = self.exercise(cmd, [True], receipt=receipt)
                    self.assertEqual(len(reads), 1)
                    self.assertEqual(row['meta']['via'], via)

    def test_unicode_spaces_and_ascii_codes_match_python_and_js(self):
        for space in ('\u3000', '\u00a0'):
            for code in ('12345', 'A12'):
                with self.subTest(space=space, code=code):
                    name = '区域经理（' + space + code + space + '）'
                    self.assertEqual(CS.norm_job_name(name), '区域经理')
                    page = Page(panelText='戊示例科技 区域经理', text=TEXT, model=TEXT)
                    self.assertTrue(CS.click_send_scoped(page, 1, TEXT,
                                                       job=dict(JOB, jobName=name))['clicked'])
