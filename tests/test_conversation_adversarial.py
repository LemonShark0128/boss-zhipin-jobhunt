"""conv-03 对抗用例（审查补充）：全部为虚构数据。"""
import copy
import sys
from unittest.mock import Mock, patch
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
sys.path.insert(0, str(ROOT / 'tests'))
import conv_actions
import conv_rules
import conv_store
import test_conversation_run as T


class Adv(T.ConversationRunTests):
    """复用替身夹具；基类用例在本类里置空，避免重复跑。"""

    def test_stale_cursor_is_not_reopened_every_hop(self):
        # 存档游标在可见历史里找不到：不能每跳都重开、占掉 per_hop 名额
        r = self.record(status='engaged')
        r['asked_round1_ts'] = r['resume_sent_ts'] = self.ts - 100
        r['last_msg_id'] = 'gone'
        conv_store.save(self.path, {r['conv_key']: r})
        self.run_hop(items=[self.item(text='虚构新消息')], history=self.history(text='虚构新消息'))
        self.br.new_tab.side_effect = [10, 11]
        _, opened, _, _, _ = self.run_hop(items=[self.item(text='虚构新消息')],
                                          history=self.history(text='虚构新消息'))
        opened.assert_not_called()

    def test_stale_conversation_does_not_starve_fresh_one(self):
        self.cfg['conversation']['per_hop'] = 1
        stale = self.record(self.item(1), status='engaged')
        stale['last_msg_id'] = 'gone'
        conv_store.save(self.path, {stale['conv_key']: stale})
        newer, older = self.item(1, text='虚构新消息', age=5), self.item(2, age=50)
        for _ in range(2):
            self.br.new_tab.side_effect = [10, 11]
            _, opened, _, _, _ = self.run_hop(items=[newer, older])
        self.assertEqual(opened.call_args.kwargs['conv_key'], 'id:fiction2')

    def test_text_ok_resume_failed_never_resends_question(self):
        self.run_hop(resume_result='send_aborted:resume_button')
        r = self.saved()
        self.assertIsNotNone(r['asked_round1_ts'])
        self.assertIsNone(r['resume_sent_ts'])
        # HR 下一句是泛招呼：只补简历，绝不再发第一轮问题
        history = self.history() + [
            {'who': 'mine', 'id': 'm2', 'text': conv_rules.DEFAULT_TEMPLATES['round1'], 'ts': self.ts - 5},
            {'who': 'friend', 'id': 'm3', 'text': '您好', 'ts': self.ts - 1}]
        self.br.new_tab.side_effect = [10, 11]
        _, _, calls, _, _ = self.run_hop(items=[self.item(text='您好', age=1)], history=history)
        self.assertEqual([c[0] for c in calls], ['resume'])

    def test_text_failure_skips_resume_and_keeps_state(self):
        _, _, calls, _, _ = self.run_hop(text_result='send_aborted:unknown_conversation')
        self.assertEqual([c[0] for c in calls], ['text'])
        r = self.saved()
        self.assertEqual(r['status'], 'new')
        self.assertIsNone(r['asked_round1_ts'])
        self.assertIsNone(r['resume_sent_ts'])

    def test_interview_invite_replies_fixed_text_only(self):
        self.cfg['user'] = {'interview_reply': '虚构固定话术'}
        _, _, calls, _, _ = self.run_hop(items=[self.item(text='明天下午来面试可以吗')],
                                         history=self.history(text='明天下午来面试可以吗'))
        self.assertEqual([c[0] for c in calls], ['text'])
        self.assertEqual(calls[0][1][5], '虚构固定话术')
        self.assertEqual(self.saved()['status'], 'manual')
        self.assertIn('需人工', '\n'.join(self.out))

    def test_default_interview_reply_passes_template_gate(self):
        # 未配置 user.interview_reply 时规则用默认话术，发送闸门必须放行，否则邀约被静默丢弃
        self.assertTrue(conv_actions._template_allowed({}, conv_rules.INTERVIEW_REPLY))

    def test_contact_exchange_goes_manual_without_send(self):
        _, _, calls, _, _ = self.run_hop(items=[self.item(text='加个微信吧')],
                                         history=self.history(text='加个微信吧'))
        self.assertEqual(calls, [])
        self.assertEqual(self.saved()['status'], 'manual')

    def test_yesterday_sends_do_not_count_toward_cap(self):
        r = self.record(self.item(3))
        r['actions'] = [{'ts': self.ts - 86400, 'type': 'send_text', 'result': 'sent'}]
        conv_store.save(self.path, {r['conv_key']: r})
        self.cfg['conversation']['daily_cap'] = 1
        _, opened, _, _, _ = self.run_hop([self.item()])
        self.assertEqual(opened.call_count, 1)

    def test_lookback_boundary_and_future_timestamp(self):
        self.cfg['conversation']['lookback_hours'] = 72
        items = [self.item(1, age=72 * 3600 + 1), self.item(2, age=-60)]
        _, opened, _, _, _ = self.run_hop(items)
        opened.assert_not_called()

    def test_dry_never_writes_archive_on_jd_failure_or_ignored(self):
        conv_store.save(self.path, {})
        before = self.path.read_bytes()
        self.run_hop(history=self.history(inbound=True), jd='', dry=True)
        self.br.new_tab.side_effect = [10, 11]
        self.run_hop(history=self.history(inbound=True), job_ok=False, dry=True)
        self.assertEqual(self.path.read_bytes(), before)

    def test_terminal_status_never_sends(self):
        for status in sorted(conv_rules.TERMINAL):
            r = self.record(status=status)
            conv_store.save(self.path, {r['conv_key']: r})
            self.br.new_tab.side_effect = [10, 11]
            _, opened, _, _, _ = self.run_hop(items=[self.item(text='发下简历')],
                                              history=self.history(text='发下简历'))
            opened.assert_not_called()

    def test_salary_answers_are_not_rejections(self):
        for text in ('底薪按城市不一致，提成另算', '各区域提成比例不一致，看业绩'):
            self.assertNotEqual(conv_rules.classify(text), 'reject', text)
        for text in ('我们感谢您的投递，但您的专业技能与我们目前的职位需求并不完全吻合',
                     '目前项目需要与您的经验不完全匹配'):
            self.assertEqual(conv_rules.classify(text), 'reject', text)

    def test_empty_history_is_transient_not_seen(self):
        # 打开后历史为空（页面没加载出来）不能把 last_seen 记成已看，否则这条消息永远漏掉
        self.run_hop(history=[])
        self.assertFalse(self.path.exists() and conv_store.load(self.path).get('id:fiction1'))

    def test_meet_invite_is_interview_not_greeting(self):
        for text in ('明天上午十点半左右，见面聊聊？', '周三下午来公司聊一下吧'):
            self.assertEqual(conv_rules.classify(text), 'interview', text)
        self.assertEqual(conv_rules.classify('方便聊聊吗'), 'greet')

    def test_system_resume_sent_bubble_prevents_resend(self):
        history = [{'who': 'mine', 'id': 'm0', 'text': '虚构招呼', 'ts': self.ts - 30},
                   {'who': 'unknown', 'id': 's1', 'classes': 'message-item item-system',
                    'text': '您的附件简历 虚构名 已发送给Boss点击查看附件', 'ts': self.ts - 20},
                   {'who': 'friend', 'id': 'm1', 'text': '您好，方便聊聊吗', 'ts': self.ts - 10}]
        _, _, calls, _, _ = self.run_hop(history=history)
        self.assertNotIn('resume', [c[0] for c in calls])

    # ---- conv-03i：简历请求卡片自动点「同意」 ----
    CARD = '我想要一份您的附件简历，您是否同意\n拒绝\n同意'

    def hop_accept(self, history, receipts=('sent',), **kw):
        import conversation
        seen = []
        receipts = list(receipts)
        def accept(*a, **k):
            seen.append(a)
            return receipts.pop(0) if len(receipts) > 1 else receipts[0]
        with patch.object(conversation.conv_actions, 'accept_resume_request', side_effect=accept):
            result = self.run_hop(history=history, **kw)
        return result, seen

    def test_card_after_earlier_receipt_never_sends_again(self):
        # 已有「附件简历已发送」证据后又来一张卡片：不再点同意，也不点发简历
        history = [{'who': 'mine', 'id': 'm0', 'text': '虚构招呼', 'ts': self.ts - 40},
                   {'who': 'friend', 'id': 'c1', 'text': self.CARD, 'ts': self.ts - 30},
                   {'who': 'unknown', 'id': 's1', 'classes': 'message-item item-system',
                    'text': '您的附件简历 虚构名 已发送给Boss点击查看附件', 'ts': self.ts - 20},
                   {'who': 'friend', 'id': 'c2', 'text': self.CARD, 'ts': self.ts - 10}]
        (_, _, calls, _, _), seen = self.hop_accept(history, items=[self.item(text=self.CARD)])
        self.assertEqual(seen, [])
        self.assertNotIn('resume', [c[0] for c in calls])

    def test_accept_pending_never_falls_back_to_toolbar(self):
        history = self.history()
        history.insert(1, {'who': 'friend', 'id': 'c1', 'text': self.CARD, 'ts': self.ts - 15})
        (result, _, calls, _, _), seen = self.hop_accept(history, receipts=('pending_verification',))
        self.assertEqual(len(seen), 1)
        self.assertNotIn('resume', [c[0] for c in calls])
        self.assertTrue(result['pending_verification'])
        self.assertIsNone(self.saved()['resume_sent_ts'])

    def test_accept_min_gap_retries_accept_not_toolbar(self):
        self.cfg['sending'] = {'min_gap_seconds': 60}
        self.cfg['_sleep'] = Mock()
        history = self.history()
        history.insert(1, {'who': 'friend', 'id': 'c1', 'text': self.CARD, 'ts': self.ts - 15})
        (_, _, calls, _, _), seen = self.hop_accept(history, receipts=('skip_min_gap(60)', 'sent'))
        self.assertEqual(len(seen), 2)
        self.assertNotIn('resume', [c[0] for c in calls])
        self.assertIsNotNone(self.saved()['resume_sent_ts'])

    def test_dry_accept_uses_dry_copy_and_leaves_config(self):
        history = self.history()
        history.insert(1, {'who': 'friend', 'id': 'c1', 'text': self.CARD, 'ts': self.ts - 15})
        (result, _, calls, _, _), seen = self.hop_accept(history, receipts=('dry_ok',), dry=True)
        self.assertEqual(len(seen), 1)
        self.assertIs(seen[0][2]['conversation']['dry'], True)
        self.assertNotIn('dry', self.cfg['conversation'])
        self.assertEqual(calls, [])
        self.assertFalse(self.path.exists())
        self.assertEqual(result['plans'][0].get('resume_via'), 'accept_card')

    def test_ignored_card_lists_once_and_sends_nothing(self):
        # conv-03n 用户决策：不过筛也回复（问问题），但绝不点「同意」/发简历，要简历只推一次
        items = [self.item(text=self.CARD)]
        history = [{'who': 'friend', 'id': 'c1', 'text': self.CARD, 'ts': self.ts - 10}]
        (result, _, calls, _, _), seen = self.hop_accept(history, items=items, job_ok=False)
        self.assertEqual(seen, [])
        self.assertEqual([kind for kind, _, _ in calls if kind == 'resume'], [])
        self.assertNotEqual(self.saved()['status'], 'ignored')
        self.assertIsNone(self.saved()['resume_sent_ts'])
        self.assertEqual(result['ignored_resume_asked'], 1)
        self.assertEqual(sum('不过筛但要简历' in line for line in self.out), 1)
        self.br.new_tab.side_effect = [10, 11]
        (result, _, calls, _, _), seen = self.hop_accept(history, items=items, job_ok=False)
        self.assertEqual((seen, result['ignored_resume_asked']), ([], 0))
        self.assertEqual([kind for kind, _, _ in calls if kind == 'resume'], [])

    def test_auto_accept_rejects_string_flag(self):
        import hunt_schedule
        errors, _ = hunt_schedule.validate_cfg({'conversation': {'auto_accept_resume_request': 'true'}})
        self.assertTrue(any('auto_accept_resume_request' in e for e in errors), errors)

    # ---- conv-03j：岗位名回填 ----
    def test_title_sources_tolerate_malformed_state(self):
        import conversation
        state = {'sent': {'j1': '坏数据'}, 'b_pool': {'j1': None}}
        self.assertEqual(conversation._job_title({}, 'j1', {'jobName': ' 虚构经理 '}, {}, state), '虚构经理')
        state = {'sent': {'j1': {'name': '   '}}, 'b_pool': {'j1': {'title': '虚构备选'}}}
        self.assertEqual(conversation._job_title({}, 'j1', {}, {}, state), '虚构备选')
        self.assertEqual(conversation._job_title({}, 'j1', {}, {}, {'sent': None}), '')

    def test_old_record_with_null_title_is_backfilled_and_shown(self):
        import conversation
        r = self.record()
        r['title'] = None
        conv_store.save(self.path, {r['conv_key']: r})
        with patch.object(conversation.hunt_state, 'load_state',
                          return_value={'sent': {r['job_id']: {'name': '虚构区域经理'}}}):
            self.run_hop(items=[self.item(text='加个微信吧')], history=self.history(text='加个微信吧'))
        self.assertEqual(self.saved()['title'], '虚构区域经理')
        self.assertTrue(any('虚构区域经理' in line for line in self.out))

    def test_no_line_ever_has_empty_title_slot(self):
        item = self.item(text='加个微信吧')
        del item['jobName']  # 真实列表接口没有 jobName
        self.run_hop(items=[item], history=self.history(text='加个微信吧'))
        self.assertTrue(self.out)
        self.assertFalse([line for line in self.out if '｜｜' in line])
        self.assertEqual(self.saved()['title'], '')

    def test_accept_js_scoped_to_friend_card_and_exact_label(self):
        br = Mock()
        br.json.return_value = {'ok': False, 'reason': 'accept_button'}
        conv_actions._accept_button(br, 1, 'id:fiction', '我想要一份您的附件简历')
        script = br.json.call_args[0][1]
        self.assertIn('item-friend', script)
        self.assertIn("=== '同意'", script)
        self.assertNotIn('发简历', script)
        self.assertIn('messages.length - 1', script)

    # conv-03k：空列表不算有效抓包
    def test_empty_index0_but_later_index_valid_needs_no_recapture(self):
        # 同一轮里 index 0 空、index 1 有会话：本轮即有效，不该重抓
        import json
        def network(tab, url, index, **kw):
            if index >= 2:  # conv-03s：读到 NO_MATCH 为止
                return {'text': '[NO_MATCH] 没有匹配 "friend/getG" 的第 %d 条请求' % index, 'isError': True}
            result = [self.item()] if index == 1 else []
            return {'text': json.dumps({'code': 0, 'zpData': {'result': result}})}
        result, _, _, _, _ = self.run_hop(network=network)
        self.assertEqual(self.br.network.call_count, 3)
        self.assertEqual(result['captured'], 1)
        self.assertNotIn('about:blank', str(self.br.nav.call_args_list))

    def list_network(self, captures):
        import json
        def network(tab, url, index, **kw):
            if index >= len(captures):
                return {'text': '[NO_MATCH] 没有匹配 "friend/getG" 的第 %d 条请求' % index, 'isError': True}
            return {'text': json.dumps({'code': 0, 'zpData': {'result': captures[index]}})}
        return network

    # conv-03s：最新 4 批都是老联系人，最近会话在第 5 批 → 仍要成为候选（真机「列表 339｜候选 0」）
    def test_recent_batch_beyond_four_captures_is_candidate(self):
        old = [[self.item(n, age=200 * 3600) for n in range(10 * b + 2, 10 * b + 5)] for b in range(4)]
        recent = self.item(1)
        captures = old + [[recent]]
        items = [it for batch in captures for it in batch]
        result, _, _, _, _ = self.run_hop(items=items, network=self.list_network(captures))
        self.assertEqual(result['captured'], 13)
        self.assertEqual(result['candidates'], 1)
        self.assertEqual(self.br.network.call_count, 6)

    # conv-03s：同一会话出现在多批里，保留 msgTime 更新的那条（不能被更旧的抓包覆盖）
    def dup_case(self, fresh_first):
        stale, fresh = self.item(1, age=200 * 3600), self.item(1)
        captures = [[fresh], [stale]] if fresh_first else [[stale], [fresh]]
        result, _, _, _, _ = self.run_hop(items=[fresh], network=self.list_network(captures))
        self.assertEqual(result['captured'], 1)
        self.assertEqual(result['candidates'], 1)

    def test_duplicate_key_newer_capture_not_overwritten_by_older(self):
        self.dup_case(fresh_first=True)

    def test_duplicate_key_older_capture_with_newer_message_wins(self):
        self.dup_case(fresh_first=False)

    # conv-03s：上限 12 次，替身永不 NO_MATCH 也不能无限读
    def test_list_capture_reads_at_most_twelve(self):
        import json
        self.run_hop(network=lambda tab, url, index, **kw: {'text': json.dumps(
            {'code': 0, 'zpData': {'result': [self.item()]}})})
        self.assertLessEqual(self.br.network.call_count, 12)

    def test_error_codes_plus_one_empty_list_reports_empty_list(self):
        # 全是 code!=0，只要见过一次 code 0 空列表，原因就报 chat_empty_list
        import json
        seen = []
        def network(tab, url, index, **kw):
            seen.append(index)
            body = {'code': 0, 'zpData': {'result': []}} if len(seen) == 6 else {'code': 37}
            return {'text': json.dumps(body)}
        with self.assertRaisesRegex(RuntimeError, '^chat_empty_list$'):
            self.run_hop(network=network)
        self.cleanup.assert_called_once_with(self.br)

    def test_result_list_is_dict_not_list_stays_invalid(self):
        import json
        with self.assertRaisesRegex(RuntimeError, '^chat_no_valid_response$'):
            self.run_hop(network=lambda *a, **kw: {'text': json.dumps(
                {'code': 0, 'zpData': {'result': {'conv_key': 'id:fiction1'}}})})

    def test_overview_shows_list_count_on_success(self):
        result, _, _, _, _ = self.run_hop()
        self.assertTrue(result['overview'].startswith('💬 对话｜列表 1（抓包 '))

    # 用户决策（10-30）：用户亲自代回只跳过那一跳，HR 再回照常自动，不转人工
    def test_user_manual_reply_skips_only_that_hop(self):
        item = self.item(mine=True)
        _, opened, calls, _, _ = self.run_hop(items=[item])
        opened.assert_not_called()
        self.assertEqual(calls, [])
        self.assertNotEqual((conv_store.load(self.path).get('id:fiction1') or {}).get('status'), 'manual')

    def test_hr_reply_after_user_manual_reply_continues_automation(self):
        row = self.record(status='engaged')
        row['resume_sent_ts'] = row['asked_round1_ts'] = self.ts - 300
        row['last_seen'] = {'ts': self.ts - 200, 'text': '虚构旧消息'}
        row['last_msg_id'] = 'm1'
        conv_store.save(self.path, {row['conv_key']: row})
        history = [{'who': 'mine', 'id': 'm0', 'text': '虚构招呼', 'ts': self.ts-400},
                   {'who': 'friend', 'id': 'm1', 'text': '虚构旧消息', 'ts': self.ts-200},
                   {'who': 'mine', 'id': 'u1', 'text': '虚构：用户亲自补充一句', 'ts': self.ts-100},
                   {'who': 'friend', 'id': 'm2', 'text': '底薪虚构，加提成', 'ts': self.ts-10}]
        result, opened, calls, _, _ = self.run_hop(
            history=history, extracted={'requirements': '虚构要求', 'salary': '虚构底薪加提成'})
        opened.assert_called_once()
        self.assertEqual(result['processed'], 1)
        self.assertTrue(any(kind == 'text' for kind, _, _ in calls))
        self.assertNotEqual(self.saved()['status'], 'manual')

    # conv-03n：黑名单 + 简历卡片：只回绝，绝不点同意/发简历；不感兴趣失败不留 pending
    def test_blacklisted_card_declines_without_resume(self):
        import conversation
        self.cfg['conversation']['decline_industries'] = ['虚构金融']
        item = self.item(text=self.CARD)
        item['brandName'] = '某某虚构金融服务'
        history = [{'who': 'friend', 'id': 'c1', 'text': self.CARD, 'ts': self.ts - 10}]
        with patch.object(conversation.conv_actions, 'mark_not_interested_company',
                          return_value='aborted:company_missing') as mark:
            (result, _, calls, _, _), seen = self.hop_accept(history, items=[item], job_ok=True)
        self.assertEqual(seen, [])
        self.assertEqual([k for k, _, _ in calls if k == 'resume'], [])
        texts = [a[5] if len(a) > 5 else kw.get('text') for k, a, kw in calls if k == 'text']
        self.assertEqual(len(texts), 1)
        self.assertIn('不太匹配', str(texts[0]))
        self.assertEqual(self.saved()['status'], 'declined')
        mark.assert_called_once()
        self.assertTrue(any('不感兴趣未点成' in n for n in result['notifications']))
        self.assertIn('回绝 1', result['overview'])
        import send_safety
        self.assertFalse(any(r.get('status') == 'pending' for r in send_safety.ledger_latest(self.cfg)))

    # conv-03o：拒信判定与问句反例
    def test_reject_patterns_and_question_counterexamples(self):
        for text in ('感谢您投递我司虚构岗位。本轮初筛已结束，经综合评估，有更适配该岗位的候选人',
                     '很遗憾，您的初筛未通过', '我们已经找到更合适的人选了'):
            with self.subTest(text=text):
                self.assertEqual(conv_rules.classify(text, self.cfg), 'reject')
        for text in ('您觉得自己更适合哪类岗位呢？', '方便说说你更匹配哪个方向吗', '初筛结束了吗？'):
            with self.subTest(text=text):
                self.assertNotEqual(conv_rules.classify(text, self.cfg), 'reject')

    # conv-03r：职责段必须在任何「要求类」标题处截断
    def test_duty_scope_stops_at_requirement_heading_variants(self):
        import hunt_filter
        for heading in ('要求：', '任职条件：', '资格要求：', '我们希望你：', '【任职资格】'):
            with self.subTest(heading=heading):
                scope = hunt_filter.jd_duty_scope('岗位职责：\n1、虚构拓展商户\n%s\n1、金融学相关专业' % heading)
                self.assertIn('虚构拓展商户', scope)
                self.assertNotIn('金融', scope)

    # scan-01：某关键词真的 0 结果（code 0、空 jobList）不能让整次补池失败
    def test_scan_zero_result_keyword_does_not_abort_whole_scan(self):
        import test_scan_capture_prime as SC
        import outbound as O
        case = SC.ScanCaptureTests('test_first_keyword_records_initial_navigation_and_saves_raw')
        case.setUp()
        try:
            case.cfg['targets'][0]['keywords'] = ['虚构冷门词', '虚构热门词']
            with case.browser([SC.EMPTY, SC.EMPTY, SC.JOBLIST]) as br:
                result = O.scan(case.cfg, br, pages=1)
            self.assertEqual(result[0], 1)
        finally:
            case.doCleanups()

    def scan_case(self):
        import test_scan_capture_prime as SC
        case = SC.ScanCaptureTests('test_first_keyword_records_initial_navigation_and_saves_raw')
        case.setUp()
        self.addCleanup(case.doCleanups)
        return SC, case

    # scan-02：真机形状——idx 0 是最后加载的页（hasMore=false），idx 1 才是第 1 页；
    # 中间夹一个只有 1 页的关键词（idx 1 NO_MATCH），整次补池不能中止，也不能丢第 1 页
    def test_scan_newest_page_has_more_false_and_single_page_keyword(self):
        import json
        import outbound as O
        SC, case = self.scan_case()
        last = {'code': 0, 'zpData': {'jobList': [{'encryptJobId': 'fiction-last'}], 'hasMore': False}}
        first = {'code': 0, 'zpData': {'jobList': [{'encryptJobId': 'fiction-first'}], 'hasMore': True}}
        case.cfg['targets'][0]['keywords'] = ['虚构两页词', '虚构单页词', '虚构末尾词']
        with case.browser([None, [last, first], [SC.JOBLIST], [SC.JOBLIST, SC.JOBLIST]]) as br:
            result = O.scan(case.cfg, br, pages=2)
        self.assertEqual(result, (5, []))
        saved = {p.name: json.loads(p.read_text()) for p in case.raw.glob('*.json')}
        self.assertEqual(saved['示例城市_虚构两页词_1.json'], first)
        self.assertEqual(saved['示例城市_虚构两页词_0.json'], last)
        self.assertNotIn('示例城市_虚构单页词_1.json', saved)
        self.assertIn('示例城市_虚构末尾词_1.json', saved)
        # 没有下一页不触发 about:blank 重抓
        navs = [a['url'] for t, a in case.transport.calls if t == 'navigate']
        self.assertNotIn('about:blank', navs)

    # scan-02：后续页是 code 37 不能被「没有下一页」吞掉
    def test_scan_later_page_code37_still_raises(self):
        import outbound as O
        SC, case = self.scan_case()
        with case.browser([None, [SC.JOBLIST, {'code': 37, 'message': '虚构风控'}]]) as br:
            with self.assertRaises(RuntimeError) as ctx:
                O.scan(case.cfg, br, pages=2)
        self.assertEqual(str(ctx.exception), 'risk_code_37')

    # scan-02：后一个关键词 idx 0 两次都抓不到，仍须在 _deadline 下中止（不能被放宽成跳过）
    def test_scan_first_page_missing_twice_still_aborts(self):
        import outbound as O
        SC, case = self.scan_case()
        case.cfg['targets'][0]['keywords'] = ['虚构正常词', '虚构失败词']
        with case.browser([None, [SC.JOBLIST], None, None]) as br:
            with self.assertRaises(RuntimeError) as ctx:
                O.scan(case.cfg, br, pages=2)
        self.assertEqual(str(ctx.exception), 'scan_network_failed')

    # conv-03q：不感兴趣弹层只认「不太感兴趣」小节的「“…”公司」，确认用 span.zp-btn-sure，绝不碰反馈输入框
    def test_not_interested_script_scoped_to_company_option(self):
        br = Mock()
        br.json.return_value = {'ok': False, 'reason': 'company_missing'}
        conv_actions._not_interested_target(br, 1, 'id:fiction', 'company')
        script = br.json.call_args[0][1]
        self.assertIn('不太感兴趣', script)
        self.assertIn('”公司$', script)
        self.assertNotIn('textarea', script)
        conv_actions._not_interested_target(br, 1, 'id:fiction', 'confirm')
        self.assertIn('.zp-dialog-footer .zp-btn-sure', br.json.call_args[0][1])

    # conv-03p：要简历识别
    def test_resume_intent_variants(self):
        for text in ('可以投递的～辛苦发一份最新版本的附件简历吗', '麻烦把简历发我一下', '方便发下最新简历吗'):
            with self.subTest(text=text):
                self.assertEqual(conv_rules.classify(text, self.cfg), 'resume')
        for text in ('我已经发过简历给您了', '您的附件简历 虚构 已发送给Boss'):
            with self.subTest(text=text):
                self.assertNotEqual(conv_rules.classify(text, self.cfg), 'resume')

    def test_blacklist_word_only_in_benefits_does_not_decline(self):
        self.cfg['conversation']['decline_industries'] = ['保险']
        jd = '岗位职责：\n虚构拓展商户\n任职要求：\n虚构三年经验\n福利待遇：\n五险一金，补充商业保险'
        result, _, calls, _, _ = self.run_hop(history=self.history(inbound=True), jd=jd)
        self.assertNotEqual(self.saved()['status'], 'declined')
        self.assertEqual(result['declined'], 0)

    # conv-03l：HR 头衔绝不当岗位名
    def test_job_title_never_falls_back_to_hr_title(self):
        c = self.module()
        for item in ({'title': '虚构招聘总监'}, {'title': '虚构HR', 'jobName': '   '},
                     {'title': '虚构经理', 'jobName': None}):
            with self.subTest(item=item):
                self.assertEqual(c._job_title(self.cfg, 'fiction-job1', {}, item, {}), '')
        self.assertEqual(c._job_title(self.cfg, 'fiction-job1', {}, {'title': '虚构HR', 'jobName': '虚构岗位'}, {}),
                         '虚构岗位')


class ResumeOnceAdv(unittest.TestCase):
    """conv-03m 对抗：简历只发一次，且慢回执不能误判 pending。"""

    def setUp(self):
        self.br = Mock()
        self.br.click.return_value = {}
        self.cfg = {'conversation': {}}
        self.before = {'conv_key': 'id:fiction', 'messages': [{'id': 'm0', 'who': 'friend', 'text': '虚构'}]}

    def go(self, receipt_at, dialog):
        polls = []
        def active(br, tab):
            polls.append(1)
            msgs = list(self.before['messages'])
            if len(polls) >= receipt_at:
                msgs.append({'id': 'sys1', 'who': 'unknown', 'text': '您的附件简历 虚构 已发送给Boss'})
            return {'conv_key': 'id:fiction', 'messages': msgs}
        with patch.object(conv_actions.chat_dom, 'active_conversation', side_effect=active), \
             patch.object(conv_actions, '_resume_dialog', return_value=dialog), \
             patch.object(conv_actions, '_resume_close_button', return_value={'present': False}), \
             patch.object(conv_actions.time, 'sleep'):
            result = conv_actions._send_resume_and_confirm(
                self.br, 1, self.cfg, 'id:fiction', self.before, '#bh-conv-accept')
        clicks = [c.args[1] for c in self.br.click.call_args_list]
        return result, clicks

    def test_slow_receipt_without_dialog_is_sent_not_pending(self):
        # 无弹窗、回执第 10 次轮询（约 5 秒）才出现：旧代码约 7 秒后才核验，不能退化成 pending 卡住账本
        result, clicks = self.go(10, {})
        self.assertEqual(result, 'sent')
        self.assertEqual(clicks, ['#bh-conv-accept'])

    def test_slow_receipt_with_confirm_dialog_clicks_confirm_once(self):
        result, clicks = self.go(10, {'html': '<div>虚构简历</div>', 'confirm': True})
        self.assertEqual(result, 'sent')
        self.assertEqual(clicks.count('#bh-conv-resume-confirm'), 1)


class NaturalReplyAdv(unittest.TestCase):
    """conv-04 对抗：经验答复不能吞掉过筛简历、答句与问句要断开、允许一个短应答词。"""

    def setUp(self):
        import test_conv_04_natural as N
        base = N.NaturalReplyTests('test_round2_lead_reaches_existing_gate_and_delivery')
        base.setUp()
        self.base = base
        self.row, self.ctx = base.row, base.ctx

    def inbound(self):
        self.row['origin'] = 'inbound'

    def test_inbound_experience_question_still_sends_resume_when_job_ok(self):
        self.inbound()
        actions, _ = self.base.decide('你好，请问有相关工作经验吗？')
        self.assertIn('send_resume', [a['type'] for a in actions])
        self.assertEqual(len([a for a in actions if a['type'] == 'send_text']), 1)

    def test_batch_greeting_then_experience_keeps_resume(self):
        self.inbound()
        msgs = [{'who': 'friend', 'ts': 19, 'text': '你好'},
                {'who': 'friend', 'ts': 20, 'text': '有相关经验吗？'}]
        actions, _ = conv_rules.decide(self.row, msgs, self.ctx)
        self.assertIn('send_resume', [a['type'] for a in actions])

    def test_inbound_not_job_ok_experience_never_sends_resume(self):
        self.inbound()
        self.ctx['job_ok'] = False
        actions, _ = self.base.decide('有相关经验吗？')
        self.assertNotIn('send_resume', [a['type'] for a in actions])

    def test_answer_without_punctuation_is_separated_from_question(self):
        import re
        self.ctx['answer'] = lambda text: '做过七年本地生活BD'
        actions, _ = self.base.decide('有相关经验吗？')
        texts = self.base.texts(actions)
        self.assertEqual(len(texts), 1)
        self.assertRegex(texts[0], r'^做过七年本地生活BD[，。]')

    def test_short_ack_word_before_fact_is_allowed(self):
        self.ctx['answer'] = lambda text: '有的，做过七年本地生活BD，'
        actions, _ = self.base.decide('有相关经验吗？')
        self.assertTrue(self.base.texts(actions)[0].startswith('有的，做过七年本地生活BD，'))

    def test_ack_word_alone_or_invented_claim_still_manual(self):
        for answer in ('有的，', '有的，做过十年连锁餐饮BD，', '有的，有的，做过七年本地生活BD，'):
            with self.subTest(answer=answer):
                row = copy.deepcopy(self.row)
                self.ctx['answer'] = lambda text, a=answer: a
                actions, planned = self.base.decide('有相关经验吗？', row)
                self.assertEqual(self.base.texts(actions), [])
                self.assertEqual(planned['status'], 'manual')


    def test_quantity_words_in_hr_answers_are_not_manual(self):
        for text in ('底薪多少看能力，面试两轮，可以线上', '团队有几个人负责上海，底薪加提成', '面试几轮看情况，一般两轮'):
            with self.subTest(text=text):
                self.assertNotEqual(conv_rules.classify(text, {}), 'manual')

    def test_quantity_followup_on_experience_is_manual(self):
        for text in ('管过多少人？', '签过几家连锁？', '带过几个人的团队？'):
            with self.subTest(text=text):
                self.assertEqual(conv_rules.classify(text, {}), 'manual')


class NotifyTextAdv(unittest.TestCase):
    """conv-03t 对抗：人工推送不能把系统气泡当 HR 原话。"""

    def notify(self, snapshot, new_msgs=()):
        import conversation
        rec = conv_store.new_record('id:fiction', 'inbound', 'fiction-job', '虚构公司', '虚构HR', '', 1)
        rec['last_seen'] = {'ts': 1, 'text': snapshot}
        result, out = {'notifications': [], 'manual': 0}, []
        conversation._notify('manual', rec, result, out, {}, new_msgs)
        return out[0]

    def test_pk_bubble_snapshot_shows_system_placeholder(self):
        line = self.notify('你与该职位竞争者PK情况\n\n共人投递，你超过竞争者\n\n查看详细分析')
        self.assertIn('（系统消息）', line)
        self.assertNotIn('PK', line)

    def test_resume_bubble_snapshot_shows_system_placeholder(self):
        self.assertIn('（系统消息）', self.notify('您的附件简历 虚构 已发送给Boss点击查看附件'))

    def test_real_hr_text_wins_over_snapshot(self):
        line = self.notify('您的附件简历 虚构 已发送给Boss', [{'who': 'friend', 'text': '虚构：方便说下离职原因吗', 'ts': 1}])
        self.assertIn('方便说下离职原因吗', line)


class CloseOwnTabsAdv(unittest.TestCase):
    """tab-01 对抗：标签页已不在算关闭；别的错误照样报；绝不碰别人的标签页。"""

    def browser(self, replies):
        import chrome_mcp
        br = chrome_mcp.Browser({})
        calls = []
        def call(tool, args, **kw):
            calls.append((tool, dict(args)))
            reply = replies[args['tabId']]
            if isinstance(reply, Exception):
                raise reply
            return reply
        br.c = Mock(call=call)
        br.opened = list(replies)
        return br, calls

    def test_no_tab_counts_as_closed_and_other_tabs_untouched(self):
        br, calls = self.browser({101: {'text': 'closed'},
                                  102: {'text': '[NO_TAB] 标签页 102 不存在', 'isError': True}})
        br.close_own_tabs()
        self.assertEqual(br.opened, [])
        self.assertEqual({a['tabId'] for _, a in calls}, {101, 102})

    def test_no_tab_lookalike_inside_other_error_still_fails(self):
        br, _ = self.browser({103: {'text': '[INTERNAL] 关闭失败（不是 [NO_TAB]）', 'isError': True}})
        with self.assertRaisesRegex(RuntimeError, 'tab_cleanup_failed'):
            br.close_own_tabs()
        self.assertEqual(br.opened, [103])


for _name in [n for n in dir(T.ConversationRunTests) if n.startswith('test_')]:
    setattr(Adv, _name, None)


if __name__ == '__main__':
    unittest.main()


# conv-03v：拒信写法与「收到简历」；每种情形独立用例，不共享 setUp 状态
class RejectWordingAdv(unittest.TestCase):
    REJECTS = (
        '很高兴收到您的简历，但是我们这次更倾向于有不同经验范围的候选人。祝您找到理想的工作！',
        '收到您的简历，经过评估，我们会优先考虑其他候选人',
        '感谢您的关注和投递，但当前职位需要与您背景不太相符',
        '抱歉，简历给到区域总看了 匹配度不是很高 期待您找到合适的机会',
        '我们非常感谢您的投递，但是很遗憾，我们的职位需要更资深的人选',
        '很遗憾，您暂时无法进入下一轮',
    )
    NOT_REJECTS = (
        '我们倾向于有餐饮经验的人选，你正好合适',
        '不相关的经历可以不用写',
        '很遗憾刚才没接到您电话，岗位的事我们再约时间',
        '很遗憾刚才没接到电话，不太方便的话我晚点再打',
        '您的经验与这个岗位不谋而合',
        '您的背景跟岗位很对口，不知道您是否方便聊聊',
        '我们不考虑其他候选人了，就等您',
        '我们不会考虑其他候选人',
        '这个岗位和您的经历相符度很高',
        '你有相关经验吗？',
        '匹配度很高，想约您聊聊',
    )

    def test_reject_variants(self):
        for text in self.REJECTS:
            with self.subTest(text=text):
                self.assertEqual(conv_rules.classify(text, {}), 'reject')

    def test_positive_or_neutral_texts_are_not_rejects(self):
        for text in self.NOT_REJECTS:
            with self.subTest(text=text):
                self.assertNotEqual(conv_rules.classify(text, {}), 'reject')

    def test_received_resume_is_not_a_request(self):
        for text in ('收到您的简历了，我们看一下', '收到简历', '简历收到了，谢谢'):
            with self.subTest(text=text):
                self.assertNotEqual(conv_rules.classify(text, {}), 'resume')
        for text in ('能发一份您的简历吗', '求一份你的简历'):
            with self.subTest(text=text):
                self.assertEqual(conv_rules.classify(text, {}), 'resume')


# conv-03u：40 行外会话；每种情形独立用例
class OutOfViewAdv(unittest.TestCase):
    module = T.ConversationRunTests.module
    item = T.ConversationRunTests.item
    record = T.ConversationRunTests.record
    history = T.ConversationRunTests.history
    run_hop = T.ConversationRunTests.run_hop

    def setUp(self):
        T.ConversationRunTests.setUp(self)

    def notices(self, result):
        return [n for n in result['notifications'] if n.startswith('📭')]

    def test_engaged_record_out_of_view_keeps_status_and_last_seen(self):
        record = self.record(status='engaged')
        record['last_seen'] = {'ts': self.ts - 500, 'text': '虚构旧消息'}
        conv_store.save(self.path, {record['conv_key']: copy.deepcopy(record)})
        result = self.run_hop(items=[self.item(text='虚构HR新问题')], row_snapshots=[[], []])[0]
        self.assertEqual(len(self.notices(result)), 1)
        saved = conv_store.load(self.path)[record['conv_key']]
        self.assertEqual(saved['status'], 'engaged')
        self.assertEqual(saved['last_seen'], record['last_seen'])

    def test_dry_run_notifies_without_writing(self):
        result = self.run_hop(items=[self.item(text='虚构HR招呼')], row_snapshots=[[], []], dry=True)[0]
        self.assertEqual(len(self.notices(result)), 1)
        self.assertFalse(self.path.exists() and conv_store.load(self.path))

    def test_notified_item_back_in_view_is_processed(self):
        item = self.item(text='虚构HR招呼')
        self.run_hop(items=[item], row_snapshots=[[], []])
        self.assertIn('out_of_view_snap', conv_store.load(self.path)['id:fiction1'])
        rows = [{'conv_key': 'id:fiction1'}]
        result, opened, _, _, _ = self.run_hop(items=[item], row_snapshots=[rows, rows, rows], dry=True)
        self.assertEqual(result['out_of_view'], 0)
        self.assertEqual([c.kwargs['conv_key'] for c in opened.call_args_list], ['id:fiction1'])


# out-02：详情页重开一次；每种情形独立用例
class DetailReopenAdv(unittest.TestCase):
    def setUp(self):
        import test_out02_detail_unavailable as D
        self.case = D.DetailUnavailableTests('test_reopen_recovers_after_eight_non_json_probes')
        self.case.setUp()
        self.addCleanup(self.case.doCleanups)

    def test_login_wall_after_reopen_is_not_logged_in(self):
        br = self.case.browser(['not JSON'] * 8 + ['{"login":true}'])
        self.assertEqual(self.case.inspect(br), ('skip_not_logged_in', ''))
        self.assertEqual(br.nav.call_count, 2)
        br.js.assert_not_called()

    def test_already_chatted_after_reopen(self):
        br = self.case.browser(['not JSON'] * 8 + ['{"c":true}'])
        self.assertEqual(self.case.inspect(br)[0], 'skip_already_chatted')

    def test_parsed_state_without_button_does_not_reopen(self):
        br = self.case.browser(['{"s":false,"c":false}'] * 8)
        self.assertEqual(self.case.inspect(br)[0], 'skip_no_button')
        self.assertEqual(br.nav.call_count, 1)
        br.js.assert_not_called()


# conv-03w：我方原文只按「快照是原文开头」认定；每种情形独立用例
class OutOfViewOwnTextAdv(unittest.TestCase):
    def setUp(self):
        import test_conv03w_out_of_view_own_text as W
        self.case = W.OutOfViewOwnTextTests('test_hr_text_is_not_own')
        self.case.setUp()
        self.addCleanup(self.case.doCleanups)

    def test_hr_quoting_our_text_midway_is_still_notified(self):
        self.case.ledger(('send', '看到你们在招虚构经理，这行我干了7年'))
        result = self.case.outside_hop('您说看到你们在招虚构经理，这行我干了7年，能具体讲讲吗')
        self.assertEqual(len(self.case.notices(result)), 1)
        self.assertEqual(result['out_of_view_own'], 0)

    def test_voided_and_failed_ledger_texts_still_count_as_ours(self):
        # 账本里 voided/failed 的文字也是我方写的；快照能对上说明其实发出去了，不该当 HR 推送
        path = self.case.ledger(('conv_text', '虚构承接句，方便说下这个岗位主要的工作要求吗'))
        path.write_text(path.read_text(encoding='utf-8').replace('"sent"', '"voided"'), encoding='utf-8')
        result = self.case.outside_hop('虚构承接句，方便说下这个岗位主要的')
        self.assertEqual(self.case.notices(result), [])
        self.assertEqual(result['out_of_view_own'], 1)


# out-01：旧文案「A额度已满」容易被读成当日额度，不得回到源码里
class WindowQuotaWordingAdv(unittest.TestCase):
    def test_old_daily_sounding_wording_is_gone(self):
        src = (ROOT / 'scripts' / 'outbound.py').read_text(encoding='utf-8')
        self.assertNotIn('A额度已满', src)
        self.assertIn('本跳A配额已满', src)


# conv-03x：发简历诊断只记录，不能改变发送结果
class ResumeTraceAdvBase(unittest.TestCase):
    def setUp(self):
        import tempfile
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.dir = Path(tmp.name)
        self.cfg = {'paths': {'workdir': tmp.name, 'state': tmp.name + '/state.json'},
                    'conversation': {}, 'sending': {'min_gap_seconds': 0}}
        self.before = {'conv_key': 'id:fiction', 'messages': [{'id': 'm0', 'who': 'friend', 'text': '虚构'}]}
        self.br = Mock()
        self.br.json.return_value = {'ok': True}
        self.br.click.return_value = {}

    def send(self, polls):
        with patch.object(conv_actions.chat_dom, 'active_conversation', side_effect=polls), \
             patch.object(conv_actions, '_resume_dialog', return_value={}), \
             patch.object(conv_actions, '_resume_close_button', return_value={'present': False}), \
             patch.object(conv_actions.time, 'sleep'), patch.object(conv_actions.HC, 'trace'):
            return conv_actions.send_resume(self.br, 1, self.cfg, 'id:fiction', 'fiction-job')

    def rows(self):
        import json
        path = self.dir / 'resume_trace.jsonl'
        return [json.loads(x) for x in path.read_text(encoding='utf-8').splitlines()] if path.exists() else []


class ResumeTraceSwitchedConvAdv(ResumeTraceAdvBase):
    def test_switched_conversation_is_recorded_and_stays_pending(self):
        # 点完发简历后面板变成别的会话：照旧判 pending，诊断要留下「会话变了」的现场
        other = {'conv_key': 'id:other', 'messages': [{'id': 'x1', 'who': 'friend', 'text': '别家虚构消息'}]}
        self.assertEqual(self.send([self.before, other]), 'pending_verification')
        rows = self.rows()
        self.assertEqual([r['event'] for r in rows], ['start', 'click', 'poll', 'end'])
        self.assertIs(rows[2]['conv_key_matches'], False)
        self.assertEqual(rows[-1]['result'], 'pending_verification')


class ResumeTraceDuplicateAdv(ResumeTraceAdvBase):
    def test_duplicate_short_circuit_records_end_without_click(self):
        receipt = {'conv_key': 'id:fiction', 'messages': self.before['messages'] + [
            {'id': 'r1', 'who': 'unknown', 'text': '您的附件简历 虚构 已发送给Boss'}]}
        self.assertEqual(self.send([self.before, receipt]), 'sent')
        self.br.click.reset_mock()
        (self.dir / 'resume_trace.jsonl').unlink()
        self.assertEqual(self.send([self.before]), 'duplicate')
        self.assertEqual(self.br.click.call_count, 0)
        self.assertEqual([(r['event'], r.get('result')) for r in self.rows()], [('end', 'duplicate')])


class ResumeTraceExceptionAdv(ResumeTraceAdvBase):
    def test_exception_still_propagates_and_is_recorded(self):
        # 诊断不能吞掉原有异常
        def polls():
            yield self.before
            raise ValueError('虚构读取异常')
        with self.assertRaises(ValueError):
            self.send(polls())
        self.assertEqual(self.rows()[-1]['result'], 'error:ValueError')


class ResumeTraceUnwritableAdv(ResumeTraceAdvBase):
    def test_trace_path_is_directory_success_unchanged(self):
        # 诊断文件位置被目录占住（写不进去），成功发送仍判 sent，账本照常记 sent
        (self.dir / 'resume_trace.jsonl').mkdir()
        receipt = {'conv_key': 'id:fiction', 'messages': self.before['messages'] + [
            {'id': 'r1', 'who': 'unknown', 'text': '您的附件简历 虚构 已发送给Boss'}]}
        self.assertEqual(self.send([self.before, receipt]), 'sent')
        self.assertEqual(self.send([self.before]), 'duplicate')


# conv-03y：把真实脚本放进按 BOSS 页面结构搭的假 DOM 里跑（node），而不只查字符串
_POPOVER_DOM = r"""
const input = JSON.parse(process.argv[1]);
const getComputedStyle = e => ({display: e.hidden ? 'none' : 'block'});
function el(tag, cls, text, kids, extra) {
  const e = Object.assign({tagName: tag.toUpperCase(), classList: cls ? cls.split(' ') : [], text: text || '',
    children: kids || [], id: '', parentElement: null, hidden: false, attrs: {}}, extra || {});
  e.children.forEach(c => c.parentElement = e);
  return e;
}
const vis = e => { for (let x = e; x; x = x.parentElement) if (x.hidden) return false; return true; };
function finish(e) {
  Object.defineProperty(e, 'offsetParent', {get: () => vis(e) ? {} : null});
  Object.defineProperty(e, 'innerText', {get: () => e.text + e.children.map(c => c.innerText).join('')});
  e.getAttribute = a => e.attrs[a] === undefined ? null : e.attrs[a];
  Object.defineProperty(e, 'outerHTML', {get: () => '<' + e.tagName + ' class="' + e.classList.join(' ') + '">' + e.innerText});
  e.removeAttribute = a => { if (a === 'id') e.id = ''; };
  e.contains = o => e.children.some(c => c === o || c.contains(o));
  e.all = () => e.children.flatMap(c => [c, ...c.all()]);
  e.querySelectorAll = s => e.all().filter(x => matches(x, s, e));
  e.querySelector = s => e.querySelectorAll(s)[0] || null;
  e.closest = s => { for (let x = e; x; x = x.parentElement) if (matches(x, s)) return x; return null; };
  e.children.forEach(finish);
  return e;
}
function one(x, part) {
  const m = part.match(/^([a-z]*)((?:\.[\w-]+)*)(?:\[([\w-]+)="([^"]*)"\])?$/);
  if (!m) return false;
  if (m[1] && x.tagName !== m[1].toUpperCase()) return false;
  if (m[2] && !m[2].split('.').filter(Boolean).every(c => x.classList.includes(c))) return false;
  if (m[3] && x.attrs[m[3]] !== m[4]) return false;
  return true;
}
function matches(x, sel, root) {
  return sel.split(',').some(alt => {
    const parts = alt.trim().split(/\s+/);
    if (!one(x, parts[parts.length - 1])) return false;
    let i = parts.length - 2;
    for (let p = x.parentElement; p && i >= 0 && p !== root; p = p.parentElement) if (one(p, parts[i])) i--;
    return i < 0;
  });
}
const sure = el('span', 'btn-v2 btn-sure-v2', '确定');
const resumePop = el('div', 'panel-resume sentence-popover', 'Boss确认后，该附件简历将直接发送至对方邮箱',
  [el('div', 'btns', '', [el('span', 'btn-v2 btn-outline-v2', '取消'), sure])], {hidden: !input.resumeOpen});
const contactSure = el('span', 'btn-v2 btn-sure-v2', '确定');
const contactPop = el('div', 'sentence-popover panel-contact', '确认与对方交换电话吗？简历',
  [el('div', 'btns', '', [el('span', 'btn-v2 btn-outline-v2', '取消'), contactSure])], {hidden: !input.contactOpen});
const resumeBtn = el('div', 'toolbar-btn tooltip tooltip-top', '发简历');
const resumeRoot = el('div', 'toolbar-btn-content', '', [resumeBtn, resumePop]);
if (input.vue) resumeRoot.__vue__ = {conversation$: input.vue};
const contactRoot = el('div', 'toolbar-btn-content btn-contact toolbar-btn tooltip tooltip-top', '换电话', [contactPop]);
const header = el('div', 'top-info-content', '', [], {attrs: {'data-conversation-id': 'fiction'}});
const panel = el('div', 'chat-conversation', '', [header, el('div', 'chat-controls', '', [resumeRoot, contactRoot])]);
finish(panel);
const document = {querySelectorAll: s => s === '.chat-conversation' ? [panel] : panel.querySelectorAll(s)};
const result = eval(input.expr);
process.stdout.write(JSON.stringify({result, sure: sure.id, contact: contactSure.id, btn: resumeBtn.id}));
"""


class ResumePopoverDomAdvBase(unittest.TestCase):
    def run_js(self, func, **state):
        import json, subprocess
        br = Mock()
        br.json.return_value = {}
        func(br, 1, 'id:fiction')
        payload = dict(expr=br.json.call_args.args[1], **state)
        run = subprocess.run(['node', '-e', _POPOVER_DOM, json.dumps(payload)],
                             capture_output=True, text=True, check=True)
        return json.loads(run.stdout)


class ResumePopoverBothOpenAdv(ResumePopoverDomAdvBase):
    def test_resume_confirm_marked_contact_never(self):
        # 简历气泡和换电话气泡同时可见：只标简历的「确定」，换电话的绝不能标
        out = self.run_js(conv_actions._resume_dialog, resumeOpen=True, contactOpen=True)
        self.assertIs(out['result'].get('confirm'), True)
        self.assertEqual(out['sure'], 'bh-conv-resume-confirm')
        self.assertEqual(out['contact'], '')


class ResumePopoverOnlyContactAdv(ResumePopoverDomAdvBase):
    def test_only_contact_popover_open_finds_nothing(self):
        out = self.run_js(conv_actions._resume_dialog, resumeOpen=False, contactOpen=True)
        self.assertFalse(out['result'].get('confirm'))
        self.assertEqual(out['contact'], '')
        self.assertEqual(out['sure'], '')


class ResumePopoverClosedAdv(ResumePopoverDomAdvBase):
    def test_closed_resume_popover_is_ignored(self):
        out = self.run_js(conv_actions._resume_dialog, resumeOpen=False, contactOpen=False)
        self.assertEqual(out['result'], {})


class ResumeButtonVueAdv(ResumePopoverDomAdvBase):
    def test_vue_preconditions_on_component_root(self):
        # 组件实例挂在按钮的父元素（组件根）上，这是真机结构
        cases = (({'bothTalked': False, 'exchangeOptGray': False, 'requestResume': 0}, False, 'resume_not_both_talked'),
                 ({'bothTalked': True, 'requestResume': 1}, False, 'resume_requested'),
                 ({'bothTalked': False, 'exchangeOptGray': True, 'requestResume': 0}, True, None),
                 ({'bothTalked': True, 'requestResume': 0}, True, None),
                 (None, True, None))
        for vue, ok, reason in cases:
            with self.subTest(vue=vue):
                out = self.run_js(conv_actions._resume_button, vue=vue)
                self.assertEqual(out['result'].get('ok'), ok)
                self.assertEqual(out['result'].get('reason'), reason)
                self.assertEqual(out['btn'], 'bh-conv-resume' if ok else '')


# conv-03z：附件简历卡片算回执（页面不再出「附件简历已发送」）
class ResumeCardReceiptAdvBase(ResumeTraceAdvBase):
    CARD = '虚构简历.pdf\n点击预览附件简历'

    def after(self, who):
        return {'conv_key': 'id:fiction', 'messages': self.before['messages'] + [
            {'id': 'blank', 'who': who, 'text': ''}, {'id': 'card', 'who': who, 'text': self.CARD}]}


class ResumeCardDirectSendAdv(ResumeCardReceiptAdvBase):
    def test_card_without_popover_is_sent_and_never_confirms(self):
        # 前端不弹确认、直接发出（handleConfirm 路径）：首轮轮询就见卡片 → sent，只点一次发简历
        self.assertEqual(self.send([self.before, self.after('unknown')]), 'sent')
        self.assertEqual([c.args[1] for c in self.br.click.call_args_list], ['#bh-conv-resume'])
        self.assertEqual(self.rows()[-1]['result'], 'sent')


class ResumeCardMineAdv(ResumeCardReceiptAdvBase):
    def test_card_classified_mine_is_also_receipt(self):
        # 刷新后卡片可能被归为我方气泡，同样算回执
        self.assertEqual(self.send([self.before, self.after('mine')]), 'sent')


class ResumeCardFriendQuoteAdv(ResumeCardReceiptAdvBase):
    def test_hr_quoting_card_text_is_not_receipt(self):
        polls = [self.before] + [self.after('friend')] * 30
        self.assertEqual(self.send(polls), 'pending_verification')


# ---- conv-05 对抗：岗位名读取与判级 ----
import test_conv05_inbound_title_grade as _c05
import chrome_mcp as _CM
import json


class Conv05TitleAdvBase(_c05.InboundTitleTests):
    def hop_raw(self, value, dry=False):
        """页面 eval 原样返回 value（不经 parse），模拟调用方拿到的任意类型。"""
        import types
        fake = types.SimpleNamespace(parse_json_payload=lambda *a, **k: value)
        with patch.object(_c05, 'CM', fake):  # 只换替身浏览器 br.json 的返回，不碰 run 自己的解析
            return self.hop('占位', dry=dry)


class Conv05NonStringTitleAdv(Conv05TitleAdvBase):
    def test_non_string_or_blank_title_takes_retry(self):
        for value in ({'title': '虚构商家bd'}, ['虚构商家bd'], 42, True, '   \n', None):
            with self.subTest(value=value):
                self.path.unlink(missing_ok=True)
                _, grade, send, resume = self.hop_raw(value)
                row = self.saved()
                self.assertEqual(row['title'], '')
                self.assertEqual(row['title_failures'], 1)
                self.assertNotIn('job_ok', row)
                grade.assert_not_called(); send.assert_not_called(); resume.assert_not_called()


class Conv05RecoverAfterFailuresAdv(Conv05TitleAdvBase):
    def test_title_found_on_third_hop_resets_and_grades(self):
        self.hop(''); self.hop('')
        self.assertEqual(self.saved()['title_failures'], 2)
        result, grade, _, _ = self.hop('虚构商家bd')
        row = self.saved()
        self.assertEqual(row['title_failures'], 0)
        self.assertNotEqual(row['status'], 'manual')
        self.assertTrue(row['job_ok'])
        self.assertEqual(grade.call_args.args[0]['jobName'], '虚构商家bd')
        self.assertFalse(any('岗位名读不到' in n for n in result['notifications']))


class Conv05PayloadQuotedObjectAdv(unittest.TestCase):
    def test_string_that_looks_like_object_text_stays_string(self):
        # 岗位名本身含花括号/引号：不能被误解析成 dict，也不能丢
        for title in ('虚构{商家}bd', '虚构"商家"bd', '[虚构]商家bd'):
            with self.subTest(title=title):
                raw = '<page-content untrusted="true">\n%s\n</page-content>' % json.dumps(title, ensure_ascii=False)
                self.assertEqual(_CM.parse_json_payload(raw, {}), title)


# ---- conv-05b 对抗：非对象返回绝不导致点击确认 ----
class Conv05bStringDialogNeverConfirmsAdv(unittest.TestCase):
    def test_success_looking_text_from_dialog_never_clicks_confirm(self):
        key = 'id:fiction-guard'
        before = {'conv_key': key, 'messages': []}
        for payload in ('{confirm:true}', 'confirm', '<div class="panel-contact">确定</div>', True):
            with self.subTest(payload=payload), patch.object(conv_actions.time, 'sleep'), \
                    patch.object(conv_actions.HC, 'trace'):
                br = Mock()
                br.json.side_effect = [before] * 8 + [payload] + [before] * 8
                br.click.return_value = {'ok': True}
                self.assertEqual(conv_actions._send_resume_and_confirm(
                    br, 1, {}, key, before, '#fiction-resume'), 'pending_verification')
                # 只点过发简历按钮本身，确认/换电话一律没点
                self.assertEqual([c.args[1] for c in br.click.call_args_list], ['#fiction-resume'])


class Conv05bMixedRowsAdv(unittest.TestCase):
    def test_one_non_object_row_drops_whole_list(self):
        import chat_dom
        br = Mock()
        br.json.return_value = [{'conv_key': 'id:a', 'text': '虚构'}, '虚构文本']
        self.assertEqual(chat_dom.conversation_rows(br, 1), [])


# ---- conv-06：40 行外滚动揭示（Claude 对抗用例，每种情形独立 class）----
import test_conv06_out_of_view_reveal as _c06
import subprocess as _sp


class Conv06AdvBase(_c06.RevealTests):
    """复用 conv-06 的离线夹具；不继承其 test_ 方法。"""
    pass


for _name in [n for n in dir(_c06.RevealTests) if n.startswith('test_')]:
    setattr(Conv06AdvBase, _name, None)


class Conv06WindowShowsSomeoneElseAdv(Conv06AdvBase):
    def test_reveal_ok_but_window_has_other_conversation(self):
        c = self.module()
        item = self.item()
        stranger = [{'conv_key': 'id:fiction-stranger', 'receipt': False}]
        with patch.object(c.chat_dom, 'reveal_conversation', return_value={'ok': True, 'index': 70}), \
             patch.object(c.chat_dom, 'reset_list_scroll') as reset:
            result, opened, calls, send_text, _ = self.run_hop(
                items=[item], row_snapshots=lambda *a: list(stranger))
        opened.assert_not_called()
        send_text.assert_not_called()
        self.assertEqual(result['reveal_failed'], 1)
        self.assertEqual(result['revealed'], 0)
        self.assertEqual(sum('📭' in line for line in self.out), 1)
        reset.assert_called_once()


class Conv06DisplacedInViewCandidateAdv(Conv06AdvBase):
    def test_in_view_candidate_pushed_out_by_reveal_is_revealed_again(self):
        c = self.module()
        near = self.item(1, age=1)      # 40 行内，较新
        far = self.item(2, age=2)       # 40 行外
        window = [[{'conv_key': near['conv_key'], 'receipt': False}]]
        def reveal(br, tab, item):
            window[0] = [{'conv_key': item['conv_key'], 'receipt': False}]
            return {'ok': True, 'index': 70}
        with patch.object(c.chat_dom, 'reveal_conversation', side_effect=reveal) as rev, \
             patch.object(c.chat_dom, 'reset_list_scroll'):
            result, opened, calls, _, _ = self.run_hop(
                items=[near, far], row_snapshots=lambda *a: list(window[0]))
        self.assertEqual(result['row_missing'], 0)
        self.assertEqual(result['processed'], 2)
        self.assertEqual(result['revealed'], 1)
        self.assertEqual([k.kwargs['conv_key'] for k in opened.call_args_list],
                         [near['conv_key'], far['conv_key']])
        self.assertIn(near['conv_key'], [a.args[2]['conv_key'] for a in rev.call_args_list])


class Conv06ResetErrorIgnoredAdv(Conv06AdvBase):
    def test_reset_bridge_error_does_not_break_hop(self):
        c = self.module()
        item = self.item()
        visible = [{'conv_key': item['conv_key'], 'receipt': False}]
        self.br.json.side_effect = RuntimeError('虚构复位失败')
        with patch.object(c.chat_dom, 'reveal_conversation', return_value={'ok': True}):
            result, opened, _, _, _ = self.run_hop(items=[item], row_snapshots=[[], visible, visible, visible])
        self.assertTrue(result['ok'])
        self.assertEqual(result['revealed'], 1)
        self.assertEqual(result['processed'], 1)
        self.assertTrue(self.br.json.called)


class Conv06TruthyNonTrueOkAdv(Conv06AdvBase):
    def test_ok_must_be_literal_true(self):
        for ok in ('true', 1, 'ok'):
            with self.subTest(ok=ok):
                self.setUp()
                (result, opened, calls, _, _), _, _ = self.hop(outcome={'ok': ok, 'index': 70})
                opened.assert_not_called()
                self.assertFalse(calls)
                self.assertEqual(result['reveal_failed'], 1)


class Conv06EmptyIdentityNeverMatchesAdv(Conv06AdvBase):
    def test_both_sides_missing_job_id_is_not_a_match(self):
        dom = self.module().chat_dom
        item = dict(self.item(), encryptJobId='')
        br = Mock()
        br.json.return_value = {}
        dom.reveal_conversation(br, 10, item)
        script = br.json.call_args.args[1]
        harness = r"""
const {script, rows} = JSON.parse(process.argv[1]);
const events = [];
const v = {$options:{name:'virtual-list'}, dataSources:rows,
  scrollToIndex(i){events.push(i);}, onScroll(){}, async $nextTick(){}};
global.document = {querySelector(){return {__vue__:v};}};
global.Event = class {constructor(t){this.type=t;}};
(async () => {console.log(JSON.stringify({result: await eval(script), events}));})();
"""
        run = _sp.run(['node', '-e', harness, json.dumps({'script': script, 'rows': [dict(item)]})],
                      capture_output=True, text=True, check=True)
        out = json.loads(run.stdout)
        self.assertEqual(out['result'], {'ok': False, 'reason': 'not_found'})
        self.assertEqual(out['events'], [])
