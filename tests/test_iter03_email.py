"""iter-03：邮件推送渠道的三个坑。

1. `"to": "a@x.com, b@y.com"`（多收件人最自然的写法）以前整串当成一个地址交给 SMTP，直接被拒；
2. 登录 / 发信失败时连接没关；
3. `smtp_port` 写成非数字时 int() 抛 ValueError 没人接，堆栈（带本机路径）进播报；problems() 也不查。

全部离线：SMTP 一律替身，不联网；HOME 指到临时目录。
"""
import os
import smtplib
import sys
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_notify01_push import Isolated, N  # noqa: E402


class EmailChannelTests(Isolated):
    def setUp(self):
        super().setUp()
        os.environ['BOSS_NOTIFY_SMTP_PASSWORD'] = 'pw'

    def email_cfg(self, **email):
        base = {'smtp_host': 'smtp.qq.com', 'user': 'me@qq.com', 'to': 'me@qq.com'}
        base.update(email)
        return self.cfg('email', email=base)

    def test_comma_separated_recipients_are_split(self):
        smtp = mock.MagicMock()
        with mock.patch.object(N.smtplib, 'SMTP_SSL', return_value=smtp):
            r = N.send(self.email_cfg(to='a@x.com, b@y.com；c@z.com'), '正文', 'tick')
        self.assertTrue(r['sent'], r)
        _, rcpts, raw = smtp.sendmail.call_args[0]
        self.assertEqual(rcpts, ['a@x.com', 'b@y.com', 'c@z.com'])
        self.assertIn('To: a@x.com, b@y.com, c@z.com', raw)

    def test_login_failure_closes_connection_and_hides_password(self):
        smtp = mock.MagicMock()
        smtp.login.side_effect = smtplib.SMTPAuthenticationError(535, b'auth failed pw')
        with mock.patch.object(N.smtplib, 'SMTP_SSL', return_value=smtp):
            r = N.send(self.email_cfg(), '正文', 'tick')
        self.assertFalse(r['ok'])
        self.assertIn('SMTPAuthenticationError', r['error'])
        self.assertNotIn('pw', r['error'])
        self.assertTrue(smtp.close.called or smtp.quit.called, '失败时要关连接')

    def test_starttls_failure_closes_connection(self):
        smtp = mock.MagicMock()
        smtp.starttls.side_effect = smtplib.SMTPNotSupportedError('no tls')
        with mock.patch.object(N.smtplib, 'SMTP', return_value=smtp):
            r = N.send(self.email_cfg(smtp_port=587), '正文', 'tick')
        self.assertFalse(r['ok'])
        self.assertTrue(smtp.close.called or smtp.quit.called)

    def test_bad_port_is_a_config_problem_not_a_crash(self):
        for port in ('abc', 0, 70000, True, 46.5):
            cfg = self.email_cfg(smtp_port=port)
            self.assertTrue(any('smtp_port' in x for x in N.problems(cfg)), port)
            with mock.patch.object(N.smtplib, 'SMTP_SSL') as ctor:
                r = N.send(cfg, '正文', 'tick')
            self.assertFalse(r['ok'], port)
            ctor.assert_not_called()

    def test_numeric_string_port_still_works(self):
        smtp = mock.MagicMock()
        with mock.patch.object(N.smtplib, 'SMTP', return_value=smtp) as ctor:
            r = N.send(self.email_cfg(smtp_port='587'), '正文', 'tick')
        self.assertTrue(r['sent'], r)
        self.assertEqual(ctor.call_args[0][:2], ('smtp.qq.com', 587))
        smtp.starttls.assert_called_once()

    def test_bad_recipient_shape_is_a_config_problem(self):
        for to in (['a@x.com', 3], [], ' , ', {'a': 1}):
            self.assertTrue(any('to' in x for x in N.problems(self.email_cfg(to=to))), to)


if __name__ == '__main__':
    unittest.main()
