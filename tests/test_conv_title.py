"""conv-03j regression tests: fictional data, temporary files, offline boundaries."""
import copy
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from contextlib import ExitStack
from datetime import datetime
from unittest.mock import Mock, patch
from zoneinfo import ZoneInfo

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
import conversation as C
import conv_store
import hunt_state


class ConversationTitleTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.path = Path(tmp.name) / 'conversations.json'
        self.state_path = Path(tmp.name) / 'state.json'
        self.state_path.write_text('{}', encoding='utf-8')
        self.cfg = {'conversation': {'enabled': True}, 'schedule': {'timezone': 'Asia/Shanghai'},
                    'paths': {'workdir': tmp.name, 'raw_dir': tmp.name, 'state': str(self.state_path)}}
        self.now = datetime(2026, 10, 29, 12, tzinfo=ZoneInfo('Asia/Shanghai'))
        self.ts = self.now.timestamp()
        self.item = {'conv_key': 'id:fiction-title', 'encryptJobId': 'fiction-job',
                     'brandName': '虚构公司', 'name': '虚构HR',
                     'lastMessageInfo': {'showText': '期望薪资多少', 'msgTime': (self.ts-10)*1000}}
        self.br = Mock()
        self.br.nav.return_value = {}
        self.br.new_tab.side_effect = [10, 11, 12]
        self.br.json.return_value = ''
        self.out = []

    def record(self, title='', origin='reply'):
        return conv_store.new_record(self.item['conv_key'], origin, 'fiction-job',
                                     '虚构公司', '虚构HR', title, self.ts)

    def saved(self):
        return conv_store.load(self.path)[self.item['conv_key']]

    def hop(self, state=None, jobs=(), inbound=False, text='期望薪资多少', dry=False,
            state_error=None, items=None, receipt='sent', extracted=None, card=False):
        items = items or [self.item]
        history = ([] if inbound else [{'who': 'mine', 'id': 'm0', 'text': '虚构招呼'}]) + [
            {'who': 'friend', 'id': 'm1', 'text': text, 'ts': self.ts-10}]
        if card:
            history.insert(0, {'who': 'friend', 'id': 'fiction-card', 'text': '我想要一份您的附件简历'})
        self.br.network.side_effect = lambda tab, url, index, **kw: {'text': json.dumps(
            {'code': 0, 'zpData': {'result': items if index == 0 else []}})}
        if state is not None:
            self.state_path.write_text(json.dumps(state), encoding='utf-8')
        state_before = self.state_path.read_bytes()
        with ExitStack() as stack:
            def p(obj, name, **kw):
                return stack.enter_context(patch.object(obj, name, **kw))
            load = p(hunt_state, 'load_state', wraps=hunt_state.load_state, side_effect=state_error)
            load_jobs = p(C.hunt_filter, 'load_jobs', return_value=list(jobs))
            p(C.chat_dom, 'conversation_rows', return_value=[{'conv_key': it['conv_key']} for it in items])
            p(C.chat_dom, 'open_by_index', return_value=('ok', '', history))
            p(C.jd_page, 'read_jd', return_value='虚构岗位职责')
            p(C.CM, 'cleanup_browser', return_value=2)
            p(C.hunt_filter, 'judge', return_value=('reject', '虚构不匹配'))
            p(C.hunt_filter, 'grade_with_jd', return_value={'grade': 'B'})
            p(C.conv_llm, 'opener', return_value='虚构开场，')
            p(C.conv_llm, 'extract', return_value=extracted or {})
            p(C.conv_llm, 'lead', return_value='')
            p(C.conv_llm, 'answer', return_value='')
            p(C.conv_actions, 'send_text', return_value=receipt)
            p(C.conv_actions, 'send_resume', return_value=receipt)
            p(C.conv_actions, 'LAST_RESUME_BUBBLES', new=2)
            result = C.run(self.cfg, self.br, self.out, now=self.now, dry=dry)
        self.assertEqual(self.state_path.read_bytes(), state_before, 'state must remain read-only')
        return result, load.call_count, load_jobs.call_count

    def test_sent_title_is_saved_and_notified_without_list_title(self):
        result, _, _ = self.hop(state={'sent': {'fiction-job': {'name': ' 虚构投递经理 '}}})
        self.assertEqual(self.saved()['title'], '虚构投递经理')
        self.assertIn('虚构公司｜虚构投递经理｜虚构HR', result['notifications'][0])

    def test_source_priority_and_whitespace_fallback(self):
        cases = [
            ({}, [{'encryptJobId': 'fiction-job', 'jobName': ' 虚构库经理 '}], {}, '虚构库经理'),
            ({'sent': {'fiction-job': {'name': '虚构投递经理'}}},
             [{'encryptJobId': 'fiction-job', 'jobName': '虚构库经理'}], {}, '虚构投递经理'),
            ({'b_pool': {'fiction-job': {'title': ' 虚构池经理 '}}}, [], {}, '虚构池经理'),
            ({'sent': {'fiction-job': {'name': '  '}}, 'b_pool': {'fiction-job': {'title': '虚构池经理'}}},
             [{'encryptJobId': 'fiction-job', 'jobName': ' 虚构库经理 '}], {}, '虚构库经理'),
            ({}, [], {'jobName': ' 虚构列表经理 ', 'title': '虚构备用经理'}, '虚构列表经理'),
            ({}, [], {'jobName': '  ', 'title': ' 虚构备用经理 '}, ''),
        ]
        for state, jobs, fields, expected in cases:
            with self.subTest(expected=expected):
                self.path.unlink(missing_ok=True)
                self.br.new_tab.side_effect = [10]
                self.item.pop('jobName', None)
                self.item.pop('title', None)
                self.item.update(fields)
                self.hop(state=state, jobs=jobs)
                self.assertEqual(self.saved()['title'], expected)

    def test_hr_title_alone_is_not_saved_as_job_title(self):
        self.item['title'] = '虚构招聘总监'
        result, _, _ = self.hop()
        self.assertEqual(self.saved()['title'], '')
        self.assertIn('虚构公司｜岗位未知｜虚构HR', result['notifications'][0])

    def test_archived_hr_title_is_healed_from_existing_jd_tab(self):
        for old, current in [('虚构HR头衔', '虚构HR头衔'),
                             ('  虚构HR头衔 ', ' 虚构HR头衔  ')]:
            with self.subTest(old=old, current=current):
                self.br.new_tab.side_effect = [10, 11]
                self.br.json.return_value = '虚构岗位甲'
                self.item['title'] = current
                conv_store.save(self.path, {self.item['conv_key']:
                                           self.record(old, origin='inbound')})
                result, _, _ = self.hop(inbound=True, text='发份简历看看')
                self.assertEqual(self.saved()['title'], '虚构岗位甲')
                self.assertIn('虚构公司｜虚构岗位甲｜虚构HR', result['notifications'][0])
        self.assertEqual(self.br.new_tab.call_count, 4, 'only chat and existing JD tabs per hop')

    def test_archived_job_title_different_from_hr_title_is_preserved(self):
        self.item['title'] = '虚构HR头衔'
        self.br.json.return_value = '虚构岗位甲'
        conv_store.save(self.path, {self.item['conv_key']:
                                   self.record('虚构岗位乙', origin='inbound')})
        self.hop(inbound=True, text='发份简历看看')
        self.assertEqual(self.saved()['title'], '虚构岗位乙')

    def test_sent_title_has_priority_when_healing_hr_title(self):
        self.item.update(title='虚构HR头衔', jobName='虚构列表岗位')
        self.br.json.return_value = '虚构页面岗位'
        conv_store.save(self.path, {self.item['conv_key']:
                                   self.record('虚构HR头衔', origin='inbound')})
        self.hop(state={'sent': {'fiction-job': {'name': '虚构投递岗位'}},
                        'b_pool': {'fiction-job': {'title': '虚构池岗位'}}},
                 jobs=[{'encryptJobId': 'fiction-job', 'jobName': '虚构库岗位'}],
                 inbound=True, text='发份简历看看')
        self.assertEqual(self.saved()['title'], '虚构投递岗位')

    def test_empty_archive_title_uses_unknown_in_manual_notification(self):
        result, _, _ = self.hop()
        self.assertEqual(self.saved()['title'], '')
        self.assertIn('虚构公司｜岗位未知｜虚构HR', result['notifications'][0])
        self.assertNotIn('｜｜', '\n'.join(result['notifications']))

    def test_old_empty_title_backfilled_but_existing_title_preserved(self):
        for old, expected in [('', '虚构新经理'), ('  ', '虚构新经理'), ('虚构原经理', '虚构原经理')]:
            with self.subTest(old=old):
                self.br.new_tab.side_effect = [10]
                conv_store.save(self.path, {self.item['conv_key']: self.record(old)})
                result, _, _ = self.hop(state={'sent': {'fiction-job': {'name': '虚构新经理'}}})
                self.assertEqual(self.saved()['title'], expected)
                self.assertIn(expected, result['notifications'][0])

    def test_state_failure_falls_back_to_job_library(self):
        result, count, _ = self.hop(state_error=OSError('虚构读取失败'),
                                  jobs=[{'encryptJobId': 'fiction-job', 'jobName': '虚构库经理'}])
        self.assertTrue(result['ok'])
        self.assertEqual(count, 1)
        self.assertEqual(self.saved()['title'], '虚构库经理')

    def test_multiple_conversations_load_state_and_jobs_once(self):
        other = copy.deepcopy(self.item)
        other.update(conv_key='id:fiction-other', encryptJobId='fiction-other-job')
        result, state_count, jobs_count = self.hop(items=[self.item, other])
        self.assertEqual(result['processed'], 2)
        self.assertEqual(state_count, 1)
        self.assertEqual(jobs_count, 1)

    def jd_dom(self, h1, page_title):
        fixture = r'''
const data = JSON.parse(process.argv[2]);
const document = {
  querySelector: selector => selector === '.job-banner h1,.job-name,h1' && data.h1 !== null
    ? {innerText: data.h1} : null,
  title: data.title
};
console.log(JSON.stringify(eval(process.argv[1])));
'''
        def evaluate(tab, expression, **kw):
            self.assertEqual(tab, 11, 'title must use the already opened JD tab')
            run = subprocess.run(['node', '-e', fixture, expression,
                                  json.dumps({'h1': h1, 'title': page_title})],
                                 capture_output=True, text=True, check=True)
            return json.loads(run.stdout)
        self.br.json.side_effect = evaluate

    def test_inbound_reads_h1_then_document_title_on_same_jd_tab(self):
        for h1, page_title, expected in [
            (' 虚构页经理 ', '「虚构备用经理招聘」_虚构公司招聘-BOSS直聘', '虚构页经理'),
            ('  ', '「虚构经理招聘」_虚构公司招聘-BOSS直聘', '虚构经理'),
            (None, '「虚构经理招聘」_虚构公司招聘-BOSS直聘', '虚构经理'),
        ]:
            with self.subTest(h1=h1, page_title=page_title):
                self.path.unlink(missing_ok=True)
                self.br.new_tab.side_effect = [10, 11]
                self.br.json.reset_mock()
                self.jd_dom(h1, page_title)
                result, _, _ = self.hop(inbound=True, text='发份简历看看')
                self.assertEqual(self.saved()['title'], expected)
                self.assertEqual(self.saved()['jd'], '虚构岗位职责')
                self.assertEqual(self.saved()['status'], 'engaged')  # conv-03n：不过筛也问问题
                self.assertEqual(self.br.new_tab.call_args_list[-1].args, ('about:blank',))
                self.assertEqual(self.br.json.call_count, 1)
                self.assertIn(expected or '岗位未知', result['notifications'][0])

    def test_inbound_empty_page_title_takes_retry_path(self):
        self.br.new_tab.side_effect = [10, 11]
        self.jd_dom('', '虚构公司招聘-BOSS直聘')
        result, _, _ = self.hop(inbound=True, text='发份简历看看')
        row = self.saved()
        self.assertEqual(row['title'], '')
        self.assertEqual(row['title_failures'], 1)
        self.assertNotIn('jd', row)
        self.assertIsNone(row.get('job_ok'))
        self.assertNotEqual(row['status'], 'manual')
        self.assertEqual(result['notifications'], [])

    def test_jd_title_error_does_not_interrupt_jd_processing(self):
        # conv-05：页面读取抛错 → 岗位名为空 → 重试路径；同跳其他会话照常处理
        other = {'conv_key': 'id:fiction-other', 'encryptJobId': 'fiction-job-2',
                 'brandName': '虚构二公司', 'name': '虚构二HR',
                 'lastMessageInfo': {'showText': '期望薪资多少', 'msgTime': (self.ts-10)*1000}}
        self.br.json.side_effect = RuntimeError('虚构页面读取失败')
        self.hop(inbound=True, text='发份简历看看', items=[self.item, other])
        records = conv_store.load(self.path)
        row = records[self.item['conv_key']]
        self.assertEqual(row['title'], '')
        self.assertEqual(row['title_failures'], 1)
        self.assertNotIn('jd', row)
        self.assertIsNone(row.get('job_ok'))
        self.assertIn('id:fiction-other', records)
        self.assertEqual(records['id:fiction-other']['title_failures'], 1)
        self.assertEqual(self.br.json.call_count, 2)

    def test_jd_title_does_not_override_known_title(self):
        self.br.json.return_value = '虚构页经理'
        self.hop(state={'sent': {'fiction-job': {'name': '虚构投递经理'}}},
                 inbound=True, text='发份简历看看')
        self.assertEqual(self.saved()['title'], '虚构投递经理')

    def test_dry_backfill_only_changes_in_memory_notification(self):
        conv_store.save(self.path, {self.item['conv_key']: self.record(origin='inbound')})
        before = self.path.read_bytes()
        self.hop(state={'b_pool': {'fiction-job': {'title': '虚构池经理'}}},
                 inbound=True, text='发份简历看看', dry=True)
        self.assertEqual(self.path.read_bytes(), before)
        self.assertIn('🧪 📎 不过筛但要简历 虚构公司｜虚构池经理｜虚构HR', self.out)

    def test_unknown_title_in_remaining_notification_paths(self):
        for scenario in ('pending', 'duplicate', 'resume_manual', 'complete', 'ignored', 'dry_ignored'):
            with self.subTest(scenario=scenario):
                self.path.unlink(missing_ok=True)
                self.br.new_tab.side_effect = [10, 11]
                self.out.clear()
                self.cfg['conversation']['auto_accept_resume_request'] = scenario != 'resume_manual'
                extracted = None
                if scenario == 'complete':
                    row = self.record()
                    row.update(status='round2', resume_sent_ts=self.ts-100,
                               asked_round1_ts=self.ts-100, asked_round2_ts=self.ts-100)
                    conv_store.save(self.path, {self.item['conv_key']: row})
                    extracted = dict(zip(C.conv_rules.ANSWER_KEYS, ['虚构工作', '虚构薪资', '两轮', '可以']))
                ignored = scenario in ('ignored', 'dry_ignored')
                # conv-05：inbound 空岗位名不再进入不过筛；给 JD 页一个岗位名，judge 替身仍判不过
                self.br.json.return_value = '虚构岗位' if ignored else ''
                result, _, _ = self.hop(
                    text='虚构回答' if scenario == 'complete' else '发份简历看看',
                    inbound=scenario in ('ignored', 'dry_ignored'), dry=scenario == 'dry_ignored',
                    receipt='pending_verification' if scenario == 'pending' else 'sent',
                    card=scenario == 'resume_manual', extracted=extracted)
                lines = [line for line in self.out if '虚构公司｜' in line]
                self.assertTrue(lines, scenario)
                shown = '虚构岗位' if ignored else '岗位未知'
                self.assertTrue(all('虚构公司｜%s｜虚构HR' % shown in line for line in lines), lines)
                self.assertNotIn('｜｜', '\n'.join(self.out))
                if scenario == 'dry_ignored':
                    self.assertFalse(self.path.exists())
                else:
                    self.assertEqual(self.saved()['title'], '虚构岗位' if ignored else '')


if __name__ == '__main__':
    unittest.main()
