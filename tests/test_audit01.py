"""audit-01：结构体检发现的问题回归用例。只用虚构数据与临时目录，不连浏览器、不调模型。"""
import io
import json
import time
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import Mock, patch

from test_phase_a import Base, B, HS, O
import test_broadcast_visibility_20261029 as V
import hunt_filter as HF


class ConnLinesTests(Base):
    """audit-01 #1：runs.log 的 bridge/extension 两行要写真实连接结果，不能恒为空。"""

    def setUp(self):
        super().setUp()
        self.browser = Mock()
        self.browser.opened = []
        self.browser.connect.return_value = 'bridge_ok'
        self.browser.wake = 'extension_ok'
        self.browser.new_tab.return_value = 1
        for target, name, value in [
                (B.CM, 'Browser', self.browser), (O, 'cleanup_browser', 0),
                (B.time, 'sleep', None), (B, 'inbox', {}),
                (O, 'check_login', (1, {'avatar': True, 'url': 'https://example.invalid/'})),
                (O, 'scan', (0, [])), (O, 'do_send', {'sent': 0, 'items': [], 'skipped': []}),
                (HS, 'candidates', [])]:
            p = patch.object(target, name, return_value=value) if name != 'sleep' else patch.object(target, name)
            p.start()
            self.addCleanup(p.stop)

    def log_after(self, *argv):
        args = B.build_parser().parse_args(['--config', str(self.path), *argv])
        with redirect_stdout(io.StringIO()):
            args.func(args)
        return Path(self.cfg['paths']['log']).read_text(encoding='utf-8')

    def test_each_command_logs_real_connection(self):
        for argv in (['inbox'], ['login-check'], ['scan'], ['send', '1'], ['run', '--send', '0']):
            with self.subTest(cmd=argv[0]):
                Path(self.cfg['paths']['log']).unlink(missing_ok=True)
                log = self.log_after(*argv)
                self.assertIn('- bridge:bridge_ok\n', log)
                self.assertIn('- extension:extension_ok\n', log)


class InboxListLabelTests(unittest.TestCase):
    """audit-01 #2：inbox 概览括号里的数是会话列表总数，不是「新招呼」。"""
    setUp = V.BroadcastTests.setUp
    save = V.BroadcastTests.save

    def test_overview_labels_list_total(self):
        now_ms = int(time.time() * 1000)
        items = [{'encryptJobId': 'jy%d' % i, 'brandName': '虚构公司%d' % i, 'name': '虚构HR%d' % i,
                  'lastMessageInfo': {'showText': '您好', 'msgTime': now_ms - 100 * 3600 * 1000,
                                      'fromSelf': False}} for i in range(3)]
        self.browser.network.return_value = {'text': json.dumps(
            {'code': 0, 'zpData': {'result': items}}, ensure_ascii=False)}
        st = HS.load_state(self.cfg)
        out = []
        with patch.object(B, 'scan_conv_states', return_value=dict(
                checked=0, failed=0, skipped_failures=0, deadline=False)), \
             patch.object(HF, 'load_jobs', return_value=[]):
            B.inbox(self.cfg, self.browser, 1, st, out)
        line = next(x for x in out if x.startswith('近 '))
        self.assertIn('（列表 3）', line)
        self.assertNotIn('新招呼', line)


class DeadCodeAfterCleanup02Tests(unittest.TestCase):
    """audit-01 #3/#4：这些符号在 scripts/ 里零调用方（#3 是 cleanup-02 的残留，#4 是 quota_for_now），已删除。"""

    def test_removed_symbols_stay_removed(self):
        import chat_dom as CD
        import greet as G
        import send_safety as SS
        import hunt_schedule as HQ
        for module, names in [(CD, ('goto_tab', 'NEW_GREET_TAB', 'clean_item',
                                    'history_fingerprint')),
                              (G, ('bound_reply',)), (SS, ('reply_operation',)),
                              (HQ, ('quota_for_now',)), (SS, ('terminal_operation',))]:
            for name in names:
                with self.subTest(name=name):
                    self.assertFalse(hasattr(module, name))


class LedgerSendLegacyBranchTests(unittest.TestCase):
    """audit-01 #5：发送闸门不再有旧 auto_reply/interview_reply/followup 的额度/去重分支；在用命令行为不变。"""

    def setUp(self):
        import tempfile
        import send_safety as SS
        self.SS = SS
        self.tmp = tempfile.TemporaryDirectory(prefix='audit01-ledger-')
        self.addCleanup(self.tmp.cleanup)
        root = Path(self.tmp.name)
        self.cfg = {'paths': {'workdir': str(root), 'state': str(root / 'state.json')},
                    'schedule': {'timezone': 'Asia/Shanghai'}, 'sending': {'min_gap_seconds': 0},
                    'inbox': {'auto_reply': {'daily_cap': 0}}}

    def test_old_inbox_cap_no_longer_read(self):
        send = Mock(return_value='replied')
        self.assertEqual(self.SS.ledger_send(self.cfg, 'auto_reply', 'id:1', '', '虚构', send), 'replied')
        send.assert_called_once()

    def test_live_commands_unchanged(self):
        # 在用的三个命令从来不受 inbox.auto_reply.daily_cap 影响；send 仍按 job_id 去重
        send = Mock(return_value='sent')
        self.assertEqual(self.SS.ledger_send(self.cfg, 'send', 'job:j1', 'j1', '虚构招呼', send), 'sent')
        self.assertEqual(self.SS.ledger_send(self.cfg, 'send', 'job:j1', 'j1', '虚构招呼', send), 'sent')
        self.assertEqual(send.call_count, 1)
        for cmd in ('conv_text', 'conv_resume'):
            with self.subTest(cmd=cmd):
                sender = Mock(return_value='sent')
                self.assertEqual(self.SS.ledger_send(self.cfg, cmd, 'pair:虚构%s|虚构HR' % cmd, '', '虚构', sender), 'sent')
                sender.assert_called_once()


class SharedSendBudgetTests(Base):
    """audit-01 #6：tick --dry-run 报的预算/配额和 do_send 真用的是同一个函数算的。"""

    def test_dry_run_and_do_send_share_send_budget(self):
        fake = (1, {'A': 1, 'B': 0})
        with patch.object(O, 'send_budget', return_value=fake) as budget:
            args = B.build_parser().parse_args(['--config', str(self.path), 'tick', '--dry-run', '--json'])
            buf = io.StringIO()
            with redirect_stdout(buf), patch.object(B.HQ, 'tick_plan', return_value={
                    'actions': ['send'], 'now': '10:00', 'timezone': 'Asia/Shanghai', 'window': '10:00'}):
                args.func(args)
            data = json.loads(buf.getvalue())
            self.assertEqual((data['budget']['limit'], data['quotas']), fake)
            st = HS.load_state(self.cfg)
            with patch.object(O.SS, 'ledger_guard'), patch.object(HS, 'candidates', return_value=[]), \
                 patch.object(HF, 'load_jobs', return_value=[]):
                res = O.do_send(self.cfg, None, None, st, None, [])
            self.assertEqual(res['quotas'], fake[1])
            self.assertEqual(budget.call_count, 2)

    def test_matrix_matches_expected_samples(self):
        # 与改前逐项对照过 384 种组合（scratchpad 脚本）；这里固定几组代表值
        st = HS.load_state(self.cfg)
        now = B.HC.now_local(self.cfg).replace(hour=12, minute=0)
        st['day_count'], st['day_by_grade'] = 0, {'A': 0, 'B': 0}
        self.assertEqual(O.send_budget(self.cfg, st, now), (8, {'A': 4, 'B': 2}))
        self.assertEqual(O.send_budget(self.cfg, st, now, want=1)[0], 1)
        st['day_count'] = 30
        self.assertEqual(O.send_budget(self.cfg, st, now)[0], 0)


class InitTemplateTests(unittest.TestCase):
    """audit-01 #8/#9：init 生成的配置不能写死旧回执正则，也不能把数据目录钉在 ~/.boss-hunt/data。"""

    def setUp(self):
        import tempfile
        self.tmp = tempfile.TemporaryDirectory(prefix='audit01-init-')
        self.addCleanup(self.tmp.cleanup)
        self.path = Path(self.tmp.name) / 'elsewhere' / 'config.json'
        args = B.build_parser().parse_args(['--config', str(self.path), 'init'])
        with redirect_stdout(io.StringIO()):
            args.func(args)
        self.raw = json.loads(self.path.read_text(encoding='utf-8'))

    def test_template_uses_code_default_resume_receipt(self):
        self.assertNotIn('resume_sent_pattern', self.raw.get('conversation') or {})

    def test_workdir_follows_config_location(self):
        cfg = B.HC.load_config(str(self.path), create_dirs=False)
        self.assertEqual(cfg['paths']['workdir'], str(self.path.parent / 'data'))


class DryRunOutsideWindowTests(Base):
    """audit-01 #10：窗口外这跳不投递，干跑的预算和各级配额都应是 0，不能报 A4/B4。"""

    def test_quotas_zero_when_no_send_action(self):
        args = B.build_parser().parse_args(['--config', str(self.path), 'tick', '--dry-run', '--json'])
        buf = io.StringIO()
        with redirect_stdout(buf), patch.object(B.HQ, 'tick_plan', return_value={
                'actions': [], 'now': '23:00', 'timezone': 'Asia/Shanghai', 'window': None}):
            args.func(args)
        data = json.loads(buf.getvalue())
        self.assertEqual(data['budget']['limit'], 0)
        self.assertFalse(any(data['quotas'].values()))


class GreetTemperatureZeroTests(unittest.TestCase):
    """audit-01 #11：user.greeting.llm.temperature=0 要原样传给模型，不能被 `or 0.7` 吞成 0.7。"""

    def test_zero_temperature_passed_through(self):
        import greet as G
        for provider, read in [('ollama', lambda p: p['options']['temperature']),
                               ('deepseek', lambda p: p['temperature'])]:
            with self.subTest(provider=provider):
                cfg = {'user': {'greeting': {'llm': {'provider': provider, 'temperature': 0,
                                                     'api_key': 'fake-test-key', 'base_url': 'http://127.0.0.1:9'}}}}
                seen = {}
                def post(url, payload, timeout, headers=None):
                    seen.update(payload)
                    return {'response': '虚构', 'choices': [{'message': {'content': '虚构'}}]}
                with patch.object(G, '_post', side_effect=post):
                    G._generate_raw(cfg, '虚构提示词')
                self.assertEqual(read(seen), 0.0)


class DumpConversationsImportTests(unittest.TestCase):
    """audit-01 #12：dump_conversations 从自己所在的 scripts/ 导入，不再写死 ~/.hermes 安装目录。"""

    def test_imports_from_own_directory(self):
        src = (Path(__file__).resolve().parents[1] / 'scripts' / 'dump_conversations.py').read_text(encoding='utf-8')
        self.assertNotIn('~/.hermes', src)
        self.assertIn('os.path.dirname(os.path.abspath(__file__))', src)

    def test_msg_time_uses_config_timezone(self):
        import dump_conversations as D
        self.assertEqual(D.fmt_ms(1793000000000, {'schedule': {'timezone': 'Asia/Shanghai'}}), '2026-10-26 15:33')


class DiagTimestampTests(unittest.TestCase):
    """audit-01 #13：诊断文件与招呼语缓存的时间按配置时区写（本机是 PST，此前差 16 小时）。"""

    def test_resume_trace_ts_in_config_timezone(self):
        import tempfile
        import conv_actions as A
        with tempfile.TemporaryDirectory(prefix='audit01-trace-') as d:
            cfg = {'paths': {'workdir': d}, 'conversation': {}, 'schedule': {'timezone': 'Asia/Shanghai'}}
            A._resume_diag(cfg, 'fiction_event')
            row = json.loads((Path(d) / 'resume_trace.jsonl').read_text(encoding='utf-8').splitlines()[-1])
        self.assertTrue(row['ts'].endswith('+08:00'), row['ts'])

    def test_greet_cache_stamp_uses_config_clock(self):
        import greet as G
        with patch.object(G.HC, 'stamp', return_value='2026-11-06 21:00') as stamp:
            self.assertEqual(G._cache_stamp({'schedule': {'timezone': 'Asia/Shanghai'}}), '2026-11-06 21:00')
        stamp.assert_called_once()


class MissingConfigTests(unittest.TestCase):
    """audit-01 #14：配置文件不存在时给一句提示（--json 时是一个 JSON 对象），不甩报错堆栈。"""

    def run_main(self, *argv):
        import contextlib
        import tempfile
        with tempfile.TemporaryDirectory(prefix='audit01-nocfg-') as d:
            missing = str(Path(d) / 'nope' / 'config.json')
            out, err = io.StringIO(), io.StringIO()
            with patch.object(B.sys, 'argv', ['boss_hunt.py', '--config', missing, *argv]), \
                 redirect_stdout(out), contextlib.redirect_stderr(err):
                rc = B.main()
            created = (Path(d) / 'nope').exists()
        return rc, out.getvalue(), err.getvalue(), created

    def test_text_mode(self):
        rc, out, err, created = self.run_main('validate')
        self.assertEqual(rc, 1)
        self.assertIn('配置文件不存在', err)
        self.assertNotIn('Traceback', err)
        self.assertFalse(created)

    def test_json_mode(self):
        rc, out, _, _ = self.run_main('--json', 'plan')
        data = json.loads(out)
        self.assertEqual((rc, data['ok'], data['cmd'], data['error']), (1, False, 'plan', 'config_not_found'))


class InboxPendingClearedByChatTests(unittest.TestCase):
    """audit-01 Q2：对话模块已回过面试固定话术的会话，inbox 不再登记/保留「待你确认」。"""
    setUp = V.BroadcastTests.setUp
    save = V.BroadcastTests.save

    def run_inbox(self, templates, preset_pending=True):
        import conv_rules
        import conv_store
        now_ms = int(time.time() * 1000)
        item = {'encryptJobId': 'jz1', 'conv_key': 'id:fiction-z1', 'brandName': '虚构丙公司', 'name': '虚构HR丙',
                'lastMessageInfo': {'showText': '明天下午方便来面试吗', 'msgTime': now_ms - 60000, 'fromSelf': False}}
        row = conv_store.new_record('id:fiction-z1', 'reply', 'jz1', '虚构丙公司', '虚构HR丙', '虚构岗位', time.time())
        row['status'] = 'manual'
        row['sent_templates'] = [conv_rules.INTERVIEW_REPLY] if templates else []
        conv_store.save(str(Path(self.cfg['paths']['workdir']) / 'conversations.json'), {'id:fiction-z1': row})
        self.browser.network.return_value = {'text': json.dumps(
            {'code': 0, 'zpData': {'result': [item]}}, ensure_ascii=False)}
        st = HS.load_state(self.cfg)
        if preset_pending:
            st['pending'] = {'jz1': {'brand': '虚构丙公司'}}
        with patch.object(B, 'scan_conv_states', return_value=dict(
                checked=0, failed=0, skipped_failures=0, deadline=False)), \
             patch.object(B, 'snapshot_message_owner', return_value='friend'), \
             patch.object(HF, 'load_jobs', return_value=[]):
            data = B.inbox(self.cfg, self.browser, 1, st, [])
        return data, st

    def test_chat_replied_interview_clears_pending(self):
        data, st = self.run_inbox(templates=True)
        self.assertNotIn('jz1', st['pending'])
        self.assertIn('jz1', st['replied'])
        self.assertEqual([m['replied'] for m in data['interviews']], [True])

    def test_not_replied_still_pending(self):
        data, st = self.run_inbox(templates=False, preset_pending=False)
        self.assertIn('jz1', st['pending'])
        self.assertEqual([m['replied'] for m in data['interviews']], [False])


class InboxNotBlockedByPendingSendTests(Base):
    """audit-01 Q3：inbox 只读，发送流水有待核对时只提示、照常读；投递/对话的闸门不变。"""

    def setUp(self):
        super().setUp()
        import send_safety as SS
        self.SS = SS
        self.browser = Mock()
        self.browser.opened = []
        self.browser.connect.return_value = 'bridge_ok'
        self.browser.wake = 'extension_ok'
        self.browser.new_tab.return_value = 1
        item = {'encryptJobId': 'jq1', 'brandName': '虚构丁公司', 'name': '虚构HR丁',
                'lastMessageInfo': {'showText': '您好', 'msgTime': int(time.time() * 1000) - 60000}}
        self.browser.network.return_value = {'text': json.dumps({'code': 0, 'zpData': {'result': [item]}})}
        for target, name, value in [(B.CM, 'Browser', self.browser), (O, 'cleanup_browser', 0),
                                    (HF, 'load_jobs', []),
                                    (B, 'scan_conv_states', dict(checked=0, failed=0, skipped_failures=0, deadline=False))]:
            p = patch.object(target, name, return_value=value)
            p.start()
            self.addCleanup(p.stop)
        p = patch.object(B.time, 'sleep')
        p.start()
        self.addCleanup(p.stop)

    def pend(self, cmd, conv_key):
        self.SS.ledger_append(self.cfg, dict(event_id='fiction-e-' + cmd, status='unknown', cmd=cmd, conv_key=conv_key,
                                             job_id='jx', text='虚构', text_sha256='0' * 64,
                                             ts='2026-11-06 10:00', ts_epoch=1793930000000))

    def cli(self, *argv):
        args = B.build_parser().parse_args(['--config', str(self.path), '--json', *argv])
        buf = io.StringIO()
        with redirect_stdout(buf):
            rc = args.func(args)
        return rc, json.loads(buf.getvalue())

    def test_conv_pending_inbox_still_reads(self):
        self.pend('conv_text', 'pair:虚构戊|虚构HR戊')
        rc, data = self.cli('inbox')
        self.assertEqual(rc, 0)
        self.assertTrue(data['pending_verification'])
        self.assertEqual(data['messages_total'], 1)

    def test_outbound_pending_inbox_still_reads_but_send_blocked(self):
        self.pend('send', 'job:jx')
        rc, data = self.cli('inbox')
        self.assertEqual(rc, 0)
        self.assertTrue(data['pending_verification'])
        self.assertEqual(data['messages_total'], 1)
        rc, data = self.cli('send', '1')
        self.assertEqual((rc, data['ok']), (1, False))
        self.assertIn('unresolved_send', data['error'])


class MinGapDefaultTests(unittest.TestCase):
    """audit-01 Q4：sending.min_gap_seconds 未写时，账本闸门/对话/投递统一按 60 秒；显式值照用。"""

    def setUp(self):
        import tempfile
        import send_safety as SS
        self.SS = SS
        self.tmp = tempfile.TemporaryDirectory(prefix='audit01-gap-')
        self.addCleanup(self.tmp.cleanup)
        self.cfg = {'paths': {'workdir': self.tmp.name, 'state': str(Path(self.tmp.name) / 'state.json')},
                    'schedule': {'timezone': 'Asia/Shanghai'}, '_clock': lambda: 1000.0}

    def test_missing_key_defaults_to_60(self):
        self.assertEqual(self.SS.min_gap_seconds(self.cfg), 60)
        send = Mock(return_value='sent')
        self.assertEqual(self.SS.ledger_send(self.cfg, 'conv_text', 'pair:虚构甲|虚构HR', '', '虚构一', send), 'sent')
        self.assertEqual(self.SS.ledger_send(self.cfg, 'conv_text', 'pair:虚构乙|虚构HR', '', '虚构二', send),
                         'skip_min_gap(60)')
        self.assertEqual(send.call_count, 1)

    def test_explicit_values_respected(self):
        for value, expected in ((0, 0), (90, 90)):
            with self.subTest(value=value):
                self.cfg['sending'] = {'min_gap_seconds': value}
                self.assertEqual(self.SS.min_gap_seconds(self.cfg), expected)


class RegexValidationTests(Base):
    """audit-01 Q5：validate 检查 inbox.patterns / conversation.patterns / conversation.system_patterns 能否编译；
    对话跳启动先校验配置，有错不连浏览器。"""

    def errs(self, **patch_cfg):
        import copy
        import hunt_schedule as HQ
        cfg = copy.deepcopy(self.cfg)
        for dotted, value in patch_cfg.items():
            section, key = dotted.split('__')
            cfg.setdefault(section, {})[key] = value
        return HQ.validate_cfg(cfg)[0]

    def test_bad_regexes_reported(self):
        cases = {'inbox__patterns': {'interview': '面试('},
                 'conversation__patterns': {'resume': '[简历'},
                 'conversation__system_patterns': ['(?P<x'],
                 }
        for dotted, value in cases.items():
            with self.subTest(key=dotted):
                self.assertTrue(any(dotted.replace('__', '.') in e for e in self.errs(**{dotted: value})))

    def test_wrong_types_reported(self):
        self.assertTrue(any('inbox.patterns' in e for e in self.errs(inbox__patterns=['面试'])))
        self.assertTrue(any('conversation.system_patterns' in e for e in self.errs(conversation__system_patterns='x')))

    def test_good_regexes_pass(self):
        before = set(self.errs())
        after = set(self.errs(inbox__patterns={'interview': '面试|复试'},
                              conversation__patterns={'resume': '简历'},
                              conversation__system_patterns=['系统提示.{0,5}']))
        self.assertEqual(after, before)

    def test_chat_refuses_invalid_config_before_connecting(self):
        raw = json.loads(self.path.read_text())
        raw['conversation'] = {'enabled': True, 'hours': list(range(24)), 'patterns': {'resume': '[简历'}}
        self.path.write_text(json.dumps(raw))
        browser = Mock()
        with patch.object(B.CM, 'Browser', return_value=browser):
            args = B.build_parser().parse_args(['--config', str(self.path), '--json', 'chat'])
            buf = io.StringIO()
            with redirect_stdout(buf):
                rc = args.func(args)
        data = json.loads(buf.getvalue())
        self.assertEqual((rc, data['ok']), (1, False))
        self.assertIn('config_invalid', data['error'])
        browser.connect.assert_not_called()


class ExitCodeAndJsonFieldTests(ConnLinesTests):
    """audit-01 Q6：没做成的命令退出码 1；send/inbox JSON 带 ok；unanswered JSON 带 schema/cmd。"""

    def cli(self, *argv):
        args = B.build_parser().parse_args(['--config', str(self.path), '--json', *argv])
        buf = io.StringIO()
        with redirect_stdout(buf):
            rc = args.func(args)
        return rc, json.loads(buf.getvalue())

    def test_login_check_not_logged_returns_1(self):
        with patch.object(O, 'check_login', return_value=(1, {'login': True, 'url': 'https://example.invalid/'})):
            rc, data = self.cli('login-check')
        self.assertEqual((rc, data['ok']), (1, False))
        rc, data = self.cli('login-check')
        self.assertEqual((rc, data['ok']), (0, True))

    def test_scan_no_pages_returns_1(self):
        rc, data = self.cli('scan')
        self.assertEqual((rc, data['ok']), (1, False))

    def test_send_and_inbox_json_have_ok(self):
        for argv in (['send', '1'], ['inbox']):
            with self.subTest(cmd=argv[0]):
                rc, data = self.cli(*argv)
                self.assertEqual((rc, data.get('ok')), (0, True))

    def test_unanswered_json_has_schema_and_cmd(self):
        rc, data = self.cli('unanswered')
        self.assertEqual((rc, data['schema'], data['cmd'], data['ok']), (0, 2, 'unanswered', True))

    def test_each_command_logs_real_connection(self):
        pass  # 父类用例不在这里重复跑


class EnvKeyLookupTests(unittest.TestCase):
    """audit-01 Q11：模型 key 依次取 环境变量 → llm.env_file / $BOSS_HUNT_ENV_FILE → ~/.boss-hunt/.env → ~/.hermes/.env（兼容）。"""

    def setUp(self):
        import tempfile
        self.tmp = tempfile.TemporaryDirectory(prefix='audit01-env-')
        self.addCleanup(self.tmp.cleanup)
        self.home = Path(self.tmp.name)
        env = {k: v for k, v in __import__('os').environ.items()
               if k not in ('FICTION_KEY', 'BOSS_HUNT_ENV_FILE')}
        env['HOME'] = str(self.home)
        p = patch.dict('os.environ', env, clear=True)
        p.start()
        self.addCleanup(p.stop)

    def write(self, rel, value):
        path = self.home / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text('OTHER=x\nFICTION_KEY="%s"\n' % value, encoding='utf-8')
        return path

    def key(self, llm=None):
        import greet as G
        return G._env_key('FICTION_KEY', llm or {})

    def test_order(self):
        self.write('.hermes/.env', 'from-hermes')
        self.assertEqual(self.key(), 'from-hermes')
        self.write('.boss-hunt/.env', 'from-boss-hunt')
        self.assertEqual(self.key(), 'from-boss-hunt')
        custom = self.write('elsewhere/my.env', 'from-env-file-var')
        __import__('os').environ['BOSS_HUNT_ENV_FILE'] = str(custom)
        self.assertEqual(self.key(), 'from-env-file-var')
        cfg_file = self.write('cfg/keys.env', 'from-config')
        self.assertEqual(self.key({'env_file': str(cfg_file)}), 'from-config')
        __import__('os').environ['FICTION_KEY'] = 'from-environ'
        self.assertEqual(self.key({'env_file': str(cfg_file)}), 'from-environ')

    def test_missing_everywhere_is_empty(self):
        self.assertEqual(self.key(), '')


class LedgerSendNoOpIdTriggerTests(unittest.TestCase):
    """audit-01 Q8：发送闸门里没有调用方的 op_id / trigger 去重分支已删；元数据照常记账。"""

    def test_metadata_keys_are_just_recorded(self):
        import tempfile
        import send_safety as SS
        with tempfile.TemporaryDirectory(prefix='audit01-opid-') as d:
            cfg = {'paths': {'workdir': d, 'state': str(Path(d) / 'state.json')},
                   'schedule': {'timezone': 'Asia/Shanghai'}, 'sending': {'min_gap_seconds': 0}}
            send = Mock(return_value='sent')
            meta = {'trigger': 'fiction-trigger', 'op_id': 'fiction-op'}
            for _ in range(2):
                self.assertEqual(SS.ledger_send(cfg, 'conv_text', 'pair:虚构|虚构HR', '', '虚构', send, dict(meta)), 'sent')
            self.assertEqual(send.call_count, 2)
            self.assertEqual(SS.ledger_latest(cfg)[-1]['trigger'], 'fiction-trigger')


class StateUnusedFieldsTests(Base):
    """audit-01 Q8：state 不再默认带没人写的 inbox_ts / resume_followups；旧 state 里已有的照常保留、不报错。"""

    def test_defaults_dropped_and_legacy_keys_kept(self):
        st = HS.load_state(self.cfg)
        self.assertNotIn('inbox_ts', st)
        self.assertNotIn('resume_followups', st)
        st['inbox_ts'] = {'fiction': 1}
        st['resume_followups'] = []
        HS.save_state(self.cfg, st)
        again = HS.load_state(self.cfg)
        self.assertEqual(again['inbox_ts'], {'fiction': 1})


class InboxUnifiedRulesTests(unittest.TestCase):
    """audit-01 Q7：收件箱的拒信/系统消息判定与对话模块统一（历史 301 条比对：差异全部是收件箱旧规则漏判/误判）。"""
    CFG = {}

    def test_rejects_previously_missed(self):
        for text in ('嗨~综合评估不太合适目前的岗位哦，后续有合适的再联系',
                     '本轮初筛已结束，经综合评估，有更适配该岗位的候选人进入后续环节',
                     '您的专业技能与我们目前的职位需求并不完全吻合，祝顺利',
                     '很抱歉我们正在寻找其他背景的候选人'):
            with self.subTest(text=text):
                self.assertEqual(B.classify(text, self.CFG), 'reject')

    def test_resume_requests_not_system(self):
        for text in ('您与我们在招职位匹配度蛮高的，方便投递一份您的附件简历吗？',
                     '方便发一份您的附件简历过来吗？'):
            with self.subTest(text=text):
                self.assertFalse(B.is_system_text(text, self.CFG))

    def test_real_system_texts_still_system(self):
        for text in ('对方已同意，您的附件简历已发送给对方', '附件简历已发送', '虚构.pdf 点击预览附件简历'):
            with self.subTest(text=text):
                self.assertTrue(B.is_system_text(text, self.CFG))

    def test_explicit_inbox_overrides_still_win(self):
        cfg = {'inbox': {'patterns': {'reject': '虚构拒绝词', 'system': '虚构系统词'}}}
        self.assertEqual(B.classify('这里有虚构拒绝词', cfg), 'reject')
        self.assertNotEqual(B.classify('综合评估不太合适', cfg), 'reject')
        self.assertTrue(B.is_system_text('虚构系统词', cfg))

    def test_interview_and_interest_unchanged(self):
        self.assertEqual(B.classify('明天下午方便来面试吗', self.CFG), 'interview')
        self.assertEqual(B.classify('对你挺感兴趣的', self.CFG), 'interested')


if __name__ == '__main__':
    unittest.main()
