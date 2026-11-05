"""Real captured markup; offline Python DOM substitute, not a JS runtime test."""
import json
import re
import sys
import tempfile
import unicodedata
import unittest
from html.parser import HTMLParser
from pathlib import Path
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
import chat_dom as CD
import boss_hunt as B
import hunt_filter as HF
import hunt_state as HS


# Capture from the construction order, retaining all five real DOM features.
REAL_ROW = '''<div class="friend-content" d-c="{dc}">
  <div class="figure"><img class="image-circle" src="…"></div>
  <div class="text">
    <div><span class="time">14:23</span></div>
    <div class="title-box"><span class="name-box">
      <span class="name-text"> 周一一 </span>
      <span> {brand} </span><i class="vline"></i><span> 猎头顾问 </span>
    </span></div>
    <div class="gray last-msg"><span class="last-msg-text">您正在与Boss周一一沟通</span>…</div>
  </div>
</div>'''


class Node:
    def __init__(self, tag='', attrs=()):
        self.tag, self.attrs, self.children = tag, dict(attrs), []

    def descendants(self):
        for child in self.children:
            if isinstance(child, Node):
                yield child
                yield from child.descendants()

    def has_class(self, name):
        return name in self.attrs.get('class', '').split()

    def text(self):
        return ''.join(c.text() if isinstance(c, Node) else c for c in self.children)


class CapturedDOM(HTMLParser):
    def __init__(self, html):
        super().__init__()
        self.root = Node()
        self.stack = [self.root]
        self.feed(html)

    def handle_starttag(self, tag, attrs):
        node = Node(tag, attrs)
        self.stack[-1].children.append(node)
        if tag not in {'img', 'br', 'hr', 'input', 'meta', 'link'}:
            self.stack.append(node)

    def handle_endtag(self, tag):
        self.stack.pop()

    def handle_data(self, data):
        self.stack[-1].children.append(data)

    def rows(self):
        """Independent interpretation of captured HTML, never calls production helpers."""
        def norm(value):
            return re.sub(r'\s+', '', unicodedata.normalize('NFKC', value)).lower()

        rows = []
        for row in self.root.descendants():
            if not row.has_class('friend-content'):
                continue
            names = [n for n in row.descendants() if n.has_class('name-text')]
            boxes = [n for n in row.descendants() if n.has_class('name-box')]
            spans = [n for n in boxes[0].descendants()
                     if n.tag == 'span' and not n.has_class('name-text')
                     and not n.has_class('vline')] if boxes else []
            boss = names[0].text().strip() if names else ''
            brand = spans[0].text().strip() if spans else ''
            title = spans[1].text().strip() if len(spans) > 1 else ''
            key = ''
            # d-c 真机实测 40 行同值（组件 id），与生产实现一致地排除在外
            for attr in ('data-conversation-id', 'data-conv-id', 'data-session-id', 'data-chat-id'):
                if row.attrs.get(attr):
                    key = 'id:' + norm(row.attrs[attr])
                    break
            if not key and brand and boss:
                key = 'pair:' + norm(brand) + '|' + norm(boss)
            rows.append(dict(brand=brand, boss=boss, title=title, conv_key=key,
                             text=re.sub(r'\s+', ' ', row.text()).strip()))
        return rows


class DOMBrowser:
    def __init__(self, html):
        self.html = html
        self.expression = ''

    def json(self, tab, expression, default=None):
        self.expression = expression
        # Match bridge JSON serialization, with values derived only from the HTML.
        return json.loads(json.dumps(CapturedDOM(self.html).rows(), ensure_ascii=False))


class ConversationDOMTests(unittest.TestCase):
    def test_rows_from_real_dom(self):
        br = DOMBrowser(''.join(REAL_ROW.format(dc=62001 + n, brand='丁示例集团%d' % n)
                               for n in range(40)))
        rows = CD.conversation_rows(br, 1)
        self.assertEqual(len(rows), 40)
        for n, row in enumerate(rows):
            # 只有 d-c、没有真会话属性时，键必须落在「品牌|HR」上，且 40 行互不相同
            self.assertEqual(row['conv_key'], 'pair:丁示例集团%d|周一一' % n)
            self.assertEqual(row['brand'], '丁示例集团%d' % n)
            self.assertEqual(row['boss'], '周一一')
            self.assertEqual(row['title'], '猎头顾问')
        # Explicit emitted-JS contract checks complement the Python substitute.
        # These do not claim to execute JavaScript or validate browser behavior.
        self.assertIn("textOf('.name-text')", br.expression)
        self.assertIn("querySelector('.name-box')", br.expression)
        self.assertIn("querySelectorAll('span:not(.name-text):not(.vline)')", br.expression)
        self.assertIn("title:", br.expression)

    def test_identity_never_uses_dc(self):
        """真机取证：消息页 40 行的 d-c 全是 "62001"（组件 id），拿它当 conv_key 会把 40 行折叠成 1 个目标。"""
        html = REAL_ROW.format(dc='62001', brand='丁示例集团')
        # 只有 d-c 时必须走「品牌|HR」，不能是 id:62001
        self.assertEqual(CD.conversation_rows(DOMBrowser(html), 1)[0]['conv_key'], 'pair:丁示例集团|周一一')
        self.assertNotIn("'d-c'", CD.CONVERSATION_JS)
        # 真正的会话属性仍然优先
        html2 = html.replace('d-c="62001"', 'data-conversation-id="old" d-c="62001"')
        self.assertEqual(CD.conversation_rows(DOMBrowser(html2), 1)[0]['conv_key'], 'id:old')
        # 什么都没有就留空，交给上层报警
        self.assertEqual(CD.conversation_rows(DOMBrowser('<div class="friend-content"></div>'), 1)[0]['conv_key'], '')
        self.assertIn("'pair:' + norm(brand) + '|' + norm(boss)", CD.CONVERSATION_JS)

    def test_zero_targets_warns(self):
        with tempfile.TemporaryDirectory(prefix='conversation-dom-') as root:
            cfg = {'paths': {'workdir': root, 'state': str(Path(root) / 'state.json')},
                   'inbox': {'auto_reply': {'enabled': True}, 'interview_auto_reply': False}}
            st = HS.load_state(cfg)
            br = Mock()
            br.network.return_value = {'text': json.dumps({'code': 0, 'zpData': {'result': []}})}
            for count in (40, 0):
                out = []
                with self.subTest(count=count), \
                     patch.object(B, 'scan_conv_states', return_value=dict(checked=0, failed=0, skipped_failures=0, deadline=False)), \
                     patch.object(HF, 'load_jobs', return_value=[]), \
                     patch.object(CD, 'goto_tab', return_value=True), \
                     patch.object(CD, 'conversation_rows', return_value=[{'conv_key': ''} for _ in range(count)]), \
                     patch.object(CD, 'open_by_index') as opener, \
                     patch.object(B, 'send_in_chat') as sender, \
                     patch.object(B.R, 'generate') as generate:
                    B.inbox(cfg, br, 1, st, out)
                warning = '⚠️ 新招呼 40 个但一个会话 ID 都读不出（页面结构可能已改版），自动回话跳过'
                self.assertEqual(warning in out, count > 0)
                opener.assert_not_called()
                sender.assert_not_called()
                generate.assert_not_called()


if __name__ == '__main__':
    unittest.main()
