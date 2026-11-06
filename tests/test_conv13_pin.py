"""conv-13：需人工的会话自动在 BOSS 置顶。临时目录、虚构数据、sleep 打桩，不连浏览器。"""
import json
import subprocess
import sys
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
import chat_dom
import conv_store
import test_conversation_run as T

NODE_HARNESS = r"""
const {script, rows, mode} = JSON.parse(process.argv[1]);
const calls = [];
const v = {$options:{name:'virtual-list'}, dataSources: rows.map(r => Object.assign({}, r))};
const mkRow = boss => {
  const vm = {$options:{name:'last-msg-content'}, boss,
    setTop(b, t) {
      calls.push(['setTop', b.uniqueId, t]);
      if (mode === 'throw') throw new Error('虚构异常');
      // conv-13b：真机只认 1；确认看 DOM 行的 boss.isTop
      if (t !== 1) return;
      if (mode === 'late') setTimeout(() => { b.isTop = 1; }, 300);
      else if (mode !== 'stuck') b.isTop = 1;
    },
    deleteBoss() { calls.push(['deleteBoss']); },
    setCancelNotInterested() { calls.push(['setCancelNotInterested']); },
    setGroupTop() { calls.push(['setGroupTop']); }};
  const holder = {__vue__: vm, parentElement: null};
  const op = {parentElement: holder};
  return {querySelector(sel) { return sel === '.user-operation' ? op : null; }, parentElement: holder,
          click() { calls.push(['click']); }, dispatchEvent() { calls.push(['dispatchEvent']); }};
};
global.document = {
  querySelector(sel) { return sel === '.user-list-content' ? {__vue__: v} : null; },
  querySelectorAll(sel) { return sel === '.friend-content' ? rows.map(mkRow) : []; }};
(async () => { const r = await eval(script); console.log(JSON.stringify({r, calls})); })();
"""


def boss(n=1, **extra):
    row = {'uniqueId': 'u%d' % n, 'encryptJobId': 'fiction-job%d' % n, 'name': '虚构HR%d' % n,
           'brandName': '虚构公司%d' % n, 'friendId': 100 + n, 'isTop': 0}
    row.update(extra)
    return row


ITEM = {'encryptJobId': 'fiction-job1', 'name': '虚构HR1', 'brandName': '虚构公司1'}


class PinConversationDomTests(unittest.TestCase):
    def run_dom(self, rows, item=ITEM, mode='normal', wait_ms=3000):
        br = Mock()
        br.json.return_value = {'ok': True}
        chat_dom.pin_conversation(br, 10, item, wait_ms=wait_ms)
        script = br.json.call_args.args[1]
        run = subprocess.run(['node', '-e', NODE_HARNESS,
                              json.dumps({'script': script, 'rows': rows, 'mode': mode})],
                             capture_output=True, text=True, check=True, timeout=30)
        data = json.loads(run.stdout)
        return data['r'], data['calls']

    def test_unique_row_calls_only_set_top_once(self):
        result, calls = self.run_dom([boss(1), boss(2)])
        self.assertEqual(result, {'ok': True})
        self.assertEqual(calls, [['setTop', 'u1', 1]])

    def test_late_confirmation_within_wait_is_ok(self):
        result, calls = self.run_dom([boss(1)], mode='late')
        self.assertEqual(result, {'ok': True})
        self.assertEqual(calls, [['setTop', 'u1', 1]])

    def test_already_top_does_not_call(self):
        result, calls = self.run_dom([boss(1, isTop=1)])
        self.assertEqual(result, {'ok': True, 'already': True})
        self.assertEqual(calls, [])

    def test_group_row_refused(self):
        result, calls = self.run_dom([boss(1, groupId=9)])
        self.assertEqual(result, {'ok': False, 'reason': 'group'})
        self.assertEqual(calls, [])

    def test_duplicate_rows_ambiguous_and_missing_not_in_view(self):
        twin = boss(1, uniqueId='u1b')
        result, calls = self.run_dom([boss(1), twin])
        self.assertEqual(result, {'ok': False, 'reason': 'ambiguous'})
        self.assertEqual(calls, [])
        result, calls = self.run_dom([boss(2), boss(3)])
        self.assertEqual(result, {'ok': False, 'reason': 'not_in_view'})
        self.assertEqual(calls, [])

    def test_each_identity_field_must_match(self):
        for field, value in (('encryptJobId', 'other-job'), ('name', '别的HR'), ('brandName', '别的公司')):
            with self.subTest(field=field):
                result, calls = self.run_dom([boss(1, **{field: value})])
                self.assertEqual(result, {'ok': False, 'reason': 'not_in_view'})
                self.assertEqual(calls, [])

    def test_friend_id_conflict_not_matched(self):
        result, calls = self.run_dom([boss(1)], item=dict(ITEM, friendId=999))
        self.assertEqual(result, {'ok': False, 'reason': 'not_in_view'})
        self.assertEqual(calls, [])

    def test_missing_item_field_refused(self):
        result, calls = self.run_dom([boss(1, brandName='')], item=dict(ITEM, brandName=''))
        self.assertEqual(result, {'ok': False, 'reason': 'bad_item'})
        self.assertEqual(calls, [])

    def test_not_confirmed_when_is_top_stays_false(self):
        result, calls = self.run_dom([boss(1)], mode='stuck', wait_ms=400)
        self.assertEqual(result, {'ok': False, 'reason': 'not_confirmed'})
        self.assertEqual(calls, [['setTop', 'u1', 1]])

    def test_exception_becomes_pin_error(self):
        result, _ = self.run_dom([boss(1)], mode='throw')
        self.assertEqual(result, {'ok': False, 'reason': 'pin_error'})

    def test_script_has_no_menu_or_destructive_calls_and_waits_three_seconds(self):
        br = Mock()
        br.json.return_value = {}
        chat_dom.pin_conversation(br, 10, ITEM)
        script = br.json.call_args.args[1]
        self.assertIn('waitMs = 3000', script)
        self.assertIn('setTop(boss, 1)', script)
        for token in ('deleteBoss', 'setCancelNotInterested', 'click', 'mouse', 'hover', 'dispatchEvent'):
            self.assertNotIn(token, script)

    def test_bridge_failures(self):
        br = Mock()
        br.json.side_effect = RuntimeError('虚构桥接失败')
        self.assertEqual(chat_dom.pin_conversation(br, 10, ITEM), {'ok': False, 'reason': 'pin_error'})
        br.json.side_effect = None
        br.json.return_value = 'garbage'
        self.assertEqual(chat_dom.pin_conversation(br, 10, ITEM), {'ok': False, 'reason': 'invalid_response'})


class PinBase(unittest.TestCase):
    setUp_base = T.ConversationRunTests.setUp
    module = T.ConversationRunTests.module
    item = T.ConversationRunTests.item
    record = T.ConversationRunTests.record
    history = T.ConversationRunTests.history
    run_hop = T.ConversationRunTests.run_hop
    saved = T.ConversationRunTests.saved

    def setUp(self):
        self.setUp_base()
        self.cfg['conversation']['pin_manual'] = True

    def pin_hop(self, pin=None, reveal=None, **kw):
        c = self.module()
        pin = pin or Mock(return_value={'ok': True})
        reveal = reveal or Mock(return_value={'ok': False, 'reason': 'not_found'})
        with patch.object(c.chat_dom, 'pin_conversation', pin), \
             patch.object(c.chat_dom, 'reveal_conversation', reveal), \
             patch.object(c.time, 'sleep') as sleep:
            result = self.run_hop(**kw)[0]
        self.sleep = sleep
        return result, pin

    def pinrec(self, n, want_ts, status='manual', **pin):
        it = self.item(n)
        row = self.record(it, status=status)
        row['pin'] = dict({'reason': 'manual', 'text': '虚构原话%d' % n, 'msg_ts': want_ts,
                           'want_ts': want_ts, 'pinned_ts': None, 'done_ts': None,
                           'done_by': None, 'failures': 0, 'pin_migrated_13b': True}, **pin)
        return row

    def store(self, *rows):
        conv_store.save(self.path, {r['conv_key']: r for r in rows})

    def load(self):
        return conv_store.load(self.path)

    def pin_block(self, result):
        return [n for n in result['notifications'] if n.startswith('📌')]


class ImmediatePinTests(PinBase):
    def test_manual_pinned_on_the_spot_block_replaces_line(self):
        result, pin = self.pin_hop(text_result='manual_required')
        pin.assert_called_once()
        self.assertEqual(pin.call_args.args[2], ITEM)
        self.assertFalse(any(n.startswith('🙋') for n in result['notifications']))
        self.assertTrue(any(line.startswith('🙋 需人工') for line in self.out))
        block = self.pin_block(result)
        self.assertEqual(len(block), 1)
        self.assertTrue(block[0].startswith('📌 新置顶 1 个（置顶待你处理共 1）\n  · 虚构公司1｜'))
        self.assertIn('：您好，方便聊聊吗（刚刚）', block[0])
        self.assertEqual(self.saved()['pin']['pinned_ts'], self.ts)
        self.assertEqual(self.saved()['pin']['reason'], 'manual')
        self.assertEqual((result['pinned'], result['pin_backlog'], result['pin_failed']), (1, 0, 0))
        self.assertIn('｜置顶 1', result['overview'])

    def test_paperclip_pinned_and_line_replaced(self):
        text = '发份简历看看'
        result, pin = self.pin_hop(items=[self.item(text=text)],
                                   history=self.history(inbound=True, text=text), job_ok=False)
        pin.assert_called_once()
        self.assertFalse(any(n.startswith('📎') for n in result['notifications']))
        self.assertTrue(any(line.startswith('📎 不过筛但要简历') for line in self.out))
        self.assertEqual(len(self.pin_block(result)), 1)
        self.assertEqual(self.saved()['pin']['reason'], 'resume_asked')
        self.assertEqual(self.saved()['pin']['text'], text)

    def test_resume_card_manual_pinned(self):
        result, pin = self.pin_hop(resume_result='send_aborted:resume_requested')
        pin.assert_called_once()
        self.assertEqual(self.saved()['pin']['reason'], 'resume_card')
        self.assertIn('请手动点「同意」', self.pin_block(result)[0])
        self.assertFalse(any(n.startswith('🙋') for n in result['notifications']))

    def test_failure_keeps_original_line_and_counts(self):
        pin = Mock(return_value={'ok': False, 'reason': 'not_in_view'})
        result, _ = self.pin_hop(pin=pin, text_result='manual_required')
        self.assertTrue(any(n.startswith('🙋 需人工') for n in result['notifications']))
        self.assertEqual(self.pin_block(result), [])
        self.assertEqual(self.saved()['pin']['failures'], 1)
        self.assertIsNone(self.saved()['pin']['pinned_ts'])
        self.assertEqual((result['pinned'], result['pin_failed']), (0, 1))
        self.assertNotIn('置顶', result['overview'])

    def test_phone_card_text_normalized(self):
        text = '对方想与您互换电话\n拒绝\n同意'
        result, _ = self.pin_hop(items=[self.item(text=text)], history=self.history(text=text),
                                 text_result='manual_required')
        self.assertEqual(self.saved()['pin']['text'], 'HR 请求交换电话')
        self.assertIn('：HR 请求交换电话（', self.pin_block(result)[0])


class ThreeFailuresTests(PinBase):
    def test_third_failure_warns_once_then_stops(self):
        self.store(self.pinrec(1, self.ts - 7200, failures=2))
        pin = Mock(return_value={'ok': False, 'reason': 'not_confirmed'})
        result, _ = self.pin_hop(pin=pin)
        pin.assert_called_once()
        warn = [n for n in result['notifications'] if n.startswith('⚠️ 置顶失败')]
        self.assertEqual(len(warn), 1)
        self.assertIn('需你手动看', warn[0])
        self.assertEqual(self.saved()['pin']['failures'], 3)
        self.br.new_tab.side_effect = [20]
        again, pin2 = self.pin_hop(pin=Mock(return_value={'ok': True}))
        pin2.assert_not_called()
        self.assertFalse(any('置顶失败' in n for n in again['notifications']))


class BacklogTests(PinBase):
    def test_oldest_first_reveal_failure_free_quota_and_sleep(self):
        self.cfg['conversation']['pin_max_per_hop'] = 2
        rows = [self.pinrec(n, self.ts - 3600 * (10 - n)) for n in (1, 2, 3, 4)]
        self.store(*rows)
        items = [self.item(n) for n in (1, 2, 3, 4)]
        visible = [{'conv_key': 'id:fiction2', 'receipt': False}, {'conv_key': 'id:fiction4', 'receipt': False},
                   {'conv_key': 'id:fiction3', 'receipt': False}]
        reveal = Mock(return_value={'ok': False, 'reason': 'not_found'})
        result, pin = self.pin_hop(items=items, reveal=reveal, row_snapshots=lambda *a: list(visible))
        # 1 最旧但不在视窗、揭示失败不占名额；2、3 置顶后满 2 个名额，4 不动
        reveal.assert_called_once()
        self.assertEqual([c.args[2]['name'] for c in pin.call_args_list], ['虚构HR2', '虚构HR3'])
        self.assertEqual(self.sleep.call_args_list, [unittest.mock.call(2)])
        saved = self.load()
        self.assertEqual(saved['id:fiction1']['pin']['failures'], 1)
        self.assertIsNone(saved['id:fiction4']['pin']['pinned_ts'])
        self.assertEqual((result['pinned'], result['pin_backlog'], result['pin_failed']), (2, 2, 1))
        block = self.pin_block(result)[0]
        self.assertTrue(block.startswith('📌 新置顶 2 个（置顶待你处理共 2）'))
        self.assertIn('虚构原话2（等了 8h）', block)

    def test_reveal_attempts_capped_at_twice_quota(self):
        self.cfg['conversation']['pin_max_per_hop'] = 1
        self.store(*[self.pinrec(n, self.ts - 100 * n) for n in (1, 2, 3, 4)])
        reveal = Mock(return_value={'ok': False, 'reason': 'not_found'})
        result, pin = self.pin_hop(items=[self.item(9)], reveal=reveal,
                                   row_snapshots=lambda *a: [{'conv_key': 'id:fiction9', 'receipt': False}])
        self.assertEqual(reveal.call_count, 2)
        self.assertEqual(result['pin_failed'], 2)

    def test_revealed_row_is_pinned(self):
        self.store(self.pinrec(1, self.ts - 100))
        visible = [{'conv_key': 'id:other', 'receipt': False}]
        def reveal(br, tab, item):
            visible[:] = [{'conv_key': 'id:fiction1', 'receipt': False}]
            return {'ok': True, 'index': 50}
        result, pin = self.pin_hop(reveal=Mock(side_effect=reveal), row_snapshots=lambda *a: list(visible))
        pin.assert_called_once()
        self.assertEqual(result['pin_backlog'], 1)
        self.assertEqual(self.saved()['pin']['pinned_ts'], self.ts)

    def test_backlog_item_falls_back_to_record_identity(self):
        self.store(self.pinrec(1, self.ts - 100))
        result, pin = self.pin_hop(items=[self.item(2)],
                                   row_snapshots=lambda *a: [{'conv_key': 'id:fiction1', 'receipt': False},
                                                             {'conv_key': 'id:fiction2', 'receipt': False}])
        self.assertIn(unittest.mock.call(self.br, 10, ITEM), pin.call_args_list)

    def test_quota_shared_with_immediate_pin(self):
        self.cfg['conversation']['pin_max_per_hop'] = 1
        self.store(self.pinrec(2, self.ts - 100))
        result, pin = self.pin_hop(items=[self.item(1), self.item(2)], text_result='manual_required')
        self.assertEqual(pin.call_count, 1)
        self.assertEqual(pin.call_args.args[2]['name'], '虚构HR1')


class MigrationTests(PinBase):
    def legacy(self):
        manual = self.record(self.item(1), status='manual')
        manual['updated_ts'] = self.ts - 3600
        manual['last_seen'] = {'ts': self.ts - 3700, 'text': '虚构旧原话'}
        old = self.record(self.item(2), status='manual')
        old['updated_ts'] = self.ts - 80 * 3600
        clip = self.record(self.item(3), status='engaged')
        clip.update(job_ok=False, resume_asked=True, updated_ts=self.ts - 7200)
        self.store(manual, old, clip)

    def test_migration_once_no_push_and_off_switch_does_not_pin(self):
        self.cfg['conversation']['pin_manual'] = False
        self.legacy()
        result, pin = self.pin_hop(items=[self.item(9)])
        pin.assert_not_called()
        saved = self.load()
        self.assertEqual(saved['id:fiction1']['pin']['reason'], 'manual')
        self.assertEqual(saved['id:fiction1']['pin']['want_ts'], self.ts - 3600)
        self.assertEqual(saved['id:fiction1']['pin']['text'], '虚构旧原话')
        self.assertNotIn('pin', saved['id:fiction2'])
        self.assertEqual(saved['id:fiction3']['pin']['reason'], 'resume_asked')
        self.assertFalse(any('置顶' in n for n in result['notifications']))
        # 只做一次：删掉一条已补登的，下跳不会重新补登
        del saved['id:fiction3']['pin']
        conv_store.save(self.path, saved)
        self.br.new_tab.side_effect = [20]
        self.pin_hop(items=[self.item(9)])
        self.assertNotIn('pin', self.load()['id:fiction3'])

    def test_migrated_backlog_pinned_when_switch_on(self):
        self.legacy()
        result, pin = self.pin_hop(items=[self.item(1), self.item(3)])
        self.assertEqual([c.args[2]['name'] for c in pin.call_args_list], ['虚构HR3', '虚构HR1'])
        self.assertEqual(result['pin_backlog'], 2)


class UnpinTests(PinBase):
    def test_user_unpinned_is_done_and_never_repinned(self):
        self.store(self.pinrec(1, self.ts - 100, pinned_ts=self.ts - 50))
        item = dict(self.item(1), isTop=0)
        result, pin = self.pin_hop(items=[item])
        pin.assert_not_called()
        pin_row = self.saved()['pin']
        self.assertEqual((pin_row['done_ts'], pin_row['done_by']), (self.ts, 'user_unpinned'))
        self.assertEqual(result['pin_done'], 1)

    def test_missing_is_top_keeps_open(self):
        self.store(self.pinrec(1, self.ts - 100, pinned_ts=self.ts - 50))
        result, pin = self.pin_hop(items=[self.item(1)])
        self.assertIsNone(self.saved()['pin']['done_ts'])
        self.assertEqual(result['pin_done'], 0)
        pin.assert_not_called()

    def test_terminal_status_done_without_unpin_call(self):
        self.store(self.pinrec(1, self.ts - 100, status='closed', pinned_ts=self.ts - 50),
                   self.pinrec(2, self.ts - 100, status='complete'))
        result, pin = self.pin_hop(items=[dict(self.item(1), isTop=1), self.item(2)])
        saved = self.load()
        self.assertEqual(saved['id:fiction1']['pin']['done_by'], 'status')
        self.assertEqual(saved['id:fiction2']['pin']['done_by'], 'status')
        pin.assert_not_called()

    def test_reregister_after_done(self):
        c = self.module()
        row = self.pinrec(1, 10, pinned_ts=20, done_ts=30, done_by='user_unpinned', failures=2)
        c._want_pin(row, 'manual', '虚构新原话', 40, 50)
        self.assertEqual(row['pin'], {'reason': 'manual', 'text': '虚构新原话', 'msg_ts': 40, 'want_ts': 50,
                                      'pinned_ts': None, 'done_ts': None, 'done_by': None, 'failures': 0,
                                      'pin_migrated_13b': True})
        open_row = self.pinrec(2, 10, failures=1)
        c._want_pin(open_row, 'resume_asked', '虚构更新', 41, 60)
        self.assertEqual((open_row['pin']['want_ts'], open_row['pin']['failures'], open_row['pin']['text']),
                         (10, 1, '虚构更新'))


class SwitchAndDryTests(PinBase):
    def test_switch_off_keeps_line_and_never_pins(self):
        self.cfg['conversation']['pin_manual'] = False
        self.store(self.pinrec(2, self.ts - 100))
        result, pin = self.pin_hop(items=[self.item(1), self.item(2)], text_result='manual_required')
        pin.assert_not_called()
        self.assertTrue(any(n.startswith('🙋 需人工') for n in result['notifications']))
        self.assertEqual(self.pin_block(result), [])

    def test_default_is_off(self):
        del self.cfg['conversation']['pin_manual']
        result, pin = self.pin_hop(text_result='manual_required')
        pin.assert_not_called()

    def test_dry_lists_keys_without_calling_or_writing(self):
        self.store(self.pinrec(2, self.ts - 100))
        before = self.path.read_text()
        text = '发份简历看看'
        result, pin = self.pin_hop(items=[self.item(text=text), self.item(2)],
                                   history=self.history(inbound=True, text=text), job_ok=False, dry=True)
        pin.assert_not_called()
        self.assertEqual(self.path.read_text(), before)
        self.assertEqual(result['pin_plan'], ['id:fiction1', 'id:fiction2'])


class SummaryAndWaitTests(PinBase):
    def test_last_hop_summary_counts_open_pins(self):
        self.now = self.now.replace(hour=21)
        self.ts = self.now.timestamp()
        self.store(self.pinrec(2, self.ts - 100, pinned_ts=self.ts - 50),
                   self.pinrec(3, self.ts - 100, pinned_ts=self.ts - 50, done_ts=self.ts - 10))
        result, _ = self.pin_hop(items=[self.item(1), self.item(2)])
        self.assertEqual(result['summary']['pinned_open'], 1)
        self.assertIn('｜📌 置顶待你处理 1', result['summary_line'])

    def test_waited_text(self):
        c = self.module()
        self.assertEqual(c._pin_waited({'msg_ts': 1000, 'want_ts': 5}, 1000 + 3599), '刚刚')
        self.assertEqual(c._pin_waited({'msg_ts': 1000}, 1000 + 3 * 3600 + 5), '等了 3h')
        self.assertEqual(c._pin_waited({'msg_ts': None, 'want_ts': 1000}, 1000 + 7200), '等了 2h')

    def test_unknown_title_in_block(self):
        row = self.pinrec(1, self.ts - 100)
        row['title'] = ''
        self.store(row)
        result, _ = self.pin_hop(items=[self.item(1)])
        self.assertIn('虚构公司1｜岗位未知｜虚构HR1', self.pin_block(result)[0])


if __name__ == '__main__':
    unittest.main()
