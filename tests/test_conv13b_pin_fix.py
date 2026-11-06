"""conv-13b：真机首跳全失败的复现。setTop 要传 1；置顶后行离开 dataSources、
变成 DOM 第 0 行（class friend-top，boss.isTop===1）；补登限 lookback；13b 一次性迁移。虚构数据。"""
import json
import subprocess
import sys
import unittest
from pathlib import Path
from unittest.mock import Mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
import chat_dom
import conv_store
import test_conv13_pin as P

# 按 11-06 真机行为做的假页面：setTop(boss, true) → 接口 code 19，什么都不变；
# setTop(boss, 1) → 该行从 dataSources 消失，DOM 里移到第 0 行、class 加 friend-top、boss.isTop=1。
REAL_HARNESS = r"""
const {script, rows, mode} = JSON.parse(process.argv[1]);
const calls = [];
const v = {$options:{name:'virtual-list'}, dataSources: rows.filter(r => !r.isTop).map(r => Object.assign({}, r))};
const els = [];
const mkRow = boss => {
  const el = {className: boss.isTop ? 'friend-content friend-top' : 'friend-content'};
  const vm = {$options:{name:'last-msg-content'}, boss,
    setTop(b, t) {
      calls.push(['setTop', b.uniqueId, t]);
      if (mode === 'throw') throw new Error('虚构异常');
      if (t !== 1 || mode === 'reject') return;           // 真机：true → 参数值错误
      const apply = () => {
        if (mode !== 'class_only') b.isTop = 1;
        el.className = 'friend-content friend-top';
        v.dataSources = v.dataSources.filter(x => x.uniqueId !== b.uniqueId);
        els.splice(els.indexOf(el), 1); els.unshift(el);
        if (mode === 'dup') els.push(mkRow(Object.assign({}, b, {uniqueId: b.uniqueId + '-copy', isTop: 0})));
      };
      if (mode === 'late') setTimeout(apply, 300); else apply();
    },
    deleteBoss() { calls.push(['deleteBoss']); },
    setCancelNotInterested() { calls.push(['setCancelNotInterested']); }};
  const holder = {__vue__: vm, parentElement: null};
  const op = {parentElement: holder};
  el.querySelector = sel => sel === '.user-operation' ? op : null;
  el.parentElement = holder;
  return el;
};
rows.forEach(r => els.push(mkRow(r)));
global.document = {
  querySelector(sel) { return sel === '.user-list-content' ? {__vue__: v} : null; },
  querySelectorAll(sel) { return sel === '.friend-content' ? els.slice() : []; }};
(async () => { const r = await eval(script); console.log(JSON.stringify({r, calls, ds: v.dataSources.length})); })();
"""


class RealPageTests(unittest.TestCase):
    def run_dom(self, rows, mode='normal', wait_ms=3000, item=P.ITEM):
        br = Mock()
        br.json.return_value = {'ok': True}
        chat_dom.pin_conversation(br, 10, item, wait_ms=wait_ms)
        run = subprocess.run(['node', '-e', REAL_HARNESS, json.dumps(
            {'script': br.json.call_args.args[1], 'rows': rows, 'mode': mode})],
            capture_output=True, text=True, check=True, timeout=30)
        return json.loads(run.stdout)

    def test_set_top_passes_one_and_confirms_from_dom(self):
        data = self.run_dom([P.boss(2), P.boss(1)])
        self.assertEqual(data['calls'], [['setTop', 'u1', 1]])
        self.assertEqual(data['r'], {'ok': True})
        self.assertEqual(data['ds'], 1)   # 置顶行已离开 dataSources

    def test_late_move_confirmed(self):
        data = self.run_dom([P.boss(1)], mode='late')
        self.assertEqual(data['r'], {'ok': True})

    def test_friend_top_class_alone_confirms(self):
        data = self.run_dom([P.boss(1)], mode='class_only')
        self.assertEqual(data['r'], {'ok': True})

    def test_rejected_request_not_confirmed(self):
        data = self.run_dom([P.boss(1)], mode='reject', wait_ms=400)
        self.assertEqual(data['r'], {'ok': False, 'reason': 'not_confirmed'})
        self.assertEqual(data['calls'], [['setTop', 'u1', 1]])

    def test_already_top_as_one_or_class(self):
        data = self.run_dom([P.boss(1, isTop=1)])
        self.assertEqual((data['r'], data['calls']), ({'ok': True, 'already': True}, []))

    def test_other_row_top_does_not_confirm(self):
        # 别人的行是 friend-top，不能当成本行置顶成功
        data = self.run_dom([P.boss(2, isTop=1), P.boss(1)], mode='reject', wait_ms=400)
        self.assertEqual(data['r'], {'ok': False, 'reason': 'not_confirmed'})

    def test_script_never_passes_true(self):
        br = Mock()
        br.json.return_value = {}
        chat_dom.pin_conversation(br, 10, P.ITEM)
        script = br.json.call_args.args[1]
        self.assertIn('setTop(boss, 1)', script)
        self.assertNotIn('setTop(boss, true)', script)
        self.assertIn('friend-top', script)


class MigrationLookbackTests(P.PinBase):
    def test_first_migration_skips_out_of_lookback(self):
        self.cfg['conversation']['pin_manual'] = False
        old_clip = self.record(self.item(1), status='engaged')
        old_clip.update(job_ok=False, resume_asked=True, updated_ts=self.ts - 7 * 86400,
                        last_seen={'ts': self.ts - 7 * 86400, 'text': '您的附件简历已发送给Boss'})
        new_clip = self.record(self.item(2), status='engaged')
        new_clip.update(job_ok=False, resume_asked=True, updated_ts=self.ts - 3600,
                        last_seen={'ts': self.ts - 3600, 'text': '虚构要简历'})
        stale_seen = self.record(self.item(3), status='manual')
        stale_seen.update(updated_ts=self.ts - 3600, last_seen={'ts': self.ts - 5 * 86400, 'text': '虚构旧话'})
        self.store(old_clip, new_clip, stale_seen)
        self.pin_hop(items=[self.item(9)])
        saved = self.load()
        self.assertFalse(isinstance(saved['id:fiction1'].get('pin'), dict))
        self.assertEqual(saved['id:fiction2']['pin']['reason'], 'resume_asked')
        self.assertFalse(isinstance(saved['id:fiction3'].get('pin'), dict))


class Migration13bTests(P.PinBase):
    def legacy13(self, n, want_ts, seen_ts, **pin):
        row = self.pinrec(n, want_ts, **pin)
        row['last_seen'] = {'ts': seen_ts, 'text': '虚构'}
        row['updated_ts'] = seen_ts
        for key in ('pin_migrated_13b',):
            row['pin'].pop(key, None)
        return row

    def test_resets_failures_drops_out_of_window_once(self):
        self.cfg['conversation']['pin_manual'] = False
        recent = self.legacy13(1, self.ts - 3600, self.ts - 3600, failures=3)
        stale = self.legacy13(2, self.ts - 7 * 86400, self.ts - 7 * 86400, failures=1)
        pinned_old = self.legacy13(3, self.ts - 7 * 86400, self.ts - 7 * 86400, pinned_ts=self.ts - 60)
        done = self.legacy13(4, self.ts - 3600, self.ts - 3600, failures=2,
                             done_ts=self.ts - 10, done_by='user_unpinned')
        self.store(recent, stale, pinned_old, done)
        result, pin = self.pin_hop(items=[self.item(9)])
        pin.assert_not_called()
        saved = self.load()
        self.assertEqual(saved['id:fiction1']['pin']['failures'], 0)
        self.assertTrue(saved['id:fiction1']['pin']['pin_migrated_13b'])
        self.assertIsNone(saved['id:fiction2']['pin'])
        self.assertEqual(saved['id:fiction3']['pin']['pinned_ts'], self.ts - 60)   # 已置顶的不撤
        self.assertEqual(saved['id:fiction4']['pin']['done_by'], 'user_unpinned')  # 已完成的不动
        self.assertFalse(any('置顶' in n for n in result['notifications']))
        # 只做一次：之后的失败计数不再被清零
        saved['id:fiction1']['pin']['failures'] = 2
        conv_store.save(self.path, saved)
        self.br.new_tab.side_effect = [20]
        self.pin_hop(items=[self.item(9)])
        self.assertEqual(self.load()['id:fiction1']['pin']['failures'], 2)

    def test_new_registration_carries_marker(self):
        self.cfg['conversation']['pin_manual'] = False
        self.pin_hop(text_result='manual_required')
        self.assertTrue(self.saved()['pin']['pin_migrated_13b'])

    def test_reset_failures_retried_when_on(self):
        self.store(self.legacy13(1, self.ts - 3600, self.ts - 3600, failures=3))
        result, pin = self.pin_hop(items=[self.item(1)])
        pin.assert_called_once()
        self.assertEqual(result['pin_backlog'], 1)

    def test_dry_does_not_write(self):
        self.store(self.legacy13(1, self.ts - 3600, self.ts - 3600, failures=3))
        before = self.path.read_text()
        self.pin_hop(items=[self.item(1)], dry=True)
        self.assertEqual(self.path.read_text(), before)


if __name__ == '__main__':
    unittest.main()
