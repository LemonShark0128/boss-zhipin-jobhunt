"""Offline scan regressions with a navigation-triggered capture transport."""
import json
from pathlib import Path
import sys
import tempfile
import time
import unittest
from unittest.mock import patch
from urllib.parse import parse_qs, urlparse

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
import chrome_mcp as CM
import outbound as O


JOBLIST = {'code': 0, 'zpData': {'jobList': [
    {'encryptJobId': 'fiction-job', 'jobName': '示例岗位', 'brandName': '虚构公司'}]}}
EMPTY = {'code': 0, 'zpData': {'jobList': []}}


class CaptureTransport:
    """Script the prime read, then bodies for navigation-triggered captures.

    The first response belongs to the ignored prime call. Only later
    navigations, after recording starts, can expose a POST body.
    """

    def __init__(self, responses):
        self.responses = iter(responses)
        self.calls = []
        self.tabs = {}
        self.closed = False

    def navigate(self, tab, url):
        state = self.tabs[tab]
        state['body'] = None
        if url != 'about:blank':
            body = next(self.responses)
            if state['recording']:
                state['body'] = body

    def call(self, tool, args, **kwargs):
        self.calls.append((tool, dict(args)))
        if tool == 'tabs':
            if args['action'] == 'new':
                tab = 100001 + len(self.tabs)
                self.tabs[tab] = {'recording': False, 'body': None}
                self.navigate(tab, args['url'])
                return {'text': '[%d]' % tab}
            if args['action'] == 'close':
                del self.tabs[args['tabId']]
                return {'text': 'closed'}
        tab = args['tabId']
        if tool == 'navigate':
            self.navigate(tab, args['url'])
        elif tool == 'network':
            assert args['body'] == 'joblist.json'
            if not self.tabs[tab]['recording']:
                # Priming is a read too; its scripted response is ignored by scan.
                next(self.responses)
            self.tabs[tab]['recording'] = True
            bodies = self.tabs[tab]['body']
            bodies = bodies if isinstance(bodies, list) else [bodies]
            body = bodies[args['index']] if args['index'] < len(bodies) else None
            if body is None:
                return {'text': '[NO_MATCH] 没有匹配 "joblist.json" 的第 %d 条请求（共 %d 条）'
                        % (args['index'], len(bodies)), 'isError': True}
            if isinstance(body, str):
                return {'text': body}
            if isinstance(body, tuple):
                return {'text': json.dumps(body[0]), 'isError': body[1]}
            return {'text': json.dumps(body)}
        elif tool == 'eval':
            return {'text': '{"login": false, "avatar": true}'}
        elif tool != 'scroll':
            raise AssertionError('unexpected tool: ' + tool)
        return {'text': 'ok'}

    def close(self):
        self.closed = True


class ScanCaptureTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory(dir=Path(__file__).resolve().parent)
        self.addCleanup(tmp.cleanup)
        self.raw = Path(tmp.name)
        self.cfg = {'paths': {'raw_dir': str(self.raw)},
                    '_deadline': time.monotonic() + 600,
                    'targets': [{'name': '虚构方向', 'city': '示例城市',
                                 'city_code': '101010100', 'keywords': ['示例关键词']}]}
        sleeper = patch.object(CM.time, 'sleep')
        sleeper.start()
        self.addCleanup(sleeper.stop)

    def browser(self, responses):
        self.transport = CaptureTransport(responses)
        browser = CM.Browser(self.cfg)
        browser.c = self.transport
        return browser

    def assert_saved(self, result, count=1):
        self.assertEqual(result, (count, []))
        files = list(self.raw.glob('*.json'))
        self.assertEqual(len(files), count)
        for path in files:
            self.assertEqual(json.loads(path.read_text()), JOBLIST)
        self.assertFalse(self.transport.tabs)
        self.assertTrue(self.transport.closed)
        self.assertNotIn('reload', [tool for tool, _ in self.transport.calls])

    def test_first_keyword_records_initial_navigation_and_saves_raw(self):
        with self.browser([None, JOBLIST]) as br:
            result = O.scan(self.cfg, br, pages=1)
        self.assert_saved(result)
        calls = self.transport.calls
        self.assertEqual(calls[0][1]['url'], 'about:blank')
        self.assertEqual(calls[1][0], 'network')
        self.assertEqual(calls[2][0], 'navigate')
        self.assertEqual(sum(tool == 'navigate' for tool, _ in calls), 1)

    def test_missing_first_capture_retries_same_tab_once(self):
        with self.browser([None, None, JOBLIST]) as br:
            result = O.scan(self.cfg, br, pages=1)
        self.assert_saved(result)
        navs = [args for tool, args in self.transport.calls if tool == 'navigate']
        self.assertEqual([args['url'] for args in navs],
                         [navs[0]['url'], 'about:blank', navs[0]['url']])
        self.assertEqual({args['tabId'] for args in navs}, {100001})

    def test_no_next_page_continues_to_next_keyword_with_deadline(self):
        self.cfg['targets'][0]['keywords'].append('另一关键词')
        with self.browser([None, JOBLIST, JOBLIST]) as br:
            result = O.scan(self.cfg, br, pages=2)
        self.assert_saved(result, count=2)
        self.assertEqual({p.name for p in self.raw.glob('*.json')},
                         {'示例城市_示例关键词_0.json', '示例城市_另一关键词_0.json'})

    def test_invalid_later_capture_ends_pagination_without_errors(self):
        for body in ('not JSON', '[NO_MATCH] no matching request', (JOBLIST, True)):
            with self.subTest(body=body):
                with self.browser([None, [JOBLIST, body, JOBLIST]]) as br:
                    result = O.scan(self.cfg, br, pages=3)
                self.assert_saved(result)
                self.assertNotIn(2, [args['index'] for tool, args in self.transport.calls
                                     if tool == 'network'])

    def test_explicit_has_more_false_still_checks_missing_next_capture(self):
        body = {'code': 0, 'zpData': dict(JOBLIST['zpData'], hasMore=False)}
        with self.browser([None, [body]]) as br:
            result = O.scan(self.cfg, br, pages=2)
        self.assertEqual(result, (1, []))
        self.assertEqual([json.loads(p.read_text()) for p in self.raw.glob('*.json')],
                         [body])
        self.assertEqual([args['index'] for tool, args in self.transport.calls
                          if tool == 'network'], [0, 0, 1])

    def test_idx_zero_has_more_false_saves_both_captured_pages(self):
        latest = {'code': 0, 'zpData': {'hasMore': False, 'jobList': [
            {'encryptJobId': 'fiction-latest', 'jobName': '虚构末页岗位'}]}}
        earlier = {'code': 0, 'zpData': {'hasMore': True, 'jobList': [
            {'encryptJobId': 'fiction-earlier', 'jobName': '虚构前页岗位'}]}}
        with self.browser([None, [latest, earlier]]) as br:
            result = O.scan(self.cfg, br, pages=2)
        self.assertEqual(result, (2, []))
        self.assertEqual({p.name: json.loads(p.read_text())
                          for p in self.raw.glob('*.json')},
                         {'示例城市_示例关键词_0.json': latest,
                          '示例城市_示例关键词_1.json': earlier})
        # The first index=0 call primes recording; capture reads then use 0, 1.
        self.assertEqual([args['index'] for tool, args in self.transport.calls
                          if tool == 'network'], [0, 0, 1])

    def test_two_pages_save_both_raw_files(self):
        with self.browser([None, [JOBLIST, JOBLIST]]) as br:
            result = O.scan(self.cfg, br, pages=2)
        self.assert_saved(result, count=2)
        self.assertEqual({p.name for p in self.raw.glob('*.json')},
                         {'示例城市_示例关键词_0.json', '示例城市_示例关键词_1.json'})

    def test_empty_first_capture_with_has_more_false_saves_next_capture(self):
        body = {'code': 0, 'zpData': {'jobList': [], 'hasMore': False}}
        with self.browser([None, [body, JOBLIST]]) as br:
            result = O.scan(self.cfg, br, pages=2)
        self.assert_saved(result)
        self.assertEqual({p.name for p in self.raw.glob('*.json')},
                         {'示例城市_示例关键词_1.json'})
        self.assertEqual([args['index'] for tool, args in self.transport.calls
                          if tool == 'network'], [0, 0, 1])

    def test_missing_or_invalid_capture_twice_still_fails_and_cleans_tabs(self):
        for body in (None, 'not JSON', (JOBLIST, True)):
            with self.subTest(body=body):
                with self.assertRaisesRegex(RuntimeError, '^scan_network_failed$'):
                    with self.browser([None, body, body]) as br:
                        O.scan(self.cfg, br, pages=1)
                self.assertFalse(self.transport.tabs)
                self.assertTrue(self.transport.closed)
                self.assertEqual(list(self.raw.iterdir()), [])
                calls = self.transport.calls
                self.assertEqual(sum(tool == 'navigate' for tool, _ in calls), 3)
                self.assertNotIn('reload', [tool for tool, _ in calls])

    def test_empty_or_nonzero_capture_skips_without_retry_and_continues(self):
        self.cfg['targets'][0]['keywords'].append('另一关键词')
        for body in (EMPTY, {'code': 0}, {'code': 99}):
            with self.subTest(body=body):
                with self.browser([None, body, JOBLIST]) as br:
                    result = O.scan(self.cfg, br, pages=1)
                self.assert_saved(result)
                navs = [args for tool, args in self.transport.calls if tool == 'navigate']
                self.assertEqual(len(navs), 2)
                self.assertTrue(all(args['url'] != 'about:blank' for args in navs))

    def test_retry_with_valid_empty_or_nonzero_capture_does_not_fail(self):
        for body in (EMPTY, {'code': 0}, {'code': 99}):
            with self.subTest(body=body):
                with self.browser([None, None, body]) as br:
                    result = O.scan(self.cfg, br, pages=1)
                self.assert_saved(result, count=0)
                self.assertEqual(sum(tool == 'navigate' for tool, _ in self.transport.calls), 3)

    def test_risk_code_keeps_existing_error_without_retry(self):
        with self.assertRaisesRegex(RuntimeError, '^risk_code_37$'):
            with self.browser([None, {'code': 37}]) as br:
                O.scan(self.cfg, br, pages=1)
        self.assertFalse(self.transport.tabs)
        self.assertTrue(self.transport.closed)
        self.assertEqual(list(self.raw.iterdir()), [])
        self.assertEqual(sum(tool == 'navigate' for tool, _ in self.transport.calls), 1)

    def test_later_keywords_reuse_tab_and_each_target_primes_its_own_tab(self):
        self.cfg['targets'][0]['keywords'].append('另一关键词')
        self.cfg['targets'].append({'city': '另一示例城市', 'city_code': '101020100',
                                    'keywords': ['新方向']})
        with self.browser([None, JOBLIST, JOBLIST, None, JOBLIST]) as br:
            result = O.scan(self.cfg, br, pages=1)
        self.assert_saved(result, count=3)
        calls = self.transport.calls
        self.assertEqual(sum(tool == 'tabs' and args['action'] == 'new'
                             for tool, args in calls), 2)
        navs = [args for tool, args in calls if tool == 'navigate']
        self.assertEqual([parse_qs(urlparse(args['url']).query)['query'][0]
                          for args in navs], ['示例关键词', '另一关键词', '新方向'])
        self.assertEqual([args['tabId'] for args in navs], [100001, 100001, 100002])

    def test_without_deadline_two_misses_return_zero(self):
        self.cfg.pop('_deadline')
        with self.browser([None, None, None]) as br:
            result = O.scan(self.cfg, br, pages=1)
        self.assert_saved(result, count=0)
        self.assertEqual(sum(tool == 'navigate' for tool, _ in self.transport.calls), 3)


if __name__ == '__main__':
    unittest.main()
