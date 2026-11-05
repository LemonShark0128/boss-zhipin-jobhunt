#!/usr/bin/env python3
"""回消息判定的离线测试集（只读，不开浏览器、不发送、不写 state）。

用法：
    python3 scripts/reply_eval.py                 # 闸门 + 离线模板（不读真实配置、不调 LLM）
    python3 scripts/reply_eval.py --llm           # 自动回的用例再跑一遍真实生成，看文案
    python3 scripts/reply_eval.py --json          # 机器可读

判定口径：
    expect=auto   期望自动回（答应 + 一个反问）
    expect=manual 期望转人工（薪资/条件/面试时间/追问细节/需上文/拒绝）
通过 = 引擎判定与 expect 一致。
另外给 auto 用例标 template_mismatch：HR 问的是要简历/定时间，模板却反问区域，属答非所问。
"""
import argparse
import json
import os
import sys
import re
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import reply as R  # noqa: E402
import hunt_config as HC  # noqa: E402

FIXTURE = os.path.join(HERE, os.pardir, "tests", "fixtures", "reply_cases.jsonl")

# 这些意图用通用模板反问「区域/团队」算答非所问
MISMATCH_HINTS = (
    ("time", ("面试", "时间", "几点", "明天", "后天")),
)


def load_cases(path=FIXTURE):
    cases = []
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                cases.append(json.loads(line))
    return cases


def template_mismatch(case, text, source):
    if source != "template":
        return ""
    hr = case.get("hr") or ""
    if re.search(r'发(?:份|一份)?简历|简历发我|发我一份简历', hr):
        if not re.search(r'整理.{0,8}发(?:您|给您)', text):
            return "模板答非所问(resume)"
    for name, keys in MISMATCH_HINTS:
        if any(k in hr for k in keys):
            return "模板答非所问(%s)" % name
    return ""


def run(cases, cfg, use_llm=False):
    # Offline evaluation must not pick up learned briefs or invoke the LLM.
    if not use_llm:
        with tempfile.TemporaryDirectory(prefix='reply-eval-') as root:
            return _run(cases, {'paths': {'human_replies': os.path.join(root, 'human.json')}})
    return _run(cases, cfg)


def _run(cases, cfg):
    rows = []
    for case in cases:
        hr = case["hr"]
        hist = case.get("history")
        ok, why = R.judge(hist) if hist else R.self_contained(hr)
        got = "auto" if ok else "manual"
        row = {"id": case["id"], "hr": hr, "expect": case["expect"], "gate": got,
               "pass": got == case["expect"], "why": case.get("why") or "",
               "gate_reason": why, "text": "", "source": "gate-only", "flag": ""}
        if got == "auto":
            gen = R.generate(cfg, {"encryptJobId": case["id"], "jobName": "城市经理"},
                             hr, history=hist)
            row["text"] = gen.get("text") or ""
            row["source"] = gen.get("source") or ""
            row["flag"] = template_mismatch(case, row["text"], row["source"])
            if not row["text"]:
                row["flag"] = (row["flag"] + " 生成失败:" + ",".join(gen.get("reasons") or [])).strip()
        rows.append(row)
    return rows


def summarize(rows):
    total = len(rows)
    passed = sum(1 for r in rows if r["pass"])
    fp = [r for r in rows if r["expect"] == "manual" and r["gate"] == "auto"]
    fn = [r for r in rows if r["expect"] == "auto" and r["gate"] == "manual"]
    flags = [r for r in rows if r.get("flag")]
    return {"total": total, "pass": passed, "miss": total - passed,
            "false_auto": len(fp), "false_manual": len(fn),
            "flagged": len(flags), "false_auto_ids": [r["id"] for r in fp],
            "false_manual_ids": [r["id"] for r in fn],
            "flagged_ids": [r["id"] for r in flags]}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--llm", action="store_true", help="自动回的用例跑真实生成（会调 LLM）")
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--config", default=os.path.expanduser("~/.boss-hunt/config.json"))
    args = ap.parse_args()

    cfg = HC.load_config(args.config, create_dirs=False) if args.llm else {}
    rows = run(load_cases(), cfg, use_llm=args.llm)
    s = summarize(rows)
    if args.json:
        print(json.dumps({"summary": s, "rows": rows}, ensure_ascii=False, indent=1))
        return 0 if s["miss"] == 0 and s["flagged"] == 0 else 1

    print("id       判定   预期   一致 来源        文案 / 备注")
    print("-" * 96)
    for r in rows:
        mark = "✓" if r["pass"] else "✗"
        note = r["text"] or r["flag"] or r["gate_reason"]
        print("%-8s %-6s %-6s %s   %-10s %s" % (r["id"], r["gate"], r["expect"], mark,
                                                r["source"], note[:46]))
    print("-" * 96)
    print("共 %d 例｜通过 %d｜不一致 %d｜误自动 %d(%s)｜漏自动 %d(%s)｜答非所问 %d(%s)" % (
        s["total"], s["pass"], s["miss"], s["false_auto"], ",".join(s["false_auto_ids"]) or "-",
        s["false_manual"], ",".join(s["false_manual_ids"]) or "-",
        s["flagged"], ",".join(s["flagged_ids"]) or "-"))
    return 0 if s["miss"] == 0 and s["flagged"] == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
