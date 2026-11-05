"""Offline regressions for round-two P0 A/B/C."""
import contextlib
import io
import json
import os
from pathlib import Path
import shutil
import sys
import subprocess
import tempfile
import unittest
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
import chat_dom as CD
import boss_hunt as B
import hunt_config as HC
import human as H
import send_safety as S


class Round2Tests(unittest.TestCase):
    def setUp(self):
        self.root = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.root)
        self.cfg = {'paths': {'workdir': str(self.root)}, 'user': {}}

    def test_substring_company_is_unknown_and_never_sends(self):
        for fields in ({}, {'brand': '甲科技供应链', 'boss': '王HR'}):
            rows = [dict(conv_key='id:wrong', text='甲科技供应链 王HR 城市经理', **fields)]
            with self.assertRaisesRegex(ValueError, 'unknown_conversation'):
                CD.select_conversation(rows, '甲科技', '王HR')
            br = Mock()
            with patch.object(CD, 'conversation_rows', return_value=rows):
                result = B.reply_in_chat(br, 1, '甲科技', '回复', '', self.cfg, '王HR')
            self.assertIn('unknown_conversation', result)
            br.fill.assert_not_called()
            br.click.assert_not_called()

    def test_exact_company_hr_pair_and_ambiguity(self):
        rows = [{'conv_key': 'id:a', 'text': '甲科技 王HR 城市经理', 'brand': '甲科技', 'boss': '王HR'},
                {'conv_key': 'id:b', 'text': '乙科技 王HR 城市经理', 'brand': '乙科技', 'boss': '王HR'}]
        self.assertEqual(CD.select_conversation(rows, '甲科技 / 王HR')['conv_key'], 'id:a')
        self.assertEqual(CD.select_conversation(rows, ' 甲 科技 / 王ＨＲ ')['conv_key'], 'id:a')
        with self.assertRaisesRegex(ValueError, 'ambiguous.*甲科技.*甲科技'):
            CD.select_conversation([rows[0], dict(rows[0], conv_key='id:c')], '甲科技 / 王HR')

    def test_fallback_requires_whole_tokens(self):
        rows = [{'conv_key': 'id:a', 'text': '甲科技 王HR 城市经理'}]
        self.assertEqual(CD.select_conversation(rows, '甲科技 / 王HR')['conv_key'], 'id:a')
        with self.assertRaisesRegex(ValueError, 'unknown_conversation'):
            CD.select_conversation(rows, '甲科')

    def test_automatic_reply_uses_source_key_even_with_duplicate_names(self):
        rows = [{'conv_key': 'id:a', 'text': '甲科技 王HR'},
                {'conv_key': 'id:b', 'text': '甲科技 王HR'}]
        with patch.object(CD, 'conversation_rows', return_value=rows), \
                patch.object(CD, 'open_by_index', return_value=('ok', '', [{'who':'friend','text':'请提供面试时段'}])) as opened, \
                patch.object(B, 'send_in_chat', return_value='replied'):
            result = B.reply_in_chat(Mock(), 1, '甲科技', '回复', '', self.cfg,
                                     '王HR', conv_key='id:b')
        self.assertEqual(result, 'replied')
        self.assertEqual(opened.call_args.args[2], 1)
        self.assertEqual(opened.call_args.kwargs['conv_key'], 'id:b')

    def test_corrupt_store_raises_and_preserves_bytes(self):
        p = Path(H.path_of(self.cfg))
        for raw in (b'{broken', b'{"entries":{},"hints":{}}', b'{"entries":[],"hints":[]}',
                    b'{"hints":{},"entries":[}', b'{"entries":[invalid],"hints":{}}',
                    b'{"hints":{"broken":},"entries":[]}', b'{"hints":{},"entries":[]} trailing',
                    b'\xff'):
            p.write_bytes(raw)
            for hints_only in (False, True):
                with self.assertRaisesRegex(H.HumanStoreError, str(p)):
                    H.load(self.cfg, hints_only=hints_only)
            with self.assertRaises(H.HumanStoreError):
                H.record('经验详细介绍', '新要点', '回复', cfg=self.cfg)
            with self.assertRaises(H.HumanStoreError):
                H.compact(self.cfg)
            self.assertEqual(p.read_bytes(), raw)

    def test_atomic_save_failure_preserves_original_and_cleans_tmp(self):
        H.save({'entries': [], 'hints': {}}, self.cfg)
        p = Path(H.path_of(self.cfg)); before = p.read_bytes()
        with patch.object(H.os, 'replace', side_effect=OSError('injected')):
            with self.assertRaises(OSError):
                H.save({'entries': [{'brief': 'new'}], 'hints': {}}, self.cfg)
        self.assertEqual(p.read_bytes(), before)
        self.assertEqual(list(self.root.glob('*.tmp.%d.*' % os.getpid())), [])

    def test_compact_does_not_swallow_human_store_error(self):
        H.record('经验详细介绍', '要点', '回复', cfg=self.cfg)
        failure = H.HumanStoreError('%s: injected read failure' % H.path_of(self.cfg))
        with patch.object(H.G, '_generate_raw', side_effect=failure):
            with self.assertRaises(H.HumanStoreError) as caught:
                H.compact(self.cfg)
        self.assertIs(caught.exception, failure)

    def test_compact_reloads_after_concurrent_record_including_dry(self):
        for dry in (False, True):
            H.save({'entries': [], 'hints': {}}, self.cfg)
            H.record('经验详细介绍', '原经验', '回复', cfg=self.cfg)
            def model(*args):
                # A child process cannot reuse our thread's lock. This also
                # proves the model boundary is outside the store transaction.
                child = subprocess.run([sys.executable, '-c',
                    'import sys,json; sys.path.insert(0,sys.argv[1]); import human as H; '
                    'cfg=json.loads(sys.argv[2]); '
                    'H.record("团队几个人","新团队要点","新回复",cfg=cfg); '
                    'H.record("经验详细介绍","新经验","新回复",cfg=cfg)',
                    str(Path(H.__file__).parent), json.dumps(self.cfg)],
                    capture_output=True, text=True, timeout=10)
                self.assertEqual(child.returncode, 0, child.stderr)
                after_record.append(Path(H.path_of(self.cfg)).read_bytes())
                return '{"经验追问":"过期模型结果"}', ''
            after_record = []
            with patch.object(H.G, '_generate_raw', side_effect=model) as generate:
                result = H.compact(self.cfg, dry=dry)
            data = H.load(self.cfg)
            self.assertEqual(len(data['entries']), 3)
            self.assertEqual(data['hints']['团队规模']['brief'], '新团队要点')
            self.assertEqual(data['hints']['经验追问']['brief'], '新经验')
            self.assertEqual({i['situation'] for i in result['items']}, {'经验追问', '团队规模'})
            self.assertTrue(all(i['method'] == 'latest' for i in result['items']))
            self.assertEqual(generate.call_count, 1)
            if dry:
                self.assertEqual(Path(H.path_of(self.cfg)).read_bytes(), after_record[0])

    def test_compact_preserves_new_entry_when_version_fields_are_unchanged(self):
        with patch.object(H, '_now', return_value='2026-10-27 10:00'):
            for i in range(50):
                H.record('经验详细介绍', '经验', '回复', cfg=self.cfg)
            def model(*args):
                H.record('团队几个人', '失败记录', '未发出', ok=False, cfg=self.cfg)
                return '{"经验追问":"合并经验"}', ''
            with patch.object(H.G, '_generate_raw', side_effect=model):
                H.compact(self.cfg)
        data = H.load(self.cfg)
        self.assertEqual(len(data['entries']), 50)
        self.assertEqual(data['entries'][-1]['brief'], '失败记录')
        self.assertFalse(data['entries'][-1]['ok'])

    def test_record_works_inside_existing_command_lock(self):
        with S.state_lock(str(self.root / 'state.json')):
            H.record('团队几个人', '要点', '回复', cfg=self.cfg)
        self.assertEqual(len(H.load(self.cfg)['entries']), 1)

    def test_compact_cli_uses_config_loader_and_reports_corruption(self):
        p = Path(H.path_of(self.cfg)); p.write_bytes(b'{broken')
        config = self.root / 'config.json'; config.write_text('{}')
        args = B.build_parser().parse_args(['--config', str(config), 'human-compact', '--json'])
        out = io.StringIO()
        with patch.object(HC, 'load_config', return_value=self.cfg) as loader, contextlib.redirect_stdout(out):
            code = args.func(args)
        self.assertNotEqual(code, 0)
        self.assertFalse(json.loads(out.getvalue())['ok'])
        loader.assert_called_once_with(str(config))
        self.assertEqual(p.read_bytes(), b'{broken')
