"""Offline tab cleanup regressions using fictional IDs and a fake transport."""
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
from chrome_mcp import Browser


def response(text='', is_error=False):
    return {'text': text, 'images': [], 'isError': is_error, 'raw': {}}


class FakeTransport:
    def __init__(self, replies):
        self.replies = replies
        self.calls = []

    def call(self, tool, arguments, timeout):
        self.calls.append((tool, dict(arguments), timeout))
        reply = self.replies[arguments['tabId']]
        if isinstance(reply, Exception):
            raise reply
        return reply


class CloseOwnTabsTests(unittest.TestCase):
    def browser(self, opened, replies):
        browser = Browser({})
        browser.opened = list(opened)
        browser.c = FakeTransport(replies)
        return browser

    def test_missing_tab_is_removed_without_inflating_actual_close_count(self):
        browser = self.browser([101, 102], {
            101: response('closed'),
            102: response('[NO_TAB] 标签页 102 不存在\n→ 没有可用的标签页。', True),
        })
        self.assertEqual(browser.close_own_tabs(), 1)
        self.assertEqual(browser.opened, [])
        self.assertEqual(browser.close_own_tabs(), 0)
        self.assertEqual(len(browser.c.calls), 2)

    def test_missing_tab_chinese_message_without_code_is_removed(self):
        browser = self.browser([102], {
            102: response('关闭失败：标签页 102 不存在', True),
        })
        self.assertEqual(browser.close_own_tabs(), 0)
        self.assertEqual(browser.opened, [])

    def test_other_error_keeps_only_failed_tab_pending(self):
        browser = self.browser([101, 102, 103], {
            101: response('closed'),
            102: response('[NO_TAB] missing', True),
            103: response('[INTERNAL] 关闭失败（不是 [NO_TAB]）', True),
        })
        with self.assertRaisesRegex(RuntimeError, r'tab_cleanup_failed: closed=1 pending=\[103\]'):
            browser.close_own_tabs()
        self.assertEqual(browser.opened, [103])

    def test_close_exception_still_fails_and_remaining_tabs_are_attempted(self):
        browser = self.browser([101, 102], {
            101: RuntimeError('[NO_TAB] transport exception'),
            102: response('closed'),
        })
        with self.assertRaisesRegex(RuntimeError, r'tab_cleanup_failed: closed=1 pending=\[101\]'):
            browser.close_own_tabs()
        self.assertEqual(browser.opened, [101])

    def test_only_owned_ids_are_closed_once_without_reload(self):
        browser = self.browser([102, 101, 102], {
            101: response('closed'), 102: response('closed'),
            999: response('unrelated user tab'),
        })
        self.assertEqual(browser.close_own_tabs(), 2)
        self.assertEqual(browser.opened, [])
        self.assertEqual(browser.c.calls, [
            ('tabs', {'action': 'close', 'tabId': 101}, 30),
            ('tabs', {'action': 'close', 'tabId': 102}, 30),
        ])


if __name__ == '__main__':
    unittest.main()
