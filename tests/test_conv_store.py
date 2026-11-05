"""Conversation persistence, using only temporary fictional records."""
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
import conv_store as S


class ConversationStoreTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.path = Path(temp.name) / 'conversations.json'

    def test_new_record_has_complete_independent_defaults(self):
        record = S.new_record('fiction:1', 'reply', 'job:1', '虚构星港', '虚构小岚', '测试岗位', 100)
        self.assertEqual(record, {
            'conv_key': 'fiction:1', 'origin': 'reply', 'job_id': 'job:1',
            'brand': '虚构星港', 'boss': '虚构小岚', 'title': '测试岗位',
            'status': 'new', 'resume_sent_ts': None, 'asked_round1_ts': None,
            'sent_templates': [], 'experience_answered_ts': None,
            'asked_round2_ts': None, 'answers': {
                'requirements': None, 'salary_structure': None,
                'interview_rounds': None, 'online_interview': None},
            'last_seen': {'ts': None, 'text': ''}, 'actions': [],
            'notified': {'manual': False, 'complete': False}, 'updated_ts': 100})
        other = S.new_record('fiction:2', 'inbound', 'job:2', '', '', '', 101)
        record['answers']['requirements'] = '测试需求'
        record['actions'].append({})
        self.assertIsNone(other['answers']['requirements'])
        self.assertEqual(other['actions'], [])

    def test_missing_file_and_round_trip(self):
        self.assertEqual(S.load(self.path), {})
        data = {'fiction:1': S.new_record('fiction:1', 'reply', 'job:1', '虚构星港', '虚构小岚', '测试岗位', 1)}
        S.save(self.path, data)
        self.assertEqual(S.load(self.path), data)

    def test_corruption_is_reported_without_overwriting(self):
        for content in ('{broken', '[]', 'null', '{"fiction:1": 7}'):
            with self.subTest(content=content):
                self.path.write_text(content, encoding='utf-8')
                with self.assertRaises(ValueError):
                    S.load(self.path)
                self.assertEqual(self.path.read_text(encoding='utf-8'), content)

    def test_replace_sees_complete_temp_and_old_destination(self):
        S.save(self.path, {'old': {}})
        real_replace = S.os.replace
        observed = []

        def replace(source, destination):
            self.assertEqual(Path(source).parent, self.path.parent)
            self.assertNotEqual(Path(source), self.path)
            observed.append(json.loads(Path(source).read_text(encoding='utf-8')))
            self.assertEqual(S.load(self.path), {'old': {'sent_templates': [], 'experience_answered_ts': None}})
            real_replace(source, destination)

        with patch.object(S.os, 'replace', side_effect=replace):
            S.save(self.path, {'new': {'brand': '虚构星港'}})
        self.assertEqual(observed, [{'new': {'brand': '虚构星港'}}])
        self.assertEqual(S.load(self.path), {'new': dict(observed[0]['new'],
                         sent_templates=[], experience_answered_ts=None)})
        self.assertEqual(list(self.path.parent.iterdir()), [self.path])

    def test_failed_replace_or_serialization_preserves_destination_and_cleans_temp(self):
        S.save(self.path, {'old': {}})
        with patch.object(S.os, 'replace', side_effect=OSError('fictional failure')):
            with self.assertRaises(OSError):
                S.save(self.path, {'new': {}})
        with self.assertRaises(TypeError):
            S.save(self.path, {'new': {'bad': object()}})
        self.assertEqual(S.load(self.path), {'old': {'sent_templates': [], 'experience_answered_ts': None}})
        self.assertEqual(list(self.path.parent.iterdir()), [self.path])

    def test_actions_keep_latest_twenty_in_order(self):
        record = {'actions': []}
        for ts in range(25):
            S.add_action(record, ts, 'send_text', 'planned')
        self.assertEqual(record['actions'], [
            {'ts': ts, 'type': 'send_text', 'result': 'planned'} for ts in range(5, 25)])
