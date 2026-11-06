"""cleanup-02：旧回消息链路移除后，inbox 只读、validate 提示、子命令下线。只用虚构数据与临时目录。"""
import io
import json
import time
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import patch

import test_broadcast_visibility_20261029 as V
from test_phase_a import Base

B = V.B
import chat_dom as CD
import conv_actions as CA
import hunt_filter as HF
import hunt_state as HS
import send_safety as SS

LEGACY_ON = {'auto_reply': {'enabled': True, 'max_tries': 8, 'daily_cap': 5},
             'interview_auto_reply': True,
             'followup': {'enabled': True, 'after_hours': 48}}
EMPTY_KEYS = ('auto_replied', 'auto_failed', 'replied_ok', 'reply_failed', 'followed_up', 'manual_reply')
COUNT_KEYS = {'new_greet', 'open_failed', 'already_mine', 'owner_unknown', 'replied', 'manual'}


class InboxReadOnlyTests(unittest.TestCase):
    setUp = V.BroadcastTests.setUp
    save = V.BroadcastTests.save

    def items(self):
        now_ms = int(time.time() * 1000)
        return [
            {'encryptJobId': 'jx1', 'brandName': '虚构甲公司', 'name': '虚构HR甲', 'jobId': 1,
             'lastMessageInfo': {'showText': '请问什么时候方便面试', 'msgTime': now_ms - 60000, 'fromSelf': False}},
            {'encryptJobId': 'jx2', 'brandName': '虚构乙公司', 'name': '虚构HR乙', 'jobId': 2,
             'lastMessageInfo': {'showText': '您好，我对这个岗位很感兴趣，期待和您沟通',
                                 'msgTime': now_ms - 50 * 3600 * 1000, 'fromSelf': True}},
        ]

    def run_inbox(self, inbox_cfg):
        self.cfg['inbox'] = inbox_cfg
        self.browser.network.return_value = {'text': json.dumps(
            {'code': 0, 'zpData': {'result': self.items()}}, ensure_ascii=False)}
        st = HS.load_state(self.cfg)
        st['sent']['jx2'] = {'ts': '2026-10-27 10:00', 'brand': '虚构乙公司'}
        HS.save_state(self.cfg, st)
        st = HS.load_state(self.cfg)
        ledger = Path(SS.ledger_path(self.cfg))
        before = ledger.read_bytes() if ledger.exists() else b''
        out = []
        with patch.object(B, 'scan_conv_states', return_value=dict(checked=0, failed=0, skipped_failures=0, deadline=False)), \
             patch.object(HF, 'load_jobs', return_value=[]), \
             patch.object(CD, 'conversation_rows', return_value=[{'conv_key': 'id:1'}]) as rows, \
             patch.object(CD, 'open_by_index', return_value=('ok', '虚构甲公司', [{'who': 'friend', 'text': '在吗'}])) as opener, \
             patch.object(CA, 'send_resume', return_value='sent') as resume:
            data = B.inbox(self.cfg, self.browser, 1, st, out)
        after = ledger.read_bytes() if ledger.exists() else b''
        return data, st, out, dict(rows=rows, opener=opener, resume=resume), before, after

    def test_legacy_switches_on_do_not_send(self):
        data, st, out, mocks, before, after = self.run_inbox(LEGACY_ON)
        for name, mock in mocks.items():
            with self.subTest(mock=name):
                mock.assert_not_called()
        self.browser.click.assert_not_called()
        self.assertEqual(before, after)
        for key in EMPTY_KEYS:
            with self.subTest(key=key):
                self.assertEqual(data[key], [])
        self.assertEqual(set(data['auto_reply_counts']), COUNT_KEYS)
        self.assertFalse(any(data['auto_reply_counts'].values()))
        self.assertFalse(any('自动回话' in line or '跟进' in line or '约面试 虚构' in line for line in out))
        self.assertEqual(st.get('followed'), [])

    def test_interview_still_registered_as_pending(self):
        data, st, out, _, _, _ = self.run_inbox({})
        self.assertEqual([(m['job_id'], m['replied']) for m in data['interviews']], [('jx1', False)])
        self.assertIn('jx1', st['pending'])
        self.assertTrue(any('约面试 1' in line for line in out))


class ValidateLegacyWarningTests(Base):
    def invoke(self, *options):
        self.path.write_text(json.dumps(self.cfg, ensure_ascii=False))
        args = B.build_parser().parse_args(['--config', str(self.path), 'validate', '--json', *options])
        stream = io.StringIO()
        with redirect_stdout(stream):
            code = args.func(args)
        return code, json.loads(stream.getvalue())

    def legacy_warnings(self, payload):
        return [w for w in payload['warnings'] if '已移除' in w]

    def test_each_enabled_switch_warns_without_failing(self):
        self.cfg['inbox'] = dict(LEGACY_ON)
        code, payload = self.invoke()
        self.assertEqual(code, 0)
        warnings = self.legacy_warnings(payload)
        self.assertEqual(len(warnings), 3)
        for key in ('inbox.auto_reply.enabled', 'inbox.interview_auto_reply', 'inbox.followup.enabled'):
            self.assertTrue(any(key in w for w in warnings), key)

    def test_disabled_or_missing_switches_do_not_warn(self):
        for inbox in ({}, {'auto_reply': {'enabled': False}, 'interview_auto_reply': False,
                           'followup': {'enabled': False}}):
            with self.subTest(inbox=inbox):
                self.cfg['inbox'] = inbox
                code, payload = self.invoke()
                self.assertEqual(code, 0)
                self.assertEqual(self.legacy_warnings(payload), [])


class RemovedCommandTests(unittest.TestCase):
    def test_removed_subcommands_are_rejected(self):
        parser = B.build_parser()
        for argv in (['reply-to', '虚构公司', '--brief', '虚构要点'], ['human-compact'], ['human-stats']):
            with self.subTest(argv=argv), redirect_stdout(io.StringIO()), \
                    patch('sys.stderr', io.StringIO()), self.assertRaises(SystemExit):
                parser.parse_args(argv)

    def test_kept_subcommands_still_parse(self):
        parser = B.build_parser()
        for argv in (['inbox'], ['chat'], ['tick', '--dry-run'], ['ledger-ack', '--list']):
            with self.subTest(argv=argv):
                self.assertTrue(callable(parser.parse_args(argv).func))


class HistoricalLedgerReplayTests(Base):
    def test_legacy_ledger_rows_still_replay_into_state(self):
        rows = [
            ('interview_reply', 'id:k1', 'jh1', '方便线上面试，请先给时段'),
            ('followup', 'id:k2', 'jh2', '想确认下这个岗位还在招吗'),
            ('auto_reply', 'id:k3', '', '可以的，请问负责哪个区域'),
            ('reply-to', 'id:k4', '', '我这边做过虚构平台的拓展'),
        ]
        self.cfg['inbox'] = {'auto_reply': {'daily_cap': 10}}
        self.cfg.setdefault('sending', {})['min_gap_seconds'] = 0
        for cmd, key, jid, text in rows:
            SS.ledger_send(self.cfg, cmd, key, jid, text, lambda: 'replied')
        st = HS.load_state(self.cfg)
        HS.refresh_ledger_counts(self.cfg, st)
        self.assertIn('jh1', st['replied'])
        self.assertIn('jh2', st['followed'])
        self.assertEqual(len(st.get('auto_replies') or {}), 2)


if __name__ == '__main__':
    unittest.main()
