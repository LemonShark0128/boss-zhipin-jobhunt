"""Batch 1 safety regressions; browser and runtime data are offline substitutes."""
import json
import os
import subprocess
import sys
from unittest.mock import Mock, patch

from test_phase_a import Base, B
import hunt_state as HS
import outbound as O
from test_round8 import Page, JOB, TEXT
import chat_send as CS
import send_safety as S


class Batch1(Base):
    def test_click_outcome_matrix(self):
        cases = [(None, 'pending'), ([], 'pending'), ({}, 'pending'),
                 ({'attempted': True, 'ok': False, 'clicked': False, 'reason': 'lost'}, 'pending'),
                 ({'attempted': True, 'clicked': True}, 'pending'),
                 ({'ok': False, 'clicked': False, 'reason': 'identity_changed'}, 'aborted'),
                 ({'attempted': False, 'ok': False, 'clicked': False, 'reason': 'identity_changed'}, 'aborted'),
                 ({'ok': True, 'clicked': True}, 'clicked'),
                 ({'attempted': True, 'ok': True, 'clicked': True}, 'clicked'),
                 ({'ok': False, 'clicked': False}, 'pending'),
                 ({'ok': True, 'clicked': False}, 'pending'),
                 ({'ok': False, 'clicked': True}, 'pending')]
        self.assertTrue(callable(getattr(CS, 'click_outcome', None)))
        for result, expected in cases:
            with self.subTest(result=result):
                self.assertEqual(CS.click_outcome(result), expected)

    def test_send_one_trusted_failure_uses_real_shared_sender(self):
        self.raw([self.job()])
        st = HS.load_state(self.cfg)
        br = Mock()
        def click(tab, selector):
            if selector == '#bh-send-btn':
                raise RuntimeError('lost receipt')
            return {}
        br.click.side_effect = click
        br.json.side_effect = lambda tab, expr, default=None: (
            {'ok': True, 'ready': True} if 'ready:true' in expr
            else {'path': '/web/geek/chat'})
        panel = {'ok': True, 'panel_text': '示例公司j1 城市经理', 'messages': []}
        with patch.object(O, 'inspect_job', return_value=('ready', '商户拓展开城')), \
             patch.object(O, 'job_identity_matches', return_value=True), \
             patch.object(O, 'greeting_switch', return_value='auto_greeting_off'), \
             patch.object(B.G, 'generate', return_value={'text': TEXT}), \
             patch.object(CS, 'chat_snapshot', return_value=panel), \
             patch.object(CS, 'type_into_chat', return_value={'ok': True}), \
             patch.object(B.time, 'sleep'):
            with self.assertRaisesRegex(RuntimeError, 'unresolved_send'):
                O.do_send(self.cfg, br, 1, st, 1, [])
        self.assertEqual(S.ledger_latest(self.cfg)[0]['status'], 'unknown')
        self.assertEqual(st['day_count'], 0)
        with self.assertRaisesRegex(RuntimeError, 'unresolved_send'):
            S.ledger_guard(self.cfg)

    def test_click_and_enter_attempt_markers(self):
        for sender in (CS.click_send_scoped, CS.press_enter_scoped):
            with self.subTest(sender=sender.__name__):
                page = Page(panelText='另一家公司', text=TEXT, model=TEXT)
                result = sender(page, 1, TEXT, job=JOB)
                self.assertEqual(result['reason'], 'identity_mismatch')
                self.assertIs(result.get('attempted'), False)
                page = Page(text=TEXT, model=TEXT, enterSends=True)
                result = sender(page, 1, TEXT, job=JOB)
                self.assertIs(result.get('attempted'), True)
        br = Mock()
        br.json.return_value = {'ready': True, 'ok': True}
        br.click.side_effect = RuntimeError('lost receipt')
        result = CS.click_send_scoped(br, 1, TEXT, job=JOB)
        self.assertIs(result.get('attempted'), True)

    def test_enter_failure_after_dispatch_is_pending(self):
        # Execute the real JS with a keyboard event failure after keydown dispatch.
        from test_round8 import FIXTURE
        page = Page(text=TEXT, model=TEXT, enterSends=True)
        broken = FIXTURE.replace("return true;\n  },", "if(e.type==='keypress')throw new Error('lost receipt'); return true;\n  },")
        with patch('test_round8.FIXTURE', broken):
            result = CS.press_enter_scoped(page, 1, TEXT, job=JOB)
        self.assertEqual(len(page.state['messages']), 2)
        self.assertIs(result.get('attempted'), True)
        self.assertEqual(CS.click_outcome(result), 'pending')

    def test_unexpected_enter_precheck_exception_is_pending(self):
        from test_round8 import FIXTURE
        page = Page(text=TEXT, model=TEXT)
        broken = FIXTURE.replace('focus(){state.focused=true}',
                                 "focus(){throw new Error('unknown DOM failure')}")
        with patch('test_round8.FIXTURE', broken):
            result = CS.press_enter_scoped(page, 1, TEXT, job=JOB)
        self.assertIs(result.get('attempted'), False)
        self.assertEqual(CS.click_outcome(result), 'pending')

    def test_numbered_roles_remain_distinct_in_python_and_js(self):
        for name in ('区域经理（2）', '（2）区域经理', '区域经理(2024)', '区域经理(1234)', '区域经理(A1)'):
            with self.subTest(name=name):
                self.assertEqual(CS.norm_job_name(name), name)
        for name in ('区域经理(J13893)', '区域经理（A12）招聘', '区域经理(12345)'):
            self.assertEqual(CS.norm_job_name(name), '区域经理')
        job = dict(JOB, jobName='区域经理（2）')
        label = JOB['brandName'] + ' 区域经理（1）'
        self.assertFalse(CS.chat_identity_evidence(label, job))
        page = Page(panelText=label, text=TEXT, model=TEXT)
        self.assertEqual(CS.click_send_scoped(page, 1, TEXT, job=job)['reason'], 'identity_mismatch')
        self.assertEqual(page.clicks, 0)

    def test_tick_dry_run_cli_does_not_create_workdir_or_raw_dir(self):
        workdir = self.root / 'absent-workdir'
        raw_dir = self.root / 'absent-raw'
        cfg = dict(self.cfg, paths={'workdir': str(workdir), 'raw_dir': str(raw_dir)})
        self.path.write_text(json.dumps(cfg))
        run = subprocess.run([sys.executable, B.__file__, '--config', str(self.path),
                              'tick', '--dry-run', '--json'], capture_output=True, text=True,
                             env=dict(os.environ, PYTHONPYCACHEPREFIX='/tmp/pyc'))
        self.assertEqual(run.returncode, 0, run.stderr)
        self.assertTrue(json.loads(run.stdout)['ok'])
        self.assertFalse(workdir.exists())
        self.assertFalse(raw_dir.exists())
