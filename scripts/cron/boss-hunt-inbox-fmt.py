#!/usr/bin/env python3
"""回消息跳播报：读 `inbox --json` 的 stdout，输出推送给用户的文本。不碰配置和运行数据。

cleanup-02 起 inbox 只读不发，回 HR 只走对话模块（chat）。所以这里只报「读到了什么」：
每跳固定一行概览（约面试 / 感兴趣 / 待你回），失败与待核对优先暴露；约面试最多列 3 条原话。
旧版（部署目录里那份）的「已回 / 失败 / 预演 / 待发资料」属于已移除的旧回话链路，恒为 0，已去掉。
"""
import json
import sys


# audit-01 R4：与 boss_hunt.PENDING_SCOPE_TEXT 同口径；对话挂起只停那个会话，别说成投递也停了
PENDING_TEXT = {
    "conv": "⚠️ 有对话发送待核对（只停那个会话，投递照常）",
    "outbound": "⚠️ 有投递发送待核对，投递暂停中（先确认那个 HR 有没有收到消息）",
    "corrupt": "⚠️ 发送流水文件损坏，投递和对话暂停中（需要先修复流水）",
}


def render(data):
    lines = []
    if data.get("dry_run"):  # onboard-01：inbox --dry-run 离线校准，不是一次真读，别出全 0 概览
        if data.get("message"):
            return [data["message"]]
        return ["🧪 回消息跳干跑失败：%s" % (data.get("error") or "未知")]
    if data.get("ok") is False:
        error = str(data.get("error") or data.get("reason") or "未知")
        if error.startswith("另一个实例在跑"):  # audit-01 R5：不推锁路径
            lines.append("⏭ 回消息跳：上一跳还在跑，本跳跳过")
        else:
            lines.append("⚠️ BOSS 回消息跳失败：%s" % error)
    if data.get("pending_verification"):
        lines.append(PENDING_TEXT.get(data.get("pending_scope"), PENDING_TEXT["outbound"]))
    if data.get("ok") is False:
        return lines  # audit-01 R4：失败时不再跟一行全 0 概览（像是读成功了只是没消息）
    # audit-01 B2：只数对话模块还没回过「请先给时段」的；已回过的由对话跳负责，不再每跳重复列
    interviews = [x for x in (data.get("interviews") or []) if isinstance(x, dict) and not x.get("replied")]
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
        data = json.JSONDecoder().raw_decode(raw[start:])[0]  # 后面跟着 stderr 文本也能读（audit-01 R4）
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
