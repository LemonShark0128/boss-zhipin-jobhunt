"""audit-01 U2：回消息跳的 wrapper 与播报收进仓库（scripts/cron/），播报不再报旧链路的「已回/失败」。

虚构数据、临时目录；wrapper 用替身 boss_hunt.py，不连浏览器。
"""
import fcntl
import importlib.util
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CRON = ROOT / 'scripts' / 'cron'


def load_fmt():
    spec = importlib.util.spec_from_file_location('inbox_fmt', CRON / 'boss-hunt-inbox-fmt.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def run_fmt(payload):
    raw = payload if isinstance(payload, str) else json.dumps(payload, ensure_ascii=False)
    p = subprocess.run([sys.executable, str(CRON / 'boss-hunt-inbox-fmt.py')], input=raw,
                       capture_output=True, text=True)
    return p.returncode, p.stdout


class InboxFmtTests(unittest.TestCase):
    def test_overview_has_no_legacy_reply_counts(self):
        data = {'cmd': 'inbox', 'interviews': [{'brand': '虚构甲公司', 'text': '明天方便来面试吗'}],
                'interest': [{'brand': '虚构乙公司'}], 'unanswered': {'counts': {'waiting_me': 2}},
                'auto_replied': [], 'replied_ok': [], 'auto_failed': [], 'reply_failed': []}
        rc, out = run_fmt(data)
        self.assertEqual(rc, 0)
        self.assertEqual(out.splitlines()[0], '💬 回消息跳｜约面试 1｜感兴趣 1｜待你回 2')
        self.assertIn('· 虚构甲公司｜明天方便来面试吗', out)
        self.assertNotIn('已回', out)
        self.assertNotIn('失败', out)

    def test_failure_and_pending_surface_first(self):
        rc, out = run_fmt({'ok': False, 'error': 'inbox_network_failed', 'pending_verification': True})
        lines = out.splitlines()
        self.assertEqual(rc, 0)
        self.assertEqual(lines[0], '⚠️ BOSS 回消息跳失败：inbox_network_failed')
        self.assertIn('待核对', lines[1])
        self.assertNotIn('ledger-ack', out)

    def test_interviews_capped_at_three(self):
        data = {'interviews': [{'brand': '虚构%d' % i, 'text': '面试'} for i in range(5)]}
        lines = load_fmt().render(data)
        self.assertEqual(sum(l.startswith('· 虚构') for l in lines), 3)
        self.assertEqual(lines[-1], '· 另有 2 个约面试')

    def test_noise_before_json_and_garbage(self):
        rc, out = run_fmt('warning: fiction noise\n' + json.dumps({'interviews': []}))
        self.assertEqual((rc, out.strip()), (0, '💬 回消息跳｜约面试 0｜感兴趣 0｜待你回 0'))
        rc, out = run_fmt('not json at all')
        self.assertEqual(rc, 0)
        self.assertIn('没有 JSON', out)


class InboxWrapperTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix='audit01-inbox-wrapper-')
        self.addCleanup(self.tmp.cleanup)
        self.repo = Path(self.tmp.name) / 'fiction-repo'
        cron = self.repo / 'scripts' / 'cron'
        cron.mkdir(parents=True)
        for name in ('boss-hunt-inbox.sh', 'boss-hunt-inbox-fmt.py'):
            shutil.copy(CRON / name, cron / name)
        self.script = cron / 'boss-hunt-inbox.sh'
        self.config = self.repo / 'config.json'
        self.config.write_text(json.dumps({'paths': {'workdir': str(self.repo / 'data')}}))
        self.env = dict(os.environ, BOSS_HUNT_CONFIG=str(self.config), BOSS_HUNT_PYTHON=sys.executable)

    def fake_cli(self, body):
        (self.repo / 'scripts' / 'boss_hunt.py').write_text(body)

    def run_wrapper(self):
        return subprocess.run(['bash', str(self.script)], env=self.env, capture_output=True, text=True)

    def test_runs_inbox_json_and_renders(self):
        self.fake_cli('import json, sys\nassert sys.argv[-2:] == ["--json", "inbox"], sys.argv\n'
                      'print(json.dumps({"cmd": "inbox", "interviews": []}))\n')
        p = self.run_wrapper()
        self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
        self.assertEqual(p.stdout.strip(), '💬 回消息跳｜约面试 0｜感兴趣 0｜待你回 0')

    def test_business_exit_code_preserved(self):
        self.fake_cli('import json, sys\nprint(json.dumps({"ok": False, "error": "fiction"}))\nsys.exit(7)\n')
        p = self.run_wrapper()
        self.assertEqual(p.returncode, 7)
        self.assertIn('回消息跳失败：fiction', p.stdout)

    def test_shared_lock_skips_quietly(self):
        self.fake_cli('print("fiction business ran")\n')
        (self.repo / 'data').mkdir()
        with (self.repo / 'data' / '.wrapperlock').open('a+') as lock:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            p = self.run_wrapper()
        self.assertEqual(p.returncode, 0)
        self.assertNotIn('fiction business ran', p.stdout)
        self.assertIn('另一个实例在跑', p.stdout)


class TickFmtTests(unittest.TestCase):
    """audit-01 R3：投递跳播报里的收件箱段与回消息跳同一口径（不报旧链路「已回/失败」）；broadcasts.log 时间按配置时区。"""

    def run_tick_fmt(self, data):
        with tempfile.TemporaryDirectory(prefix='audit01-tickfmt-') as d:
            log = Path(d) / 'broadcasts.log'
            env = dict(os.environ, BOSS_BROADCAST_LOG=str(log))
            p = subprocess.run([sys.executable, str(ROOT / 'scripts' / 'boss-hunt-tick-fmt.py')],
                               input=json.dumps(data, ensure_ascii=False), capture_output=True, text=True, env=env)
            return p.returncode, p.stdout, log.read_text(encoding='utf-8')

    def test_inbox_section_new_wording(self):
        data = {'cmd': 'tick', 'ok': True, 'actions': ['inbox'], 'window': '10:00', 'timezone': 'Asia/Shanghai',
                'inbox': {'interviews': [{'brand': '虚构'}], 'interest': [], 'unanswered': {'counts': {'waiting_me': 2}},
                          'auto_reply_counts': {'new_greet': 0}}}
        rc, out, _ = self.run_tick_fmt(data)
        self.assertEqual(rc, 0)
        self.assertIn('💬 回消息｜约面试 1｜感兴趣 0｜待你回 2', out)
        self.assertNotIn('已回', out)
        self.assertNotIn('自动回话', out)

    def test_broadcast_log_uses_config_timezone(self):
        from datetime import datetime
        from zoneinfo import ZoneInfo
        data = {'cmd': 'tick', 'ok': False, 'error': 'fiction_error', 'actions': [], 'window': None, 'timezone': 'Asia/Tokyo'}
        before = datetime.now(ZoneInfo('Asia/Tokyo')).strftime('%Y-%m-%d %H')
        _, _, log = self.run_tick_fmt(data)
        after = datetime.now(ZoneInfo('Asia/Tokyo')).strftime('%Y-%m-%d %H')
        self.assertTrue(log.startswith('[' + before) or log.startswith('[' + after), log[:40])


class FmtAndWrapperFailureTests(unittest.TestCase):
    """audit-01 R4：失败时不再跟一行全 0 概览；JSON 后面跟着 stderr 文本也能解析；wrapper 读不到配置时推人话不推堆栈。"""

    def test_failure_has_no_zero_overview(self):
        lines = load_fmt().render({'ok': False, 'error': 'inbox_network_failed'})
        self.assertEqual(lines, ['⚠️ BOSS 回消息跳失败：inbox_network_failed'])

    def test_trailing_noise_after_json(self):
        rc, out = run_fmt(json.dumps({'interviews': []}) + '\nfiction trace line on stderr\n')
        self.assertEqual((rc, out.strip()), (0, '💬 回消息跳｜约面试 0｜感兴趣 0｜待你回 0'))
        for script in (ROOT / 'scripts' / 'boss-hunt-tick-fmt.py', CRON / 'boss-hunt-chat-fmt.py'):
            with self.subTest(script=script.name):
                with tempfile.TemporaryDirectory(prefix='audit01-noise-') as d:
                    p = subprocess.run([sys.executable, str(script)],
                                       input=json.dumps({'cmd': 'x', 'ok': False, 'error': 'fiction_err'}) + '\nnoise\n',
                                       capture_output=True, text=True,
                                       env=dict(os.environ, BOSS_BROADCAST_LOG=str(Path(d) / 'b.log')))
                self.assertIn('fiction_err', p.stdout)
                self.assertNotIn('无法解析', p.stdout)
                self.assertNotIn('渲染失败', p.stdout)

    def test_wrappers_unreadable_config_push_human_text(self):
        for name, fmt_name in (('boss-hunt-inbox.sh', 'boss-hunt-inbox-fmt.py'), ('boss-hunt-chat.sh', 'boss-hunt-chat-fmt.py')):
            with self.subTest(wrapper=name), tempfile.TemporaryDirectory(prefix='audit01-wrapcfg-') as d:
                repo = Path(d) / 'fiction-repo'
                cron = repo / 'scripts' / 'cron'
                cron.mkdir(parents=True)
                shutil.copy(CRON / name, cron / name)
                shutil.copy(CRON / fmt_name, cron / fmt_name)
                (repo / 'scripts' / 'boss_hunt.py').write_text('print("should not run")\n')
                bad = Path(d) / 'bad-config.json'
                bad.write_text('{坏', encoding='utf-8')
                if name == 'boss-hunt-chat.sh':
                    bad.write_text('[]', encoding='utf-8')  # audit-01 R5（测试缺口）：顶层不是对象也推人话
                env = dict(os.environ, BOSS_HUNT_CONFIG=str(bad), BOSS_HUNT_PYTHON=sys.executable)
                p = subprocess.run(['bash', str(cron / name)], env=env, capture_output=True, text=True)
                self.assertNotEqual(p.returncode, 0)
                self.assertIn('配置', p.stdout)
                self.assertNotIn('Traceback', p.stdout)
                self.assertNotIn(str(bad), p.stdout)
                self.assertNotIn('should not run', p.stdout)


class TickFmtLedgerCorruptTests(unittest.TestCase):
    """audit-01 R5：投递跳遇流水文件损坏时推人话，不带 ledger-repair 命令（R4-A2 同类）。"""

    def test_corrupt_ledger_humanized(self):
        spec = importlib.util.spec_from_file_location('tick_fmt_r5', ROOT / 'scripts' / 'boss-hunt-tick-fmt.py')
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        lines = module.render({'ok': False, 'error': 'skip_ledger_corrupt: 请先 ledger-repair --apply', 'window': '10:00'})
        text = '\n'.join(lines)
        self.assertNotIn('ledger-repair', text)
        self.assertIn('发送流水', text)


class LockErrorAndTickWrapperTests(unittest.TestCase):
    """audit-01 R5：锁被占的报错推人话（不带本机路径）；仓库提供投递跳 wrapper，与对话/回消息跳共用 .wrapperlock。"""
    LOCKED = '另一个实例在跑(pid unknown)：遗留目录锁 /Users/fiction/.boss-hunt/data/.lock'

    def test_fmts_hide_lock_path(self):
        spec = importlib.util.spec_from_file_location('chat_fmt_r5', CRON / 'boss-hunt-chat-fmt.py')
        chat = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(chat)
        texts = ['\n'.join(load_fmt().render({'ok': False, 'error': self.LOCKED})),
                 chat.render({'cmd': 'chat', 'ok': False, 'error': self.LOCKED})]
        for text in texts:
            with self.subTest(text=text):
                self.assertNotIn('/Users/', text)
                self.assertIn('本跳跳过', text)

    def test_tick_wrapper_shares_lock_and_renders(self):
        self.assertTrue((CRON / 'boss-hunt-tick.sh').exists())
        with tempfile.TemporaryDirectory(prefix='audit01-tickwrap-') as d:
            repo = Path(d) / 'fiction-repo'
            cron = repo / 'scripts' / 'cron'
            cron.mkdir(parents=True)
            shutil.copy(CRON / 'boss-hunt-tick.sh', cron / 'boss-hunt-tick.sh')
            shutil.copy(ROOT / 'scripts' / 'boss-hunt-tick-fmt.py', repo / 'scripts' / 'boss-hunt-tick-fmt.py')
            (repo / 'scripts' / 'boss_hunt.py').write_text(
                'import json, sys\nassert sys.argv[-2:] == ["--json", "tick"], sys.argv\n'
                'print(json.dumps({"cmd": "tick", "ok": False, "error": "fiction_tick_error"}))\nsys.exit(3)\n')
            config = repo / 'config.json'
            config.write_text(json.dumps({'paths': {'workdir': str(repo / 'data')}}))
            env = dict(os.environ, BOSS_HUNT_CONFIG=str(config), BOSS_HUNT_PYTHON=sys.executable,
                       BOSS_BROADCAST_LOG=str(Path(d) / 'b.log'))
            p = subprocess.run(['bash', str(cron / 'boss-hunt-tick.sh')], env=env, capture_output=True, text=True)
            self.assertEqual(p.returncode, 3, p.stdout + p.stderr)
            self.assertIn('fiction_tick_error', p.stdout)
            (repo / 'data').mkdir(exist_ok=True)
            with (repo / 'data' / '.wrapperlock').open('a+') as lock:
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
                p = subprocess.run(['bash', str(cron / 'boss-hunt-tick.sh')], env=env, capture_output=True, text=True)
            self.assertEqual(p.returncode, 0)
            self.assertNotIn('fiction_tick_error', p.stdout)


class WrapperForwardsArgsTests(unittest.TestCase):
    """audit-01 R6：wrapper 必须把额外参数（如 --dry-run）转给子命令；文档教「真实 wrapper 加 --dry-run」，丢了就是真跳真发。"""

    def test_dry_run_forwarded(self):
        for name, sub in (('boss-hunt-tick.sh', 'tick'), ('boss-hunt-chat.sh', 'chat'), ('boss-hunt-inbox.sh', 'inbox')):
            with self.subTest(wrapper=name), tempfile.TemporaryDirectory(prefix='audit01-args-') as d:
                repo = Path(d) / 'fiction-repo'
                cron = repo / 'scripts' / 'cron'
                cron.mkdir(parents=True)
                shutil.copy(CRON / name, cron / name)
                for fmt in ('boss-hunt-chat-fmt.py', 'boss-hunt-inbox-fmt.py'):
                    shutil.copy(CRON / fmt, cron / fmt)
                shutil.copy(ROOT / 'scripts' / 'boss-hunt-tick-fmt.py', repo / 'scripts' / 'boss-hunt-tick-fmt.py')
                seen = Path(d) / 'argv.json'
                (repo / 'scripts' / 'boss_hunt.py').write_text(
                    'import json, sys\nopen(%r, "w").write(json.dumps(sys.argv[1:]))\n'
                    'print(json.dumps({"cmd": "x", "ok": True}))\n' % str(seen))
                config = repo / 'config.json'
                config.write_text(json.dumps({'paths': {'workdir': str(repo / 'data')}}))
                env = dict(os.environ, BOSS_HUNT_CONFIG=str(config), BOSS_HUNT_PYTHON=sys.executable,
                           BOSS_BROADCAST_LOG=str(Path(d) / 'b.log'))
                subprocess.run(['bash', str(cron / name), '--dry-run'], env=env, capture_output=True, text=True)
                argv = json.loads(seen.read_text())
                self.assertEqual(argv[-2:], [sub, '--dry-run'], argv)
                subprocess.run(['bash', str(cron / name)], env=env, capture_output=True, text=True)
                self.assertEqual(json.loads(seen.read_text())[-1], sub)


class InterviewCountExcludesRepliedTests(unittest.TestCase):
    """audit-01 B2（用户 11-06「都按推荐」）：「约面试 N」只数对话模块还没回过的，已回过的不再列（免得一天 3 跳重复推）。"""
    DATA = {'interviews': [{'brand': '虚构甲', 'text': '明天面试可以吗', 'replied': True},
                           {'brand': '虚构乙', 'text': '周五方便面试吗', 'replied': False}],
            'interest': [], 'unanswered': {'counts': {'waiting_me': 0}}}

    def test_inbox_fmt(self):
        lines = load_fmt().render(dict(self.DATA))
        self.assertEqual(lines[0], '💬 回消息跳｜约面试 1｜感兴趣 0｜待你回 0')
        self.assertTrue(any('虚构乙' in l for l in lines))
        self.assertFalse(any('虚构甲' in l for l in lines))

    def test_tick_fmt(self):
        spec = importlib.util.spec_from_file_location('tick_fmt_b2', ROOT / 'scripts' / 'boss-hunt-tick-fmt.py')
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        lines = module.render({'ok': True, 'actions': ['inbox'], 'window': '10:00', 'inbox': dict(self.DATA)})
        self.assertIn('💬 回消息｜约面试 1｜感兴趣 0｜待你回 0', lines)


class WrapperConfigShapeAndBroadcastLogTests(unittest.TestCase):
    """audit-01 R6：三个 wrapper 遇 paths/workdir 形状不对推人话；broadcasts.log 跟配置的 workdir 走。"""

    def setup_repo(self, d, name):
        repo = Path(d) / 'fiction-repo'
        cron = repo / 'scripts' / 'cron'
        cron.mkdir(parents=True)
        shutil.copy(CRON / name, cron / name)
        for fmt in ('boss-hunt-chat-fmt.py', 'boss-hunt-inbox-fmt.py'):
            shutil.copy(CRON / fmt, cron / fmt)
        shutil.copy(ROOT / 'scripts' / 'boss-hunt-tick-fmt.py', repo / 'scripts' / 'boss-hunt-tick-fmt.py')
        return repo, cron

    def test_bad_paths_shapes_push_human_text(self):
        for name in ('boss-hunt-tick.sh', 'boss-hunt-chat.sh', 'boss-hunt-inbox.sh'):
            for raw in ('{"paths": "abc"}', '{"paths": {"workdir": 123}}', '{坏', '[]'):
                with self.subTest(wrapper=name, raw=raw), tempfile.TemporaryDirectory(prefix='audit01-wshape-') as d:
                    repo, cron = self.setup_repo(d, name)
                    (repo / 'scripts' / 'boss_hunt.py').write_text('print("should not run")\n')
                    cfg = Path(d) / 'c.json'
                    cfg.write_text(raw, encoding='utf-8')
                    env = dict(os.environ, BOSS_HUNT_CONFIG=str(cfg), BOSS_HUNT_PYTHON=sys.executable,
                               BOSS_BROADCAST_LOG=str(Path(d) / 'b.log'))
                    p = subprocess.run(['bash', str(cron / name)], env=env, capture_output=True, text=True)
                    self.assertNotEqual(p.returncode, 0)
                    self.assertIn('配置', p.stdout)
                    self.assertNotIn('Traceback', p.stdout)
                    self.assertNotIn('should not run', p.stdout)

    def test_broadcast_log_follows_workdir(self):
        with tempfile.TemporaryDirectory(prefix='audit01-blog-') as d:
            repo, cron = self.setup_repo(d, 'boss-hunt-tick.sh')
            workdir = Path(d) / 'my-data'
            (repo / 'scripts' / 'boss_hunt.py').write_text(
                'import json\nprint(json.dumps({"cmd": "tick", "ok": False, "error": "fiction", "workdir": %r}))\n' % str(workdir))
            cfg = Path(d) / 'c.json'
            cfg.write_text(json.dumps({'paths': {'workdir': str(workdir)}}))
            env = {k: v for k, v in os.environ.items() if k != 'BOSS_BROADCAST_LOG'}
            env.update(BOSS_HUNT_CONFIG=str(cfg), BOSS_HUNT_PYTHON=sys.executable, HOME=str(Path(d) / 'home'))
            subprocess.run(['bash', str(cron / 'boss-hunt-tick.sh')], env=env, capture_output=True, text=True)
            self.assertTrue((workdir / 'broadcasts.log').exists())
            self.assertFalse((Path(d) / 'home' / '.boss-hunt').exists())


class TickFmtErrorsAndDryRunTests(unittest.TestCase):
    """audit-01 R7：投递跳播报认 errors[]（配置有误）与干跑；干跑不写 broadcasts.log。"""

    def run_fmt(self, data):
        with tempfile.TemporaryDirectory(prefix='audit01-r7fmt-') as d:
            log = Path(d) / 'b.log'
            p = subprocess.run([sys.executable, str(ROOT / 'scripts' / 'boss-hunt-tick-fmt.py')],
                               input=json.dumps(data, ensure_ascii=False), capture_output=True, text=True,
                               env=dict(os.environ, BOSS_BROADCAST_LOG=str(log)))
            return p.stdout, log.exists()

    def test_config_errors_shown(self):
        out, logged = self.run_fmt({'cmd': 'tick', 'ok': False, 'errors': ['schedule.windows 必须是列表', 'b', 'c', 'd']})
        self.assertIn('配置有误', out)
        self.assertIn('schedule.windows 必须是列表', out)
        self.assertNotIn('未知', out)
        self.assertNotIn('\n· d', out)
        self.assertTrue(logged)

    def test_dry_run_plan_line_and_no_log(self):
        out, logged = self.run_fmt({'cmd': 'tick', 'ok': True, 'dry_run': True, 'actions': ['send'], 'window': '10:00',
                                    'budget': {'limit': 3}, 'quotas': {'A': 2, 'B': 1}})
        self.assertIn('干跑', out)
        self.assertIn('10:00', out)
        self.assertFalse(logged)
        out, logged = self.run_fmt({'cmd': 'tick', 'ok': True, 'dry_run': True, 'actions': [], 'window': None,
                                    'budget': {'limit': 0}, 'quotas': {'A': 0, 'B': 0}})
        self.assertIn('不在窗口', out)
        self.assertFalse(logged)
        out, logged = self.run_fmt({'cmd': 'tick', 'ok': False, 'dry_run': True, 'errors': ['x 坏了']})
        self.assertIn('x 坏了', out)
        self.assertFalse(logged)


class BroadcastLogOnFailureTests(unittest.TestCase):
    """audit-01 R7：tick 失败、JSON 里没有 workdir 时，broadcasts.log 仍按配置推导的 workdir 写（不落到 ~/.boss-hunt）。"""

    def test_failure_without_workdir_uses_config(self):
        with tempfile.TemporaryDirectory(prefix='audit01-r7log-') as d:
            workdir = Path(d) / 'my-data'
            cfg = Path(d) / 'c.json'
            cfg.write_text(json.dumps({'paths': {'workdir': str(workdir)}}))
            env = {k: v for k, v in os.environ.items() if k != 'BOSS_BROADCAST_LOG'}
            env.update(BOSS_HUNT_CONFIG=str(cfg), HOME=str(Path(d) / 'home'))
            subprocess.run([sys.executable, str(ROOT / 'scripts' / 'boss-hunt-tick-fmt.py')],
                           input='Traceback (most recent call last):\n  boom\n', capture_output=True, text=True, env=env)
            self.assertTrue((workdir / 'broadcasts.log').exists())
            self.assertFalse((Path(d) / 'home' / '.boss-hunt').exists())


class DryRunFailureNotLoggedTests(unittest.TestCase):
    """audit-01 R8：干跑遇到不走 errors[] 的失败（load_config 抛错、wrapper 判配置坏、输出不是 JSON）时，
    播报仍是干跑口径、不写 broadcasts.log；原来按真跳失败推并写一条假记录。"""

    def fmt(self, raw, env_extra=None):
        with tempfile.TemporaryDirectory(prefix='audit01-r8fmt-') as d:
            log = Path(d) / 'b.log'
            env = dict(os.environ, BOSS_BROADCAST_LOG=str(log))
            env.pop('BOSS_TICK_DRY_RUN', None)
            env.update(env_extra or {})
            p = subprocess.run([sys.executable, str(ROOT / 'scripts' / 'boss-hunt-tick-fmt.py')],
                               input=raw if isinstance(raw, str) else json.dumps(raw, ensure_ascii=False),
                               capture_output=True, text=True, env=env)
            return p.stdout, log.exists()

    def test_fmt_dry_run_failure_without_errors(self):
        out, logged = self.fmt({'cmd': 'tick', 'ok': False, 'dry_run': True, 'error': 'filters 必须是对象'})
        self.assertIn('干跑', out)
        self.assertIn('filters 必须是对象', out)
        self.assertNotIn('不在窗口', out)
        self.assertFalse(logged)

    def test_fmt_env_marks_dry_run_even_without_json(self):
        out, logged = self.fmt('Traceback (most recent call last):\n  boom\n', {'BOSS_TICK_DRY_RUN': '1'})
        self.assertIn('干跑', out)
        self.assertFalse(logged)
        out, logged = self.fmt({'cmd': 'tick', 'ok': False, 'error': 'x 坏了'}, {'BOSS_TICK_DRY_RUN': '1'})
        self.assertIn('干跑', out)
        self.assertIn('x 坏了', out)
        self.assertFalse(logged)

    def test_main_fallback_marks_dry_run(self):
        with tempfile.TemporaryDirectory(prefix='audit01-r8main-') as d:
            cfg = Path(d) / 'c.json'
            cfg.write_text(json.dumps({'filters': 'x', 'paths': {'workdir': str(Path(d) / 'wd')}}))
            p = subprocess.run([sys.executable, str(ROOT / 'scripts' / 'boss_hunt.py'), '--config', str(cfg),
                                '--json', 'tick', '--dry-run'], capture_output=True, text=True,
                               env=dict(os.environ, HOME=str(Path(d) / 'home')))
            data = json.loads(p.stdout)
            self.assertFalse(data['ok'])
            self.assertTrue(data.get('dry_run'))
            missing = Path(d) / 'nope.json'
            p = subprocess.run([sys.executable, str(ROOT / 'scripts' / 'boss_hunt.py'), '--config', str(missing),
                                '--json', 'tick', '--dry-run'], capture_output=True, text=True,
                               env=dict(os.environ, HOME=str(Path(d) / 'home')))
            self.assertTrue(json.loads(p.stdout).get('dry_run'))

    def setup_repo(self, d):
        repo = Path(d) / 'fiction-repo'
        cron = repo / 'scripts' / 'cron'
        cron.mkdir(parents=True)
        shutil.copy(CRON / 'boss-hunt-tick.sh', cron / 'boss-hunt-tick.sh')
        shutil.copy(ROOT / 'scripts' / 'boss-hunt-tick-fmt.py', repo / 'scripts' / 'boss-hunt-tick-fmt.py')
        return repo, cron

    def test_wrapper_dry_run_bad_config_not_logged(self):
        with tempfile.TemporaryDirectory(prefix='audit01-r8wrap-') as d:
            repo, cron = self.setup_repo(d)
            (repo / 'scripts' / 'boss_hunt.py').write_text('print("should not run")\n')
            cfg = Path(d) / 'cfg' / 'c.json'
            cfg.parent.mkdir()
            cfg.write_text('{', encoding='utf-8')
            env = {k: v for k, v in os.environ.items() if k not in ('BOSS_BROADCAST_LOG', 'BOSS_TICK_DRY_RUN')}
            env.update(BOSS_HUNT_CONFIG=str(cfg), BOSS_HUNT_PYTHON=sys.executable, HOME=str(Path(d) / 'home'))
            p = subprocess.run(['bash', str(cron / 'boss-hunt-tick.sh'), '--dry-run'], env=env,
                               capture_output=True, text=True)
            self.assertIn('干跑', p.stdout)
            self.assertIn('配置文件读不到或格式不对', p.stdout)
            self.assertFalse((cfg.parent / 'data' / 'broadcasts.log').exists())
            self.assertFalse((Path(d) / 'home' / '.boss-hunt').exists())

    def test_wrapper_dry_run_load_config_error_not_logged(self):
        with tempfile.TemporaryDirectory(prefix='audit01-r8wrap2-') as d:
            repo, cron = self.setup_repo(d)
            (repo / 'scripts' / 'boss_hunt.py').write_text(
                'import json\nprint(json.dumps({"cmd": "tick", "ok": False, "error": "filters 必须是对象"}, ensure_ascii=False))\n'
                'raise SystemExit(1)\n')
            wd = Path(d) / 'wd'
            cfg = Path(d) / 'c.json'
            cfg.write_text(json.dumps({'paths': {'workdir': str(wd)}}))
            env = {k: v for k, v in os.environ.items() if k not in ('BOSS_BROADCAST_LOG', 'BOSS_TICK_DRY_RUN')}
            env.update(BOSS_HUNT_CONFIG=str(cfg), BOSS_HUNT_PYTHON=sys.executable, HOME=str(Path(d) / 'home'))
            p = subprocess.run(['bash', str(cron / 'boss-hunt-tick.sh'), '--dry-run'], env=env,
                               capture_output=True, text=True)
            self.assertIn('干跑', p.stdout)
            self.assertIn('filters 必须是对象', p.stdout)
            self.assertFalse((wd / 'broadcasts.log').exists())
            # 不带 --dry-run 的真跳失败仍照常落盘
            subprocess.run(['bash', str(cron / 'boss-hunt-tick.sh')], env=env, capture_output=True, text=True)
            self.assertTrue((wd / 'broadcasts.log').exists())


class TickFmtLogPriorityAndDryRunDetailTests(unittest.TestCase):
    """audit-01 R8：第七轮变异核对存活的 M09/M11/M13/M14 补用例。"""

    def run_fmt(self, data, env):
        return subprocess.run([sys.executable, str(ROOT / 'scripts' / 'boss-hunt-tick-fmt.py')],
                              input=json.dumps(data, ensure_ascii=False), capture_output=True, text=True, env=env)

    def clean_env(self, **kw):
        env = {k: v for k, v in os.environ.items()
               if k not in ('BOSS_BROADCAST_LOG', 'BOSS_HUNT_CONFIG', 'BOSS_TICK_DRY_RUN')}
        env.update(kw)
        return env

    def test_env_log_beats_json_workdir(self):  # M09
        with tempfile.TemporaryDirectory(prefix='audit01-r8m09-') as d:
            a = Path(d) / 'a.log'
            b = Path(d) / 'b'
            self.run_fmt({'cmd': 'tick', 'ok': False, 'error': 'x', 'workdir': str(b)},
                         self.clean_env(BOSS_BROADCAST_LOG=str(a), HOME=str(Path(d) / 'home')))
            self.assertTrue(a.exists())
            self.assertFalse((b / 'broadcasts.log').exists())

    def test_config_workdir_tilde_expanded(self):  # M11
        with tempfile.TemporaryDirectory(prefix='audit01-r8m11-') as d:
            home = Path(d) / 'home'
            home.mkdir()
            cfg = Path(d) / 'c.json'
            cfg.write_text(json.dumps({'paths': {'workdir': '~/wd'}}))
            cwd = Path(d) / 'cwd'
            cwd.mkdir()
            subprocess.run([sys.executable, str(ROOT / 'scripts' / 'boss-hunt-tick-fmt.py')],
                           input='{"cmd": "tick", "ok": false, "error": "x"}', capture_output=True, text=True,
                           env=self.clean_env(BOSS_HUNT_CONFIG=str(cfg), HOME=str(home)), cwd=str(cwd))
            self.assertTrue((home / 'wd' / 'broadcasts.log').exists())
            self.assertFalse((cwd / '~').exists())

    def test_dry_run_line_has_actions_and_budget(self):  # M13/M14
        with tempfile.TemporaryDirectory(prefix='audit01-r8m13-') as d:
            p = self.run_fmt({'cmd': 'tick', 'ok': True, 'dry_run': True, 'actions': ['send', 'inbox'],
                              'window': '10:00', 'budget': {'limit': 3}, 'quotas': {'A': 2, 'B': 1}},
                             self.clean_env(BOSS_BROADCAST_LOG=str(Path(d) / 'b.log')))
            self.assertIn('send/inbox', p.stdout)
            self.assertIn('预算 3', p.stdout)
            self.assertIn('A 2 · B 1', p.stdout)


if __name__ == '__main__':
    unittest.main()
