"""harness-01：换 harness / 换模型服务商的可自动验证部分。

两种角色分开测：
- 作为招呼语 / 对话的 LLM 服务商（DeepSeek、Kimi=Moonshot、MiniMax，都走 OpenAI 兼容
  /chat/completions）：validate 规则、key 查找顺序、请求落到配置的 base_url/model —— 全用替身，不真调接口。
- 作为 harness（WorkBuddy / DeepSeek Harness / Kimi Code / MiniMax Code）：仓库侧只能验证
  「文档写到了、写的配置真能过 validate」；harness 本身能力见 docs/agent-adapters.md 的核实标注。

临时 HOME，不读写 ~/.boss-hunt 与 ~/.hermes。
"""
import json
import os
import re
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / 'scripts'
sys.path.insert(0, str(SCRIPTS))
import conv_llm  # noqa: E402
import greet as G  # noqa: E402
import hunt_schedule as HQ  # noqa: E402

ADAPTERS = ROOT / 'docs' / 'agent-adapters.md'

PRESETS = {
    'deepseek': {'provider': 'deepseek', 'base_url': 'https://api.deepseek.com',
                 'model': 'deepseek-flash', 'api_key_env': 'DEEPSEEK_API_KEY'},
    'kimi': {'provider': 'openai', 'base_url': 'https://api.moonshot.cn/v1',
             'model': 'kimi-k3.1-pro', 'api_key_env': 'MOONSHOT_API_KEY'},
    'minimax': {'provider': 'openai', 'base_url': 'https://api.minimax.io/v1',
                'model': 'MiniMax-M3.1-Flash', 'api_key_env': 'MINIMAX_API_KEY'},
}


def example_cfg():
    cfg = json.loads((ROOT / 'assets' / 'config.example.json').read_text(encoding='utf-8'))
    cfg['paths'] = {'workdir': '/nonexistent-fiction-workdir'}
    return cfg


def with_llm(llm, mode='llm', conversation=False):
    cfg = example_cfg()
    cfg['user']['greeting']['mode'] = mode
    cfg['user']['greeting']['llm'] = dict(cfg['user']['greeting']['llm'], **llm)
    cfg['conversation']['enabled'] = conversation
    return cfg


class TempHome(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory(prefix='harness01-')
        self.addCleanup(tmp.cleanup)
        self.home = Path(tmp.name)
        env = {k: v for k, v in os.environ.items()
               if not k.endswith('_API_KEY') and k not in ('BOSS_HUNT_ENV_FILE', 'BOSS_HUNT_CONFIG')}
        env['HOME'] = str(self.home)
        p = patch.dict(os.environ, env, clear=True)
        p.start()
        self.addCleanup(p.stop)

    def env_file(self, rel, text):
        path = self.home / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding='utf-8')
        return path


class ProviderValidateTests(TempHome):
    def test_presets_pass_validate_with_key_in_environment(self):
        for name, llm in PRESETS.items():
            with self.subTest(provider=name):
                os.environ[llm['api_key_env']] = 'fiction-key'
                errs, warns = HQ.validate_cfg(with_llm(llm))
                self.assertEqual(errs, [])
                self.assertFalse([w for w in warns if 'key' in w.lower()], warns)

    def test_missing_key_is_a_warning_naming_the_env_var(self):
        errs, warns = HQ.validate_cfg(with_llm(PRESETS['kimi']))
        self.assertEqual(errs, [])
        self.assertTrue(any('MOONSHOT_API_KEY' in w for w in warns), warns)

    def test_key_from_boss_hunt_env_file_counts(self):
        self.env_file('.boss-hunt/.env', 'export MINIMAX_API_KEY="fiction"\n')
        errs, warns = HQ.validate_cfg(with_llm(PRESETS['minimax']))
        self.assertEqual(errs, [])
        self.assertFalse([w for w in warns if 'MINIMAX_API_KEY' in w], warns)

    def test_vendor_name_as_provider_is_rejected_when_llm_is_used(self):
        for vendor in ('kimi', 'moonshot', 'minimax', 'qwen'):
            with self.subTest(provider=vendor):
                errs, _ = HQ.validate_cfg(with_llm(dict(PRESETS['kimi'], provider=vendor)))
                self.assertTrue(any('provider' in e and 'openai' in e for e in errs), errs)

    def test_unknown_provider_blocks_when_only_conversation_uses_llm(self):
        errs, _ = HQ.validate_cfg(with_llm(dict(PRESETS['kimi'], provider='kimi'),
                                           mode='template', conversation=True))
        self.assertTrue(any('provider' in e for e in errs), errs)

    def test_unknown_provider_only_warns_when_llm_unused(self):
        errs, warns = HQ.validate_cfg(with_llm(dict(PRESETS['kimi'], provider='kimi'), mode='template'))
        self.assertFalse([e for e in errs if 'provider' in e], errs)
        self.assertTrue(any('provider' in w for w in warns), warns)

    def test_provider_is_case_insensitive(self):
        os.environ['DEEPSEEK_API_KEY'] = 'fiction'
        errs, _ = HQ.validate_cfg(with_llm(dict(PRESETS['deepseek'], provider='DeepSeek')))
        self.assertEqual(errs, [])

    def test_cloud_provider_without_explicit_base_url_is_an_error(self):
        os.environ['MOONSHOT_API_KEY'] = 'fiction'
        cfg = with_llm(PRESETS['kimi'])
        del cfg['user']['greeting']['llm']['base_url']
        errs, _ = HQ.validate_cfg(cfg)
        self.assertTrue(any('base_url' in e for e in errs), errs)

    def test_cloud_base_url_must_be_http(self):
        os.environ['MOONSHOT_API_KEY'] = 'fiction'
        errs, _ = HQ.validate_cfg(with_llm(dict(PRESETS['kimi'], base_url='api.moonshot.cn/v1')))
        self.assertTrue(any('base_url' in e for e in errs), errs)

    def test_local_openai_compatible_server_is_allowed(self):
        os.environ['FICTION_LOCAL_KEY'] = 'x'
        errs, _ = HQ.validate_cfg(with_llm({'provider': 'openai', 'base_url': 'http://127.0.0.1:1234/v1',
                                            'model': 'fiction-local', 'api_key_env': 'FICTION_LOCAL_KEY'}))
        self.assertEqual(errs, [])

    def test_default_ollama_config_unchanged(self):
        errs, warns = HQ.validate_cfg(with_llm({}))
        self.assertEqual(errs, [])
        self.assertFalse([w for w in warns if 'provider' in w or 'key' in w.lower()], warns)


class ProviderRequestTests(TempHome):
    """请求落到配置的 base_url + /chat/completions，带 Bearer key 与 model；替身代替网络。"""

    def capture(self, cfg, call):
        seen = {}

        def fake_post(url, payload, timeout, headers=None):
            seen.update(url=url, payload=payload, headers=headers or {})
            return {'choices': [{'message': {'content': '虚构回复'}}]}

        with patch.object(G, '_post', fake_post):
            result = call(cfg)
        return seen, result

    def test_each_preset_hits_its_chat_completions_endpoint(self):
        for name, llm in PRESETS.items():
            with self.subTest(provider=name):
                os.environ[llm['api_key_env']] = 'key-' + name
                seen, (text, err) = self.capture(with_llm(llm), lambda c: G._generate_raw(c, '虚构提示'))
                self.assertEqual((text, err), ('虚构回复', ''))
                self.assertEqual(seen['url'], llm['base_url'].rstrip('/') + '/chat/completions')
                self.assertEqual(seen['payload']['model'], llm['model'])
                self.assertEqual(seen['headers']['Authorization'], 'Bearer key-' + name)

    def test_key_lookup_order_with_vendor_env_name(self):
        llm = PRESETS['kimi']

        def key_used(extra=None):
            seen, _ = self.capture(with_llm(dict(llm, **(extra or {}))), lambda c: G._generate_raw(c, 'p'))
            return seen['headers']['Authorization']

        # 从最低优先级往上逐层加，每加一层都应该换成新的那层
        self.env_file('.hermes/.env', 'MOONSHOT_API_KEY=from-hermes\n')
        self.assertEqual(key_used(), 'Bearer from-hermes')
        self.env_file('.boss-hunt/.env', 'MOONSHOT_API_KEY=from-boss-hunt\n')
        self.assertEqual(key_used(), 'Bearer from-boss-hunt')
        custom = self.env_file('x/custom.env', 'MOONSHOT_API_KEY=from-env-var-file\n')
        os.environ['BOSS_HUNT_ENV_FILE'] = str(custom)
        self.assertEqual(key_used(), 'Bearer from-env-var-file')
        cfg_file = self.env_file('x/cfg.env', 'MOONSHOT_API_KEY=from-llm-env-file\n')
        self.assertEqual(key_used({'env_file': str(cfg_file)}), 'Bearer from-llm-env-file')
        os.environ['MOONSHOT_API_KEY'] = 'from-environ'
        seen, _ = self.capture(with_llm(dict(llm, env_file=str(cfg_file))), lambda c: G._generate_raw(c, 'p'))
        self.assertEqual(seen['headers']['Authorization'], 'Bearer from-environ')

    def test_no_key_never_calls_network(self):
        with patch.object(G, '_post', side_effect=AssertionError('不该发请求')):
            self.assertEqual(G._generate_raw(with_llm(PRESETS['minimax']), 'p'), (None, 'llm_no_key'))
        ok, msg = G.ollama_status(with_llm(PRESETS['minimax']))
        self.assertEqual((ok, msg), (False, 'no_key:MINIMAX_API_KEY'))

    def test_conversation_llm_uses_same_provider(self):
        os.environ['DEEPSEEK_API_KEY'] = 'conv-key'
        cfg = with_llm(PRESETS['deepseek'], mode='template', conversation=True)
        seen, _ = self.capture(cfg, lambda c: conv_llm.lead(c, 'round1', '虚构 HR 消息', [], []))
        self.assertEqual(seen['url'], 'https://api.deepseek.com/chat/completions')
        self.assertEqual(seen['headers']['Authorization'], 'Bearer conv-key')


class ValidateCliTests(TempHome):
    def test_validate_cli_with_kimi_preset_in_temp_home(self):
        config = self.home / 'cfg' / 'config.json'
        config.parent.mkdir()
        cfg = with_llm(PRESETS['kimi'])
        cfg['paths'] = {'workdir': str(self.home / 'data')}
        config.write_text(json.dumps(cfg, ensure_ascii=False), encoding='utf-8')
        self.env_file('.boss-hunt/.env', 'MOONSHOT_API_KEY=fiction\n')
        p = subprocess.run([sys.executable, str(SCRIPTS / 'boss_hunt.py'), '--config', str(config),
                            '--json', 'validate'], env=dict(os.environ), capture_output=True, text=True)
        data = json.loads(p.stdout)
        self.assertEqual((p.returncode, data['ok'], data['errors']), (0, True, []))


class AdapterDocTests(TempHome):
    text = ADAPTERS.read_text(encoding='utf-8')

    def test_named_harnesses_are_covered(self):
        for name in ('WorkBuddy', 'DeepSeek Harness', 'Kimi Code', 'MiniMax Code'):
            self.assertIn(name, self.text)
        self.assertIn('未核实', self.text)

    def test_two_deepseek_roles_are_separated(self):
        self.assertRegex(self.text, r'作为 harness')
        self.assertRegex(self.text, r'作为.*(LLM|模型)服务商')

    def test_documented_provider_blocks_pass_validate(self):
        blocks = [json.loads(b) for b in re.findall(r'```json\n(.*?)\n```', self.text, re.S)]
        llms = [b for b in blocks if isinstance(b, dict) and 'provider' in b]
        self.assertGreaterEqual(len(llms), 3, '文档应给 DeepSeek / Kimi / MiniMax 三份可直接粘贴的 llm 配置')
        for llm in llms:
            with self.subTest(provider=llm.get('provider'), base_url=llm.get('base_url')):
                os.environ[llm['api_key_env']] = 'fiction'
                errs, _ = HQ.validate_cfg(with_llm(llm))
                self.assertEqual(errs, [])
                self.assertTrue(G._cloud(G.block(with_llm(llm))['llm']))


if __name__ == '__main__':
    unittest.main()
