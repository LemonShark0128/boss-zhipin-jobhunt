"""投递模块归属、单向依赖与回消息隔离边界。"""
import ast
import importlib
import subprocess
import sys
import unittest
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parents[1] / 'scripts'
sys.path.insert(0, str(SCRIPTS))

SYMBOLS = {'jd_page': ['JD_EXPR', 'read_jd'],
 'hunt_config': ['DEFAULT_DIR',
                 'TZ_DEFAULT',
                 'TICK_WAKE_WAIT_SECONDS',
                 'config_path',
                 'same_hr_window_error',
                 'load_config',
                 'cfg_tz',
                 'now_local',
                 'stamp',
                 'parse_local_ts',
                 'trace'],
 'hunt_filter': ['GRADE_RULES_VERSION',
                 'EXP_MIN',
                 'EXP_MAX',
                 'DEG_RANK',
                 'JD_FIELDS',
                 'parse_salary',
                 'any_match',
                 'exp_bounds',
                 'judge',
                 'score',
                 'load_jobs',
                 'grading_enabled',
                 'grade_job',
                 'jd_redline_scope',
                 'grade_with_jd',
                 'near_miss',
                 'grade_sort_key',
                 'stable_fingerprint',
                 'legacy_grade_fingerprint',
                 'jd_fingerprint',
                 'rule_fingerprint',
                 'grade_fingerprint'],
 'hunt_schedule': ['grade_limits',
                   'remaining_windows',
                   'window_minutes',
                   'quotas_for_now',
                   'shrink_quotas',
                   'check_deadline',
                   'tick_deadline',
                   'tick_plan',
                   'missed_windows',
                   'tick_today_sent',
                   'target_of',
                   'validate_cfg'],
 'hunt_state': ['load_state',
                'save_state',
                'record_outcome',
                'refresh_ledger_counts',
                'migrate_ledger',
                'atomic_json',
                'contact_last_sent',
                'same_hr_filter',
                'candidates',
                'cached_jd',
                'cached_grade'],
 'outbound': ['APPWALL_RE',
              'render',
              'verify_marker',
              'login_state',
              'check_login',
              'switch_status',
              'greeting_switch',
              'page_guard',
              'scan',
              'LAST_GREET',
              'read_jd',
              'inspect_job',
              'prepare_grade',
              'job_identity_matches',
              'send_one',
              'do_send',
              'cleanup_browser']}


class ModuleBoundaryTests(unittest.TestCase):
    def test_symbol_ownership(self):
        boss = importlib.import_module('boss_hunt')
        for name, symbols in SYMBOLS.items():
            self.assertIsNotNone(importlib.util.find_spec(name), name)
            module = importlib.import_module(name)
            for symbol in symbols:
                with self.subTest(module=name, symbol=symbol):
                    self.assertTrue(hasattr(module, symbol))
                    self.assertFalse(hasattr(boss, symbol))

    def test_fresh_imports_are_acyclic(self):
        names = list(SYMBOLS)
        for first in names:
            with self.subTest(first=first):
                code = "import importlib, sys; sys.path.insert(0, %r); " % str(SCRIPTS)
                code += '; '.join("importlib.import_module(%r)" % name
                                  for name in [first] + [n for n in names if n != first])
                result = subprocess.run([sys.executable, '-c', code], capture_output=True, text=True)
                self.assertEqual(result.returncode, 0, result.stderr)
        for index, name in enumerate(names):
            tree = ast.parse((SCRIPTS / (name + '.py')).read_text())
            for node in ast.walk(tree):
                imported = []
                if isinstance(node, ast.Import):
                    imported = [alias.name for alias in node.names]
                elif isinstance(node, ast.ImportFrom):
                    imported = [node.module]
                for dependency in imported:
                    if dependency in names:
                        self.assertLess(names.index(dependency), index)

    def test_outbound_does_not_import_reply_modules(self):
        path = SCRIPTS / 'outbound.py'
        self.assertTrue(path.exists())
        tree = ast.parse(path.read_text())
        forbidden = {'reply', 'human', 'conv_state', 'conv_parse',
                     'conversation', 'conv_actions', 'conv_rules', 'conv_llm', 'conv_store'}
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                self.assertTrue(forbidden.isdisjoint(alias.name for alias in node.names))
            elif isinstance(node, ast.ImportFrom):
                self.assertNotIn(node.module, forbidden)

    def test_conversation_does_not_import_outbound(self):
        # audit-01 Q8：对话模块不反向依赖投递模块；读 JD 走 jd_page，收尾走 chrome_mcp.cleanup_browser
        tree = ast.parse((SCRIPTS / 'conversation.py').read_text())
        imported = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imported.update(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom):
                imported.add(node.module)
        self.assertNotIn('outbound', imported)
        self.assertIn('jd_page', imported)
