"""safety-01：账本未核实按会话隔离。虚构数据、临时目录，不连浏览器。"""
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
sys.path.insert(0, str(ROOT / 'tests'))
import send_safety as SS
import test_conversation_run as T


def _cfg(tmp):
    return {'paths': {'workdir': tmp, 'state': str(Path(tmp) / 'state.json')},
            'schedule': {'timezone': 'Asia/Shanghai'},
            'sending': {'min_gap_seconds': 0}}  # audit-01 Q4：缺省间隔改 60 后，这里显式不限间隔


def _row(event_id, cmd, conv_key='', status='pending', **kw):
    return dict(event_id=event_id, cmd=cmd, conv_key=conv_key, status=status,
                ts='2026-11-05 12:00', ts_epoch=1, text='虚构', text_sha256='0', **kw)


class LedgerScopeTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.cfg = _cfg(self.tmp.name)

    def write(self, *rows, tail=b''):
        data = b''.join((json.dumps(r, ensure_ascii=False) + '\n').encode() for r in rows) + tail
        (Path(self.tmp.name) / 'send_ledger.jsonl').write_bytes(data)

    def blocks(self, **kw):
        try:
            SS.ledger_guard(self.cfg, **kw)
        except RuntimeError:
            return True
        return False

    def test_conv_pending_isolated_from_outbound_and_other_conv(self):
        for cmd in ('conv_text', 'conv_resume'):
            for status in ('pending', 'unknown'):
                with self.subTest(cmd=cmd, status=status):
                    self.write(_row('e1', cmd, 'pair:甲|虚构HR', status))
                    self.assertTrue(self.blocks())
                    self.assertFalse(self.blocks(scope='outbound'))
                    self.assertFalse(self.blocks(scope='conv', conv_key='pair:乙|虚构HR'))
                    self.assertTrue(self.blocks(scope='conv', conv_key='pair:甲|虚构HR'))
                    self.assertFalse(self.blocks(scope='conv'))
                    self.assertEqual(SS.conv_pending(self.cfg), {'pair:甲|虚构HR': ['e1']})

    def test_resolved_conv_rows_do_not_count(self):
        for status in ('sent', 'failed', 'voided'):
            with self.subTest(status=status):
                self.write(_row('e1', 'conv_resume', 'pair:甲|虚构HR'), _row('e1', 'conv_resume', 'pair:甲|虚构HR', status))
                self.assertFalse(self.blocks(scope='conv', conv_key='pair:甲|虚构HR'))
                self.assertEqual(SS.conv_pending(self.cfg), {})

    def test_non_conv_pending_blocks_every_scope(self):
        for cmd in ('send', 'auto_reply', 'interview_reply', 'followup', 'ledger-repair'):
            with self.subTest(cmd=cmd):
                self.write(_row('e1', cmd, 'job:虚构'))
                self.assertTrue(self.blocks())
                self.assertTrue(self.blocks(scope='outbound'))
                self.assertTrue(self.blocks(scope='conv', conv_key='pair:甲|虚构HR'))
                self.assertEqual(SS.conv_pending(self.cfg), {})

    def test_conv_row_without_key_blocks_every_scope(self):
        self.write(_row('e1', 'conv_text', ''))
        self.assertTrue(self.blocks(scope='outbound'))
        self.assertTrue(self.blocks(scope='conv', conv_key='pair:甲|虚构HR'))
        self.assertEqual(SS.conv_pending(self.cfg), {})

    def test_corrupt_tail_blocks_every_scope(self):
        self.write(_row('e1', 'conv_text', 'pair:甲|虚构HR', 'sent'), tail=b'{"broken')
        for kw in ({}, {'scope': 'outbound'}, {'scope': 'conv', 'conv_key': 'pair:甲|虚构HR'}):
            with self.subTest(kw=kw):
                self.assertTrue(self.blocks(**kw))

    def test_quarantine_file_blocks_every_scope(self):
        self.write()
        (Path(self.tmp.name) / 'send_ledger.corrupt-x').write_bytes(b'x')
        self.assertTrue(self.blocks(scope='outbound'))
        self.assertTrue(self.blocks(scope='conv', conv_key='pair:甲|虚构HR'))

    def test_unknown_scope_rejected(self):
        self.write()
        with self.assertRaises(ValueError):
            SS.ledger_guard(self.cfg, scope='job')

    def test_verify_only_touches_own_conversation(self):
        base = dict(msg_count=0, last_message_id='', click_ts_epoch=0)
        self.write(_row('a', 'conv_text', 'pair:甲|虚构HR', **base), _row('b', 'conv_text', 'pair:乙|虚构HR', **base))
        seen = []
        SS.ledger_guard(self.cfg, verify=lambda row: seen.append(row['event_id']) or True,
                        scope='conv', conv_key='pair:甲|虚构HR')
        self.assertEqual(seen, ['a'])
        self.assertEqual(SS.conv_pending(self.cfg), {'pair:乙|虚构HR': ['b']})

    def test_ledger_send_scopes(self):
        self.write(_row('e1', 'conv_resume', 'pair:甲|虚构HR'))
        send = Mock(return_value='sent')
        self.assertEqual(SS.ledger_send(self.cfg, 'send', 'job:j1', 'j1', '虚构招呼', send), 'sent')
        self.assertEqual(SS.ledger_send(self.cfg, 'conv_text', 'pair:乙|虚构HR', '', '虚构问句', send), 'sent')
        self.assertEqual(send.call_count, 2)
        for cmd in ('conv_text', 'conv_resume'):
            with self.subTest(cmd=cmd), self.assertRaises(RuntimeError):
                SS.ledger_send(self.cfg, cmd, 'pair:甲|虚构HR', '', '虚构', send)
        with self.assertRaises(RuntimeError):
            SS.ledger_send(self.cfg, 'auto_reply', 'pair:丙|虚构HR', '', '虚构', send)
        self.assertEqual(send.call_count, 2)

    def test_outbound_pending_still_blocks_conv_send(self):
        self.write(_row('e1', 'send', 'job:j1'))
        send = Mock(return_value='sent')
        with self.assertRaises(RuntimeError):
            SS.ledger_send(self.cfg, 'conv_text', 'pair:乙|虚构HR', '', '虚构', send)
        send.assert_not_called()


class ConversationRunScopeTests(unittest.TestCase):
    setUp = T.ConversationRunTests.setUp
    module = T.ConversationRunTests.module
    item = T.ConversationRunTests.item
    history = T.ConversationRunTests.history
    run_hop = T.ConversationRunTests.run_hop

    def ledger(self, *rows):
        (Path(self.tmp.name) / 'send_ledger.jsonl').write_text(
            ''.join(json.dumps(r, ensure_ascii=False) + '\n' for r in rows))

    def test_pending_conversation_skipped_others_processed(self):
        items = [self.item(1, age=1), self.item(2, age=5)]
        self.ledger(_row('e1', 'conv_resume', items[0]['conv_key']))
        result, opened, _, _, _ = self.run_hop(items=items)
        self.assertTrue(result['ok'])
        self.assertFalse(result['pending_verification'])
        self.assertEqual(result['pending_conversations'], 1)
        self.assertEqual([c.kwargs['conv_key'] for c in opened.call_args_list], ['id:fiction2'])
        self.assertEqual(result['processed'], 1)
        warn = [l for l in self.out if l.startswith('⚠️ 待核实')]
        self.assertEqual(warn, ['⚠️ 待核实 id:fiction1（发送结果待核对，只停这个会话）'])
        self.assertEqual(result['notifications'][0], warn[0])

    def test_pending_conversation_label_uses_record(self):
        import conv_store
        it = self.item(1)
        rec = conv_store.new_record(it['conv_key'], 'reply', '', '虚构甲', '虚构HR1', '虚构岗位', self.ts)
        rec['status'] = 'engaged'
        conv_store.save(self.path, {it['conv_key']: rec})
        self.ledger(_row('e1', 'conv_text', it['conv_key']))
        result, opened, _, _, _ = self.run_hop(items=[it])
        opened.assert_not_called()
        self.assertIn('⚠️ 待核实 虚构甲｜虚构岗位｜虚构HR1（发送结果待核对，只停这个会话）', self.out)

    def test_pending_out_of_view_conversation_not_revealed(self):
        from unittest.mock import patch
        c = self.module()
        it = self.item(1)
        self.ledger(_row('e1', 'conv_text', it['conv_key']))
        with patch.object(c.chat_dom, 'reveal_conversation') as reveal:
            result, opened, _, _, _ = self.run_hop(items=[it], row_snapshots=lambda *a: [])
        reveal.assert_not_called()
        opened.assert_not_called()
        self.assertFalse(any('📭' in l for l in self.out))

    def test_outbound_pending_still_stops_whole_hop(self):
        self.ledger(_row('e1', 'send', 'job:j1'))
        result, opened, _, _, _ = self.run_hop()
        opened.assert_not_called()
        self.assertFalse(result['ok'])
        self.assertTrue(result['pending_verification'])
        self.assertTrue(result['error'].startswith('unresolved_send'))


if __name__ == '__main__':
    unittest.main()


class _Stop(BaseException):
    pass


class CallSiteScopeTests(unittest.TestCase):
    """各发送入口传对 scope：对话发送锁本会话，投递入口不看对话行。"""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.cfg = _cfg(self.tmp.name)
        self.calls = []

    def spy(self, *a, **kw):
        self.calls.append(kw)
        raise _Stop()

    def test_outbound_entries_use_outbound_scope(self):
        from unittest.mock import patch
        import outbound
        with patch.object(outbound.SS, 'ledger_guard', side_effect=self.spy):
            for fn in (lambda: outbound.send_one(Mock(), 1, {'encryptJobId': 'j1'}, self.cfg),
                       lambda: outbound.do_send(self.cfg, Mock(), 1, {}, 0, [])):
                with self.assertRaises(_Stop):
                    fn()
        self.assertEqual(self.calls, [{'scope': 'outbound'}, {'scope': 'outbound'}])

    def test_do_send_runs_past_guard_with_conv_pending(self):
        import outbound
        (Path(self.tmp.name) / 'send_ledger.jsonl').write_text(
            json.dumps(_row('e1', 'conv_resume', 'pair:甲|虚构HR'), ensure_ascii=False) + '\n')
        self.cfg['sending'] = {'daily_cap': 0}
        out = []
        res = outbound.do_send(self.cfg, Mock(), 1, {'day_count': 0}, 0, out)
        self.assertEqual(res['sent'], 0)
        self.assertTrue(any('额度已用完' in line for line in out))

    def test_conv_text_and_resume_use_conv_scope(self):
        from unittest.mock import patch
        import conv_actions, conv_rules
        key = 'pair:甲|虚构HR'
        template = next(iter(conv_rules.DEFAULT_TEMPLATES.values()))
        before = {'conv_key': key, 'messages': [{'who': 'friend', 'id': 'm1', 'text': '虚构'}]}
        with patch.object(conv_actions.SS, 'ledger_guard', side_effect=self.spy), \
             patch.object(conv_actions.chat_dom, 'active_conversation', return_value=before):
            with self.assertRaises(_Stop):
                conv_actions.send_text(Mock(), 1, self.cfg, key, {}, template, template)
            with self.assertRaises(_Stop):
                conv_actions.send_resume(Mock(), 1, self.cfg, key, 'j1')
        self.assertEqual(self.calls[0]['scope'], 'conv')
        self.assertEqual(self.calls[0]['conv_key'], key)
        self.assertIn('verify', self.calls[0])
        self.assertEqual(self.calls[1], {'scope': 'conv', 'conv_key': key})

    def test_locked_command_and_tick_use_outbound_scope(self):
        import inspect
        import boss_hunt as B
        self.assertIn("ledger_guard(cfg, scope='outbound')", inspect.getsource(B.locked_command))
        self.assertIn("ledger_guard(cfg, scope='outbound')", inspect.getsource(B.run_tick))
        # 旧 inbox 与 reset-state 仍是全局闸
        self.assertIn('ledger_guard(cfg)\n', inspect.getsource(B.inbox))
        self.assertIn('ledger_guard(cfg)\n', inspect.getsource(B.cmd_reset_state))
