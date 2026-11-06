"""Offline conv-03 acceptance: all identities and messages are fictional."""
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from contextlib import ExitStack
from datetime import datetime
from unittest.mock import Mock, patch
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
import conv_store
import conv_rules
import hunt_schedule


class ConversationRunTests(unittest.TestCase):
    def test_split_first_round_batch_persists_round2_without_complete_notice(self):
        row = self.record(status='engaged')
        row['resume_sent_ts'] = row['asked_round1_ts'] = self.ts - 100
        conv_store.save(self.path, {row['conv_key']: row})
        texts = ['主要工作是测试虚构产品', '底薪六到十六', '职级底薪，加奖金']
        history = [{'who': 'friend', 'id': 'fiction-answer%d' % i,
                    'text': text, 'ts': self.ts - 15 + i} for i, text in enumerate(texts)]
        result, _, calls, _, _ = self.run_hop(
            items=[self.item(text=texts[-1], age=13)], history=history,
            extracted={'requirements': '测试虚构产品', 'salary_structure': '底薪六到十六，加奖金'})
        self.assertEqual(self.saved()['status'], 'round2')
        self.assertEqual(result['round2'], 1)
        self.assertEqual(result['complete'], 0)
        self.assertEqual([call[0] for call in calls], ['text'])
        self.assertFalse(self.saved()['notified']['complete'])
        self.assertNotIn('📇 问齐', '\n'.join(self.out))

        self.now = self.now.replace(minute=1)
        self.ts = self.now.timestamp()
        answer = '两轮，可以线上'
        history.append({'who': 'friend', 'id': 'fiction-next-hop', 'text': answer, 'ts': self.ts-10})
        result, _, calls, _, _ = self.run_hop(
            items=[self.item(text=answer)], history=history,
            extracted={'interview_rounds': '两轮', 'online_interview': '可以线上'})
        self.assertEqual(self.saved()['status'], 'complete')
        self.assertEqual(result['complete'], 1)
        self.assertFalse(calls)
        self.assertIn('两轮｜可以线上', '\n'.join(self.out))

    def test_long_resume_request_sends_resume_and_first_question(self):
        text = '可以投递的～辛苦发一份最新版本的附件简历吗'
        result, _, calls, _, _ = self.run_hop(
            items=[self.item(text=text)], history=self.history(text=text))
        self.assertEqual([call[0] for call in calls], ['text', 'resume'])
        self.assertEqual(result['round1'], 1)
        self.assertEqual(result['resume_sent'], 1)
        self.assertEqual(self.saved()['status'], 'engaged')

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = Path(self.tmp.name) / 'conversations.json'
        self.cfg = {'conversation': {'enabled': True}, 'schedule': {'timezone': 'Asia/Shanghai'},
                    'paths': {'workdir': self.tmp.name, 'raw_dir': self.tmp.name}}
        self.now = datetime(2026, 10, 29, 12, tzinfo=ZoneInfo('Asia/Shanghai'))
        self.ts = self.now.timestamp()
        self.out = []
        self.br = Mock()
        self.br.nav.return_value = {}
        self.br.new_tab.side_effect = [10, 11, 12, 13]
        self.br.network.return_value = {'text': json.dumps({'code': 0, 'zpData': {'result': []}})}

    def module(self):
        self.assertTrue((ROOT / 'scripts/conversation.py').exists(), 'conversation orchestrator is missing')
        import conversation
        return conversation

    def item(self, n=1, text='您好，方便聊聊吗', age=10, mine=None):
        item = {'conv_key': 'id:fiction%d' % n, 'encryptJobId': 'fiction-job%d' % n,
                'brandName': '虚构公司%d' % n, 'name': '虚构HR%d' % n, 'jobName': '虚构经理',
                'lastMessageInfo': {'showText': text, 'msgTime': (self.ts-age)*1000}}
        if mine is not None:
            item['lastMessageInfo']['fromSelf'] = mine
        return item

    def record(self, item=None, status='new'):
        it = item or self.item()
        r = conv_store.new_record(it['conv_key'], 'reply', it['encryptJobId'], it['brandName'],
                                  it['name'], it['jobName'], self.ts)
        r['status'] = status
        return r

    def history(self, inbound=False, text='您好，方便聊聊吗'):
        return ([] if inbound else [{'who': 'mine', 'id': 'm0', 'text': '虚构招呼', 'ts': self.ts-20}]) + [
            {'who': 'friend', 'id': 'm1', 'text': text, 'ts': self.ts-10}]

    def run_hop(self, items=None, history=None, text_result='sent', resume_result='sent',
                jd='虚构JD', job_ok=True, opener='', extracted=None, dry=False, receipts=None, row_snapshots=None, network=None):
        c = self.module()
        items = items if items is not None else [self.item()]
        self.br.network.side_effect = network or (lambda tab, url, index, **kw: {'text': json.dumps(
            {'code': 0, 'zpData': {'result': items if index == 0 else []}})})
        calls = []
        with ExitStack() as stack:
            def p(obj, name, **kw):
                return stack.enter_context(patch.object(obj, name, **kw))
            opened = p(c.chat_dom, 'open_by_index', return_value=('ok', '', history if history is not None else self.history()))
            p(c.chat_dom, 'conversation_rows', side_effect=row_snapshots, return_value=[{
                'conv_key': it['conv_key'], 'receipt': (receipts or {}).get(it['conv_key'], False)
            } for it in items])
            p(c.jd_page, 'read_jd', return_value=jd)
            self.cleanup = p(c.CM, 'cleanup_browser', return_value=2)
            p(c.hunt_filter, 'load_jobs', return_value=[])
            p(c.hunt_filter, 'judge', return_value=('pass' if job_ok else 'reject', '虚构原因'))
            p(c.hunt_filter, 'grade_with_jd', return_value={'grade': 'B'})
            p(c.conv_llm, 'opener', return_value=opener, side_effect=opener if callable(opener) else None)
            p(c.conv_llm, 'extract', return_value=extracted or {})
            p(c.conv_llm, 'lead', return_value='')
            p(c.conv_llm, 'answer', return_value='')
            text_send = p(c.conv_actions, 'send_text', side_effect=lambda *a, **kw: calls.append(('text', a, kw)) or text_result)
            def send_resume(*a, **kw):
                cfg = a[2] if len(a) > 2 else kw['cfg']
                if cfg.get('conversation', {}).get('dry') is True:
                    return 'dry_ok'
                calls.append(('resume', a, kw))
                return resume_result
            resume_send = p(c.conv_actions, 'send_resume', side_effect=send_resume)
            result = c.run(self.cfg, self.br, self.out, now=self.now, dry=dry)
        return result, opened, calls, text_send, resume_send

    def saved(self):
        return conv_store.load(self.path)['id:fiction1']

    def test_min_gap_retries_same_action_once_without_repeating_text(self):
        c = self.module()
        self.cfg['sending'] = {'min_gap_seconds': 60}
        self.cfg['_sleep'] = Mock()
        with patch.object(c.conv_actions, 'send_resume', side_effect=['skip_min_gap(60)', 'sent']) as send:
            # run_hop supplies other offline boundaries, including its own sender;
            # wrap the real executor to install this receipt sequence at execution.
            execute = c._execute
            def deliver(*args):
                with patch.object(c.conv_actions, 'send_resume', send):
                    return execute(*args)
            with patch.object(c, '_execute', side_effect=deliver):
                result, _, calls, _, _ = self.run_hop()
        self.assertEqual(send.call_count, 2)
        self.assertEqual(send.call_args_list[0], send.call_args_list[1])
        self.cfg['_sleep'].assert_called_once_with(61)
        self.assertEqual([a[0] for a in calls], ['text'])
        self.assertEqual(result['resume_sent'], 1)
        self.assertEqual(self.saved()['status'], 'engaged')

    def test_min_gap_retry_failure_and_abnormal_receipts_keep_failure_state(self):
        c = self.module()
        self.cfg['sending'] = {'min_gap_seconds': 60}
        for receipts, attempts, waits in ((['skip_min_gap(65)', 'skip_min_gap(1)'], 2, [66]),
                                         (['skip_min_gap(66)'], 1, []),
                                         (['skip_min_gap(-1)'], 1, []),
                                         (['skip_min_gap(1)junk'], 1, [])):
            with self.subTest(receipts=receipts):
                conv_store.save(self.path, {})
                self.br.new_tab.side_effect = [10]
                self.cfg['_sleep'] = Mock()
                execute = c._execute
                send = Mock(side_effect=receipts)
                def deliver(*args):
                    with patch.object(c.conv_actions, 'send_resume', send):
                        return execute(*args)
                with patch.object(c, '_execute', side_effect=deliver):
                    result, _, _, _, _ = self.run_hop()
                self.assertEqual(send.call_count, attempts)
                self.assertEqual([call.args[0] for call in self.cfg['_sleep'].call_args_list], waits)
                self.assertEqual(result['resume_sent'], 0)
                self.assertIsNone(self.saved()['resume_sent_ts'])
                self.assertEqual(self.saved()['status'], 'new')

    def test_duplicate_resume_bubbles_warn_but_commit_success(self):
        c = self.module()
        with patch.object(c.conv_actions, 'LAST_RESUME_BUBBLES', 2, create=True):
            result, _, _, _, _ = self.run_hop()
        self.assertEqual(result.get('resume_duplicates'), 1)
        line = '⚠️ 简历重复 虚构公司1｜虚构经理｜虚构HR1：出现 2 条已发送'
        self.assertIn(line, result['notifications'])
        self.assertIn(line, self.out)
        self.assertEqual(self.saved()['status'], 'engaged')
        self.assertEqual(result['resume_sent'], 1)

    def test_old_resume_request_card_prevents_resume_but_allows_text(self):
        self.cfg['conversation']['auto_accept_resume_request'] = False
        for configured in (None, '虚构附件请求卡'):
            with self.subTest(configured=configured):
                conv_store.save(self.path, {})
                self.br.new_tab.side_effect = [10]
                card = configured or '我想要一份您的附件简历'
                if configured:
                    self.cfg['conversation']['resume_request_card'] = configured
                history = self.history()
                history.insert(0, {'who': 'friend', 'id': 'old-card',
                                   'text': card + '，您是否同意 [拒绝][同意]'})
                result, _, calls, _, resume = self.run_hop(history=history)
                self.assertEqual([a[0] for a in calls], ['text'])
                resume.assert_not_called()
                self.assertEqual(self.saved()['status'], 'manual')
                self.assertIsNone(self.saved()['resume_sent_ts'])
                self.assertEqual(result['manual'], 1)
                self.assertTrue(any('对方发了简历请求卡片，请手动点「同意」' in n
                                    for n in result['notifications']))

    def card_history(self):
        history = self.history()
        history.insert(0, {'who': 'friend', 'id': 'fiction-card',
                           'text': '我想要一份您的附件简历，您是否同意\n拒绝\n同意'})
        return history

    def test_accept_card_commits_resume_without_toolbar(self):
        c = self.module()
        with patch.object(c.conv_actions, 'accept_resume_request', return_value='sent', create=True) as accept:
            result, _, calls, _, toolbar = self.run_hop(history=self.card_history())
        accept.assert_called_once()
        toolbar.assert_not_called()
        self.assertEqual([a[0] for a in calls], ['text'])
        self.assertEqual(self.saved()['status'], 'engaged')
        self.assertIsNotNone(self.saved()['resume_sent_ts'])
        self.assertIn(('resume_via', 'accept_card'), [(a['type'], a['result']) for a in self.saved()['actions']])
        self.assertEqual(result['resume_sent'], 1)

    def test_accept_card_failure_goes_manual_without_toolbar(self):
        c = self.module()
        with patch.object(c.conv_actions, 'accept_resume_request', return_value='send_aborted:accept_button', create=True):
            result, _, _, _, toolbar = self.run_hop(history=self.card_history())
        toolbar.assert_not_called()
        self.assertEqual(self.saved()['status'], 'manual')
        self.assertTrue(any('请手动点「同意」' in n for n in result['notifications']))

    def test_card_followed_by_sent_evidence_never_clicks_resume(self):
        c = self.module()
        history = self.card_history()
        history.insert(1, {'who': 'unknown', 'id': 'fiction-receipt', 'classes': 'item-system',
                           'text': '附件简历已发送', 'ts': self.ts-15})
        with patch.object(c.conv_actions, 'accept_resume_request', create=True) as accept:
            _, _, calls, _, toolbar = self.run_hop(history=history)
        accept.assert_not_called()
        toolbar.assert_not_called()
        self.assertEqual([a[0] for a in calls], ['text'])
        self.assertEqual(self.saved()['resume_sent_ts'], self.ts-15)

    def test_dry_accept_card_locates_without_click_or_save(self):
        c = self.module()
        with patch.object(c.conv_actions, 'accept_resume_request', return_value='dry_ok', create=True) as accept:
            result, _, _, _, toolbar = self.run_hop(history=self.card_history(), dry=True)
        self.assertEqual(result['plans'][0]['resume_via'], 'accept_card')
        self.assertEqual(result['plans'][0]['locate'], 'dry_ok')
        self.assertTrue(accept.call_args.args[2]['conversation']['dry'])
        self.assertNotIn('dry', self.cfg['conversation'])
        toolbar.assert_not_called()
        self.br.click.assert_not_called()
        self.assertFalse(self.path.exists())

    def test_ignored_resume_request_notifies_once_and_counts_summary(self):
        self.now = self.now.replace(hour=21)
        text = '发份简历看看'
        result, _, calls, _, _ = self.run_hop(items=[self.item(text=text)],
            history=self.history(inbound=True, text=text), job_ok=False)
        self.assertEqual([call[0] for call in calls], ['text'])
        self.assertEqual(self.saved()['status'], 'engaged')
        self.assertTrue(self.saved()['resume_asked'])
        self.assertEqual(result['ignored_resume_asked'], 1)
        self.assertEqual(result['summary']['ignored_resume_asked'], 1)
        self.assertIn('其中要简历 1', result['summary_line'])
        self.assertTrue(any(n.startswith('📎 不过筛但要简历') for n in result['notifications']))
        self.br.new_tab.side_effect = [10]
        again, _, calls, _, _ = self.run_hop(items=[self.item(text=text)],
            history=self.history(inbound=True, text=text), job_ok=False)
        self.assertFalse(again['notifications'])
        self.assertFalse(calls)

    def test_dry_ignored_resume_request_does_not_save(self):
        result, _, calls, _, _ = self.run_hop(items=[self.item(text='发份简历看看')],
            history=self.history(inbound=True, text='发份简历看看'), job_ok=False, dry=True)
        self.assertTrue(result['plans'][0]['ignored_resume_asked'])
        self.assertFalse(calls)
        self.assertFalse(self.path.exists())

    def test_auto_accept_requires_boolean(self):
        cfg = json.loads((ROOT / 'assets/config.example.json').read_text())
        for value in (None, 1, 'true', [], {}):
            cfg['conversation']['auto_accept_resume_request'] = value
            errors, _ = hunt_schedule.validate_cfg(cfg)
            self.assertTrue(any('conversation.auto_accept_resume_request' in e for e in errors), errors)

    def test_accept_card_retries_gap_once_and_warns_on_two_bubbles(self):
        c = self.module()
        self.cfg['sending'] = {'min_gap_seconds': 60}
        self.cfg['_sleep'] = Mock()
        with patch.object(c.conv_actions, 'accept_resume_request', side_effect=['skip_min_gap(60)', 'sent']) as accept, \
             patch.object(c.conv_actions, 'LAST_RESUME_BUBBLES', 2):
            result, _, _, _, toolbar = self.run_hop(history=self.card_history())
        self.assertEqual(accept.call_count, 2)
        self.assertEqual(accept.call_args_list[0], accept.call_args_list[1])
        self.cfg['_sleep'].assert_called_once_with(61)
        toolbar.assert_not_called()
        self.assertEqual(result['resume_duplicates'], 1)
        self.assertEqual(self.saved()['status'], 'engaged')

    def test_accept_pending_receipt_does_not_commit_resume_timestamp(self):
        c = self.module()
        with patch.object(c.conv_actions, 'accept_resume_request', return_value='pending_verification'):
            result, _, _, _, toolbar = self.run_hop(history=self.card_history())
        self.assertTrue(result['pending_verification'])
        self.assertIsNone(self.saved()['resume_sent_ts'])
        self.assertIsNotNone(self.saved()['asked_round1_ts'])
        toolbar.assert_not_called()

    def test_latest_card_only_resolved_by_later_system_evidence(self):
        c = self.module()
        card = {'who': 'friend', 'text': '我想要一份您的附件简历'}
        sent = {'who': 'unknown', 'classes': 'item-system', 'text': '附件简历已发送'}
        self.assertIsNone(c._resume_card(self.cfg, [card, sent]))
        self.assertEqual(c._resume_card(self.cfg, [card, sent, card]), '我想要一份您的附件简历')
        self.assertEqual(c._resume_card(self.cfg, [sent, card]), '我想要一份您的附件简历')
        self.assertEqual(c._resume_card(self.cfg, [card, dict(sent, who='friend', classes='item-friend')]),
                         '我想要一份您的附件简历')

    def test_mine_resume_request_card_does_not_block_resume(self):
        history = self.history()
        history[0]['text'] = '我想要一份您的附件简历'
        result, _, calls, _, _ = self.run_hop(history=history)
        self.assertEqual([a[0] for a in calls], ['text', 'resume'])
        self.assertEqual(result['manual'], 0)

    def test_resume_request_card_validation(self):
        cfg = json.loads((ROOT / 'assets/config.example.json').read_text())
        for value in (None, 1, False, [], {}):
            cfg['conversation']['resume_request_card'] = value
            errors, _ = hunt_schedule.validate_cfg(cfg)
            self.assertTrue(any('conversation.resume_request_card' in e for e in errors), errors)
        for value in ('', '虚构附件请求卡'):
            cfg['conversation']['resume_request_card'] = value
            errors, _ = hunt_schedule.validate_cfg(cfg)
            self.assertFalse([e for e in errors if 'conversation.resume_request_card' in e])

    def test_empty_raw_history_keeps_archive_and_retries_next_hop(self):
        row = self.record(status='engaged')
        row['resume_sent_ts'] = row['asked_round1_ts'] = self.ts - 30
        row['last_seen'] = {'ts': self.ts - 20, 'text': '虚构旧消息'}
        row['last_msg_id'] = 'm0'
        conv_store.save(self.path, {row['conv_key']: row})
        before = self.path.read_bytes()
        _, _, calls, _, _ = self.run_hop(history=[])
        self.assertEqual(calls, [])
        self.assertEqual(self.path.read_bytes(), before)

        self.br.new_tab.side_effect = [10, 11]
        self.run_hop(history=self.history(text='虚构岗位负责测试，底薪加奖金'),
                     extracted={'requirements': '虚构岗位负责测试',
                                'salary_structure': '底薪加奖金'})
        self.assertEqual(self.saved()['status'], 'round2')
        self.assertEqual(self.saved()['last_msg_id'], 'm1')

    def test_only_changed_recent_hr_conversation_opens(self):
        items = [self.item(i) for i in range(1, 6)]
        records = {}
        for it in items[:2]:
            r = self.record(it)
            r['last_seen'] = {'ts': self.ts-10, 'text': it['lastMessageInfo']['showText']}
            records[it['conv_key']] = r
        items[3] = self.item(4, age=73*3600)
        conv_store.save(self.path, records)
        _, opened, _, _, _ = self.run_hop(items, receipts={'id:fiction3': True})
        self.assertEqual(opened.call_count, 1)
        self.assertEqual(opened.call_args.kwargs['conv_key'], 'id:fiction5')

    def test_missing_from_self_without_receipt_opens(self):
        item = self.item(text='方便发份简历吗')
        self.assertNotIn('fromSelf', item['lastMessageInfo'])
        result, opened, calls, _, _ = self.run_hop(
            items=[item], history=self.history(text='方便发份简历吗'))
        self.assertEqual(result['candidates'], 1)
        opened.assert_called_once_with(self.br, 10, 0, conv_key='id:fiction1')
        self.assertEqual([call[0] for call in calls], ['text', 'resume'])
        self.assertEqual(self.saved()['status'], 'engaged')

    def test_receipt_excludes_even_when_legacy_from_self_is_false(self):
        for mine in (None, False):
            with self.subTest(from_self=mine):
                self.br.new_tab.side_effect = [10]
                result, opened, calls, _, _ = self.run_hop(
                    items=[self.item(mine=mine)], receipts={'id:fiction1': True})
                self.assertEqual(result['candidates'], 0)
                opened.assert_not_called()
                self.assertFalse(calls)
                self.assertFalse(self.path.exists())

    def test_legacy_boolean_from_self_true_excludes_without_receipt(self):
        result, opened, calls, _, _ = self.run_hop(items=[self.item(mine=True)])
        self.assertEqual(result['candidates'], 0)
        opened.assert_not_called()
        self.assertFalse(calls)

    def test_conversation_rows_reads_receipt_element(self):
        c = self.module()
        # Execute the actual generated JS against a fictional DOM, offline.
        fixture = r'''
const elements = [null, '[送达]', '[已读]'].map((receipt, i) => ({
  innerText: '虚构会话' + i,
  closest: () => null,
  getAttribute: attr => attr === 'data-conversation-id' ? 'fiction' + i : null,
  querySelector: selector => selector === '.message-status' && receipt !== null
    ? {textContent: receipt} : null
}));
const document = {querySelectorAll: () => elements};
console.log(JSON.stringify(eval(process.argv[1])));
'''
        def evaluate(tab, expression, default=None):
            run = subprocess.run(['node', '-e', fixture, expression],
                                 capture_output=True, text=True, check=True)
            return json.loads(run.stdout)
        self.br.json.side_effect = evaluate
        rows = c.chat_dom.conversation_rows(self.br, 10)
        self.assertEqual([row['receipt'] for row in rows], [False, True, True])
        self.assertEqual([row['conv_key'] for row in rows],
                         ['id:fiction0', 'id:fiction1', 'id:fiction2'])

    def test_reply_text_before_resume_commits_engaged(self):
        _, _, calls, _, _ = self.run_hop()
        self.assertEqual([a[0] for a in calls], ['text', 'resume'])
        self.assertEqual(self.saved()['status'], 'engaged')
        self.assertTrue(all(a['result'] == 'sent' for a in self.saved()['actions']))

    def test_pending_resume_restores_field_and_stops_entire_hop(self):
        _, opened, _, _, _ = self.run_hop([self.item(), self.item(2)], resume_result='pending_verification')
        r = self.saved()
        self.assertIsNone(r['resume_sent_ts'])
        self.assertIsNotNone(r['asked_round1_ts'])
        self.assertNotEqual(r['status'], 'engaged')
        self.assertEqual(r['actions'][-1]['result'], 'pending_verification')
        self.assertIn('待核实', '\n'.join(self.out))
        self.assertEqual(opened.call_count, 1)

    def test_manual_text_never_sends_resume(self):
        _, _, _, _, resume = self.run_hop(text_result='manual_required')
        self.assertEqual(self.saved()['status'], 'manual')
        self.assertIsNone(self.saved()['asked_round1_ts'])
        self.assertIn('需人工', '\n'.join(self.out))
        resume.assert_not_called()

    def test_inbound_unqualified_replies_without_resume(self):
        _, _, calls, _, _ = self.run_hop(history=self.history(inbound=True), job_ok=False)
        self.assertEqual(self.saved()['status'], 'engaged')
        self.assertEqual([call[0] for call in calls], ['text'])

    def test_jd_failure_three_hops_retries_unchanged_preview(self):
        for i in range(3):
            self.br.new_tab.side_effect = [10, 11]
            self.run_hop(history=self.history(inbound=True), jd='')
            self.assertEqual(self.saved()['status'], 'manual' if i == 2 else 'new')
        self.assertIn('需人工', '\n'.join(self.out))

    def test_empty_opener_uses_fallback_and_caches_jd(self):
        self.cfg['conversation']['opener_fallback'] = '虚构兜底句，'
        _, _, calls, _, _ = self.run_hop(history=self.history(inbound=True))
        self.assertEqual(calls[0][1][5], '虚构兜底句，方便说下这个岗位主要的工作要求，和薪资结构（底薪/提成/奖金）吗？')
        self.assertTrue(self.saved()['job_ok'])
        self.assertTrue(self.saved()['jd_hash'])

    def test_daily_cap_counts_conversations_not_actions(self):
        r = self.record()
        r['actions'] = [{'ts': self.ts, 'type': 'send_text', 'result': 'sent'},
                        {'ts': self.ts, 'type': 'send_resume', 'result': 'sent'}]
        conv_store.save(self.path, {r['conv_key']: r})
        self.cfg['conversation']['daily_cap'] = 1
        _, opened, _, _, _ = self.run_hop([self.item(2)])
        opened.assert_not_called()

    def test_dry_is_read_only_and_shows_full_text(self):
        conv_store.save(self.path, {})
        before = self.path.read_bytes()
        result, _, calls, _, _ = self.run_hop(dry=True)
        self.assertEqual(calls, [])
        self.assertEqual(self.path.read_bytes(), before)
        self.assertIn('方便说下这个岗位主要的工作要求', '\n'.join(self.out))
        self.assertTrue(result['dry_run'])

    def test_config_dry_is_read_only(self):
        self.cfg['conversation']['dry'] = True
        _, _, calls, _, _ = self.run_hop()
        self.assertFalse(calls)
        self.assertFalse(self.path.exists())

    def test_complete_card_is_not_repeated(self):
        r = self.record(status='round2')
        r['resume_sent_ts'] = r['asked_round1_ts'] = r['asked_round2_ts'] = self.ts-100
        conv_store.save(self.path, {r['conv_key']: r})
        answers = dict(zip(conv_rules.ANSWER_KEYS, ['虚构工作', '虚构薪资', '两轮', '可以']))
        self.run_hop(items=[self.item(text='虚构回答')], history=self.history(text='虚构回答'), extracted=answers)
        self.assertEqual(self.saved()['status'], 'complete')
        self.assertIn('📇 问齐 虚构公司1｜虚构经理｜虚构HR1｜虚构工作｜虚构薪资｜两轮｜可以', '\n'.join(self.out))
        self.out.clear()
        _, opened, _, _, _ = self.run_hop(items=[self.item(text='虚构回答')])
        opened.assert_not_called()
        self.assertNotIn('📇 问齐', '\n'.join(self.out))

    def test_unknown_owner_skips_and_cleanup_runs(self):
        result, _, calls, _, _ = self.run_hop(history=[{'who': 'unknown', 'id': 'u', 'text': '虚构'}])
        self.assertFalse(calls)
        self.assertEqual(result['owner_unknown'], 1)
        self.assertFalse(self.path.exists())

    def test_guard_stops_before_browser(self):
        c = self.module()
        with patch.object(c.SS, 'ledger_guard', side_effect=RuntimeError('unresolved_send: fictional')):
            result = c.run(self.cfg, self.br, self.out, now=self.now)
        self.br.new_tab.assert_not_called()
        self.assertTrue(result['pending_verification'])
        self.assertIn('待核实', '\n'.join(self.out))

    def test_hours_and_disabled_do_not_open_browser(self):
        c = self.module()
        for options in [{'enabled': False}, {'enabled': True, 'hours': [21]}]:
            self.cfg['conversation'] = options
            c.run(self.cfg, self.br, self.out, now=self.now)
        self.br.new_tab.assert_not_called()

    def test_validation_rejects_each_wrong_type(self):
        cfg = json.loads((ROOT / 'assets/config.example.json').read_text())
        bad = {'enabled': 1, 'dry': 'false', 'daily_cap': True, 'per_hop': 0,
               'lookback_hours': -1, 'hours': [24], 'templates': {'round1': 2}, 'opener_fallback': []}
        for key, value in bad.items():
            cfg['conversation'] = {key: value}
            errors, _ = hunt_schedule.validate_cfg(cfg)
            self.assertTrue(any('conversation.' + key in e for e in errors), (key, errors))
        for value in [None, [], False, 'invalid']:
            cfg['conversation'] = value
            self.assertTrue(any('conversation' in e for e in hunt_schedule.validate_cfg(cfg)[0]))
        cfg['conversation'] = {'enabled': False, 'dry': True, 'hours': [0, 21], 'templates': {}}
        self.assertFalse([e for e in hunt_schedule.validate_cfg(cfg)[0] if 'conversation' in e])

    def test_cli_help_without_config_access(self):
        p = subprocess.run([sys.executable, str(ROOT/'scripts/boss_hunt.py'), 'chat', '--help'], capture_output=True, text=True)
        self.assertEqual(p.returncode, 0, p.stderr)
        self.assertIn('--dry', p.stdout)

    def test_formatter_silence_and_cards(self):
        path = ROOT/'scripts/cron/boss-hunt-chat-fmt.py'
        self.assertTrue(path.exists(), 'chat formatter is missing')
        for data, expected in [({'candidates': 0}, ''),
                               ({'candidates': 1, 'overview': '虚构概览', 'notifications': ['📇 问齐 虚构卡片', '🙋 需人工 虚构请求']},
                                '📇 问齐 虚构卡片\n🙋 需人工 虚构请求\n')]:
            p = subprocess.run([sys.executable, str(path)], input=json.dumps(data), capture_output=True, text=True)
            self.assertEqual(p.returncode, 0, p.stderr)
            self.assertEqual(p.stdout, expected)

    def test_template_mismatch_goes_manual_without_resume(self):
        _, _, _, _, resume = self.run_hop(text_result='send_aborted:template_mismatch')
        self.assertEqual(self.saved()['status'], 'manual')
        self.assertIn('需人工', '\n'.join(self.out))
        resume.assert_not_called()

    def test_template_mismatch_persists_failure_and_notifies_only_once(self):
        result, _, _, _, resume = self.run_hop(text_result='send_aborted:template_mismatch')
        row = self.saved()
        self.assertEqual(row['status'], 'manual')
        self.assertTrue(row['notified']['manual'])
        self.assertEqual(row['last_seen'], {'ts': self.ts-10, 'text': '您好，方便聊聊吗'})
        self.assertIsNone(row['asked_round1_ts'])
        self.assertIsNone(row['resume_sent_ts'])
        self.assertEqual(row['actions'], [{'ts': self.ts, 'type': 'send_text',
                                           'result': 'send_aborted:template_mismatch'}])
        self.assertEqual(result['manual'], 1)
        self.assertIn('需人工', '\n'.join(self.out))
        resume.assert_not_called()
        self.out.clear()
        self.br.new_tab.side_effect = [10]
        _, opened, calls, _, _ = self.run_hop(items=[self.item(text='虚构后续消息', age=1)])
        opened.assert_not_called()
        self.assertFalse(calls)
        self.assertNotIn('需人工 虚构公司', '\n'.join(self.out))

    def test_interview_gate_accepts_default_and_configured_reply(self):
        import conv_actions
        cfg = {'user': {'interview_reply': '虚构固定面试回复'}}
        for text in (conv_rules.INTERVIEW_REPLY, '虚构固定面试回复'):
            with self.subTest(text=text):
                self.assertTrue(conv_actions._template_allowed(cfg, text))
        for text in ('虚构未授权话术', '', None):
            with self.subTest(text=text):
                self.assertFalse(conv_actions._template_allowed(cfg, text))

    def test_lost_or_duplicate_cursor_requires_manual_without_send(self):
        for cursor in ('fiction-gone', 'm1'):
            with self.subTest(cursor=cursor):
                row = self.record(status='engaged')
                row['last_msg_id'] = cursor
                conv_store.save(self.path, {row['conv_key']: row})
                history = self.history() + [
                    {'who': 'friend', 'id': 'm1', 'text': '虚构后续消息', 'ts': self.ts-5}]
                self.br.new_tab.side_effect = [10]
                self.out.clear()
                _, _, calls, _, _ = self.run_hop(history=history)
                saved = self.saved()
                self.assertEqual(saved['status'], 'manual')
                self.assertEqual(saved['last_seen'], {'ts': self.ts-10, 'text': '您好，方便聊聊吗'})
                self.assertEqual(saved['actions'], [])
                self.assertIn('需人工', '\n'.join(self.out))
                self.assertFalse(calls)

    def test_dom_mine_updates_preview_without_sending(self):
        r = self.record(status='engaged')
        conv_store.save(self.path, {r['conv_key']: r})
        history = [{'who': 'mine', 'id': 'm2', 'text': '虚构已回', 'ts': self.ts-5}]
        result, opened, calls, _, _ = self.run_hop(history=history)
        self.assertEqual(result['candidates'], 1)
        self.assertEqual(result['processed'], 0)
        opened.assert_called_once()
        self.assertFalse(calls)
        self.assertEqual(self.saved()['status'], 'engaged')
        self.assertEqual(self.saved()['last_seen'],
                         {'ts': self.ts-10, 'text': '您好，方便聊聊吗'})
        self.br.new_tab.side_effect = [10]
        _, opened, calls, _, _ = self.run_hop(history=history)
        opened.assert_not_called()
        self.assertFalse(calls)

    def test_dry_lost_cursor_does_not_save(self):
        r = self.record(status='engaged')
        r['last_msg_id'] = 'fiction-gone'
        conv_store.save(self.path, {r['conv_key']: r})
        before = self.path.read_bytes()
        result, _, calls, _, _ = self.run_hop(dry=True)
        self.assertEqual(self.path.read_bytes(), before)
        self.assertFalse(calls)
        self.assertTrue(result['plans'])

    def test_later_extraction_does_not_rewrite_earlier_text_template(self):
        history = self.history() + [{'who': 'friend', 'id': 'm2', 'text': '虚构工作和薪资', 'ts': self.ts-5}]
        _, _, calls, _, _ = self.run_hop(history=history, extracted={
            'requirements': '虚构工作', 'salary_structure': '虚构薪资'})
        self.assertEqual(calls[0][1][6:8], (
            '方便说下这个岗位主要的工作要求，和薪资结构（底薪/提成/奖金）吗？', ''))
        self.assertEqual(self.saved()['status'], 'engaged')

    def test_dry_inbound_does_not_write_llm_cache(self):
        c = self.module()
        real_opener = c.conv_llm.opener
        with patch.object(c.conv_llm.G, '_generate_raw', return_value=('我有虚构客户维护经验。', '')), \
             patch.object(c.conv_llm.G, 'basic_safety', return_value=(True, '')):
            self.run_hop(history=self.history(inbound=True), opener=real_opener, dry=True)
        self.assertEqual(list(Path(self.tmp.name).iterdir()), [])
        self.assertEqual(self.cfg['paths']['workdir'], self.tmp.name)

    def test_cli_disabled_json_with_temporary_config(self):
        self.cfg['conversation']['enabled'] = False
        cfg_path = Path(self.tmp.name)/'config.json'
        cfg_path.write_text(json.dumps(self.cfg))
        p = subprocess.run([sys.executable, str(ROOT/'scripts/boss_hunt.py'),
                            '--config', str(cfg_path), '--json', 'chat'], capture_output=True, text=True)
        self.assertEqual(p.returncode, 0, p.stderr)
        data = json.loads(p.stdout)
        self.assertEqual(data['cmd'], 'chat')
        self.assertEqual(data['schema'], 2)
        self.assertEqual(data['reason'], 'disabled')

    def test_successful_jd_is_reused_on_next_hr_message(self):
        self.run_hop(history=self.history(inbound=True), text_result='send_aborted:unknown_conversation')
        c = self.module()
        # Re-run with the cached verdict; the second hop must never read a JD.
        self.br.new_tab.side_effect = [10]
        self.br.network.side_effect = lambda tab, url, index, **kw: {'text': json.dumps(
            {'code': 0, 'zpData': {'result': [self.item()] if index == 0 else []}})}
        with patch.object(c.chat_dom, 'conversation_rows', return_value=[{'conv_key': 'id:fiction1'}]), \
             patch.object(c.chat_dom, 'open_by_index', return_value=('ok', '', self.history(inbound=True))), \
             patch.object(c.jd_page, 'read_jd', side_effect=AssertionError('cached JD reread')), \
             patch.object(c.CM, 'cleanup_browser'), \
             patch.object(c.conv_llm, 'opener', return_value='虚构对口句，'), \
             patch.object(c.conv_actions, 'send_text', return_value='sent'), \
             patch.object(c.conv_actions, 'send_resume', return_value='sent'):
            c.run(self.cfg, self.br, self.out, now=self.now)
        self.assertEqual(self.saved()['status'], 'engaged')

    def test_cli_login_failure_closes_owned_tabs(self):
        import argparse
        import boss_hunt as boss
        cfg_path = Path(self.tmp.name)/'config.json'
        cfg_path.write_text(json.dumps(self.cfg))
        browser = Mock(spec=boss.CM.Browser)
        browser.opened = [123]
        browser.close_own_tabs.return_value = 1
        args = argparse.Namespace(config=str(cfg_path), dry=False, cmd='chat', json=True)
        with patch.object(boss.CM, 'Browser', return_value=browser), \
             patch.object(boss.HC, 'now_local', return_value=self.now), \
             patch.object(boss.O, 'check_login', return_value=(123, {'login': True})), \
             self.assertRaisesRegex(RuntimeError, 'not_logged_in'):
            boss.cmd_chat.__wrapped__(args)
        browser.close_own_tabs.assert_called_once()
        browser.close.assert_called_once()

    def test_dry_respects_remaining_daily_budget(self):
        self.cfg['conversation']['daily_cap'] = 1
        result, opened, calls, _, _ = self.run_hop([self.item(), self.item(2)], dry=True)
        self.assertEqual(opened.call_count, 1)
        self.assertEqual(len(result['plans']), 1)
        self.assertFalse(calls)

    def test_partial_second_round_text_uses_only_missing_template(self):
        r = self.record(status='engaged')
        r['asked_round1_ts'] = r['resume_sent_ts'] = self.ts-30
        conv_store.save(self.path, {r['conv_key']: r})
        _, _, calls, _, _ = self.run_hop(items=[self.item(text='虚构回答')],
            history=self.history(text='虚构回答'),
            extracted={'requirements': '虚构工作', 'online_interview': '可以'})
        self.assertEqual(calls[0][1][5:8], ('面试大概有几轮？', '面试大概有几轮？', ''))
        self.assertEqual(self.saved()['status'], 'round2')

    def test_failed_round2_keeps_answers_without_committing_question(self):
        r = self.record(status='engaged')
        r['asked_round1_ts'] = r['resume_sent_ts'] = self.ts-30
        conv_store.save(self.path, {r['conv_key']: r})
        self.run_hop(items=[self.item(text='虚构回答')], history=self.history(text='虚构回答'),
                     extracted={'requirements': '虚构工作'}, text_result='send_aborted:unknown_conversation')
        self.assertEqual(self.saved()['status'], 'engaged')
        self.assertEqual(self.saved()['answers']['requirements'], '虚构工作')
        self.assertIsNone(self.saved()['asked_round2_ts'])

    def test_summary_at_last_hour(self):
        self.now = self.now.replace(hour=21)
        result, _, _, _, _ = self.run_hop()
        self.assertEqual(result['summary']['engaged'], 1)
        self.assertIn('等对方回复 1', '\n'.join(self.out))

    def test_system_class_messages_do_not_hide_resume_request(self):
        history = self.history(text='请发一份简历')
        history.insert(1, {'who': 'unknown', 'classes': 'message-item item-system',
                           'text': '虚构平台自动匹配提示', 'id': 'sys'})
        history.append({'who': 'unknown', 'classes': ['item-tip'],
                        'text': '虚构平台提示', 'id': 'tip'})
        result, _, calls, _, _ = self.run_hop(history=history)
        self.assertEqual(result['owner_unknown'], 0)
        self.assertEqual(result['processed'], 1)
        self.assertEqual([call[0] for call in calls], ['text', 'resume'])
        self.assertEqual(self.saved()['status'], 'engaged')

    def test_filtered_history_empty_or_mine_only_updates_last_seen(self):
        for keep_mine in (False, True):
            with self.subTest(keep_mine=keep_mine):
                row = self.record(status='engaged')
                row['last_msg_id'] = 'fiction-old'
                conv_store.save(self.path, {row['conv_key']: row})
                history = self.history()[:1] if keep_mine else []
                history.append({'who': 'unknown', 'classes': 'item-time',
                                'text': '虚构时间提示'})
                self.br.new_tab.side_effect = [10]
                result, _, calls, _, _ = self.run_hop(history=history)
                self.assertEqual(result['owner_unknown'], 0)
                self.assertEqual(result['processed'], 0)
                self.assertFalse(calls)
                saved = self.saved()
                self.assertEqual(saved['status'], 'engaged')
                self.assertEqual(saved['last_msg_id'], 'fiction-old')
                self.assertEqual(saved['last_seen']['text'], '您好，方便聊聊吗')

    def test_system_text_cards_are_removed_from_extraction_context(self):
        c = self.module()
        row = self.record(status='engaged')
        row['resume_sent_ts'] = row['asked_round1_ts'] = self.ts-30
        conv_store.save(self.path, {row['conv_key']: row})
        history = self.history(text='虚构岗位主要负责测试')
        history.insert(1, {'who': 'unknown', 'classes': 'item-system', 'text': '虚构系统提示'})
        history.append({'who': 'friend', 'id': 'card', 'text': ' 你与该职位 竞争者PK情况\n'})
        with patch.object(c.conv_rules, 'decide', wraps=c.conv_rules.decide) as decide:
            result, _, _, _, _ = self.run_hop(history=history, extracted={'requirements': '虚构测试'})
        decide.assert_called_once()
        context = decide.call_args.args[2]
        self.assertEqual([m['text'] for m in context['history']],
                         ['虚构招呼', '虚构岗位主要负责测试'])
        self.assertEqual(result['owner_unknown'], 0)
        self.assertEqual(self.saved()['status'], 'round2')

    def test_system_texts_exact_match_and_config_override(self):
        for configured, text, processed in (
                (None, '你与该职位竞争者PK情况', 0),
                (None, '你与该职位竞争者PK情况，请发简历', 1),
                (['虚构平台卡片'], ' 虚构 平台卡片\n', 0),
                ([], '你与该职位竞争者PK情况', 1)):
            with self.subTest(configured=configured, text=text):
                if configured is None:
                    self.cfg['conversation'].pop('system_texts', None)
                else:
                    self.cfg['conversation']['system_texts'] = configured
                conv_store.save(self.path, {})
                self.br.new_tab.side_effect = [10]
                result, _, calls, _, _ = self.run_hop(items=[self.item(text=text)],
                                                     history=self.history(text=text))
                self.assertEqual(result['processed'], processed)
                if not processed:
                    self.assertFalse(calls)
                    self.assertEqual(self.saved()['last_seen']['text'], text.strip())

    def test_row_reordering_uses_fresh_unique_index(self):
        c = self.module()
        first, second = self.item(age=1), self.item(2, age=2)
        rows = [{'conv_key': first['conv_key']}, {'conv_key': second['conv_key']}]
        result, opened, _, _, _ = self.run_hop([first, second], dry=True,
                                              row_snapshots=[rows, rows, rows[::-1]])
        self.assertEqual(result['processed'], 2)
        self.assertEqual([(call.args[2], call.kwargs['conv_key']) for call in opened.call_args_list],
                         # conv-11：最旧优先，仍使用每次刷新后的实际索引。
                         [(1, 'id:fiction2'), (1, 'id:fiction1')])

    def test_missing_or_duplicate_fresh_row_is_counted_and_skipped(self):
        for fresh_rows in ([], [{'conv_key': 'id:fiction1'}]*2):
            with self.subTest(fresh_rows=fresh_rows):
                self.br.new_tab.side_effect = [10]
                result, opened, calls, _, _ = self.run_hop(
                    row_snapshots=[[{'conv_key': 'id:fiction1'}], fresh_rows])
                # conv-11：缺行先揭示，失败计 reveal_failed；重复行仍计 row_missing。
                missing = int(bool(fresh_rows))
                self.assertEqual(result.get('row_missing'), missing)
                self.assertEqual(result['reveal_failed'], int(not fresh_rows))
                self.assertIn('row_missing %d' % missing, result['overview'])
                self.assertIn('row_missing %d' % missing, '\n'.join(self.out))
                opened.assert_not_called()
                self.assertFalse(calls)
                # conv-11：缺行揭示失败先通知落盘；后续重复行子用例保留该去重记录。
                self.assertTrue(self.path.exists())

    def test_empty_network_retries_same_tab_once_then_succeeds(self):
        c = self.module()
        for has_reload in (False, True):
            with self.subTest(has_reload=has_reload):
                self.br = Mock(spec=c.CM.Browser)
                self.br.new_tab.return_value = 10
                self.br.opened = [99, '10']
                self.br.c = Mock()
                self.br.c.call.return_value = {'isError': True}
                if has_reload:
                    self.br.reload = Mock()
                responses = iter([{'text': '[NO_MATCH] 虚构空响应'},
                    {'text': json.dumps({'code': 0, 'zpData': {'result': [self.item()]}})},
                    {'text': '[NO_MATCH] 虚构结束'}])
                try:
                    result, _, _, _, _ = self.run_hop(
                        items=[], network=lambda *a, **kw: next(responses))
                except (RuntimeError, ValueError) as exc:
                    self.fail('empty capture must retry on the same tab: %s' % exc)
                self.assertTrue(result['ok'])
                self.assertEqual(result['tabs_closed'], 2)
                self.br.new_tab.assert_called_once_with(c.chat_dom.CHAT_URL)
                self.assertEqual([call.args[0] for call in self.br.network.call_args_list], [10]*3)
                self.assertEqual([call.kwargs['index'] for call in self.br.network.call_args_list],
                                 [0, 0, 1])
                expected_nav = [unittest.mock.call(10, c.chat_dom.CHAT_URL, wait=5)]
                if has_reload:
                    self.br.reload.assert_called_once_with(10)
                else:
                    expected_nav += [unittest.mock.call(10, 'about:blank', wait=1),
                                     unittest.mock.call(10, c.chat_dom.CHAT_URL, wait=6)]
                self.assertEqual(self.br.nav.call_args_list, expected_nav)
                self.br.c.call.assert_not_called()
                self.assertEqual(self.br.opened, [99, '10'])
                self.cleanup.assert_called_once_with(self.br)

    def test_empty_list_retries_same_tab_then_captures_conversation(self):
        c = self.module()
        self.br = Mock(spec=c.CM.Browser)
        self.br.new_tab.return_value = 10
        responses = iter([{'text': json.dumps({'code': 0, 'zpData': {'result': []}})}] +
                         [{'text': '[NO_MATCH] 虚构无数据'}] +
                         [{'text': json.dumps({'code': 0, 'zpData': {'result': [self.item()]}})}] +
                         [{'text': '[NO_MATCH] 虚构无数据'}])
        result, opened, _, _, _ = self.run_hop(network=lambda *a, **kw: next(responses))
        self.assertEqual(self.br.network.call_count, 4)
        self.assertEqual(self.br.nav.call_args_list, [
            unittest.mock.call(10, c.chat_dom.CHAT_URL, wait=5),
            unittest.mock.call(10, 'about:blank', wait=1),
            unittest.mock.call(10, c.chat_dom.CHAT_URL, wait=6)])
        self.br.new_tab.assert_called_once_with(c.chat_dom.CHAT_URL)
        self.assertTrue(result['ok'])
        self.assertEqual(result.get('captured'), 1)
        self.assertEqual(result['processed'], 1)
        opened.assert_called_once()
        self.cleanup.assert_called_once_with(self.br)

    def test_empty_lists_fail_after_retry_without_opening_and_clean_up(self):
        c = self.module()
        with patch.object(c.chat_dom, 'open_by_index') as opened:
            with self.assertRaisesRegex(RuntimeError, '^chat_empty_list$'):
                self.run_hop(items=[])
        opened.assert_not_called()
        self.assertEqual(self.br.network.call_count, 24)
        self.cleanup.assert_called_once_with(self.br)
        self.assertFalse(self.path.exists())

    def test_keyless_nonempty_list_retries_then_succeeds(self):
        responses = iter([{'text': json.dumps({'code': 0, 'zpData': {'result': [
            {'jobName': '虚构无标识岗位'}, None, '虚构无标识项']}})},
            {'text': '[NO_MATCH] 虚构结束'},
            {'text': json.dumps({'code': 0, 'zpData': {'result': [self.item()]}})},
            {'text': '[NO_MATCH] 虚构结束'}])
        result, _, _, _, _ = self.run_hop(network=lambda *a, **kw: next(responses))
        self.assertEqual(self.br.network.call_count, 4)
        self.assertEqual(result.get('captured'), 1)
        self.assertEqual(result['processed'], 1)

    def test_keyless_lists_fail_without_empty_list_reason(self):
        c = self.module()
        with patch.object(c.chat_dom, 'open_by_index') as opened:
            with self.assertRaisesRegex(RuntimeError, '^chat_no_valid_response$'):
                self.run_hop(network=lambda *a, **kw: {'text': json.dumps(
                    {'code': 0, 'zpData': {'result': [{'jobName': '虚构无标识岗位'}]}})})
        opened.assert_not_called()
        self.assertEqual(self.br.network.call_count, 24)
        self.cleanup.assert_called_once_with(self.br)

    def test_capture_count_deduplicates_before_candidate_filtering_and_overview(self):
        result, _, _, _, _ = self.run_hop(
            items=[self.item(), self.item(), self.item(2, mine=True)])
        self.assertEqual(result.get('captured'), 2)
        self.assertEqual(result['candidates'], 1)
        self.assertIn('列表 2（抓包 12）｜候选 1', result['overview'])
        self.assertIn(result['overview'], self.out)

    def test_cli_empty_capture_reports_failure_json_and_formatter_warning(self):
        import argparse
        import io
        from contextlib import redirect_stdout
        import boss_hunt as boss
        c = self.module()
        cfg_path = Path(self.tmp.name) / 'fiction-config.json'
        cfg_path.write_text(json.dumps(self.cfg))
        args = argparse.Namespace(config=str(cfg_path), dry=False, cmd='chat', json=True)
        output = io.StringIO()
        def clean_owned_tabs(br):
            br.opened = []
            return 2
        with patch.object(boss.CM, 'Browser', return_value=self.br), \
             patch.object(boss.HC, 'now_local', return_value=self.now), \
             patch.object(boss.O, 'check_login', return_value=(10, {'avatar': True})), \
             patch.object(c.chat_dom, 'open_by_index') as opened, \
             patch.object(c.CM, 'cleanup_browser', side_effect=clean_owned_tabs) as cleanup, \
             redirect_stdout(output):
            code = boss.cmd_chat(args)
        data = json.loads(output.getvalue())
        self.assertEqual(code, 1)
        self.assertFalse(data['ok'])
        self.assertEqual(data['error'], 'chat_empty_list')
        self.assertEqual(self.br.network.call_count, 24)
        opened.assert_not_called()
        cleanup.assert_called_once_with(self.br)
        formatted = subprocess.run([sys.executable, str(ROOT/'scripts/cron/boss-hunt-chat-fmt.py')],
                                   input=json.dumps(data), capture_output=True, text=True)
        self.assertEqual(formatted.returncode, 0, formatted.stderr)
        self.assertEqual(formatted.stdout, '⚠️ 对话失败：chat_empty_list\n')

    def test_empty_network_fails_after_one_retry_and_cleans_up(self):
        c = self.module()
        self.br = Mock(spec=c.CM.Browser)
        self.br.new_tab.return_value = 10
        self.br.opened = []  # The retry must not assume the tab is still tracked here.
        self.br.c = Mock()
        self.br.c.call.return_value = {}
        with self.assertRaisesRegex(RuntimeError, '^chat_no_valid_response$'):
            self.run_hop(network=lambda *a, **kw: {'text': '[NO_MATCH] 虚构空响应'})
        self.br.new_tab.assert_called_once_with(c.chat_dom.CHAT_URL)
        self.assertEqual(self.br.network.call_count, 2)
        self.br.c.call.assert_not_called()
        self.cleanup.assert_called_once_with(self.br)

    def test_retry_navigation_failure_still_cleans_up(self):
        c = self.module()
        self.br = Mock(spec=c.CM.Browser)
        self.br.new_tab.return_value = 10
        self.br.c = Mock()
        self.br.c.call.return_value = {'isError': True}
        self.br.nav.side_effect = [{}, RuntimeError('fiction_retry_navigation_failed')]
        with self.assertRaisesRegex(RuntimeError, '^fiction_retry_navigation_failed$'):
            self.run_hop(network=lambda *a, **kw: {'text': '[NO_MATCH] 虚构空响应'})
        self.cleanup.assert_called_once_with(self.br)

    def test_meeting_invitation_sends_fixed_reply_and_enters_manual(self):
        text = '后天上午九点左右，见面聊聊？'
        result, _, calls, _, _ = self.run_hop(items=[self.item(text=text)],
                                             history=self.history(text=text))
        self.assertEqual([call[0] for call in calls], ['text'])
        self.assertEqual(calls[0][1][5], '希望线上，请先给时段，我确认后答复')
        self.assertEqual(self.saved()['status'], 'manual')
        self.assertEqual(result['manual'], 1)

    def test_system_receipts_reply_salary_runs_real_text_sender_for_round2(self):
        c = self.module()
        row = self.record(status='engaged')
        row['asked_round1_ts'] = self.ts-30
        conv_store.save(self.path, {row['conv_key']: row})
        text = '底薪八千加提成'
        history = self.history(text=text)
        history[0]['text'] = conv_rules.DEFAULT_TEMPLATES['round1']
        for n in (1, 2):
            history.insert(1, dict(id='fiction-system%d' % n, who='unknown',
                classes='item-system', text='您的附件简历 虚构小禾 已发送给Boss', ts=self.ts-15))
        self.cfg['paths']['state'] = str(Path(self.tmp.name) / 'state.json')
        self.cfg['sending'] = {'min_gap_seconds': 0}
        sender = c.conv_actions.send_text
        execute = c._execute
        def deliver(*args):
            with patch.object(c.conv_actions, 'send_text', sender):
                return execute(*args)
        after = dict(conv_key=row['conv_key'], messages=history + [dict(
            id='fiction-round2', who='mine', text=conv_rules.DEFAULT_TEMPLATES['round2'])])
        with patch.object(c, '_execute', side_effect=deliver), \
             patch.object(c.chat_dom, 'active_conversation', side_effect=[dict(conv_key=row['conv_key'], messages=history), after]), \
             patch.object(c.conv_actions.CS, 'type_into_chat', return_value={'ok': True}), \
             patch.object(c.chat_dom, 'atomic_chat_click', return_value=dict(ok=True, clicked=True, conv_key=row['conv_key'])), \
             patch.object(c.conv_actions.time, 'sleep'):
            result, _, _, _, _ = self.run_hop(items=[self.item(text=text)], history=history,
                                             extracted={'salary_structure': '底薪八千加提成'})
        self.assertEqual(result['owner_unknown'], 0)
        self.assertEqual(self.saved()['status'], 'round2')
        self.assertEqual(self.saved()['asked_round2_ts'], self.ts)

    def test_reply_new_with_only_system_and_hr_enters_decide(self):
        row = self.record()
        conv_store.save(self.path, {row['conv_key']: row})
        history = [dict(id='fiction-system', who='unknown', classes='item-system',
                        text='附件简历已发送', ts=self.ts-15),
                   dict(id='fiction-hr', who='friend', text='您好，方便聊聊吗', ts=self.ts-10)]
        result, _, calls, _, resume = self.run_hop(history=history)
        self.assertEqual((result['owner_unknown'], result['processed']), (0, 1))
        self.assertEqual([a[0] for a in calls], ['text'])
        resume.assert_not_called()
        self.assertEqual(self.saved()['status'], 'engaged')
        self.assertIn({'ts': self.ts, 'type': 'resume_seen', 'result': 'observed'}, self.saved()['actions'])

    def test_owner_abort_explains_new_with_resume_seen(self):
        row = self.record()
        conv_store.save(self.path, {row['conv_key']: row})
        history = [dict(id='fiction-system', who='unknown', classes='item-system',
                        text='附件简历已发送', ts=self.ts-15),
                   dict(id='fiction-hr', who='friend', text='您好，方便聊聊吗', ts=self.ts-10)]
        result, _, _, _, _ = self.run_hop(history=history, text_result='send_aborted:owner_unknown')
        saved = self.saved()
        self.assertEqual((result['owner_unknown'], result['processed']), (0, 1))
        self.assertEqual(saved['status'], 'new')
        self.assertIsNone(saved['asked_round1_ts'])
        self.assertEqual(saved['resume_sent_ts'], self.ts-15)
        self.assertEqual([(a['type'], a['result']) for a in saved['actions']],
                         [('resume_seen', 'observed'), ('send_text', 'send_aborted:owner_unknown')])

    def test_system_resume_evidence_prevents_resend_and_is_saved(self):
        for text, timestamp in (('您的附件简历 虚构小禾 已发送给Boss', self.ts-15),
                                ('简历已发送', None),
                                ('已向虚构HR发送附件简历', (self.ts-15)*1000)):
            with self.subTest(text=text, timestamp=timestamp):
                conv_store.save(self.path, {})
                self.br.new_tab.side_effect = [10]
                history = self.history(text='能求一份你的简历吗')
                history.append({'who': 'unknown', 'classes': 'item-system',
                                'text': text, 'ts': timestamp})
                result, _, calls, _, _ = self.run_hop(history=history)
                self.assertEqual([call[0] for call in calls], ['text'])
                saved = self.saved()
                self.assertEqual(saved['resume_sent_ts'], self.ts if timestamp is None else self.ts-15)
                self.assertEqual([a for a in saved['actions'] if a['type'] == 'resume_seen'],
                                 [{'ts': self.ts, 'type': 'resume_seen', 'result': 'observed'}])
                self.assertEqual(result['resume_sent'], 0)

    def test_dry_system_resume_evidence_changes_plan_without_saving(self):
        row = self.record()
        conv_store.save(self.path, {row['conv_key']: row})
        before = self.path.read_bytes()
        history = self.history(text='请发简历') + [
            {'who': 'unknown', 'classes': ['item-system'], 'text': '简历已发送'}]
        result, _, calls, _, _ = self.run_hop(history=history, dry=True)
        self.assertEqual([a['type'] for a in result['plans'][0]['actions']], ['send_text'])
        self.assertEqual(self.path.read_bytes(), before)
        self.assertFalse(calls)

    def test_resume_evidence_override_and_existing_timestamp_are_respected(self):
        self.cfg['conversation']['resume_sent_pattern'] = '虚构附件交付完成'
        for existing in (None, self.ts-60):
            with self.subTest(existing=existing):
                row = self.record()
                row['resume_sent_ts'] = existing
                conv_store.save(self.path, {row['conv_key']: row})
                self.br.new_tab.side_effect = [10]
                history = self.history() + [{'who': 'unknown', 'classes': 'item-system',
                                             'text': '虚构附件交付完成', 'ts': self.ts-15}]
                _, _, calls, _, _ = self.run_hop(history=history)
                self.assertEqual([call[0] for call in calls], ['text'])
                saved = self.saved()
                self.assertEqual(saved['resume_sent_ts'], existing if existing is not None else self.ts-15)
                self.assertEqual(sum(a['type'] == 'resume_seen' for a in saved['actions']),
                                 1 if existing is None else 0)

    def test_resume_received_with_sent_evidence_sends_nothing(self):
        row = self.record(status='engaged')
        row['resume_sent_ts'] = row['asked_round1_ts'] = self.ts-30
        conv_store.save(self.path, {row['conv_key']: row})
        text = '简历已收到，我们看下'
        _, _, calls, _, _ = self.run_hop(items=[self.item(text=text)], history=self.history(text=text))
        self.assertFalse(calls)
        self.assertEqual(self.saved()['resume_sent_ts'], self.ts-30)

    def test_resume_sent_pattern_validation(self):
        cfg = json.loads((ROOT / 'assets/config.example.json').read_text())
        for value in (None, 1, False, [], {}, '['):
            with self.subTest(value=value):
                cfg['conversation']['resume_sent_pattern'] = value
                errors, _ = hunt_schedule.validate_cfg(cfg)
                self.assertTrue(any('conversation.resume_sent_pattern' in e for e in errors), errors)
        for value in ('', '虚构简历.*已发送'):
            cfg['conversation']['resume_sent_pattern'] = value
            errors, _ = hunt_schedule.validate_cfg(cfg)
            self.assertFalse([e for e in errors if 'conversation.resume_sent_pattern' in e])

    def test_system_texts_validation(self):
        cfg = json.loads((ROOT / 'assets/config.example.json').read_text())
        for value in (None, '虚构提示', {}, [1], [False], ['虚构提示', None]):
            with self.subTest(value=value):
                cfg['conversation']['system_texts'] = value
                errors, _ = hunt_schedule.validate_cfg(cfg)
                self.assertTrue(any('conversation.system_texts' in e for e in errors), errors)
        for value in ([], ['虚构提示']):
            cfg['conversation']['system_texts'] = value
            errors, _ = hunt_schedule.validate_cfg(cfg)
            self.assertFalse([e for e in errors if 'conversation.system_texts' in e])

    def test_wrapper_preserves_business_exit_and_shared_lock(self):
        import fcntl
        import shutil
        script = ROOT/'scripts/cron/boss-hunt-chat.sh'
        self.assertTrue(script.exists(), 'chat wrapper is missing')
        repo = Path(self.tmp.name)/'fiction-repo'
        cron = repo/'scripts/cron'
        cron.mkdir(parents=True)
        shutil.copy(script, cron/script.name)
        (cron/'boss-hunt-chat-fmt.py').write_text('import sys; print("fiction render failure"); sys.exit(4)')
        (repo/'scripts/boss_hunt.py').write_text('import sys; print("fiction business failure"); sys.exit(7)')
        config = repo/'config.json'
        config.write_text(json.dumps({'paths': {'workdir': str(repo/'data')}}))
        env = dict(os.environ, BOSS_HUNT_CONFIG=str(config), BOSS_HUNT_PYTHON=sys.executable)
        p = subprocess.run(['bash', str(cron/script.name)], env=env, capture_output=True, text=True)
        self.assertEqual(p.returncode, 7, p.stdout+p.stderr)
        self.assertIn('业务退出码 7', p.stdout)
        with (repo/'data/.wrapperlock').open('a+') as lock:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            p = subprocess.run(['bash', str(cron/script.name)], env=env, capture_output=True, text=True)
        self.assertEqual(p.returncode, 0)
        self.assertNotIn('fiction business failure', p.stdout)


if __name__ == '__main__':
    unittest.main()
