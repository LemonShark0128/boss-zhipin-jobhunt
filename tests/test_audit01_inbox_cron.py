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


if __name__ == '__main__':
    unittest.main()
