"""status-02：一次性放弃 72h 外的旧会话积压（ignored 标记），有新消息自动恢复。虚构数据、临时目录。"""
import json
import unittest
from unittest.mock import Mock, patch

import test_broadcast_visibility_20261029 as V
from test_phase_a import Base

B = V.B
import chat_dom as CD
import hunt_filter as HF
import hunt_state as HS

OLD = {'status': 'unknown', 'last_ts': 1000.0, 'ignored': True, 'ignored_last_ts': 1000.0}


class ReportAndRejectTests(Base):
    def test_unanswered_report_skips_ignored(self):
        st = {'conv_state': {'pair:旧|甲': dict(OLD),
                             'pair:旧|乙': dict(OLD, status='pending_verify'),
                             'pair:新|丙': {'status': 'unknown', 'last_ts': 5000.0},
                             'pair:新|丁': {'status': 'waiting_me', 'last_ts': 5000.0}}}
        counts = B.unanswered_report(st, now=6000)['counts']
        self.assertEqual((counts['unknown'], counts['pending_verify'], counts['waiting_me']), (1, 0, 1))

    def test_stale_rejects_skip_ignored(self):
        st = {'conv_state': {'pair:旧|甲': dict(OLD, status='stale', job_id='jo', brand='虚构旧'),
                             'pair:新|乙': {'status': 'stale', 'last_ts': 1000.0, 'job_id': 'jn', 'brand': '虚构新'}}}
        rows = B.collect_stale_rejects(self.cfg, st, now=1000000)
        self.assertEqual(set(rows), {'jn'})


class ScanTests(Base):
    def run_scan(self, st, rows, items=None):
        reads = []
        def read(br, tab, row):
            reads.append(row['conv_key'])
            return {'side': 'them', 'ts': row.get('last_ts') or 6000, 'text': '虚构新消息'}
        with patch.object(CD, 'conversation_rows', return_value=rows), \
                patch.object(B, 'read_status_message', side_effect=read):
            result = B.scan_conv_states(self.cfg, Mock(), 1, st, now=7000, items=items)
        return result, reads

    def test_ignored_without_new_message_is_not_scanned_or_counted(self):
        st = {'conv_state': {'pair:旧|甲': dict(OLD), 'pair:新|乙': {'status': 'unknown', 'last_ts': 5000.0}}}
        result, reads = self.run_scan(st, [{'conv_key': 'pair:旧|甲'}, {'conv_key': 'pair:新|乙'}])
        self.assertEqual(reads, ['pair:新|乙'])
        self.assertEqual(result['skipped_failures'], 0)
        self.assertTrue(st['conv_state']['pair:旧|甲']['ignored'])

    def test_new_message_revives_ignored_conversation(self):
        st = {'conv_state': {'pair:旧|甲': dict(OLD)}}
        items = {'j1': {'brandName': '旧', 'name': '甲', 'lastTS': 6000000}}
        rows = [{'conv_key': 'pair:旧|甲', 'brand': '旧', 'boss': '甲'}]
        result, reads = self.run_scan(st, rows, items)
        self.assertEqual(reads, ['pair:旧|甲'])
        self.assertNotIn('ignored', st['conv_state']['pair:旧|甲'])
        self.assertEqual(st['conv_state']['pair:旧|甲']['status'], 'waiting_me')


class InboxReaddTests(unittest.TestCase):
    setUp = V.BroadcastTests.setUp
    save = V.BroadcastTests.save

    def run_inbox(self, st, msg_ms):
        item = {'encryptJobId': 'jx', 'brandName': '旧', 'name': '甲',
                'lastMessageInfo': {'showText': '虚构旧消息', 'msgTime': msg_ms}}
        self.browser.network.return_value = {'text': json.dumps({'code': 0, 'zpData': {'result': [item]}})}
        with patch.object(B, 'scan_conv_states', return_value=dict(checked=0, failed=0, skipped_failures=0, deadline=False)), \
                patch.object(HF, 'load_jobs', return_value=[]), \
                patch.object(CD, 'source_conversation_key', return_value='pair:旧|甲'):
            B.inbox(self.cfg, self.browser, 1, st, [])
        return st['conv_state']['pair:旧|甲']

    def test_inbox_keeps_ignored_when_snapshot_not_newer(self):
        st = HS.load_state(self.cfg)
        st['conv_state'] = {'pair:旧|甲': dict(OLD)}
        state = self.run_inbox(st, 1000000)
        self.assertTrue(state.get('ignored'))
        self.assertEqual(state['last_ts'], 1000.0)

    def test_inbox_revives_on_newer_snapshot(self):
        st = HS.load_state(self.cfg)
        st['conv_state'] = {'pair:旧|甲': dict(OLD)}
        state = self.run_inbox(st, 6000000)
        self.assertNotIn('ignored', state)
        self.assertEqual(state['last_ts'], 6000.0)


if __name__ == '__main__':
    unittest.main()
