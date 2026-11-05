"""Offline fault injection for the six sending safety regressions."""
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
import chat_dom as CD
import boss_hunt as B
import hunt_config as HC
import hunt_state as HS
import outbound as O
import greet as G
import human as H
import reply as R


class SafetyTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.cfg = {'paths': {'state': str(self.root/'state.json'), 'workdir': str(self.root),
                              'human_replies': str(self.root/'human.json')},
                    'schedule': {'timezone': 'Asia/Shanghai'}, 'user': {}}

    def test_corrupt_state_preserves_bytes(self):
        p = Path(self.cfg['paths']['state']); p.write_bytes(b'{broken')
        with self.assertRaisesRegex(ValueError, 'state.json'):
            HS.load_state(self.cfg)
        self.assertEqual(p.read_bytes(), b'{broken')

    def test_wrong_state_types(self):
        for value in [{'sent': []}, {'day_count': '0'}, {'auto_replies': []}, {'sent': {'j': []}}]:
            Path(self.cfg['paths']['state']).write_text(json.dumps(value))
            with self.assertRaises(ValueError):
                HS.load_state(self.cfg)

    def test_second_lock_rejected(self):
        with B.state_lock(self.cfg['paths']['state']):
            with self.assertRaisesRegex(RuntimeError, '另一个实例在跑'):
                with B.state_lock(self.cfg['paths']['state']):
                    self.fail('second instance entered')

    def test_click_does_not_switch_conversation(self):
        br = Mock()
        br.json.side_effect = [[{'conv_key': 'id:new', 'text': '公司 HR'}], True,
                               {'conv_key': 'id:old', 'messages': [{'who': 'friend', 'text': '你好'}]}]
        with patch.object(B.time, 'sleep'):
            status, _, messages = CD.open_by_index(br, 1, 0)
        self.assertEqual(status, 'error: unknown_conversation')
        self.assertEqual(messages, [])
        br.fill.assert_not_called()

    def test_ambiguous_company_rejected(self):
        rows = [{'conv_key': 'id:1', 'text': '公司 张HR'}, {'conv_key': 'id:2', 'text': '公司 李HR'}]
        with self.assertRaisesRegex(ValueError, 'ambiguous.*张HR.*李HR'):
            CD.select_conversation(rows, '公司')

    def test_send_rechecks_identity(self):
        br = Mock(); br.json.return_value = {'conv_key': 'id:old', 'messages': []}
        self.assertEqual(B.send_in_chat(br, 1, '可以聊聊，负责哪个区域？', '可以',
                                       conv_key='id:new', cfg=self.cfg), 'error: unknown_conversation')
        br.fill.assert_not_called()

    def test_unknown_delivery_blocks_retry(self):
        def crashed():
            raise TimeoutError('after click')
        with self.assertRaises(TimeoutError):
            B.ledger_send(self.cfg, 'auto_reply', 'id:1', '', '可以聊聊吗？', crashed)
        rows = [json.loads(x) for x in (self.root/'send_ledger.jsonl').read_text().splitlines()]
        self.assertEqual([r['status'] for r in rows], ['pending', 'unknown'])
        self.assertEqual(B.ledger_count(self.cfg, {'auto_reply'}), 0)
        sender = Mock(return_value='replied')
        with self.assertRaisesRegex(RuntimeError, 'unresolved_send'):
            B.ledger_send(self.cfg, 'auto_reply', 'id:1', '', '可以聊聊吗？', sender)
        sender.assert_not_called()

    def test_failed_hint_is_not_learned(self):
        H.record('面试时间？', '明天下午到', '好的', ok=False, cfg=self.cfg)
        self.assertEqual(H.find_hint('面试时间？', self.cfg), (None, None))

    def test_hint_cannot_bypass_judge(self):
        H.record('面试时间？', '明天下午到', '好的', cfg=self.cfg)
        with patch.object(G, '_generate_raw') as model:
            result = R.generate(self.cfg, {}, '', history=[{'who': 'friend', 'text': '面试时间？'}])
        self.assertFalse(result['text']); model.assert_not_called()

    def test_commitment_is_manual_required(self):
        ok, reasons = R.check('可以，明天下午我准时到现场面试，您把地址发我好吗？')
        self.assertFalse(ok); self.assertIn('manual_required', reasons)

    def test_salary_cannot_support_achievements(self):
        cfg = {'user': {'greeting': {'facts': ['带6人团队，新签80家'], 'length': {'min': 1, 'max': 380}}}}
        job = {'jobName': '城市经理', 'salaryDesc': '20-30K'}
        text = '城市经理，带20人团队做到30万业绩。' + G.closings_of(cfg)[0]
        ok, reasons = G.check(text, cfg, job)
        self.assertFalse(ok)
        self.assertTrue(any('numbers_not_in_facts' in r for r in reasons))
        text = '城市经理，带6人团队，新签80家。' + G.closings_of(cfg)[0]
        self.assertTrue(G.check(text, cfg, job)[0])

    def test_cli_corrupt_state_never_creates_browser(self):
        config = self.root/'config.json'; config.write_text(json.dumps(self.cfg))
        state = Path(self.cfg['paths']['state']); state.write_bytes(b'{bad')
        args = B.build_parser().parse_args(['--config', str(config), 'send', '1'])
        with patch.object(B.CM, 'Browser') as browser:
            self.assertEqual(args.func(args), 1)
        browser.assert_not_called(); self.assertEqual(state.read_bytes(), b'{bad')

    def test_process_lock_rejected_without_sending(self):
        import subprocess
        script = ('from send_safety import state_lock\nimport sys\n'
                  'with state_lock(sys.argv[1]):\n print("SENT")\n')
        with B.state_lock(self.cfg['paths']['state']):
            result = subprocess.run([sys.executable, '-c', script, self.cfg['paths']['state']],
                                    env={**__import__('os').environ, 'PYTHONPATH': str(Path(B.__file__).parent)},
                                    text=True, capture_output=True)
        self.assertNotEqual(result.returncode, 0)
        self.assertNotIn('SENT', result.stdout)
        self.assertIn('另一个实例在跑(pid', result.stderr)

    def test_concurrent_atomic_writes_leave_complete_json(self):
        from concurrent.futures import ThreadPoolExecutor
        def write(n):
            HS.save_state(self.cfg, {'writer': n, 'payload': 'x'*10000})
            return json.loads(Path(self.cfg['paths']['state']).read_text())['payload']
        with ThreadPoolExecutor(max_workers=4) as pool:
            self.assertTrue(all(x == 'x'*10000 for x in pool.map(write, range(40))))
        self.assertEqual(list(self.root.glob('state.json.tmp.*')), [])

    def test_success_ledger_restores_lost_state_and_quota(self):
        B.ledger_send(self.cfg, 'send', 'job:j1', 'j1', 'text', lambda: 'sent',
                      {'grade': 'A', 'target': '方向', 'brand': '公司', 'boss': 'HR'})
        state = HS.load_state(self.cfg)
        self.assertEqual(state['day_count'], 1)
        self.assertEqual(state['day_by_grade'], {'A': 1})
        self.assertIn('j1', state['sent'])
        self.assertIn('公司|HR', state['contacts'])

    def test_reconciliation_counts_once_without_resending(self):
        try:
            B.ledger_send(self.cfg, 'auto_reply', 'id:1', '', 'text',
                          lambda: (_ for _ in ()).throw(TimeoutError()),
                          {'msg_count': 1, 'last_message_id': 'h1', 'click_ts_epoch': 1000000})
        except TimeoutError:
            pass
        from send_safety import confirms_new_bubble
        conversation = {'conv_key': 'id:1', 'messages': [{'id': 'h1'},
            {'id': 'm1', 'who': 'mine', 'text': 'text', 'ts': '1001'}]}
        B.ledger_guard(self.cfg, verify=lambda row: confirms_new_bubble(row, conversation))
        B.ledger_guard(self.cfg, verify=lambda row: confirms_new_bubble(row, conversation))
        self.assertEqual(B.ledger_count(self.cfg, {'auto_reply'}), 1)

    def test_fallback_reason_for_salary_number_theft(self):
        self.cfg['user']['greeting'] = {'mode': 'llm', 'facts': ['带6人团队，新签80家'],
                                       'length': {'min': 1, 'max': 380}, 'llm': {'retries': 0}}
        job = {'jobName': '城市经理', 'salaryDesc': '20-30K'}
        candidate = '城市经理，带20人团队做到30万业绩。' + G.closings_of(self.cfg)[0]
        with patch.object(G, '_generate_raw', return_value=(candidate, '')):
            result = G.generate(self.cfg, job, fallback=lambda j: '安全模板', use_cache=False)
        self.assertEqual(result['source'], 'template_fallback')
        self.assertIn('numbers_not_in_facts', result['fallback_reason'])

    def test_unsafe_hint_draft_does_not_fall_back_to_auto_send(self):
        with patch.object(H, 'find_hint', return_value=('其他', '同意明天到现场面试')):
            result = R.generate(self.cfg, {}, '', history=[{'who': 'friend', 'text': '你好方便聊聊吗？'}])
        self.assertEqual(result['source'], 'manual_required')
        self.assertFalse(result['text'])

    def test_click_error_is_unknown_not_retryable_failure(self):
        with self.assertRaisesRegex(RuntimeError, 'unresolved_send'):
            B.ledger_send(self.cfg, 'auto_reply', 'id:1', '', 'text', lambda: 'click_err')
        self.assertEqual(B.ledger_latest(self.cfg)[0]['status'], 'unknown')

    def test_other_commitments_are_manual(self):
        for text in ['下个月一号入职，具体流程是什么？', '月薪两万可以，具体流程是什么？',
                     '保证拿到offer，具体流程是什么？']:
            self.assertIn('manual_required', R.check(text)[1], text)

    def test_chat_constants_available(self):
        self.assertEqual(CD.CHAT_URL, 'https://www.zhipin.com/web/geek/chat')
        self.assertEqual(CD.NEW_GREET_TAB, '新招呼')

    def test_sent_followup_replays_and_does_not_send_twice(self):
        sender = Mock(return_value='replied')
        B.ledger_send(self.cfg, 'followup', 'id:1', 'j1', 'text', sender)
        restored = HS.load_state(self.cfg)
        self.assertEqual(restored['followed'], ['j1'])
        B.ledger_send(self.cfg, 'followup', 'id:1', 'j1', 'text', sender)
        self.assertEqual(sender.call_count, 1)
        self.assertEqual(B.ledger_count(self.cfg, {'followup'}), 1)

    def test_sent_interview_replays_and_clears_pending(self):
        HS.save_state(self.cfg, {'pending': {'j1': {}}})
        B.ledger_send(self.cfg, 'interview_reply', 'id:1', 'j1', 'text', lambda: 'replied')
        restored = HS.load_state(self.cfg)
        self.assertEqual(restored['replied'], ['j1'])
        self.assertEqual(restored['pending'], {})

    def test_old_body_or_input_marker_is_not_send_confirmation(self):
        br = Mock()
        before = {'conv_key': 'id:1', 'messages': [{'who': 'mine', 'text': '方便，岗位负责哪个区域？'}]}
        br.json.side_effect = lambda tab,expr,default=None: {'ok':True} if 'dispatchEvent' in expr else before
        br.fill.return_value = {}; br.click.return_value = {}
        with patch.object(B.time, 'sleep'), patch.object(CD, 'atomic_chat_click',
                return_value={'ok':True,'clicked':True,'conv_key':'id:1'}):
            with self.assertRaisesRegex(RuntimeError, 'unresolved_send'):
                B.send_in_chat(br, 1, '方便，岗位负责哪个区域？', '方便', 'id:1', self.cfg,
                               'reply-to', new_turn=True)
        self.assertEqual(B.ledger_latest(self.cfg)[0]['status'], 'unknown')

    def test_successful_chat_records_confirmed_own_bubble(self):
        br = Mock()
        before = {'conv_key': 'id:1', 'messages': [{'id': 'h1', 'who': 'friend', 'text': '方便聊聊吗？'}]}
        after = {'conv_key': 'id:1', 'messages': before['messages'] + [
            {'id': 'm1', 'who': 'mine', 'text': '方便，岗位负责哪个区域？'}]}
        br.json.side_effect = [before, {'ok':True}, after]
        br.fill.return_value = {}; br.click.return_value = {}
        with patch.object(B.time, 'sleep'), patch.object(CD, 'atomic_chat_click',
                return_value={'ok':True,'clicked':True,'conv_key':'id:1'}):
            self.assertEqual(B.send_in_chat(br, 1, '方便，岗位负责哪个区域？', '方便',
                                           'id:1', self.cfg), 'replied')
        self.assertEqual(B.ledger_count(self.cfg, {'auto_reply'}), 1)

    def test_reply_to_ambiguous_target_never_generates(self):
        config = self.root/'config.json'; config.write_text(json.dumps(self.cfg))
        args = B.build_parser().parse_args(['--config', str(config), 'reply-to', '公司', '--brief', '方便聊聊'])
        br = Mock(); br.close_own_tabs.return_value = 1
        with patch.object(B.CM, 'Browser', return_value=br), patch.object(B.time, 'sleep'), \
                patch.object(CD, 'goto_tab', return_value=True), patch.object(CD, 'conversation_rows', return_value=[
                    {'conv_key': 'id:1', 'text': '公司 张HR'}, {'conv_key': 'id:2', 'text': '公司 李HR'}]), \
                patch.object(R, 'generate') as model:
            self.assertEqual(args.func(args), 2)
        model.assert_not_called(); br.fill.assert_not_called()

    def test_migration_completes_after_partial_legacy_import(self):
        from send_safety import ledger_append
        st = HS.load_state(self.cfg)
        st['sent'] = {'j1': {'ts': HC.stamp(self.cfg), 'grade': 'A'},
                      'j2': {'ts': HC.stamp(self.cfg), 'grade': 'B'}}
        ledger_append(self.cfg, {'event_id': 'legacy-job:j1', 'ts': HC.stamp(self.cfg),
                                'cmd': 'send', 'job_id': 'j1', 'status': 'sent'})
        HS.migrate_ledger(self.cfg, st)
        HS.migrate_ledger(self.cfg, st)
        self.assertEqual(B.ledger_count(self.cfg, {'send'}), 2)

    def test_salary_promises_without_salary_word(self):
        for text in ['每月两万可以，负责哪个区域？', '两万我接受，负责哪个区域？']:
            self.assertIn('manual_required', R.check(text)[1])

    def test_greeting_wrong_loaded_job_never_generates(self):
        job = {'encryptJobId': 'intended', 'jobName': '城市经理', 'brandName': '甲公司'}
        br = Mock(); br.json.return_value = {'job_id': 'other', 'title': '城市经理', 'brand': '乙公司'}
        with patch.object(O, 'inspect_job', return_value=('ready', 'JD')), patch.object(G, 'generate') as model:
            self.assertEqual(O.send_one(br, 1, job, self.cfg), 'error: unknown_conversation')
        model.assert_not_called(); br.click.assert_not_called()

    def test_reply_migration_does_not_double_count_conv_key_history(self):
        text = '方便，负责哪个区域？'
        B.ledger_send(self.cfg, 'auto_reply', 'id:1', '', text, lambda: 'replied')
        HS.save_state(self.cfg, {'auto_replies': {'id:1': {'text': text, 'ts': HC.stamp(self.cfg)}}})
        state = HS.load_state(self.cfg)
        HS.migrate_ledger(self.cfg, state)
        self.assertEqual(B.ledger_count(self.cfg, {'auto_reply'}), 1)

    def test_invalid_nested_state_fields_report_path(self):
        for value in [{'sent': {'j': {'ts': 123}}}, {'day_count': -1}, {'contacts': [{}]}]:
            Path(self.cfg['paths']['state']).write_text(json.dumps(value))
            with self.assertRaisesRegex(ValueError, 'state.json'):
                HS.load_state(self.cfg)

    def test_reply_state_uses_event_id_across_clock_boundary(self):
        text = '方便，负责哪个区域？'
        B.ledger_send(self.cfg, 'auto_reply', 'id:1', '', text, lambda: 'replied')
        state = HS.load_state(self.cfg)
        HS.remember_reply(self.cfg, state, 'id:1', 'auto_reply', {'source': 'llm'})
        with patch.object(HC, 'stamp', return_value='2099-01-01 00:00'):
            HS.save_state(self.cfg, state)
            HS.migrate_ledger(self.cfg, HS.load_state(self.cfg))
        events = [r for r in B.ledger_latest(self.cfg) if r['cmd'] == 'auto_reply']
        self.assertEqual(len(events), 1)
        self.assertIn(events[0]['event_id'], state['auto_replies'])
