#!/usr/bin/env python3
"""只读导出 BOSS 会话列表（每条会话的最后一条消息文本），用于「谁回了 / 回了什么」取证。

用法:
    python3 scripts/dump_conversations.py                 # JSON 打到 stdout
    python3 scripts/dump_conversations.py -o conv.json    # 同时落盘
    python3 scripts/dump_conversations.py --config PATH

不发任何消息；收尾只关自己开的标签页（close_own_tabs），绝不动用户的页面。
副作用：打开消息页会把未读清零（每轮 inbox 巡检本来也会），报给用户时要说明。

输出: {"ok","myuid","pages","total","conv":[{job_id,brand,boss,title_hr,msg_time,ts,text}]}
  · title_hr 是**招聘方自己的职位**（HR / 招聘主管 / HRBP），不是岗位名——
    岗位名按 job_id 回查 ~/.boss-hunt/data/raw/ 快照，查不到再按 brand 匹配。
  · text 可能为空（该岗的 lastMessageInfo 在部分分页响应里是 null），空 ≠ 没回复。
  · 判「有没有回复」不要用 fromId，用 greet_cache.json 的招呼语做文本/时间比对，
    见 references/round-audit.md 第 6 节。
"""
import argparse
import json
import os
import re
import sys
import time

SK = os.path.expanduser("~/.hermes/skills/productivity/boss-zhipin-jobhunt/scripts")
if SK not in sys.path:
    sys.path.insert(0, SK)
import chrome_mcp as CM  # noqa: E402


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--config", default=os.path.expanduser("~/.boss-hunt/config.json"))
    ap.add_argument("-o", "--out", default=None)
    ap.add_argument("--max-pages", type=int, default=6)
    args = ap.parse_args()

    with open(args.config, encoding="utf-8") as f:
        cfg = json.load(f)
    br = CM.Browser(cfg)
    try:
        br.connect()
        tab = br.new_tab("https://www.zhipin.com/web/geek/chat")
        time.sleep(7)

        myuid, items, pages = None, {}, []
        u = br.network(tab, "getUserInfo", index=0, max_body=20000)
        d = CM.parse_network_json(u.get("text") or "")
        if d:
            myuid = (d.get("zpData") or {}).get("userId")

        for i in range(args.max_pages):
            r = br.network(tab, "friend/getG", index=i, max_body=400000)
            d = CM.parse_network_json(r.get("text") or "")
            if not d:
                pages.append(0)
                continue
            res = (d.get("zpData") or {}).get("result") or []
            pages.append(len(res))
            for it in res:
                if isinstance(it, dict) and it.get("encryptJobId"):
                    items[it["encryptJobId"]] = it
            if not res:
                break

        conv = []
        for jid, it in items.items():
            info = it.get("lastMessageInfo") or {}
            ts = info.get("msgTime") or it.get("lastTS") or 0
            conv.append({
                "job_id": jid,
                "brand": it.get("brandName"),
                "boss": it.get("name"),
                "title_hr": it.get("title"),
                "msg_time": time.strftime("%Y-%m-%d %H:%M", time.localtime(ts / 1000)) if ts else None,
                "ts": ts,
                "text": re.sub(r"\s+", " ", info.get("showText") or "").strip(),
            })
        conv.sort(key=lambda x: -(x["ts"] or 0))

        payload = {"ok": True, "myuid": myuid, "pages": pages, "total": len(conv), "conv": conv}
        out = json.dumps(payload, ensure_ascii=False)
        if args.out:
            with open(args.out, "w") as f:
                f.write(out + "\n")
        print(out)
    finally:
        try:
            br.close_own_tabs()
        finally:
            br.close()


if __name__ == "__main__":
    main()
