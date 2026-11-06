"""岗位详情页 JD 读取：投递（outbound）与对话（conversation）共用，只读不点。

audit-01 Q8：原先在 outbound 里，对话模块为此反向依赖投递模块；挪到这里后两边各自依赖本模块。
"""
import re

import chrome_mcp as CM
import greet as G

JD_EXPR = ("(()=>{const sels=['.job-sec-text','.job-detail-section .job-sec-text',"
           "'.job-detail-body','.job-detail-section','.job-detail'];"
           "for(const s of sels){const e=document.querySelector(s);"
           "const t=((e&&e.innerText)||'').replace(/\\s+/g,' ').trim();"
           "if(t.length>60)return t.slice(0,1800);}"
           "const b=(document.body.innerText||'').replace(/\\s+/g,' ');"
           "const m=b.search(/职位描述|岗位职责|工作职责/);"
           "return m>=0?b.slice(m,m+1500):''})()")


def read_jd(br, tab, cfg):
    """读详情页的 JD 正文（截断）。只是读，点任何按钮之前做。"""
    try:
        r = br.js(tab, JD_EXPR, timeout=30)
        t = CM.unwrap(r.get("text") or "")
    except Exception:
        return ""
    t = re.sub(r"\s+", " ", t or "").strip()
    cap = int(G.block(cfg)["llm"].get("max_jd_chars") or 1000)
    return t[: max(cap * 2, cap)]
