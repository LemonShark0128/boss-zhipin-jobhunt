"""对话跳：先看对方问什么，再决定回什么（2026-11-01 真实事故回归）。

三条原话来自 12:31–12:33 的实际流水：HR 问经验 / 说条件 / 要简历，
对话跳却一律回「面试大概有几轮？可以安排线上面试吗？」。
"""
import os
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, os.pardir, "scripts"))

import conv_rules as R  # noqa: E402

# 真实/hr 侧话术 → 期望意图
CASES = [
    ("请问有相关工作经验吗？可以行业专业岗位开始吗？", "experience"),
    ("我们这边主要是做抖音团购这个板块的业务的，无责4000+前置收费或者置换按最高3", "manual"),
    ("简历发我一下", "resume"),
    ("底薪加奖金，面试两轮，可以线上", "other"),
    ("方便聊聊吗？", "greet"),
    ("明天下午三点来面试可以吗", "interview"),
    ("不太合适，后续有机会再沟通", "reject"),
]


class ChatIntentTest(unittest.TestCase):
    def test_real_messages_classified(self):
        for text, expect in CASES:
            with self.subTest(text=text):
                self.assertEqual(R.classify(text), expect)

    def test_round2_template_never_sent_for_manual_messages(self):
        """人工类消息不得触发 round2 模板（这就是事故的形态）。"""
        for text, expect in CASES:
            if expect not in ("manual", "experience"):
                continue
            record = {
                "brand": "虚构公司", "title": "虚构岗位", "boss": "虚构HR",
                "origin": "reply", "status": "engaged",
                "last_seen": {"ts": 1, "text": ""},
                "answers": {"requirements": "虚构要求", "salary_structure": None,
                            "interview_rounds": None, "online_interview": None},
                "asked_round1_ts": 1, "asked_round2_ts": None,
                "resume_sent_ts": None, "updated_ts": 0,
                "notified": {"manual": False, "complete": False, "closed": False, "declined": False},
                "actions": [],
            }
            ctx = {"cfg": {}, "now_ts": 2, "history": [],
                   "extract": lambda msgs: {"salary_structure": "虚构底薪"},
                   "opener": lambda: "虚构开场", "job_ok": True}
            actions, row = R.decide(record, [{"text": text, "ts": 2}], ctx)
            texts = [a["text"] for a in actions if a["type"] == "send_text"]
            self.assertFalse(any("面试" in t for t in texts), (text, texts))
            self.assertEqual(row["status"], "manual")


if __name__ == "__main__":
    unittest.main()
