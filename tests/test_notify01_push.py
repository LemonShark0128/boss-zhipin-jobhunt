"""notify-01：定时结果推给用户本人。

系统 cron 跑 wrapper 时没有任何 Agent 在场，wrapper 的 stdout 只有 Hermes 这类调度器会转发；
其他 Agent（Kimi Code、MiniMax Code、Claude Code、Codex……）收不到。所以由脚本按 config.notify
把每跳播报推到用户选的渠道，并要求各平台的 Agent 在上手访谈里问清「推给谁」、配好、发一条测试。

全部离线：HTTP 发送一律替身（N._http），SMTP 替身，命令渠道用本机 python 写临时文件；
wrapper 实测在临时 HOME + 假 npx 下跑。不读写 ~/.boss-hunt 与 ~/.hermes。
"""
import base64
import hashlib
import hmac
import json
import os
import subprocess
import sys
import tempfile
import unittest
import urllib.parse
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / 'scripts'
sys.path.insert(0, str(SCRIPTS))
sys.path.insert(0, str(Path(__file__).resolve().parent))
import hunt_schedule as HQ  # noqa: E402
import notify as N  # noqa: E402
from test_onboard01_new_user import Sandbox  # noqa: E402

SECRET_ENV = ('BOSS_NOTIFY_SERVERCHAN_KEY', 'BOSS_NOTIFY_PUSHPLUS_TOKEN', 'BOSS_NOTIFY_WECOM_WEBHOOK',
              'BOSS_NOTIFY_FEISHU_WEBHOOK', 'BOSS_NOTIFY_FEISHU_SECRET', 'BOSS_NOTIFY_DINGTALK_WEBHOOK',
              'BOSS_NOTIFY_DINGTALK_SECRET', 'BOSS_NOTIFY_BARK_KEY', 'BOSS_NOTIFY_NTFY_TOPIC',
              'BOSS_NOTIFY_TELEGRAM_TOKEN', 'BOSS_NOTIFY_TELEGRAM_CHAT_ID', 'BOSS_NOTIFY_SMTP_PASSWORD')


def example_cfg():
    return json.loads((ROOT / 'assets' / 'config.example.json').read_text(encoding='utf-8'))


class Isolated(unittest.TestCase):
    """每个用例一个空 HOME，环境里没有任何推送凭据，_env_key 不会摸到真实 ~/.boss-hunt/.env。"""

    def setUp(self):
        tmp = tempfile.TemporaryDirectory(prefix='notify01-')
        self.addCleanup(tmp.cleanup)
        self.tmp = Path(tmp.name)
        clean = {k: v for k, v in os.environ.items() if k not in SECRET_ENV + ('BOSS_HUNT_ENV_FILE',)}
        clean['HOME'] = str(self.tmp)
        patcher = mock.patch.dict(os.environ, clean, clear=True)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.calls = []

    def fake_http(self, reply=(200, '{"errcode":0,"code":0}')):
        def http(url, body=None, headers=None, timeout=10):
            self.calls.append({'url': url, 'body': body, 'headers': dict(headers or {})})
            return reply
        return mock.patch.object(N, '_http', side_effect=http)

    def cfg(self, channel, **extra):
        cfg = {'notify': dict({'channel': channel}, **extra)}
        return cfg

    def body_json(self, i=0):
        return json.loads(self.calls[i]['body'].decode('utf-8'))


class ChannelRequestTests(Isolated):
    def test_none_sends_nothing(self):
        with self.fake_http():
            r = N.send(self.cfg('none'), '本跳投了 3 个', 'tick')
        self.assertEqual((r['ok'], r['sent']), (True, False))
        self.assertEqual(self.calls, [])

    def test_missing_block_means_none(self):
        with self.fake_http():
            r = N.send({}, 'x', 'tick')
        self.assertEqual((r['ok'], r['sent'], r['channel']), (True, False, 'none'))

    def test_empty_text_is_not_pushed(self):
        os.environ['BOSS_NOTIFY_WECOM_WEBHOOK'] = 'https://qyapi.weixin.qq.com/cgi-bin/webhook/send?key=K1'
        with self.fake_http():
            r = N.send(self.cfg('wecom'), '  \n', 'chat')
        self.assertEqual((r['ok'], r['sent']), (True, False))
        self.assertEqual(self.calls, [])

    def test_wecom_text_message(self):
        os.environ['BOSS_NOTIFY_WECOM_WEBHOOK'] = 'https://qyapi.weixin.qq.com/cgi-bin/webhook/send?key=K1'
        with self.fake_http((200, '{"errcode":0,"errmsg":"ok"}')):
            r = N.send(self.cfg('wecom'), '📮 投递｜发 3', 'tick')
        self.assertTrue(r['sent'], r)
        call = self.calls[0]
        self.assertEqual(call['url'], 'https://qyapi.weixin.qq.com/cgi-bin/webhook/send?key=K1')
        self.assertEqual(call['headers']['Content-Type'], 'application/json')
        body = self.body_json()
        self.assertEqual(body['msgtype'], 'text')
        self.assertIn('📮 投递｜发 3', body['text']['content'])
        self.assertIn('BOSS', body['text']['content'])  # 带标题，钉钉关键词与人眼都靠它

    def test_wecom_content_capped_at_2048_bytes(self):
        os.environ['BOSS_NOTIFY_WECOM_WEBHOOK'] = 'https://qyapi.weixin.qq.com/cgi-bin/webhook/send?key=K1'
        with self.fake_http((200, '{"errcode":0}')):
            N.send(self.cfg('wecom'), '汉' * 3000, 'inbox')
        content = self.body_json()['text']['content']
        self.assertLessEqual(len(content.encode('utf-8')), 2048)
        self.assertIn('截断', content)

    def test_wecom_errcode_is_failure_and_hides_secret(self):
        os.environ['BOSS_NOTIFY_WECOM_WEBHOOK'] = 'https://qyapi.weixin.qq.com/cgi-bin/webhook/send?key=SECRETKEY'
        reply = (200, '{"errcode":93000,"errmsg":"invalid webhook url, hint: [1], from ip: 1.2.3.4"}')
        with self.fake_http(reply):
            r = N.send(self.cfg('wecom'), 'x', 'tick')
        self.assertEqual((r['ok'], r['sent']), (False, False))
        self.assertIn('93000', r['error'])
        self.assertNotIn('SECRETKEY', r['error'])
        self.assertNotIn('1.2.3.4', r['error'])

    def test_network_error_hides_url(self):
        os.environ['BOSS_NOTIFY_WECOM_WEBHOOK'] = 'https://qyapi.weixin.qq.com/cgi-bin/webhook/send?key=SECRETKEY'

        def boom(url, body=None, headers=None, timeout=10):
            raise OSError('connect failed to %s' % url)
        with mock.patch.object(N, '_http', side_effect=boom):
            r = N.send(self.cfg('wecom'), 'x', 'tick')
        self.assertFalse(r['ok'])
        self.assertNotIn('SECRETKEY', r['error'])

    def test_feishu_plain_and_signed(self):
        os.environ['BOSS_NOTIFY_FEISHU_WEBHOOK'] = 'https://open.feishu.cn/open-apis/bot/v2/hook/abc'
        with self.fake_http((200, '{"code":0,"msg":"success"}')):
            self.assertTrue(N.send(self.cfg('feishu'), '你好', 'inbox')['sent'])
        body = self.body_json()
        self.assertEqual(body['msg_type'], 'text')
        self.assertIn('你好', body['content']['text'])
        self.assertNotIn('sign', body)
        os.environ['BOSS_NOTIFY_FEISHU_SECRET'] = 'S3'
        with self.fake_http((200, '{"code":0}')), mock.patch.object(N.time, 'time', return_value=1700000000.5):
            N.send(self.cfg('feishu'), '你好', 'inbox')
        body = self.body_json(1)
        self.assertEqual(body['timestamp'], '1700000000')
        want = base64.b64encode(hmac.new(b'1700000000\nS3', b'', hashlib.sha256).digest()).decode()
        self.assertEqual(body['sign'], want)

    def test_feishu_code_nonzero_is_failure(self):
        os.environ['BOSS_NOTIFY_FEISHU_WEBHOOK'] = 'https://open.feishu.cn/open-apis/bot/v2/hook/abc'
        with self.fake_http((200, '{"code":19021,"msg":"sign match fail"}')):
            r = N.send(self.cfg('feishu'), 'x', 'tick')
        self.assertFalse(r['ok'])
        self.assertIn('19021', r['error'])

    def test_dingtalk_signed_url(self):
        os.environ['BOSS_NOTIFY_DINGTALK_WEBHOOK'] = 'https://oapi.dingtalk.com/robot/send?access_token=T'
        os.environ['BOSS_NOTIFY_DINGTALK_SECRET'] = 'SEC'
        with self.fake_http((200, '{"errcode":0,"errmsg":"ok"}')), \
                mock.patch.object(N.time, 'time', return_value=1700000000.123):
            self.assertTrue(N.send(self.cfg('dingtalk'), '约面', 'inbox')['sent'])
        url = self.calls[0]['url']
        q = urllib.parse.parse_qs(urllib.parse.urlparse(url).query)
        self.assertEqual(q['access_token'], ['T'])
        self.assertEqual(q['timestamp'], ['1700000000123'])
        want = base64.b64encode(hmac.new(b'SEC', b'1700000000123\nSEC', hashlib.sha256).digest()).decode()
        self.assertEqual(q['sign'], [want])
        body = self.body_json()
        self.assertEqual(body['msgtype'], 'text')
        self.assertIn('约面', body['text']['content'])

    def test_serverchan_turbo_and_sc3(self):
        os.environ['BOSS_NOTIFY_SERVERCHAN_KEY'] = 'SCT123abc'
        with self.fake_http((200, '{"code":0}')):
            self.assertTrue(N.send(self.cfg('serverchan'), '正文', 'tick')['sent'])
        self.assertEqual(self.calls[0]['url'], 'https://sctapi.ftqq.com/SCT123abc.send')
        body = self.body_json()
        self.assertIn('BOSS', body['title'])
        self.assertEqual(body['desp'], '正文')
        os.environ['BOSS_NOTIFY_SERVERCHAN_KEY'] = 'sctp4567tABC'
        with self.fake_http((200, '{"code":0}')):
            N.send(self.cfg('serverchan'), '正文', 'tick')
        self.assertEqual(self.calls[1]['url'], 'https://4567.push.ft07.com/send/sctp4567tABC.send')

    def test_serverchan_error_code_is_failure(self):
        os.environ['BOSS_NOTIFY_SERVERCHAN_KEY'] = 'SCTbad'
        with self.fake_http((200, '{"message":"[AUTH]错误的Key","code":40001}')):
            r = N.send(self.cfg('serverchan'), 'x', 'tick')
        self.assertFalse(r['ok'])
        self.assertIn('40001', r['error'])

    def test_pushplus(self):
        os.environ['BOSS_NOTIFY_PUSHPLUS_TOKEN'] = 'PPT'
        with self.fake_http((200, '{"code":200,"msg":"请求成功","data":"x"}')):
            self.assertTrue(N.send(self.cfg('pushplus'), '正文<b>', 'chat')['sent'])
        self.assertEqual(self.calls[0]['url'], 'https://www.pushplus.plus/send')
        body = self.body_json()
        self.assertEqual((body['token'], body['template'], body['content']), ('PPT', 'txt', '正文<b>'))
        with self.fake_http((200, '{"code":903,"msg":"用户令牌不正确"}')):
            self.assertFalse(N.send(self.cfg('pushplus'), 'x', 'chat')['ok'])

    def test_bark_default_and_self_hosted_server(self):
        os.environ['BOSS_NOTIFY_BARK_KEY'] = 'BK'
        with self.fake_http((200, '{"code":200,"message":"success"}')):
            self.assertTrue(N.send(self.cfg('bark'), '正文', 'inbox')['sent'])
        self.assertEqual(self.calls[0]['url'], 'https://api.day.app/BK')
        form = urllib.parse.parse_qs(self.calls[0]['body'].decode())
        self.assertEqual(form['body'], ['正文'])
        self.assertIn('BOSS', form['title'][0])
        with self.fake_http((200, '{"code":200}')):
            N.send(self.cfg('bark', server='https://bark.example.com/'), '正文', 'inbox')
        self.assertEqual(self.calls[1]['url'], 'https://bark.example.com/BK')
        with self.fake_http((200, '{"code":400,"message":"failed to get device token"}')):
            self.assertFalse(N.send(self.cfg('bark'), 'x', 'inbox')['ok'])

    def test_ntfy_utf8_title_is_rfc2047_encoded(self):
        os.environ['BOSS_NOTIFY_NTFY_TOPIC'] = 'my-secret-topic'
        with self.fake_http((200, '{"id":"x","event":"message"}')):
            self.assertTrue(N.send(self.cfg('ntfy'), '正文' * 3000, 'tick')['sent'])
        call = self.calls[0]
        self.assertEqual(call['url'], 'https://ntfy.sh/my-secret-topic')
        title = call['headers']['Title']
        self.assertTrue(title.startswith('=?UTF-8?B?') and title.endswith('?='), title)
        self.assertIn('BOSS', base64.b64decode(title[10:-2]).decode('utf-8'))
        self.assertLessEqual(len(call['body']), 4096)

    def test_telegram(self):
        os.environ['BOSS_NOTIFY_TELEGRAM_TOKEN'] = '123:ABC'
        os.environ['BOSS_NOTIFY_TELEGRAM_CHAT_ID'] = '42'
        with self.fake_http((200, '{"ok":true,"result":{}}')):
            self.assertTrue(N.send(self.cfg('telegram'), 'x' * 5000, 'tick')['sent'])
        self.assertEqual(self.calls[0]['url'], 'https://api.telegram.org/bot123:ABC/sendMessage')
        body = self.body_json()
        self.assertEqual(body['chat_id'], '42')
        self.assertLessEqual(len(body['text']), 4096)
        with self.fake_http((200, '{"ok":false,"error_code":400,"description":"chat not found"}')):
            r = N.send(self.cfg('telegram'), 'x', 'tick')
        self.assertFalse(r['ok'])
        self.assertNotIn('123:ABC', r['error'])

    def test_http_error_status_is_failure(self):
        os.environ['BOSS_NOTIFY_NTFY_TOPIC'] = 't'
        with self.fake_http((403, 'forbidden')):
            r = N.send(self.cfg('ntfy'), 'x', 'tick')
        self.assertFalse(r['ok'])
        self.assertIn('403', r['error'])

    def test_email_via_smtp_ssl(self):
        os.environ['BOSS_NOTIFY_SMTP_PASSWORD'] = 'pw'
        email = {'smtp_host': 'smtp.qq.com', 'user': 'me@qq.com', 'to': 'me@qq.com'}
        smtp = mock.MagicMock()
        with mock.patch.object(N.smtplib, 'SMTP_SSL', return_value=smtp) as ctor:
            r = N.send(self.cfg('email', email=email), '正文', 'inbox')
        self.assertTrue(r['sent'], r)
        ctor.assert_called_once()
        self.assertEqual(ctor.call_args[0][:2], ('smtp.qq.com', 465))
        smtp.login.assert_called_once_with('me@qq.com', 'pw')
        sender, rcpts, raw = smtp.sendmail.call_args[0]
        self.assertEqual((sender, rcpts), ('me@qq.com', ['me@qq.com']))
        self.assertIn('Subject: =?utf-8?', raw)

    def test_command_channel_gets_text_on_stdin(self):
        out = self.tmp / 'got.txt'
        code = ('import os,sys;open(sys.argv[1],"w",encoding="utf-8").write('
                'os.environ["BOSS_NOTIFY_TITLE"]+"|"+sys.stdin.read())')
        r = N.send(self.cfg('command', command=[sys.executable, '-c', code, str(out)]), '正文', 'chat')
        self.assertTrue(r['sent'], r)
        title, body = out.read_text(encoding='utf-8').split('|', 1)
        self.assertIn('对话', title)
        self.assertEqual(body.strip(), '正文')

    def test_command_failure_reported(self):
        r = N.send(self.cfg('command', command=[sys.executable, '-c', 'import sys;sys.exit(3)']), 'x', 'chat')
        self.assertFalse(r['ok'])
        self.assertIn('3', r['error'])

    def test_missing_secret_is_failure_without_network(self):
        with self.fake_http():
            r = N.send(self.cfg('serverchan'), 'x', 'tick')
        self.assertFalse(r['ok'])
        self.assertIn('BOSS_NOTIFY_SERVERCHAN_KEY', r['error'])
        self.assertEqual(self.calls, [])

    def test_secret_read_from_env_file(self):
        env_file = self.tmp / '.boss-hunt' / '.env'
        env_file.parent.mkdir()
        env_file.write_text('BOSS_NOTIFY_PUSHPLUS_TOKEN="FROMFILE"\n', encoding='utf-8')
        with self.fake_http((200, '{"code":200}')):
            self.assertTrue(N.send(self.cfg('pushplus'), 'x', 'tick')['sent'])
        self.assertEqual(self.body_json()['token'], 'FROMFILE')


class ValidateTests(Isolated):
    def test_example_config_declares_channel_and_is_clean(self):
        cfg = example_cfg()
        self.assertEqual(cfg['notify']['channel'], 'none')
        errs, warns = HQ.validate_cfg(cfg)
        self.assertEqual(errs, [])
        self.assertFalse([w for w in warns if 'notify' in w], warns)

    def test_notify_problems_warn_but_never_block_delivery(self):
        for notify, needle in (({'channel': 'wechat'}, 'notify.channel'),
                               ({'channel': 'wecom'}, 'BOSS_NOTIFY_WECOM_WEBHOOK'),
                               ({'channel': 'email', 'email': {}}, 'notify.email'),
                               ({'channel': 'command', 'command': 'echo hi'}, 'notify.command'),
                               ('wecom', 'notify 必须是对象')):
            cfg = example_cfg()
            cfg['notify'] = notify
            errs, warns = HQ.validate_cfg(cfg)
            self.assertEqual(errs, [], notify)
            self.assertTrue(any(needle in w for w in warns), (notify, warns))

    def test_check_reports_ready(self):
        os.environ['BOSS_NOTIFY_WECOM_WEBHOOK'] = 'https://qyapi.weixin.qq.com/cgi-bin/webhook/send?key=K'
        ok, label, problems = N.check(self.cfg('wecom'))
        self.assertEqual((ok, problems), (True, []))
        self.assertIn('企业微信', label)

    def test_webhook_must_be_https(self):
        os.environ['BOSS_NOTIFY_WECOM_WEBHOOK'] = 'qyapi.weixin.qq.com/cgi-bin/webhook/send?key=K'
        ok, _, problems = N.check(self.cfg('wecom'))
        self.assertFalse(ok)
        self.assertTrue(any('https' in p for p in problems))


class CliAndWrapperTests(unittest.TestCase):
    def setUp(self):
        self.box = Sandbox(self)
        rc, data, p = self.box.cli('init')
        self.assertEqual(rc, 0, p.stderr)
        self.got = self.box.home / 'pushed.txt'
        self.cmd = [sys.executable, '-c',
                    'import sys;open(sys.argv[1],"a",encoding="utf-8").write(sys.stdin.read()+"\\n---\\n")',
                    str(self.got)]

    def use_command_channel(self):
        self.box.edit_config(lambda c: c.__setitem__('notify', {'channel': 'command', 'command': self.cmd}))

    def run_cli(self, *argv, stdin=''):
        p = subprocess.run([sys.executable, str(SCRIPTS / 'boss_hunt.py'), '--json'] + list(argv),
                           cwd=str(ROOT), env=self.box.env, capture_output=True, text=True,
                           input=stdin, timeout=60)
        return p.returncode, json.loads(p.stdout), p

    def test_check_on_fresh_install_says_not_configured(self):
        rc, data, _ = self.run_cli('notify', '--check')
        self.assertEqual(rc, 0)
        self.assertEqual((data['channel'], data['configured']), ('none', False))
        self.assertIn('notify.channel', data['message'])

    def test_check_missing_secret_fails(self):
        self.box.edit_config(lambda c: c.__setitem__('notify', {'channel': 'pushplus'}))
        rc, data, _ = self.run_cli('notify', '--check')
        self.assertEqual((rc, data['ok']), (1, False))
        self.assertTrue(any('BOSS_NOTIFY_PUSHPLUS_TOKEN' in p for p in data['problems']))

    def test_test_without_channel_refuses(self):
        rc, data, _ = self.run_cli('notify', '--test')
        self.assertEqual((rc, data['ok'], data['sent']), (1, False, False))

    def test_test_message_goes_out_and_is_logged(self):
        self.use_command_channel()
        rc, data, p = self.run_cli('notify', '--test')
        self.assertEqual((rc, data['sent']), (0, True), p.stdout)
        self.assertIn('推送测试', self.got.read_text(encoding='utf-8'))
        log = (self.box.workdir / 'runs.log').read_text(encoding='utf-8')
        self.assertIn('推送', log)

    def test_stdin_push(self):
        self.use_command_channel()
        rc, data, _ = self.run_cli('notify', '--stdin', '--source', 'inbox', stdin='📬 回消息｜约面 1\n')
        self.assertEqual((rc, data['sent']), (0, True))
        self.assertIn('约面 1', self.got.read_text(encoding='utf-8'))

    def test_wrapper_dry_run_shows_push_status_and_never_pushes(self):
        self.use_command_channel()
        for name in ('boss-hunt-tick.sh', 'boss-hunt-chat.sh', 'boss-hunt-inbox.sh'):
            p = self.box.wrapper(name, '--dry-run')
            self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
            self.assertIn('推送', p.stdout, name)
            self.assertIn('自定义命令', p.stdout, name)
        self.assertFalse(self.got.exists(), '干跑不能真推')
        self.box.assert_no_browser(self)

    def test_wrapper_dry_run_without_channel_tells_user(self):
        p = self.box.wrapper('boss-hunt-inbox.sh', '--dry-run')
        self.assertIn('notify.channel', p.stdout)

    def test_real_inbox_hop_pushes_its_broadcast(self):
        # 假 npx 让收件箱连不上浏览器 → 失败播报；这段播报必须原样推到用户渠道，退出码仍是业务退出码
        self.use_command_channel()
        p = self.box.wrapper('boss-hunt-inbox.sh')
        self.assertTrue(self.got.exists(), p.stdout + p.stderr)
        pushed = self.got.read_text(encoding='utf-8')
        self.assertIn('回消息跳', pushed)
        first_line = p.stdout.strip().splitlines()[0]
        self.assertIn(first_line, pushed)

    def test_real_hop_push_failure_does_not_change_exit_code(self):
        self.box.edit_config(lambda c: c.__setitem__(
            'notify', {'channel': 'command', 'command': [sys.executable, '-c', 'import sys;sys.exit(5)']}))
        p_fail = self.box.wrapper('boss-hunt-inbox.sh')
        self.box.edit_config(lambda c: c.__setitem__('notify', {'channel': 'none'}))
        p_none = self.box.wrapper('boss-hunt-inbox.sh')
        self.assertEqual(p_fail.returncode, p_none.returncode)
        self.assertIn('推送失败', p_fail.stdout)


class DocTests(unittest.TestCase):
    """每个平台的 Agent 读到的入口文件都要让它主动问「推给谁」、配好、发测试。"""

    def read(self, rel):
        return (ROOT / rel).read_text(encoding='utf-8')

    def test_agent_entrypoints_require_asking_where_to_push(self):
        for rel in ('AGENTS.md', 'SKILL.md', 'docs/onboarding-interview.md'):
            text = self.read(rel)
            self.assertIn('notify --test', text, rel)
            self.assertIn('推给谁', text, rel)

    def test_interview_makes_push_a_must_ask(self):
        text = self.read('docs/onboarding-interview.md')
        must = text.split('## 用户常忘')[0]
        self.assertIn('notify.channel', must)

    def test_adapters_cover_every_platform_and_channel(self):
        text = self.read('docs/agent-adapters.md')
        section = text.split('定时结果推给谁', 1)[1]
        for name in ('Hermes', 'Claude Code', 'Codex', 'Kimi Code', 'MiniMax Code', 'DeepSeek Harness', 'WorkBuddy'):
            self.assertIn(name, section, name)
        conf = self.read('docs/configuration.md')
        for channel, spec in N.CHANNELS.items():
            self.assertIn('`%s`' % channel, conf, channel)
            for env in spec['env'] + spec.get('optional', []):
                self.assertIn(env, conf, env)


if __name__ == '__main__':
    unittest.main()
