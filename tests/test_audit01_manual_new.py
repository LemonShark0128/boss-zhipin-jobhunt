"""audit-01 U1：「需人工」会话又来新的 HR 消息时提醒一次并重新置顶；只看列表快照，不打开、不回复。

虚构数据、临时目录，不连浏览器、不调模型。
"""
import unittest
from unittest.mock import patch

import test_conversation_run as T
import conv_store


class ManualNewMessageTests(unittest.TestCase):
    setUp = T.ConversationRunTests.setUp
    module = T.ConversationRunTests.module
    item = T.ConversationRunTests.item
    history = T.ConversationRunTests.history
    run_hop = T.ConversationRunTests.run_hop
    record = T.ConversationRunTests.record

    HR = '您好，岗位还在招，方便明天电话沟通一下吗'

    def manual(self, item, seen_age=3600, status='manual'):
        row = self.record(item, status=status)
        row['last_seen'] = {'ts': self.ts - seen_age, 'text': '旧消息'}
        row['notified']['manual'] = True
        return row

    def hop(self, items, records, dry=False):
        conv_store.save(self.path, records)
        c = self.module()
        with patch.object(c.chat_dom, 'reveal_conversation', return_value={'ok': False, 'reason': 'not_found'}), \
             patch.object(c.chat_dom, 'reset_list_scroll'):
            return self.run_hop(items=items, dry=dry)

    def lines(self):
        return [l for l in self.out if '需人工·新消息' in l]

    def test_new_hr_message_pushed_once_and_pinned(self):
        it = self.item(1, text=self.HR, age=60)
        result, opened, calls, _, _ = self.hop([it], {it['conv_key']: self.manual(it)})
        opened.assert_not_called()
        self.assertFalse(calls)
        self.assertEqual(len(self.lines()), 1)
        self.assertIn('虚构公司1', self.lines()[0])
        self.assertIn(self.HR[:10], self.lines()[0])
        self.assertEqual(result['manual_new'], 1)
        saved = conv_store.load(self.path)[it['conv_key']]
        self.assertEqual(saved['status'], 'manual')
        self.assertEqual(saved['manual_new_snap']['text'], self.HR)
        self.assertEqual(saved['pin']['reason'], 'manual_new')
        self.assertIsNone(saved['pin']['done_ts'])
        self.assertTrue(any(l in result['notifications'] for l in self.lines()))

    def test_same_message_not_pushed_again(self):
        it = self.item(1, text=self.HR, age=60)
        self.hop([it], {it['conv_key']: self.manual(it)})
        self.out.clear()
        self.br.new_tab.side_effect = [20, 21, 22, 23]
        result, _, _, _, _ = self.hop([it], conv_store.load(self.path))
        self.assertEqual(self.lines(), [])
        self.assertEqual(result['manual_new'], 0)

    def test_unpinned_then_new_message_repins(self):
        it = self.item(1, text=self.HR, age=60)
        row = self.manual(it)
        row['pin'] = {'reason': 'manual', 'text': '旧', 'msg_ts': self.ts - 3600, 'want_ts': self.ts - 3600,
                      'pinned_ts': self.ts - 3500, 'done_ts': self.ts - 1000, 'done_by': 'user_unpinned',
                      'failures': 0, 'pin_migrated_13b': True}
        self.hop([it], {it['conv_key']: row})
        saved = conv_store.load(self.path)[it['conv_key']]
        self.assertIsNone(saved['pin']['done_ts'])
        self.assertIsNone(saved['pin']['pinned_ts'])

    def test_message_not_newer_than_last_seen_ignored(self):
        it = self.item(1, text=self.HR, age=7200)
        self.hop([it], {it['conv_key']: self.manual(it, seen_age=3600)})
        self.assertEqual(self.lines(), [])

    def test_override_record_without_last_seen_uses_updated_ts(self):
        it = self.item(1, text=self.HR, age=7200)
        row = self.manual(it)
        row['last_seen'] = {'ts': None, 'text': ''}
        row['updated_ts'] = self.ts - 3600
        self.hop([it], {it['conv_key']: row})
        self.assertEqual(self.lines(), [])

    def test_own_side_or_system_messages_ignored(self):
        cases = [self.item(1, text=self.HR, age=60, mine=True),
                 self.item(2, text='对方已同意，您的附件简历已发送给对方', age=60),
                 self.item(3, text='附件简历请求已发送', age=60)]
        self.hop(cases, {it['conv_key']: self.manual(it) for it in cases})
        self.assertEqual(self.lines(), [])

    def test_other_terminal_statuses_untouched(self):
        items = [self.item(n, text=self.HR, age=60) for n in (1, 2, 3)]
        records = {it['conv_key']: self.manual(it, status=s)
                   for it, s in zip(items, ('complete', 'closed', 'declined'))}
        self.hop(items, records)
        self.assertEqual(self.lines(), [])

    def test_at_most_three_per_hop(self):
        items = [self.item(n, text=self.HR + str(n), age=60 + n) for n in range(1, 6)]
        result, _, _, _, _ = self.hop(items, {it['conv_key']: self.manual(it) for it in items})
        self.assertEqual(len(self.lines()), 3)
        saved = conv_store.load(self.path)
        self.assertEqual(sum('manual_new_snap' in saved[it['conv_key']] for it in items), 3)

    def test_dry_run_reports_without_writing(self):
        it = self.item(1, text=self.HR, age=60)
        self.hop([it], {it['conv_key']: self.manual(it)}, dry=True)
        self.assertEqual(len(self.lines()), 1)
        self.assertNotIn('manual_new_snap', conv_store.load(self.path)[it['conv_key']])


    def test_message_before_user_unpinned_counts_as_seen(self):
        # 21:30 真机误报复现：消息 11:51 到，17:30 置顶，用户之后取消置顶（=看过），本跳不该再提醒
        it = self.item(1, text=self.HR, age=4 * 3600)
        row = self.manual(it, seen_age=30 * 3600)
        row['pin'] = {'reason': 'manual', 'text': '旧', 'msg_ts': self.ts - 30 * 3600, 'want_ts': self.ts - 30 * 3600,
                      'pinned_ts': self.ts - 2 * 3600, 'done_ts': None, 'done_by': None,
                      'failures': 0, 'pin_migrated_13b': True}
        it['isTop'] = 0  # 用户已取消置顶，本跳先登记 done，再判新消息
        self.hop([it], {it['conv_key']: row})
        self.assertEqual(self.lines(), [])
        self.assertEqual(conv_store.load(self.path)[it['conv_key']]['pin']['done_by'], 'user_unpinned')

    def test_message_after_user_unpinned_still_pushed(self):
        it = self.item(1, text=self.HR, age=60)
        row = self.manual(it, seen_age=30 * 3600)
        row['pin'] = {'reason': 'manual', 'text': '旧', 'msg_ts': self.ts - 30 * 3600, 'want_ts': self.ts - 30 * 3600,
                      'pinned_ts': self.ts - 5 * 3600, 'done_ts': self.ts - 3600, 'done_by': 'user_unpinned',
                      'failures': 0, 'pin_migrated_13b': True}
        self.hop([it], {it['conv_key']: row})
        self.assertEqual(len(self.lines()), 1)


class ConfiguredSystemPatternTests(ManualNewMessageTests):
    """audit-01 R3：conversation.system_patterns 要在对话模块每一处都生效（原来只在推送文案一处）。"""

    def test_configured_pattern_not_pushed_as_manual_new(self):
        self.cfg['conversation']['system_patterns'] = ['虚构系统提示.{0,10}']
        it = self.item(1, text='虚构系统提示：本岗位已更新', age=60)
        self.hop([it], {it['conv_key']: self.manual(it)})
        self.assertEqual(self.lines(), [])

    def test_configured_pattern_not_pushed_as_out_of_view(self):
        self.cfg['conversation']['system_patterns'] = ['虚构系统提示.{0,10}']
        it = self.item(1, text='虚构系统提示：本岗位已更新', age=60)
        c = self.module()
        with patch.object(c.chat_dom, 'reveal_conversation', return_value={'ok': False, 'reason': 'not_found'}), \
             patch.object(c.chat_dom, 'reset_list_scroll'):
            self.run_hop(items=[it], row_snapshots=lambda *a: [])
        self.assertFalse(any('📭' in l for l in self.out))

    def test_send_guard_receives_configured_patterns(self):
        import conv_actions as A
        self.cfg['conversation']['system_patterns'] = ['虚构系统提示.{0,10}']
        js = A.chat_dom.conv_parse.system_message_parser_js(
            A.conv_parse.system_message_texts(self.cfg), A.conv_parse.system_patterns(self.cfg))
        self.assertIn(__import__('json').dumps('虚构系统提示.{0,10}'), js)
        seen = {}
        def fake_click(br, tab, key, text, expected, *, system_texts=None, system_pats=None):
            seen['pats'] = [p.pattern for p in system_pats or []]
            return {'ok': False, 'reason': 'fiction_stop'}
        before = {'conv_key': 'id:fiction', 'messages': [
            {'id': 'a', 'who': 'friend', 'text': '虚构问题'},
            {'id': 'b', 'who': 'unknown', 'text': '虚构系统提示：已更新'}]}
        with patch.object(A.chat_dom, 'active_conversation', return_value=before), \
             patch.object(A.CS, 'type_into_chat', return_value={'ok': True}), \
             patch.object(A.chat_dom, 'atomic_chat_click', side_effect=fake_click), \
             patch.object(A.SS, 'ledger_guard'):
            reply = A.conv_rules.INTERVIEW_REPLY
            cfg = dict(self.cfg, sending={'min_gap_seconds': 0},
                       paths=dict(self.cfg['paths'], state=self.tmp.name + '/state.json'))
            receipt = A.send_text(self.br, 1, cfg, 'id:fiction', {}, reply, reply)
        self.assertNotEqual(receipt, 'send_aborted:owner_unknown')
        self.assertEqual(seen.get('pats'), ['虚构系统提示.{0,10}'])


class ManualNewExclusionTests(ManualNewMessageTests):
    """audit-01 R4（测试缺口）：需人工·新消息的排除规则与 updated_ts 正向用例。"""

    def ledger(self, **row):
        import send_safety as SS
        cfg = dict(self.cfg, paths=dict(self.cfg['paths'], state=self.tmp.name + '/state.json'))
        SS.ledger_append(cfg, dict(dict(event_id='fiction-' + row.get('status', 'sent'), cmd='conv_text', job_id='',
                                        text_sha256='0' * 64, ts='2026-10-29 11:00', ts_epoch=1), **row))

    def test_own_ledger_text_not_pushed(self):
        own = '方便说下这个岗位主要的工作要求和薪资结构吗，谢谢'
        self.ledger(conv_key='id:fiction-other', text=own, status='sent')
        it = self.item(1, text=own[:20], age=60)
        self.hop([it], {it['conv_key']: self.manual(it)})
        self.assertEqual(self.lines(), [])

    def test_pending_conversation_not_pushed(self):
        it = self.item(1, text=self.HR, age=60)
        self.ledger(conv_key=it['conv_key'], text='虚构', status='unknown')
        self.hop([it], {it['conv_key']: self.manual(it)})
        self.assertEqual(self.lines(), [])

    def test_blocked_brand_not_pushed(self):
        self.cfg['filters'] = {'brand_block': ['虚构公司1']}
        it = self.item(1, text=self.HR, age=60)
        self.hop([it], {it['conv_key']: self.manual(it)})
        self.assertEqual(self.lines(), [])

    def test_override_record_newer_than_updated_ts_pushed(self):
        it = self.item(1, text=self.HR, age=60)
        row = self.manual(it)
        row['last_seen'] = {'ts': None, 'text': ''}
        row['updated_ts'] = self.ts - 3600
        self.hop([it], {it['conv_key']: row})
        self.assertEqual(len(self.lines()), 1)


class MinGapDefaultRetryThresholdTests(unittest.TestCase):
    """audit-01 R4（测试缺口）：缺省间隔改 60 后，对话跳遇 skip_min_gap(≤65) 重试一次、睡 N+1 秒；更长的不重试。"""
    setUp = T.ConversationRunTests.setUp
    module = T.ConversationRunTests.module
    item = T.ConversationRunTests.item
    history = T.ConversationRunTests.history
    run_hop = T.ConversationRunTests.run_hop
    saved = T.ConversationRunTests.saved

    def hop_with_receipts(self, receipts):
        from unittest.mock import Mock
        c = self.module()
        self.cfg.pop('sending', None)
        self.cfg['_sleep'] = Mock()
        with patch.object(c.conv_actions, 'send_resume', side_effect=receipts) as send:
            execute = c._execute
            def deliver(*args):
                with patch.object(c.conv_actions, 'send_resume', send):
                    return execute(*args)
            with patch.object(c, '_execute', side_effect=deliver):
                self.run_hop()
        return send

    def test_default_60_retries_once(self):
        send = self.hop_with_receipts(['skip_min_gap(60)', 'sent'])
        self.assertEqual(send.call_count, 2)
        self.cfg['_sleep'].assert_called_once_with(61)

    def test_longer_gap_not_retried(self):
        send = self.hop_with_receipts(['skip_min_gap(70)'])
        self.assertEqual(send.call_count, 1)
        self.cfg['_sleep'].assert_not_called()


class Round4GapTests(ManualNewMessageTests):
    """audit-01 R5（测试缺口，第四轮变异未抓到的）：屏蔽公司推送与简历卡片判断也用配置的系统正则；对话跳流水损坏文案。"""

    def test_blocked_brand_push_hides_configured_system_text(self):
        self.cfg['conversation']['system_patterns'] = ['虚构系统提示.{0,10}']
        self.cfg['filters'] = {'brand_block': ['虚构公司1']}
        it = self.item(1, text='虚构系统提示：本岗位已更新', age=60)
        self.hop([it], {})
        line = next(l for l in self.out if l.startswith('🔒 屏蔽公司'))
        self.assertNotIn('虚构系统提示', line)

    def test_resume_card_resolved_by_configured_system_receipt(self):
        c = self.module()
        cfg = dict(self.cfg, conversation=dict(self.cfg['conversation'], system_patterns=['虚构回执提示.{0,20}'],
                                               resume_sent_pattern='虚构回执提示'))
        history = [{'who': 'friend', 'text': '我想要一份您的附件简历，您是否同意'},
                   {'who': 'unknown', 'text': '虚构回执提示：已处理'}]
        self.assertIsNone(c._resume_card(cfg, history))

    def test_run_corrupt_ledger_text(self):
        c = self.module()
        out = []
        with patch.object(c.SS, 'ledger_guard', side_effect=RuntimeError('skip_ledger_corrupt: 请先 ledger-repair --apply')):
            result = c.run(self.cfg, self.br, out)
        text = '\n'.join(result['notifications'])
        self.assertIn('流水文件损坏', text)
        self.assertNotIn('ledger-repair', text)


if __name__ == '__main__':
    unittest.main()
