"""conv_key 必须每会话唯一：实测 BOSS 消息页 d-c 全表同值，曾把 40 行折叠成 1 个目标。"""
import os, sys, unittest
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'scripts'))
import chat_dom as CD


class StubBrowser:
    def __init__(self, rows):
        self.rows = rows

    def json(self, tab, expr, default=None):
        return [dict(r) for r in self.rows]


class ConvKeyUniqueTest(unittest.TestCase):
    def _rows(self, keys, brands, bosses):
        return [{'conv_key': k, 'brand': b, 'boss': o, 'title': 'HR'}
                for k, b, o in zip(keys, brands, bosses)]

    def test_duplicate_keys_are_rekeyed_by_pair(self):
        rows = self._rows(['id:62001'] * 3, ['甲示例', '乙示例', '丙示例服饰'], ['周一一', '吴二二', '郑三三'])
        out = CD.conversation_rows(StubBrowser(rows), None)
        keys = [r['conv_key'] for r in out]
        self.assertEqual(len(keys), 3)
        self.assertEqual(len(set(keys)), 3, keys)
        self.assertTrue(all(k.startswith('pair:') for k in keys), keys)

    def test_duplicate_pair_falls_back_to_index(self):
        rows = self._rows(['id:62001', 'id:62001'], ['丙示例', '丙示例'], ['张三', '张三'])
        out = CD.conversation_rows(StubBrowser(rows), None)
        keys = [r['conv_key'] for r in out]
        self.assertEqual(len(set(keys)), 2, keys)

    def test_unique_keys_untouched(self):
        rows = self._rows(['id:1', 'id:2'], ['甲示例', '乙示例'], ['周一一', '吴二二'])
        out = CD.conversation_rows(StubBrowser(rows), None)
        self.assertEqual([r['conv_key'] for r in out], ['id:1', 'id:2'])


if __name__ == '__main__':
    unittest.main()
