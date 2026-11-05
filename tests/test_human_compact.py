"""Offline human memory limits and CLI regression tests."""
import contextlib
import io
import json
from pathlib import Path
import shutil
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
import boss_hunt as B
import human as H
import reply as R


class HumanCompactTests(unittest.TestCase):
    def setUp(self):
        self.root = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.root)
        self.cfg = {'paths': {'workdir': str(self.root)}, 'user': {}}
        self.config = self.root / 'config.json'
        self.config.write_text(json.dumps(self.cfg), encoding='utf-8')

    def cli(self, *args):
        parsed = B.build_parser().parse_args(['--config', str(self.config), *args])
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            self.assertEqual(parsed.func(parsed), 0)
        return out.getvalue()

    def seed(self, count=60):
        entries = [{'ts': '2026-09-01 10:00', 'situation': '经验追问',
                    'hr_text': 'PRIVATE_HR', 'final': 'PRIVATE_FINAL',
                    'brief': '要点%d。' % i, 'ok': True} for i in range(count)]
        H.save({'entries': entries, 'hints': {'经验追问': {
            'brief': '最新要点。', 'count': count, 'last': '2026-10-01 10:00'}}}, self.cfg)
        return entries

    def test_record_archives_ten_old_entries_by_own_month(self):
        expected = []
        for i in range(60):
            ts = '2026-%02d-%02d 10:00' % (8 + i // 5, i % 5 + 1) if i < 10 else '2026-10-01 10:00'
            with patch.object(H, '_now', return_value=ts):
                H.record('经验详细介绍', '要点%d' % i, '回复%d' % i, cfg=self.cfg)
            if i < 10:
                expected.append(H.load(self.cfg)['entries'][-1])
        data = H.load(self.cfg)
        self.assertEqual(len(data['entries']), 50)
        self.assertEqual(data['entries'][0]['brief'], '要点10')
        archived = []
        for name in ['human_replies-202608.jsonl', 'human_replies-202609.jsonl']:
            archived.extend(json.loads(line) for line in (self.root / name).read_text().splitlines())
        self.assertEqual(archived, expected)
        self.assertEqual(data['hints']['经验追问']['count'], 60)

    def test_bad_timestamp_archives_to_current_month(self):
        entries = self.seed(50)
        entries[0]['ts'] = 'bad timestamp'
        data = H.load(self.cfg); data['entries'] = entries; H.save(data, self.cfg)
        with patch.object(H, '_now', return_value='2026-10-27 10:00'):
            H.record('经验详细介绍', '要点', '回复', cfg=self.cfg)
        self.assertEqual(json.loads((self.root / 'human_replies-202610.jsonl').read_text()), entries[0])

    def test_brief_limit_preserves_last_punctuation(self):
        brief = ('字' * 19 + '。') * 10
        H.record('经验详细介绍', brief, '回复', cfg=self.cfg)
        learned = H.find_hint('经验详细介绍', self.cfg)[1]
        self.assertLessEqual(len(learned), 120)
        self.assertTrue(learned.endswith('。'))
        H.record('经验详细介绍', '字' * 200, '回复', cfg=self.cfg)
        self.assertEqual(H.find_hint('经验详细介绍', self.cfg)[1], '字' * 120)

    def test_hints_only_does_not_decode_entries(self):
        self.seed()
        original = json.loads
        def decode(value, *args, **kwargs):
            self.assertNotIn('PRIVATE_HR', value)
            self.assertNotIn('PRIVATE_FINAL', value)
            return original(value, *args, **kwargs)
        with patch.object(H.json, 'loads', side_effect=decode):
            data = H.load(self.cfg, hints_only=True)
            self.assertEqual(data['entries'], [])
            self.assertEqual(data['hints']['经验追问']['brief'], '最新要点。')

    def test_hints_only_handles_nested_strings_and_both_key_orders(self):
        entries = [{"nested": ["hints", {"text": '引号"反斜杠\\以及},]'}]}]
        hint = {'经验追问': {'brief': '本人要点。', 'count': 1, 'last': ''}}
        for data in ({'entries': entries, 'hints': hint}, {'hints': hint, 'entries': entries}):
            H.save(data, self.cfg)
            self.assertEqual(H.load(self.cfg, hints_only=True), {'entries': [], 'hints': hint})

    def test_archives_are_never_read_by_reply_or_stats(self):
        self.seed()
        archive = self.root / 'human_replies-202609.jsonl'
        archive.write_text('PRIVATE_ARCHIVE', encoding='utf-8')
        import builtins
        original_open = builtins.open
        def guarded_open(path, mode='r', *args, **kwargs):
            self.assertNotEqual(Path(path), archive)
            return original_open(path, mode, *args, **kwargs)
        with patch('builtins.open', side_effect=guarded_open):
            self.assertEqual(H.find_hint('经验详细介绍', self.cfg)[1], '最新要点。')
            self.cli('human-stats', '--json')
            with patch.object(R.G, '_generate_raw', return_value=('可以聊聊，负责哪个区域？', '')):
                R.generate(self.cfg, {}, '方便聊聊吗')

    def test_reply_loads_only_hints_and_logs_injection_size(self):
        self.seed()
        original = H.load
        def load(cfg=None, hints_only=False):
            self.assertTrue(hints_only)
            data = original(cfg, hints_only=hints_only)
            self.assertEqual(data['entries'], [])
            return data
        logs = []
        with patch.object(H, 'load', side_effect=load), patch.object(R.G, '_generate_raw', return_value=('可以聊聊，负责哪个区域？', '')):
            result = R.generate(self.cfg, {}, '方便聊聊吗', log=logs.append)
        self.assertEqual(result['source'], 'template')
        self.assertTrue(R.check(result['text'], self.cfg)[0])
        self.assertIn('hint_injected_chars=0', '\n'.join(logs))
        with patch.object(R.G, '_generate_raw', return_value=('可以聊聊，负责哪个区域？', '')):
            R.generate(self.cfg, {}, '方便聊聊吗', force=True, log=logs.append)
        self.assertIn('hint_injected_chars=5', '\n'.join(logs))

    def test_stats_never_exposes_entries_and_text_has_eight_lines(self):
        self.seed()
        data = H.load(self.cfg)
        data['hints'] = {'情况%d' % i: {'brief': '字' * 120, 'count': i, 'last': ''} for i in range(10)}
        H.save(data, self.cfg)
        for args in [('human-stats',), ('human-stats', '--json')]:
            text = self.cli(*args)
            self.assertNotIn('PRIVATE_HR', text)
            self.assertNotIn('PRIVATE_FINAL', text)
            if '--json' not in args:
                self.assertLessEqual(len(text.splitlines()), 8)
                self.assertNotIn('字' * 61, text)
            else:
                self.assertEqual(set(json.loads(text)['hints'][0]), {'situation', 'count', 'last', 'brief'})

    def test_compact_failure_is_latest_and_preserves_entries(self):
        entries = self.seed()
        with patch.object(H.G, '_generate_raw', side_effect=RuntimeError('offline')) as model:
            result = json.loads(self.cli('human-compact', '--json'))
        self.assertEqual(model.call_count, 1)
        self.assertEqual(result['model_calls'], 1)
        self.assertEqual(result['items'][0]['method'], 'latest')
        self.assertEqual(H.load(self.cfg)['entries'], entries)
        self.assertEqual(H.load(self.cfg)['hints']['经验追问']['brief'], '最新要点。')
        self.assertEqual(list(self.root.glob('*.jsonl')), [])

    def test_compact_dry_and_success_never_prompt_with_raw_entries(self):
        entries = self.seed()
        # Compaction output must be supported by a human-confirmed source.
        data = H.load(self.cfg)
        data['entries'][-1]['brief'] = '合并要点。'
        entries = data['entries']
        H.save(data, self.cfg)
        before = Path(H.path_of(self.cfg)).read_bytes()
        def model(cfg, prompt):
            self.assertNotIn('PRIVATE_HR', prompt)
            self.assertNotIn('PRIVATE_FINAL', prompt)
            return json.dumps({'经验追问': '合并要点。'}, ensure_ascii=False), ''
        with patch.object(H.G, '_generate_raw', side_effect=model) as generate:
            text = self.cli('human-compact', '--dry')
            self.assertIn('method=llm', text)
            self.assertIn('before=', text)
            self.assertEqual(Path(H.path_of(self.cfg)).read_bytes(), before)
            result = json.loads(self.cli('human-compact', '--json'))
        self.assertEqual(generate.call_count, 2)
        self.assertEqual(result['items'][0]['after'], 5)
        self.assertEqual(H.load(self.cfg)['entries'], entries)
        self.assertEqual(H.find_hint('经验详细介绍', self.cfg)[1], '合并要点。')

    def test_compact_fallback_latest_repeated_brief_and_model_errors(self):
        for raw in [('ignored', 'offline'), ('invalid JSON', ''), ('[]', ''), ('{"经验追问": ""}', '')]:
            H.save({'entries': [
                {'ts': '2026-10-01 10:00', 'situation': '经验追问', 'brief': '重复要点。', 'ok': True},
                {'ts': '2026-10-02 10:00', 'situation': '经验追问', 'brief': '旧要点。', 'ok': True}],
                'hints': {'经验追问': {'brief': '重复要点。', 'count': 10, 'last': '2026-10-03 10:00'}}}, self.cfg)
            with patch.object(H.G, '_generate_raw', return_value=raw) as model:
                result = json.loads(self.cli('human-compact', '--json'))
            self.assertEqual(model.call_count, 1)
            self.assertEqual(result['items'][0]['method'], 'latest')
            self.assertEqual(H.load(self.cfg)['hints']['经验追问'], {
                'brief': '重复要点。', 'count': 10, 'last': '2026-10-03 10:00'})

    def test_compact_empty_memory_does_not_call_model_or_create_store(self):
        with patch.object(H.G, '_generate_raw') as model:
            result = json.loads(self.cli('human-compact', '--json'))
        model.assert_not_called()
        self.assertEqual(result['model_calls'], 0)
        self.assertFalse(Path(H.path_of(self.cfg)).exists())
        # The shared config loader and persistent lock create infrastructure only.
        self.assertEqual({p.name for p in self.root.iterdir()}, {'config.json', '.lock', 'raw'})
        self.assertEqual(list((self.root / 'raw').iterdir()), [])

    def test_compact_one_call_for_multiple_situations_and_limits_result(self):
        self.seed()
        data = H.load(self.cfg)
        data['hints']['加微信'] = {'brief': '先聊岗位。', 'count': 2, 'last': ''}
        H.save(data, self.cfg)
        with patch.object(H.G, '_generate_raw', return_value=(json.dumps({
                '经验追问': ('字' * 18 + '。') * 12, '加微信': '先了解岗位。'}), '')) as model:
            result = json.loads(self.cli('--json', 'human-compact'))
        model.assert_called_once()
        self.assertEqual(len(result['items']), 2)
        for hint in H.load(self.cfg)['hints'].values():
            self.assertLessEqual(len(hint['brief']), 120)
