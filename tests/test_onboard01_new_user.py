"""onboard-01：新用户从零上手的路径能真实跑通，文档里写的命令真实存在、参数对得上。

路径：init → validate → template → plan --top N（只报告）→ tick --dry-run →
三条仓库 wrapper（tick / chat / inbox）加 --dry-run 校准。

全部在临时 HOME / 临时配置下跑真实 CLI 与真实 wrapper；PATH 前置一个会立刻失败的假 npx，
任何意外的连浏览器动作都会失败而不是碰到本机真 Chrome。不读写 ~/.boss-hunt 与 ~/.hermes。
"""
import contextlib
import io
import json
import os
import re
import subprocess
import sys
import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / 'scripts'
CLI = SCRIPTS / 'boss_hunt.py'
CRON = SCRIPTS / 'cron'
sys.path.insert(0, str(SCRIPTS))
import boss_hunt as B  # noqa: E402

DOCS = ['README.md', 'SKILL.md', 'AGENTS.md', 'docs/onboarding-interview.md', 'docs/agent-adapters.md']
WRAPPERS = {'boss-hunt-tick.sh': 'tick', 'boss-hunt-chat.sh': 'chat', 'boss-hunt-inbox.sh': 'inbox'}


class Sandbox:
    """临时 HOME + 假 npx；env 里去掉会把命令引向真实目录的变量。"""

    def __init__(self, case):
        tmp = tempfile.TemporaryDirectory(prefix='onboard01-')
        case.addCleanup(tmp.cleanup)
        self.home = Path(tmp.name) / 'home'
        bin_dir = Path(tmp.name) / 'bin'
        bin_dir.mkdir(parents=True)
        self.home.mkdir()
        self.npx_log = Path(tmp.name) / 'npx-called'
        npx = bin_dir / 'npx'
        npx.write_text('#!/bin/sh\necho "$@" >> "%s"\nexit 97\n' % self.npx_log, encoding='utf-8')
        npx.chmod(0o755)
        env = {k: v for k, v in os.environ.items()
               if k not in ('BOSS_HUNT_CONFIG', 'BOSS_HUNT_ENV_FILE', 'BOSS_TRACE')}
        env.update(HOME=str(self.home), PATH=str(bin_dir) + os.pathsep + env.get('PATH', ''),
                   BOSS_HUNT_PYTHON=sys.executable)
        self.env = env
        self.config = self.home / '.boss-hunt' / 'config.json'
        self.workdir = self.home / '.boss-hunt' / 'data'

    def cli(self, *argv):
        p = subprocess.run([sys.executable, str(CLI), '--json'] + list(argv), cwd=str(ROOT),
                           env=self.env, capture_output=True, text=True, timeout=120)
        try:
            data = json.loads(p.stdout)
        except ValueError:
            data = None
        return p.returncode, data, p

    def wrapper(self, name, *argv):
        return subprocess.run(['/bin/bash', str(CRON / name)] + list(argv), cwd=str(self.home),
                              env=self.env, capture_output=True, text=True, timeout=120)

    def edit_config(self, fn):
        cfg = json.loads(self.config.read_text(encoding='utf-8'))
        fn(cfg)
        self.config.write_text(json.dumps(cfg, ensure_ascii=False), encoding='utf-8')

    def assert_no_browser(self, case):
        case.assertFalse(self.npx_log.exists(), '不该调用 npx（意味着试图连浏览器）')


class NewUserPathTests(unittest.TestCase):
    def setUp(self):
        self.box = Sandbox(self)
        rc, data, p = self.box.cli('init')
        self.assertEqual((rc, data and data['ok']), (0, True), p.stderr)
        self.assertEqual(Path(data['config']), self.box.config)

    def test_init_config_passes_validate_without_errors(self):
        rc, data, p = self.box.cli('validate')
        self.assertEqual(rc, 0, p.stdout + p.stderr)
        self.assertTrue(data['ok'])
        self.assertEqual(data['errors'], [])
        self.assertEqual(Path(data['workdir']), self.box.workdir)
        self.assertEqual(data['timezone'], 'Asia/Shanghai')
        # 新用户第一次 validate 不该就看到警告（MiniMax Code 实测暴露：示例 daily_cap=30 一跑就报「偏高」）
        self.assertEqual(data['warnings'], [])

    def test_example_quota_matches_documented_default(self):
        cfg = json.loads(self.box.config.read_text(encoding='utf-8'))
        sending = cfg['sending']
        self.assertTrue(15 <= sending['daily_cap'] <= 20, 'README 写默认 15–20/天')
        self.assertEqual(sum(sending['grade_quota'].values()), sending['daily_cap'])
        self.assertGreaterEqual(sending['min_gap_seconds'], 60)

    def test_init_refuses_to_overwrite(self):
        rc, data, _ = self.box.cli('init')
        self.assertEqual((rc, data['ok'], data['error']), (1, False, 'exists'))

    def test_template_renders_within_recommended_length(self):
        rc, data, _ = self.box.cli('template')
        self.assertEqual(rc, 0)
        lo, hi = data['recommended_chars']
        self.assertTrue(lo <= data['chars'] <= hi, data)
        self.assertNotIn('{', data['text'])  # 占位符都渲染掉了

    def test_plan_only_reports_on_fresh_install(self):
        rc, data, _ = self.box.cli('plan', '--top', '15')
        self.assertEqual(rc, 0)
        self.assertEqual(data['items'], [])
        self.assertEqual(data['summary']['candidates'], 0)
        self.assertEqual(data['summary']['day_count'], 0)
        self.assertFalse((self.box.workdir / 'state.json').exists(), 'plan 只报告，不该写 state')
        self.box.assert_no_browser(self)

    def test_tick_dry_run_in_and_out_of_window(self):
        rc, data, _ = self.box.cli('tick', '--dry-run')
        self.assertEqual((rc, data['ok'], data['dry_run']), (0, True, True))
        now = datetime.now(ZoneInfo('Asia/Shanghai')).strftime('%H:%M')

        def add_window(cfg):
            cfg['schedule']['windows'].append(now)

        self.box.edit_config(add_window)
        rc, data, _ = self.box.cli('tick', '--dry-run')
        self.assertEqual(rc, 0)
        self.assertEqual(data['actions'], ['send'])
        self.assertGreater(data['budget']['limit'], 0)
        self.assertFalse((self.box.workdir / 'runs.log').exists(), '干跑不写日志')
        self.assertFalse((self.box.workdir / 'state.json').exists(), '干跑不记账')
        self.box.assert_no_browser(self)

    def test_three_wrappers_dry_run_calibrate_offline(self):
        for name in WRAPPERS:
            with self.subTest(wrapper=name):
                p = self.box.wrapper(name, '--dry-run')
                self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
                self.assertIn('🧪', p.stdout)
                self.assertNotIn('usage:', p.stdout)
                self.assertNotIn('渲染失败', p.stdout)
        self.assertFalse((self.box.workdir / 'state.json').exists())
        self.assertFalse((self.box.workdir / 'broadcasts.log').exists(), '干跑不进播报日志')
        self.box.assert_no_browser(self)

    def test_chat_dry_run_explains_what_a_real_hop_would_do(self):
        rc, data, _ = self.box.cli('chat', '--dry-run')
        self.assertEqual((rc, data['ok'], data['dry_run']), (0, True, True))
        self.assertFalse(data['would_connect'])
        self.assertIn('conversation.enabled', data['message'])

        def enable_all_hours(cfg):
            cfg['conversation']['enabled'] = True
            cfg['conversation']['hours'] = list(range(24))

        self.box.edit_config(enable_all_hours)
        rc, data, _ = self.box.cli('chat', '--dry-run')
        self.assertEqual((rc, data['ok']), (0, True))
        self.assertTrue(data['would_connect'])
        self.assertIn('未连浏览器', data['message'])
        self.box.assert_no_browser(self)

    def test_inbox_dry_run_does_not_open_browser(self):
        rc, data, _ = self.box.cli('inbox', '--dry-run')
        self.assertEqual((rc, data['ok'], data['dry_run']), (0, True, True))
        self.assertIn('只读', data['message'])
        self.assertFalse((self.box.workdir / 'state.json').exists())
        self.box.assert_no_browser(self)

    def test_hop_dry_run_reports_config_errors(self):
        self.box.edit_config(lambda cfg: cfg['user'].update(greeting_template='短'))
        for cmd in ('chat', 'inbox'):
            with self.subTest(cmd=cmd):
                rc, data, _ = self.box.cli(cmd, '--dry-run')
                self.assertEqual((rc, data['ok'], data['dry_run']), (1, False, True))
                self.assertTrue(any('greeting_template' in e for e in data['errors']))
        p = self.box.wrapper('boss-hunt-inbox.sh', '--dry-run')
        self.assertEqual(p.returncode, 1)
        self.assertIn('greeting_template', p.stdout)
        self.box.assert_no_browser(self)

    def test_missing_config_dry_run_json_marks_dry_run(self):
        self.box.config.unlink()
        for cmd in ('chat', 'inbox'):
            rc, data, _ = self.box.cli(cmd, '--dry-run')
            self.assertEqual((rc, data['error'], data.get('dry_run')), (1, 'config_not_found', True))


# --------------------------------------------------------------------------
# 文档里的命令必须真实存在、参数对得上
# --------------------------------------------------------------------------
PLACEHOLDER = re.compile(r'<[^<>]+>')
# 行内代码里「看起来像命令、其实不是要执行的命令」的片段，逐条写明原因（按文本，不按行号）
NOT_COMMANDS = {
    'send --json': 'README「JSON 输出」字段表的行标题',
    'ledger-ack --json': 'README「JSON 输出」字段表的行标题',
    'inbox --config X': 'SKILL.md 里的反例：写成这样会报 unrecognized arguments',
    'scan --keyword': '行文里点名参数，不是完整命令',
    'greet --job-id': '行文里点名参数，不是完整命令',
}


def _clean(argv_text):
    text = argv_text.split('#', 1)[0]
    text = re.split(r'\s\|\s|\s\|$|`|；|，|）|（', text)[0]
    text = PLACEHOLDER.sub('x', text)
    return text.strip()


def _argv(text):
    out = []
    for tok in text.split():
        tok = tok.strip('\'"')
        if tok == 'N':
            tok = '1'
        out.append(tok)
    return out


def doc_cli_commands():
    """返回 (文件, 行号, argv)：所有「boss_hunt.py <子命令> …」与以子命令开头且带参数的行内代码。"""
    subs = set(B.build_parser()._subparsers._group_actions[0].choices)
    found = []
    for rel in DOCS:
        for no, line in enumerate((ROOT / rel).read_text(encoding='utf-8').splitlines(), 1):
            for m in re.finditer(r'boss_hunt\.py((?:\s+[^\s`#|]+)+)', line):
                text = _clean(m.group(1))
                if not text or '[' in text or '{' in text or '…' in text or '...' in text:
                    continue  # 用法提要（[--top 20] / {a|b}）不是可执行命令
                found.append((rel, no, _argv(text)))
            for m in re.finditer(r'`([^`]+)`', line):
                span = m.group(1).strip()
                head = span.split()[0] if span.split() else ''
                if head in subs and len(span.split()) > 1 and 'boss_hunt.py' not in span:
                    text = _clean(span)
                    if '[' in text or '{' in text or '|' in text or '…' in text or span in NOT_COMMANDS:
                        continue
                    found.append((rel, no, _argv(text)))
    return found


def doc_wrapper_calls():
    found = []
    for rel in DOCS:
        for no, line in enumerate((ROOT / rel).read_text(encoding='utf-8').splitlines(), 1):
            for m in re.finditer(r'scripts/cron/(boss-hunt-[a-z]+\.sh)((?:[ \t]+--?[\w-]+)*)', line):
                found.append((rel, no, m.group(1), m.group(2).split()))
    return found


class DocCommandTests(unittest.TestCase):
    def parse(self, argv):
        with contextlib.redirect_stderr(io.StringIO()) as err, contextlib.redirect_stdout(io.StringIO()):
            try:
                B.build_parser().parse_args(argv)
            except SystemExit as exc:
                return exc.code, err.getvalue()
        return 0, ''

    def test_collector_finds_the_onboarding_path(self):
        cmds = {tuple(a) for _, _, a in doc_cli_commands()}
        for want in (('init',), ('validate',), ('template',), ('plan', '--top', '15'), ('tick', '--dry-run')):
            self.assertIn(want, cmds, '文档里应写出新用户路径命令 %s' % ' '.join(want))
        wrappers = {(w, tuple(a)) for _, _, w, a in doc_wrapper_calls()}
        for name in WRAPPERS:
            self.assertIn((name, ('--dry-run',)), wrappers, '文档应写出 %s --dry-run 校准' % name)

    def test_every_documented_cli_command_parses(self):
        bad = []
        for rel, no, argv in doc_cli_commands():
            code, err = self.parse(argv)
            if code:
                bad.append('%s:%d %s → %s' % (rel, no, ' '.join(argv), err.strip().splitlines()[-1:]))
        self.assertEqual(bad, [])

    def test_every_documented_wrapper_exists_and_forwards_valid_args(self):
        bad = []
        for rel, no, name, args in doc_wrapper_calls():
            if not (CRON / name).is_file():
                bad.append('%s:%d 不存在 %s' % (rel, no, name))
                continue
            sub = WRAPPERS.get(name)
            if sub is None:
                bad.append('%s:%d 未知 wrapper %s' % (rel, no, name))
                continue
            code, err = self.parse(['--json', sub] + args)
            if code:
                bad.append('%s:%d %s %s → %s' % (rel, no, name, ' '.join(args), err.strip()))
        self.assertEqual(bad, [])

    def test_not_commands_exemptions_are_still_in_docs(self):
        text = '\n'.join((ROOT / rel).read_text(encoding='utf-8') for rel in DOCS)
        stale = [span for span in NOT_COMMANDS if '`%s`' % span not in text]
        self.assertEqual(stale, [], '文档里已没有这些片段，豁免要删掉')

    def test_wrappers_in_repo_are_all_documented(self):
        documented = {w for _, _, w, _ in doc_wrapper_calls()}
        on_disk = {p.name for p in CRON.glob('boss-hunt-*.sh')}
        self.assertEqual(on_disk - documented, set())


if __name__ == '__main__':
    unittest.main()
