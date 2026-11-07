"""iter-04：~/.boss-hunt/.env 的两种常见写法以前读错。

- `KEY = value`（等号两边带空格）→ 报「没找到凭据」，用户明明写了；
- `KEY=value  # 备注`（行尾注释）→ 注释被粘进 webhook / key，`notify --check` 还说「凭据就绪」，真跳才失败。

模型 key 与推送凭据共用 greet._env_key，一起修。引号里的 `#` 是值的一部分（webhook、密码里可能有）。
HOME 指到临时目录，不碰真实 ~/.boss-hunt/.env。
"""
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
import greet as G  # noqa: E402

NAME = 'BOSS_ITER04_KEY'


class EnvFileParsingTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory(prefix='iter04-')
        self.addCleanup(tmp.cleanup)
        home = Path(tmp.name)
        env = {k: v for k, v in os.environ.items() if k not in (NAME, 'BOSS_HUNT_ENV_FILE')}
        env['HOME'] = str(home)
        patcher = mock.patch.dict(os.environ, env, clear=True)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.env_file = home / '.boss-hunt' / '.env'
        self.env_file.parent.mkdir()

    def read(self, content):
        self.env_file.write_text(content, encoding='utf-8')
        return G._env_key(NAME)

    def test_existing_forms_still_work(self):
        self.assertEqual(self.read('%s=abc\n' % NAME), 'abc')
        self.assertEqual(self.read('export %s="abc"\n' % NAME), 'abc')
        self.assertEqual(self.read("%s='abc'\n" % NAME), 'abc')
        self.assertEqual(self.read('%s_OTHER=zzz\n%s=abc\n' % (NAME, NAME)), 'abc')

    def test_spaces_around_equals(self):
        self.assertEqual(self.read('%s = abc\n' % NAME), 'abc')
        self.assertEqual(self.read('export %s =  "abc"\n' % NAME), 'abc')

    def test_trailing_comment_is_not_part_of_value(self):
        url = 'https://qyapi.weixin.qq.com/cgi-bin/webhook/send?key=K1'
        self.assertEqual(self.read('%s=%s  # 企业微信\n' % (NAME, url)), url)
        self.assertEqual(self.read('%s="abc" # note\n' % NAME), 'abc')

    def test_hash_inside_value_is_kept(self):
        self.assertEqual(self.read('%s="pa#ss"\n' % NAME), 'pa#ss')
        self.assertEqual(self.read("%s='pa #ss'  # c\n" % NAME), 'pa #ss')
        self.assertEqual(self.read('%s=pa#ss\n' % NAME), 'pa#ss')  # 无空格的 # 不算注释

    def test_commented_out_line_is_ignored(self):
        self.assertEqual(self.read('# %s=old\n%s=new\n' % (NAME, NAME)), 'new')

    def test_prefix_name_does_not_match(self):
        self.assertEqual(self.read('%sX=abc\n' % NAME), '')


if __name__ == '__main__':
    unittest.main()
