#!/usr/bin/env python3
"""投递跳的播报格式化：读 tick --json 的 stdout，输出要推到微信的文本。

规则（用户 2026-10-28 明确要"每跳都能看到"）：
- 窗口内每跳至少一行；0 发出时补一行跳过原因
- 窗口外保持静默；两个例外：①「错过窗口」提示（每个窗口只报一次）② 窗口外的补跑不输出「今日已发」
- 任何异常都不能让播报吞掉业务结果，也不能让 wrapper 误报成功
"""
import json
import os
import sys
from datetime import datetime

SKIP_NAMES = {
    "skip_same_hr": "同HR已投",
    "skip_grade": "判级不过",
    "skip_jd_redline": "踩JD红线",
    "skip_no_button": "无沟通按钮",
    "skip_already_chatted": "已投过",
    "skip_captcha": "验证码",
    "skip_need_app": "需跳App",
}

STOP_NAMES = {
    "quota_exhausted": "额度用尽",
    "captcha": "撞验证码，本轮停",
    "network": "网络/登录异常，本轮停",
    "inspect_cap": "JD读取到上限",
    "pool_exhausted": "池子已空",
    "none": "",
}


def render(d):
    lines = []
    win = d.get("window") or (d.get("catch_up") or {}).get("window") or "?"
    err = str(d.get("error") or "")
    if err.startswith("unresolved_send"):
        lines.append("⛔ 有待核对的发送，投递和回复全部暂停")
        lines.append("   处理：python3 boss_hunt.py ledger-ack --list")
    elif "captcha" in err:
        lines.append("⚠️ 撞验证码，本轮停（额度留到下跳）")
    elif d.get("ok") is False:
        lines.append("⚠️ BOSS 投递跳失败：%s" % (d.get("error") or d.get("reason") or "未知"))
    if d.get("reason") == "already_done":
        lines.append("⏭ %s 跳已完成，跳过" % win)

    if d.get("bridge_recovered"):
        lines.append("桥离线已唤醒 Chrome 并恢复")
    catch_up = d.get("catch_up")
    if catch_up:
        lines.append("🔁 补跑 %s 跳：发出 %d" % (catch_up["window"], catch_up["sent"]))

    today = d.get("today_sent") or {}
    count = int(today.get("count") or 0)
    if count > 0 and d.get("window"):
        items = (today.get("items") or [])[:5]
        brands = "、".join("%s(%s)" % (item.get("brand", ""), item.get("grade", "")) for item in items)
        if count > len(items):
            brands += "＋%d" % (count - len(items))
        lines.append("📋 今日已发 %d：%s" % (count, brands))
    caught_up = (d.get("catch_up") or {}).get("window")
    for missed in d.get("missed") or []:
        if caught_up and missed.get("window") == caught_up:
            continue  # 已经补跑过这一跳，不再当成「被吞了」重复播报
        lines.append("⏰ %s 跳被吞了（晚 %s 分钟，超出 ±10 分钟容差），额度已顺延" % (
            missed["window"], missed["delay_min"]))

    send = d.get("send")
    if isinstance(send, dict) and send:
        sent = int(send.get("sent") or 0)
        head = "📮 %s 跳｜发出 %d｜今日 %d/%d" % (
            win, sent, int(send.get("day_count") or 0), int(send.get("daily_cap") or 0))
        if sent:
            by_grade = send.get("by_grade")
            if isinstance(by_grade, dict):
                parts = ["%s %d" % (k, v) for k, v in by_grade.items() if isinstance(v, int)]
                if parts:
                    head += "（" + " · ".join(parts) + "）"
        lines.append(head)
        if not sent:
            counts = {}
            for item in (send.get("skipped") or []):
                result = item.get("result") if isinstance(item, dict) else None
                if result:
                    counts[result] = counts.get(result, 0) + 1
            top = sorted(counts.items(), key=lambda kv: -kv[1])[:3]
            if top:
                lines.append("   " + " · ".join("%s %d" % (SKIP_NAMES.get(k, k), v) for k, v in top))
            else:
                quiet = send.get("quiet")
                qtop = sorted(quiet.items(), key=lambda kv: -kv[1])[:3] if isinstance(quiet, dict) else []
                if qtop:
                    lines.append("   " + " · ".join("%s %d" % kv for kv in qtop))
                reason = STOP_NAMES.get(str(send.get("stop_reason") or "none"), "")
                if reason:
                    lines.append("   " + reason)
            pending = send.get("retry_pending")
            if isinstance(pending, int) and pending > 0:
                lines.append("   🔁 待重试 %d 个" % pending)

        if sent > 0 and send.get("stop_reason") == "inspect_cap":
            quiet = send.get("quiet") or {}
            pending = quiet.get("JD读取上限，留到下一跳")
            if isinstance(pending, int):
                lines.append("⏳ JD读取到上限，还有 %d 个候选留下跳" % pending)
            else:
                lines.append("⏳ JD读取到上限，部分候选留下跳")

    if d.get("scan_error"):
        lines.append("⚠️ 补池失败（%s），本跳用旧快照继续投" % d["scan_error"])
    scan = d.get("scan")
    if isinstance(scan, dict) and scan.get("pages_saved") is not None:
        lines.append("🔄 补池 %d 页" % int(scan.get("pages_saved") or 0))

    inbox = d.get("inbox")
    if isinstance(inbox, dict) and inbox:
        counts = inbox.get("auto_reply_counts") or {}
        if counts.get("new_greet", 0) > 0:
            lines.append("自动回话：新招呼 %d 个｜回 %d｜需人工 %d｜打开失败 %d｜我最后说过 %d｜归属不明 %d" % (
                counts["new_greet"], counts["replied"], counts["manual"], counts["open_failed"],
                counts["already_mine"], counts["owner_unknown"]))
        interviews = inbox.get("interviews") or []
        replied = inbox.get("auto_replied") or inbox.get("replied_ok") or []
        failed = inbox.get("auto_failed") or inbox.get("reply_failed") or []
        lines.append("💬 回消息｜已回 %d｜约面试 %d｜失败 %d" % (
            len(replied), len(interviews), len(failed)))

    summary = d.get("summary")
    if isinstance(summary, dict) and summary:
        if not summary.get("activity"):
            lines.append("📊 今天没有活动")
        else:
            grades = summary.get("by_grade") if isinstance(summary.get("by_grade"), dict) else {}
            parts = ["%s %d" % (k, v) for k, v in grades.items() if isinstance(v, int)]
            lines.append("📊 今天小结")
            lines.append("投递 %d/%d（%s）" % (summary.get("sent", 0), summary.get("daily_cap", 0), " · ".join(parts)))
            lines.append("被「不合适」%d 个｜72h 无回复 %d 个｜累计 %d" % (
                summary.get("rejects", 0), summary.get("no_reply", 0), summary.get("cumulative", 0)))
            breakdown = summary.get("reason_breakdown")
            if isinstance(breakdown, dict) and breakdown:
                lines.append("原因：" + "、".join("%s %d" % (k, v) for k, v in breakdown.items()))
            lines.append("待你确认 %d 条" % summary.get("pending", 0))
    return lines


def main():
    raw = sys.stdin.read()
    start = raw.find("{")
    data = {}
    if start < 0:
        lines = ["⚠️ BOSS 投递跳：输出里没有 JSON", raw[-300:]]
    else:
        try:
            data = json.loads(raw[start:])
            if not isinstance(data, dict):
                raise ValueError("json is not an object")
        except Exception:
            data = {}
            lines = ["⚠️ BOSS 投递跳失败：输出无法解析", raw[-300:]]
        else:
            try:
                lines = render(data)
            except Exception as exc:  # 播报坏掉不能掩盖业务结果
                lines = ["⚠️ BOSS 投递跳：播报渲染失败 %s" % exc]
    if lines:
        text = "\n".join(str(x) for x in lines)
        print(text)
        try:
            path = os.path.expanduser(os.environ.get("BOSS_BROADCAST_LOG") or "~/.boss-hunt/data/broadcasts.log")
            os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
            with open(path, "a", encoding="utf-8") as log:
                log.write("[%s] window=%s\n%s\n---\n" % (
                    datetime.now().strftime("%Y-%m-%d %H:%M:%S"), data.get("window") or "?", text))
        except Exception as exc:
            print("播报落盘失败:%s" % exc, file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
