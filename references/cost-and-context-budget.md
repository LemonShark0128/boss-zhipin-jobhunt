# 成本与上下文预算

回答「这个流程到底花多少 token / 时间，能不能更省」时先读这一页。分三类成本，混在一起谈会谈错。

## 三类成本，别混

| 成本 | 花在哪 | 谁承担 |
|--|--|--|
| 磁盘 + CPU | `state.json`、`raw/`、`runs.log`、缓存文件的读写 | 本机，**零 token** |
| 模型 API | 招呼语（每岗 1 次）、自动回复（每条 1 次）、话术合并 | 按次计费 |
| Agent 上下文 | 会话里把文件读进 prompt、把日志贴进对话 | 最贵，且会挤掉后续上下文 |

判据：**脚本 `json.load` 一个大文件不花 token；Agent `read_file` 同一个文件才花。** 用户问「是不是每次都要读一遍」时，先分清是谁在读。

## 实测体量（用于估算，不要当固定值）

- `data/state.json`：几百 KB（含 `sent` 里每条完整招呼语正文、`grade_cache`），约 5–8 万 token
- `data/runs.log`：几百 KB（每轮几百行），约 3–6 万 token
- `data/greet_cache.json`：几十 KB，只增不减（按 `jobId` + 指纹 + `PROMPT_VERSION`）
- `data/human_replies.json`：每条最多 ~800 字（`hr_text` 400 + `final` 400），100 条约 6–7 万 token

结论：**任何会话整份读 state.json 或 runs.log 一次，上下文基本就废了。** 这不是理论风险，是单次操作就能踩到的事故。

## 会话侧的硬规矩

1. 要数字就调 `--json` 摘要（`plan` / `inbox` / `summary` / `rejects` / `grades`），或写个一次性 Python 脚本在终端里算完只打印结论。
2. 要看某一条：按 jobId / 品牌过滤后再打印，不打印整个结构。
3. 要看失败模式：`re.split(r'\n(?=【)', log)` 只取最后一段，再 `Counter` 统计，别 `cat`。
4. 汇报给用户的是结论 + 链接，不是文件内容。

## 模型侧：注入项逐个砍

- 招呼语：`user.greeting.facts` 9–12 条足够；`max_jd_chars` 别随手调到几千；事实条数越多，模型越容易抓错重点（也越贵）。
- 自动回复：只注入**一条** hint brief（`H.find_hint()` 只返回一个字符串），绝不把 `entries` 带进 prompt。
- 话术合并（`human-compact`）：同类多条 brief 合并成一条 canonical，用一次模型调用换掉以后每次的重复注入。
- 缓存：升 `PROMPT_VERSION` 会让历史话术全部失效，之后每条都多一次模型调用 —— 升版前先告知用户这个代价。

## 增长治理（append-only 文件必须设上限）

| 文件 | 治理规则 |
|--|--|
| `human_replies.json` → `entries` | 只保留最近 50 条，超出按月归档到 `human_replies-YYYYMM.jsonl`（只追加、永不读回用于生成） |
| `human_replies.json` → `hints` | 每类情况只留一条合并后的 brief，硬上限 120 字，附 `count` / `last` |
| 读 hints 的路径 | `load(cfg, hints_only=True)` 之类的开关，回复路径不解析 `entries` |
| `greet_cache.json` | 按 `PROMPT_VERSION` 整批作废旧键；要清就给用户算清「多少条会重写」 |
| `runs.log` / `state.sent` | 按天/按月轮转；`sent` 里存整段招呼语正文只为了复核，可按需裁成前 80 字 |
| `raw/` 快照 | 只作回放证据，定期归档；`load_jobs` 应按抓取时间取最新，不按文件名排序 |

## 让成本可见

- 回复日志里记 `hint_injected_chars=<len(brief)>`，一眼看出注入量有没有失控。
- 定时任务是 `no_agent` 纯脚本，本身零 LLM token；真正按次计费的只有招呼语与自动回复。所以「省 token」的第一手段是**把批量活放在脚本里、会话只收一行摘要**，不是给 prompt 加约束。
