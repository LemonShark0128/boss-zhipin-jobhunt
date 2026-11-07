# 命令行与 JSON 输出

所有命令都在仓库根运行，形式是 `python3 scripts/boss_hunt.py` 后接子命令。上手流程见 [README](../README.md)，
接入 Agent / harness 见 [agent-adapters.md](agent-adapters.md)。

## 子命令

```bash
python3 scripts/boss_hunt.py init|validate|template|login-check|preflight
python3 scripts/boss_hunt.py greet --job-id <id> [--times 2] [--no-cache]   # 离线预览现写的招呼语
python3 scripts/boss_hunt.py scan  [--target 名称] [--keyword 词] [--pages 3]
python3 scripts/boss_hunt.py plan  [--top 20]     # 干跑：只报告
python3 scripts/boss_hunt.py send  N              # 真发
python3 scripts/boss_hunt.py inbox [--dry-run]    # 盯消息 + 分类（只读，不发消息）；--dry-run 不开浏览器
python3 scripts/boss_hunt.py chat  [--dry-run]    # 对话模块：回 HR / 发简历（会真发）；--dry-run 离线校准，--dry 连浏览器预演
python3 scripts/boss_hunt.py tick  [--dry-run]    # 定时投递跳入口：按窗口补池 + 投递
python3 scripts/boss_hunt.py run   [--send N] [--inbox]   # 手动一键，定时任务不用它
python3 scripts/boss_hunt.py notify --check       # 推送配置与凭据是否就绪（不联网）
python3 scripts/boss_hunt.py notify --test        # 真发一条测试到你配置的渠道
```

`notify --stdin [--source tick|chat|inbox]` 由 wrapper 调用，把渲染好的播报推出去，一般不用手敲。
加 `--json` 时输出 `channel` / `label` / `configured` / `ok`，`--check` 另有 `problems[]`，发送时另有 `sent` / `error`；
任何输出都不含凭据。渠道与凭据见 [configuration.md](configuration.md#推送定时结果推给谁)。

配置查找顺序：`--config PATH` → `$BOSS_HUNT_CONFIG` → `~/.boss-hunt/config.json`。
运行数据（`config.json`、`state.json`、`raw/`、`runs.log`、`send_ledger.jsonl`、`conversations.json`）默认在
`~/.boss-hunt/`，本项目不会把它们上传到任何地方（招呼语 / 承接句若配置云端模型，见 FAQ「数据会上传到哪」）。

输出示例：

```text
【2026-10-22 12:17 Shanghai】
- bridge:bridge_ok
- extension:extension_ok
- scan: +3 页
- 候选 42（A18/B24）
- A 已发｜商务拓展经理｜某某科技｜20-30K·14薪
- 本轮发出 5，今日累计 5/15
- 近 72 小时新消息 12 条（列表 40）｜对口 4｜感兴趣 2｜约面试 1
- 已关自己开的标签页 2 个
```

### JSON 输出（给 harness / 脚本）

任何命令都能加 `--json`（放全局位置或子命令后面都行）。stdout 只有一个 JSON 对象、
没有其它文本；人读的块照样写进 `runs.log`，日志仍然可 grep。

```bash
python3 scripts/boss_hunt.py plan --json --top 5 | jq '.summary'
python3 scripts/boss_hunt.py inbox --json | jq '.interviews'
python3 scripts/boss_hunt.py validate --json   # 有错时 exit=1
```

固定字段：`schema`（当前 2）、`cmd`、`ok`。常用结构：

| 命令 | 关键字段 |
|--|--|
| `plan --json` | `summary{candidates,grade_a,grade_b,unsent,remaining_today,daily_cap}`、`items[]{grade,score,job_id,title,salary,brand,city,district,flags,url}` |
| `scan --json` | `pages_saved`、`errors[]`、`jobs_total`、`candidates`、`grade_a`、`grade_b` |
| `send --json` | `ok`、`sent`、`day_count`、`daily_cap`、`items[]{job_id,title,brand,salary,result}`、`skipped[]` |
| `inbox --json` | `ok`、`pending_verification`（有待核对的发送时为真，收件箱照常只读）、`unanswered.counts`、`messages_total`、`new_messages`、`new_matches[]`、`interest[]`、`interviews[]`（含 `replied`）；`replied_ok[]`、`reply_failed[]`、`auto_replied[]`、`auto_failed[]`、`manual_reply[]`、`followed_up[]`、`auto_reply_counts` 属已移除的旧回消息链路，恒为空/0，仅为兼容保留 |
| `validate --json` | `ok`、`errors[]`、`warnings[]`、`targets[]`、`daily_cap`、`timezone` |
| `login-check --json` | `logged`、`bridge`、`extension`、`url` |
| `preflight --json` | `logged`、`greeting_switch`、`day_count`、`daily_cap` |
| `template --json` | `text`、`chars`、`marker`、`recommended_chars`、`hard_max` |
| `grade --json` | `grade`、`reason`、`scores`、`final` |
| `grades --json` | `counts`、`applied`、`total`、`items[]` |
| `tick --json` | `actions`、`window`、`workdir`、`dry_run`（干跑）、`send` / `inbox` / `summary`（执行时）；配置有误时 `ok=false` 与 `errors[]`（本跳不跑） |
| `rejects --json` | `total`、`by_kind` / `by_attribution` / `by_reason` / `by_grade` / `by_target` |
| `summary --json` | `sent`、`by_grade`、`rejects`、`no_reply`、`reason_breakdown`、`pending`、`activity` |
| `run --json` | `scan`、`candidates`、`send`、`inbox`、`tabs_closed` |
| `chat --json` | `ok`、`overview`、`notifications[]`、`candidates`、`processed`、`resume_sent`、`round1`/`round2`、`complete`、`manual`、`manual_new`、`declined`、`pinned`、`pending_verification` |
| `unanswered --json` | `ok`、`counts{waiting_me,waiting_them,stale,unknown,pending_verify}`、`items{…}` |
| `greet --json` | `ok`、`job_id`、`title`、`brand`、`jd_source`、`runs[]{text,source,chars,gate_ok,gate_reasons}` |
| `ledger-ack --json` | `ok`、`items[]`（`--list`）或 `event`（核对后）；有错时 `error` |
| `ledger-repair --json` | `ok`、`dry_run` 及诊断字段 |
| `init --json` | `ok`、`config`；已存在时 `error: exists` |

约定：`ok` 只表示「这条命令该做的事做成了」，**不等于「发出去了」**；`ok=false` 时退出码为 1
（未登录、开关不明、没抓到页、配置缺失或无效等）；发送结果看
`items[].result`（`sent` / `skip_already_chatted` / `unverified` …）。`schema` 变更会在
CHANGELOG 注明。
