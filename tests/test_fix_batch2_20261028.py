"""Batch 2 state machine acceptance: temporary state and offline browser doubles only."""
import io
import json
from pathlib import Path
from collections import Counter
from contextlib import redirect_stdout
from unittest.mock import Mock, patch

from test_phase_a import Base, B
import hunt_config as HC
import hunt_filter as HF
import hunt_state as HS
import outbound as O


class Batch2Tests(Base):
    def setUp(self):
        super().setUp()
        self.now = HC.now_local(self.cfg).replace(hour=22, minute=0, second=0, microsecond=0)
        self.clock = patch.object(HC, 'now_local', return_value=self.now)
        self.clock.start()
        self.addCleanup(self.clock.stop)
        self.sleep = patch.object(B.time, 'sleep')
        self.sleep.start()
        self.addCleanup(self.sleep.stop)

    def test_record_outcome_table(self):
        # A missing/wrong transition must change the return value or persisted maps.
        cases = [
            ('grade', 'ready', 'none', False, False, False, False),
            ('grade', 'skip_grade', 'terminal', True, False, False, False),
            ('grade', 'skip_jd_redline', 'terminal', True, False, False, False),
            ('grade', 'skip_missing_jd', 'retry', False, True, False, False),
            ('grade', 'skip_no_button', 'retry', False, True, False, False),
            ('grade', 'skip_already_chatted', 'terminal', True, False, True, False),
            ('grade', 'skip_need_app', 'terminal', True, False, False, False),
            ('grade', 'skip_captcha', 'halt', False, False, False, False),
            ('grade', 'skip_network', 'halt', False, False, False, False),
            ('grade', 'skip_not_logged_in', 'halt', False, False, False, False),
            ('grade', 'detail_unavailable', 'halt', False, False, False, False),
            ('send', 'sent', 'none', False, False, False, False),
            ('send', 'skip_no_button', 'retry', False, True, False, False),
            ('send', 'error: unknown_conversation', 'retry', False, True, False, False),
            ('send', 'skip_already_chatted', 'terminal', True, False, True, False),
            ('send', 'skip_grade', 'terminal', True, False, False, False),
            ('send', 'skip_jd_redline', 'terminal', True, False, False, False),
            ('send', 'err_no_greeting', 'terminal', True, False, False, False),
            ('send', 'err_template_too_long', 'terminal', True, False, False, False),
            ('send', 'skip_min_gap(37)', 'halt', False, False, False, False),
            ('send', 'skip_daily_cap', 'halt', False, False, False, False),
            ('send', 'send_aborted:panel_not_ready', 'terminal', True, False, False, True),
            ('send', 'fill_err', 'terminal', True, False, False, True),
            ('send', 'stale_draft', 'terminal', True, False, False, True),
            ('send', 'error: chat_not_opened', 'retry', False, True, False, False),
            ('send', 'chat_not_opened', 'terminal', True, False, False, True),
            ('send', 'pending_verification', 'halt', False, False, False, False),
            ('send', 'unknown', 'halt', False, False, False, False),
        ]
        self.assertTrue(hasattr(HS, 'record_outcome'), '统一结果状态机尚未实现')
        for phase, status, result, checked, retry, chatted, opened in cases:
            with self.subTest(phase=phase, status=status):
                st = HS.load_state(self.cfg)
                self.assertEqual(HS.record_outcome(self.cfg, st, self.job(), status, phase, '2026-10-28 10:00'), result)
                for key, present in [('checked_jobs', checked), ('retry_jobs', retry),
                                     ('platform_chatted', chatted), ('opened_no_greet', opened)]:
                    self.assertEqual('j1' in st.get(key, {}), present, key)
                if checked:
                    self.assertIsInstance(st['checked_jobs']['j1'], str)
                if retry:
                    self.assertEqual(st['retry_jobs']['j1'], {'fp': HF.stable_fingerprint(self.job(), self.cfg),
                        'n': 1, 'last': HC.stamp(self.cfg), 'window': '2026-10-28 10:00'})
                if opened:
                    self.assertEqual(st['opened_no_greet']['j1'], {'ts': HC.stamp(self.cfg), 'reason': status})

    def test_three_windows_cover_unseen_before_retry(self):
        self.raw([self.job(str(i), '示例品牌行业') for i in range(35)])
        st = HS.load_state(self.cfg)
        reads = []
        def inspect(br, tab, job, cfg):
            jid = job['encryptJobId']; reads.append(jid)
            return ('skip_no_button', '') if int(jid) < 24 else ('ready', '门店运营')
        with patch.object(O, 'inspect_job', side_effect=inspect), patch.object(O, 'send_one', return_value='sent'):
            first = O.do_send(self.cfg, None, None, st, 1, [], window_key='2026-10-28 10:00')
            self.assertEqual(first['inspects'], 24)
            self.assertEqual(first['stop_reason'], 'inspect_cap')
            self.assertEqual(len(st['retry_jobs']), 24)
            self.assertTrue(all(r['n'] == 1 for r in st['retry_jobs'].values()))
            second = O.do_send(self.cfg, None, None, st, 30, [], window_key='2026-10-28 12:00')
            self.assertEqual(reads[24:35], [str(i) for i in range(24, 35)])
            self.assertEqual(second['inspects'], 24)
            self.assertEqual(reads[35:48], [str(i) for i in range(13)])
            third = O.do_send(self.cfg, None, None, st, 30, [], window_key='2026-10-28 14:00')
            self.assertEqual(third['retry_pending'], 11)
            O.do_send(self.cfg, None, None, st, 30, [], window_key='2026-10-28 16:00')
        self.assertEqual(len(set(reads)), 35)
        self.assertEqual({i: Counter(reads)[str(i)] for i in range(24)}, {i: 3 for i in range(24)})
        self.assertTrue(all(str(i) in st['checked_jobs'] for i in range(24)))
        self.assertEqual(st['retry_jobs'], {})

    def test_same_window_and_changed_fingerprint(self):
        self.raw([self.job()]); st = HS.load_state(self.cfg)
        with patch.object(O, 'inspect_job', return_value=('skip_no_button', '')) as reader:
            first = O.do_send(self.cfg, None, None, st, 1, [], window_key='2026-10-28 10:00')
            second = O.do_send(self.cfg, None, None, HS.load_state(self.cfg), 1, [], window_key='2026-10-28 10:00')
            self.assertEqual(reader.call_count, 1)
            self.assertEqual(first['retry_pending'], 1)
            self.assertEqual(sum(second['quiet'].values()), 1)
            self.raw([dict(self.job(), salaryDesc='25-30K')])
            changed = HS.load_state(self.cfg)
            O.do_send(self.cfg, None, None, changed, 1, [], window_key='2026-10-28 10:00')
        self.assertEqual(changed['retry_jobs']['j1']['n'], 1)

    def test_halts_do_not_deliver_deferred_or_consume_retry(self):
        for status, reason in [('skip_captcha', 'captcha'), ('skip_network', 'network'),
                               ('skip_not_logged_in', 'network'), ('detail_unavailable', 'network')]:
            with self.subTest(status=status):
                # Independent failures must not reuse JD cached by a previous subtest.
                Path(self.cfg['paths']['state']).unlink(missing_ok=True)
                self.raw([self.job(str(i), '示例品牌行业') for i in range(5)])
                st = HS.load_state(self.cfg)
                with patch.object(O, 'inspect_job', side_effect=[('ready', '门店运营'), ('ready', '门店运营'), (status, ''), ('ready', '门店运营'), ('ready', '门店运营')]) as reader, \
                     patch.object(O, 'send_one') as sender:
                    data = O.do_send(self.cfg, None, None, st, 1, [])
                self.assertEqual(reader.call_count, 3)
                sender.assert_not_called()
                self.assertEqual(data['stop_reason'], reason)
                self.assertNotIn('2', st['retry_jobs'])
                self.assertNotIn('2', st['checked_jobs'])
                self.assertNotIn('2', HS.load_state(self.cfg)['checked_jobs'])

    def test_captcha_tick_json_failure(self):
        self.raw([self.job(str(i), '示例品牌行业') for i in range(5)])
        browser = Mock(); browser.close_own_tabs.return_value = 1; browser.connect.return_value = 'offline'
        self.path.write_text(json.dumps(self.cfg))
        args = B.build_parser().parse_args(['--config', str(self.path), 'tick', '--json'])
        stream = io.StringIO()
        with patch.object(B.CM, 'Browser', return_value=browser), \
             patch.object(O, 'check_login', return_value=(1, {'avatar': True})), \
             patch.object(O, 'inspect_job', side_effect=[('ready', '门店运营'), ('ready', '门店运营'), ('skip_captcha', '')]) as reader, \
             patch.object(O, 'send_one') as sender, redirect_stdout(stream):
            B.cmd_tick(args)
        data = json.loads(stream.getvalue())
        self.assertFalse(data['ok']); self.assertIn('captcha_wall', data['error'])
        self.assertEqual(reader.call_count, 3); sender.assert_not_called()
        st = HS.load_state(self.cfg)
        self.assertNotIn('2', st['retry_jobs']); self.assertNotIn('2', st['checked_jobs'])
        self.assertNotIn(self.now.strftime('%Y-%m-%d 22:00'), st.get('ticks_done', []))

    def test_day_rollover_preserves_permanent_maps(self):
        st = HS.load_state(self.cfg)
        st.update(day='2000-01-01', retry_jobs={'j1': {'fp': 'fp', 'n': 1, 'last': 'ts', 'window': 'w'}},
                  checked_jobs={'j1': 'fp'}, platform_chatted={'j2': 'ts'},
                  opened_no_greet={'j3': {'ts': 'ts', 'reason': 'fill_err'}})
        HS.save_state(self.cfg, st)
        loaded = HS.load_state(self.cfg)
        self.assertEqual(loaded['retry_jobs'], {}); self.assertEqual(loaded['checked_jobs'], {})
        self.assertEqual(loaded['platform_chatted'], {'j2': 'ts'})
        self.assertEqual(loaded['opened_no_greet'], st['opened_no_greet'])

    def test_send_retry_then_sent_is_not_checked(self):
        self.raw([self.job()]); st = HS.load_state(self.cfg)
        with patch.object(O, 'inspect_job', return_value=('ready', '商户拓展开城')), \
             patch.object(O, 'send_one', side_effect=['skip_no_button', 'sent']) as sender:
            O.do_send(self.cfg, None, None, st, 1, [], window_key='2026-10-28 10:00')
            self.assertEqual(st['retry_jobs']['j1']['n'], 1)
            O.do_send(self.cfg, None, None, st, 1, [], window_key='2026-10-28 12:00')
        self.assertEqual(sender.call_count, 2)
        self.assertIn('j1', st['sent']); self.assertNotIn('j1', st['checked_jobs'])
        self.assertNotIn('j1', st['retry_jobs'])

    def test_opened_terminal_and_summary(self):
        self.raw([self.job()]); st = HS.load_state(self.cfg)
        with patch.object(O, 'inspect_job', return_value=('ready', '商户拓展开城')), \
             patch.object(O, 'send_one', return_value='send_aborted:panel_not_ready') as sender:
            O.do_send(self.cfg, None, None, st, 1, [], window_key='2026-10-28 10:00')
            O.do_send(self.cfg, None, None, st, 1, [], window_key='2026-10-28 12:00')
        self.assertEqual(sender.call_count, 1)
        self.assertIn('j1', st['checked_jobs']); self.assertIn('j1', st['opened_no_greet'])
        summary = B.daily_summary(self.cfg, st)
        self.assertEqual(summary['opened_no_greet'], 1)
        self.assertIn('已开会话未发招呼 1 个', '\n'.join(B.summary_lines(summary)))

    def test_platform_chatted_never_read_even_after_rollover(self):
        self.raw([self.job()]); st = HS.load_state(self.cfg)
        with patch.object(O, 'inspect_job', return_value=('skip_already_chatted', '')) as reader:
            O.do_send(self.cfg, None, None, st, 1, [])
            self.assertIn('j1', st['platform_chatted'])
            st['day'] = '2000-01-01'; HS.save_state(self.cfg, st)
            O.do_send(self.cfg, None, None, HS.load_state(self.cfg), 1, [])
        self.assertEqual(reader.call_count, 1)

    def test_zero_retry_and_quota_return_contract(self):
        self.cfg['sending']['transient_retry_max'] = 0
        self.raw([self.job()]); st = HS.load_state(self.cfg)
        with patch.object(O, 'inspect_job', return_value=('skip_missing_jd', '')):
            O.do_send(self.cfg, None, None, st, 1, [])
        self.assertIn('j1', st['checked_jobs']); self.assertEqual(st['retry_jobs'], {})
        data = O.do_send(self.cfg, None, None, st, 0, [])
        self.assertEqual(data['stop_reason'], 'quota_exhausted')
        self.assertEqual(data['quiet'], {}); self.assertEqual(data['retry_pending'], 0)

    def test_send_stage_halts_for_a_and_deferred_b(self):
        for industry, jd in [('示例平台行业', '商户拓展开城'), ('示例品牌行业', '门店运营')]:
            for status, reason in [('skip_captcha', 'captcha'), ('skip_network', 'network'),
                                   ('skip_not_logged_in', 'network'), ('skip_min_gap(37)', 'quota_exhausted'),
                                   ('skip_daily_cap', 'quota_exhausted'), ('pending_verification', 'none'),
                                   ('unknown', 'none')]:
                with self.subTest(industry=industry, status=status):
                    self.raw([self.job(str(i), industry) for i in range(4)])
                    st = HS.load_state(self.cfg)
                    st['grade_cache'] = {}; st['b_pool'] = {}
                    with patch.object(O, 'inspect_job', return_value=('ready', jd)), \
                         patch.object(O, 'send_one', return_value=status) as sender:
                        data = O.do_send(self.cfg, None, None, st, 4, [])
                    self.assertEqual(sender.call_count, 1)
                    self.assertEqual(data['stop_reason'], reason)
                    self.assertEqual(st['retry_jobs'], {})
                    self.assertEqual(st['checked_jobs'], {})

    def test_cached_b_opened_terminal_is_not_redelivered(self):
        self.cfg['sending']['grade_quota'] = {'A': 0, 'B': 8}
        self.raw([self.job(industry='示例品牌行业')]); st = HS.load_state(self.cfg)
        with patch.object(O, 'inspect_job', return_value=('ready', '门店运营')), \
             patch.object(O, 'send_one', return_value='fill_err') as sender:
            O.do_send(self.cfg, None, None, st, 1, [], window_key='2026-10-28 10:00')
            self.assertIn('j1', st['b_pool'])
            O.do_send(self.cfg, None, None, HS.load_state(self.cfg), 1, [], window_key='2026-10-28 12:00')
        self.assertEqual(sender.call_count, 1)

    def test_existing_retry_at_limit_is_checked_without_reading(self):
        self.raw([self.job()]); st = HS.load_state(self.cfg)
        job, = HF.load_jobs(self.cfg)
        st['retry_jobs']['j1'] = {'fp': HF.stable_fingerprint(job, self.cfg), 'n': 3,
                                 'last': '2026-10-28 10:00', 'window': '2026-10-28 10:00'}
        with patch.object(O, 'inspect_job') as reader:
            data = O.do_send(self.cfg, None, None, st, 1, [], window_key='2026-10-28 12:00')
        reader.assert_not_called()
        self.assertIn('j1', st['checked_jobs']); self.assertEqual(st['retry_jobs'], {})
        self.assertEqual(data['quiet'], {'今日重试已用完': 1})

    def test_cached_c_and_ambiguous_ready_are_terminal(self):
        self.raw([self.job()]); st = HS.load_state(self.cfg)
        job, = HF.load_jobs(self.cfg)
        with patch.object(O, 'inspect_job', return_value=('ready', '普通职责')):
            O.prepare_grade(self.cfg, None, None, job, st)
        with patch.object(O, 'inspect_job') as reader, patch.object(O, 'send_one') as sender:
            data = O.do_send(self.cfg, None, None, st, 1, [])
        self.assertEqual(data['inspects'], 0)
        reader.assert_not_called(); sender.assert_not_called()
        self.assertIn('j1', st['checked_jobs'])
        st['checked_jobs'].clear(); st['grade_cache'].clear()
        with patch.object(O, 'prepare_grade', return_value={'grade': 'B?', 'final': False, 'status': 'ready'}), \
             patch.object(O, 'send_one') as sender:
            O.do_send(self.cfg, None, None, st, 1, [])
        sender.assert_not_called()
        self.assertIn('j1', st['checked_jobs'])

    def test_send_exception_preserves_outcome_maps(self):
        self.raw([self.job()]); st = HS.load_state(self.cfg)
        with patch.object(O, 'inspect_job', return_value=('ready', '商户拓展开城')), \
             patch.object(O, 'send_one', side_effect=RuntimeError('unresolved_send: test')):
            with self.assertRaisesRegex(RuntimeError, 'unresolved_send'):
                O.do_send(self.cfg, None, None, st, 1, [])
        for key in ('checked_jobs', 'retry_jobs', 'platform_chatted', 'opened_no_greet'):
            self.assertEqual(st[key], {})

    def test_greeting_configuration_errors_are_terminal_and_alert(self):
        for status in ('err_no_greeting', 'err_template_too_long'):
            with self.subTest(status=status):
                self.raw([self.job()]); st = HS.load_state(self.cfg); out = []
                st['checked_jobs'] = {}
                with patch.object(O, 'inspect_job', return_value=('ready', '商户拓展开城')), \
                     patch.object(O, 'send_one', return_value=status):
                    O.do_send(self.cfg, None, None, st, 1, out)
                self.assertIn('j1', st['checked_jobs'])
                self.assertTrue(any('配置问题' in line and status in line for line in out))
