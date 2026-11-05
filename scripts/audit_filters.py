#!/usr/bin/env python3
"""回放历史 raw 快照，检查筛选规则有没有把不该投的岗位放进来。

不联网、不发消息，只读 workdir 里的 raw/ 与 config.json。

用法:
    python3 scripts/audit_filters.py                     # 用 ~/.boss-hunt/config.json
    python3 scripts/audit_filters.py --config other.json
    python3 scripts/audit_filters.py --residue '工程师|架构师|产品经理'

输出：候选总数、A/B 分级、残留清单。残留 > 0 时退出码 1。
"""
import argparse
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import hunt_config as HC  # noqa: E402
import hunt_filter as HF  # noqa: E402
import hunt_state as HS  # noqa: E402

DEFAULT_RESIDUE = r"工程师|架构师|技术|实施|研发|算法|开发|运维|测试|产品经理|产品负责人|产品总监"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--config", default=os.path.expanduser("~/.boss-hunt/config.json"))
    ap.add_argument("--residue", default=DEFAULT_RESIDUE,
                    help="判定为不该投的标题正则（默认技术/产品岗）")
    ap.add_argument("--top", type=int, default=10)
    a = ap.parse_args()

    cfg = HC.load_config(os.path.expanduser(a.config))
    jobs = HF.load_jobs(cfg)
    cs = HS.candidates(cfg)
    print("原始岗位 %d｜候选 %d" % (len(jobs), len(cs)))
    print("分级：A %d / B %d" % (sum(1 for x in cs if x["grade"] == "A"),
                                 sum(1 for x in cs if x["grade"] == "B")))

    pat = re.compile(a.residue)
    bad = [x for x in cs if pat.search(x["job"].get("jobName") or "")]
    print("残留 %d" % len(bad))
    for x in bad[:a.top]:
        print("  ! %s｜%s｜%s" % (x["grade"], x["job"].get("jobName"),
                                  x["job"].get("brandName") or ""))

    print("头部 %d 条：" % a.top)
    for x in cs[:a.top]:
        j = x["job"]
        print("  %s %2d｜%s｜%s｜%s" % (x["grade"], x["score"], j.get("jobName"),
                                        j.get("brandName") or "", j.get("salaryDesc") or ""))
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
