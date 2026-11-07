"""iter-02：还没 init（或配置写坏了）就跑 wrapper，要告诉用户下一步做什么。

新用户常见顺序：照 README 先试 `bash scripts/cron/boss-hunt-*.sh --dry-run`，再回头 init。
以前三条 wrapper 只说「配置文件读不到或格式不对」，不说是没有还是写坏了、该跑什么；
对话跳干跑还渲染成「⚠️ 对话失败」，像是真跳出了事故。

改后：没有配置文件 → 提示先 init；JSON 写坏 → 给出第几行第几列并提示 validate。
推送文本仍不带本机路径（audit-01 R4）；干跑一律以 🧪 开头。全部在临时 HOME + 假 npx 下跑。
"""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_onboard01_new_user import Sandbox  # noqa: E402

WRAPPERS = ('boss-hunt-tick.sh', 'boss-hunt-chat.sh', 'boss-hunt-inbox.sh')


class MissingConfigTests(unittest.TestCase):
    def setUp(self):
        self.box = Sandbox(self)

    def assert_no_local_path(self, text):
        self.assertNotIn(str(self.box.home), text)
        self.assertNotIn('Traceback', text)

    def test_dry_run_before_init_says_run_init(self):
        for name in WRAPPERS:
            p = self.box.wrapper(name, '--dry-run')
            out = p.stdout
            self.assertNotEqual(p.returncode, 0, name)
            self.assertIn('init', out, name + out)
            self.assertIn('还没有配置文件', out, name + out)
            self.assertTrue(out.lstrip().startswith('🧪'), name + out)
            self.assertNotIn('对话失败', out, name)
            self.assert_no_local_path(out)
            self.assertFalse((self.box.home / '.boss-hunt').exists(), '干跑不该替用户建目录')
        self.box.assert_no_browser(self)

    def test_real_hop_before_init_says_run_init(self):
        for name in WRAPPERS:
            p = self.box.wrapper(name)
            self.assertNotEqual(p.returncode, 0, name)
            self.assertIn('init', p.stdout, name + p.stdout)
            self.assert_no_local_path(p.stdout)
        self.box.assert_no_browser(self)

    def test_broken_json_points_to_line_and_validate(self):
        self.box.config.parent.mkdir(parents=True)
        self.box.config.write_text('{\n  "user": {,\n}\n', encoding='utf-8')
        for name in WRAPPERS:
            p = self.box.wrapper(name, '--dry-run')
            out = p.stdout
            self.assertNotEqual(p.returncode, 0, name)
            self.assertIn('第 2 行', out, name + out)
            self.assertIn('validate', out, name + out)
            self.assertTrue(out.lstrip().startswith('🧪'), name + out)
            self.assert_no_local_path(out)
        self.box.assert_no_browser(self)


if __name__ == '__main__':
    unittest.main()
