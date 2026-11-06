#!/usr/bin/env python3
"""回消息跳播报：读 `inbox --json` 的 stdout，输出推送给用户的文本。不碰配置和运行数据。

cleanup-02 起 inbox 只读不发，回 HR 只走对话模块（chat）。所以这里只报「读到了什么」：
每跳固定一行概览（约面试 / 感兴趣 / 待你回），失败与待核对优先暴露；约面试最多列 3 条原话。
旧版（部署目录里那份）的「已回 / 失败 / 预演 / 待发资料」属于已移除的旧回话链路，恒为 0，已去掉。
"""
import json
import sys


def render(data):
    lines = []
    if data.get("ok") is False:
        lines.append("⚠️ BOSS 回消息跳失败：%s" % (data.get("error") or data.get("reason") or "未知"))
    if data.get("pending_verification"):
        lines.append("⚠️ 有待核对的发送，投递和对话暂停中（先确认那个 HR 有没有收到消息）")
    interviews = [x for x in (data.get("interviews") or []) if isinstance(x, dict)]
    interest = data.get("interest") or []
    counts = (data.get("unanswered") or {}).get("counts") or {}
    lines.append("💬 回消息跳｜约面试 %d｜感兴趣 %d｜待你回 %d" % (
        len(interviews), len(interest), int(counts.get("waiting_me") or 0)))
    for item in interviews[:3]:
        lines.append("· %s｜%s" % (item.get("brand") or item.get("brandName") or "?",
                                   (item.get("text") or "")[:40]))
    if len(interviews) > 3:
        lines.append("· 另有 %d 个约面试" % (len(interviews) - 3))
    return lines


def main():
    raw = sys.stdin.read()
    start = raw.find("{")
    if start < 0:
        print("⚠️ BOSS 回消息跳：输出里没有 JSON")
        print(raw[-300:])
        return 0
    try:
        data = json.loads(raw[start:])
        if not isinstance(data, dict):
            raise ValueError("json is not an object")
    except Exception:
        print("⚠️ BOSS 回消息跳失败：输出无法解析")
        print(raw[-300:])
        return 0
    try:
        lines = render(data)
    except Exception as exc:
        lines = ["⚠️ BOSS 回消息跳：播报渲染失败 %s" % exc]
    print("\n".join(str(x) for x in lines))
    return 0


if __name__ == "__main__":
    sys.exit(main())
