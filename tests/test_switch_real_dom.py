"""真实 DOM 回归：招呼语开关（2026-10-28 线上实测结构）。

线上实际结构（notify-set?type=greetSet）：
    <span class="ui-switch"><input type="hidden" value="0"><span class="ui-switch-inner"></span></span>
没有 ui-switch-checked、没有 aria-checked —— 只凭 class 判会永远得到 unknown（实测 preflight 就是这样）。
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "scripts"))
import outbound as O  # noqa: E402

CTX = "启用招呼语 启用后，将在沟通时自动发送招呼语"


def row(on=None, hid=None, aria=None, checked=False, t=CTX):
    return {"t": t, "on": on, "hid": hid, "aria": aria, "checked": checked}


class SwitchRealDom(unittest.TestCase):
    def test_hidden_input_off_is_off(self):
        # 线上实测：hidden value=0 就是关闭
        self.assertEqual(O.switch_status([row(on=False, hid="0")]), "auto_greeting_off")

    def test_hidden_input_on_is_on(self):
        self.assertEqual(O.switch_status([row(on=True, hid="1")]), "auto_greeting_on")

    def test_no_evidence_is_unknown(self):
        self.assertEqual(O.switch_status([row(on=None)]), "auto_greeting_unknown")

    def test_switch_pair_conflict_is_unknown(self):
        self.assertEqual(
            O.switch_status([row(on=True), row(on=False)]),
            "auto_greeting_unknown",
        )

    def test_unrelated_rows_ignored(self):
        rows = [row(on=True, hid="1", t="消息免打扰"), row(on=False, hid="0")]
        self.assertEqual(O.switch_status(rows), "auto_greeting_off")

    def test_empty_page_is_unknown(self):
        self.assertEqual(O.switch_status([]), "auto_greeting_unknown")
        self.assertEqual(O.switch_status(None), "auto_greeting_unknown")


if __name__ == "__main__":
    unittest.main()
