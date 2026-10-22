---
name: boss-zhipin-jobhunt
description: BOSS直聘求职自动化：访谈需求→登录→筛选→打招呼→盯回复。
---

# BOSS直聘 求职助手（可复用版）

> 公开仓库：<https://github.com/LemonShark0128/boss-zhipin-jobhunt>（MIT）。
> 本目录是技能本体，同时就是仓库工作副本；对外交付给链接即可。
>
> 这个 `SKILL.md` 不是 Hermes 专属：frontmatter 符合 Agent Skills 规范，Hermes 和
> Claude Code 用同一个文件直接装载；Codex / Cursor 等读仓库根的 `AGENTS.md`；
> 各 Agent 的工具名映射见 `docs/agent-adapters.md`。

把「帮用户在 BOSS 上找工作」跑成一个可交付的流程：先访谈需求，再让用户自己登录，
然后按配置筛选、发招呼、盯回复。**所有个人信息都在 `config.json`，代码里没有任何人的经历、
城市或关键词**，所以这个目录可以直接复制给别人用。

两种跑法：

- **脚本模式（推荐）**：`scripts/boss_hunt.py` + huashu-chrome，全程零 token，可定时。
- **代理模式**：没有 huashu-chrome 时，Agent 用**自己的浏览器工具**按
  `docs/agent-driven-mode.md` 逐步做（Hermes 是 `browser_exec(local=True)`；Claude Code /
  Codex 挂 Playwright 或 chrome-devtools MCP；完整映射见 `docs/agent-adapters.md`）。
  耗 token，适合 ≤10 个/天或调试。

## 硬边界（先跟用户讲清楚，别跳过）

1. 只驱动用户**自己已登录**的浏览器。永远不代填手机号、密码、短信码、验证码；
   看到登录墙就停下请用户自己扫码。
2. 不替用户答应面试时间，不改简历，不伪造经历。HR 约时间只回「请先给时段，我确认后答复」。
3. 有平台风控风险，节奏按 `docs/risk-control.md`；不承诺「一定安全」。
4. 同一账号不要再跑第二套自动化（别的油猴脚本/工具），会双发同一条招呼。
5. 收尾只关本技能自己开的标签页，绝不动用户其他页面。

## 前置依赖

Chrome（已登录 BOSS）+ Python 3.9+ + Node ≥20（`npx`）+ [huashu-chrome](https://github.com/alchaincyf/huashu-chrome)
（MIT，运行时 `npx` 拉取，不打包）**及其 Chrome 扩展**。

扩展必须由用户手动装一次：商店
<https://chromewebstore.google.com/detail/foiljmaplphdfimfcnfdpekhdnfbgfbf>，
或 `npx -y huashu-chrome extension` 打印目录后在 `chrome://extensions` 加载已解压的扩展。
装完 `npx -y huashu-chrome doctor` 应显示「Chrome 扩展在线」。
不要替用户装（浏览器不允许脚本代装），也不要改上游代码。

## 五步上线

### 1. 访谈（一次问完）

必问 8 条 + 用户常忘的 10 条，都在 `docs/onboarding-interview.md`。
最少要拿到：**岗位关键词、城市、薪资下限、年限/学历、排除行业、三段带数字的成绩、
每日上限、面试怎么回**。拿不全就先按默认值跑，之后改配置。

### 2. 登录

让用户在自己的 Chrome 里打开 `https://www.zhipin.com` 扫码登录，然后：

```bash
python3 scripts/boss_hunt.py login-check
```

输出 `✅ 已登录（读到头像/用户区）` 才继续；`🔑 未登录` 就等用户登完再跑。

### 3. 生成并校验配置

```bash
python3 scripts/boss_hunt.py init            # 生成 ~/.boss-hunt/config.json
# 编辑 user / targets / filters（字段见下表）
python3 scripts/boss_hunt.py validate        # 离线校验，有 ❌ 必须改
python3 scripts/boss_hunt.py template        # 预览招呼语 + 字数 + 校验标记
```

`template` 输出的字数要 80–200 字；>380 会被拦。校验标记（招呼语开头 8 个字）
是后续判断「到底发出去没有」的依据。

### 4. 干跑校准

```bash
python3 scripts/boss_hunt.py preflight           # 登录 + 招呼语开关 + 今日额度
python3 scripts/boss_hunt.py scan --pages 1      # 抓 1 页验证链路
python3 scripts/boss_hunt.py plan --top 15       # 让用户看名单，不对就调过滤
python3 scripts/boss_hunt.py send 1              # 首跑只发 1 个
```

`preflight` 若报 `auto_greeting_on`，必须让用户关掉 BOSS 自带招呼语
（或 `preflight --fix`）：开着会让 HR 收到两条（默认话术 + 你的模板）。

**验收证据**（缺一条就别说跑通了）：

| 步骤 | 成功标志 |
|--|--|
| scan | `抓取 N 页响应`，N≥1；`库内岗位 M` 与预期同量级 |
| plan | 前几条是用户真想要的方向；不合理就调 `title_allow`/`industry_block` |
| send | 有 `A/B 已发｜岗位｜公司｜薪资` 行，且 `今日累计 1/N` |
| inbox | `近 72 小时新消息 …`；面试类只出现「已回，请先给时段」 |
| 收尾 | `已关自己开的标签页 N 个`；用户自己的页面没动 |

**回归与验证边界**

- 改筛选规则后，别只看「候选从 93 变成 289」这种数量变化：把新规则跑在历史 `raw/`
  快照上，与旧结果做集合差，逐条看差异是不是想要的（新增的猎头/代招/经验超标过滤会
  体现为「旧有我无」）。数量只是线索，差异清单才是证据。
- 真实发送属于用户决策，不要用「发几个试试」验证链路：读路径（`login-check` /
  `scan` / `plan` / `inbox` 只读）跑通 + 防重复守卫跑通（对已投岗位必须返回
  `skip_already_chatted` 且不点按钮）就够，真发先问用户。

### 5. 交给定时任务（可选）

写一个 wrapper（`~/.boss-hunt/run.sh`）：

```bash
#!/bin/bash
cd ~/.boss-hunt
caffeinate -i -t 900 /usr/bin/python3 <skill>/scripts/boss_hunt.py run --send 5 --inbox
```

挂定时与 Agent 无关：Hermes 用 `cronjob_manage` 建 `no_agent=true` 的任务；
Claude Code / Codex 直接写系统 `crontab` 或 `launchd`（见 `docs/agent-adapters.md`）。
时间按 `schedule.windows`（例 `0 11,15,21 * * *`，以配置时区求值）。
纯脚本任务崩了不会有聊天消息，只能从作业状态和 `~/.boss-hunt/data/runs.log` 看到——
改完必须用真实入口跑一遍再交付。

## 常用命令

```bash
python3 scripts/boss_hunt.py init|validate|template|login-check|preflight
python3 scripts/boss_hunt.py scan  [--target 名称] [--keyword 词] [--pages 3]
python3 scripts/boss_hunt.py plan  [--top 20]          # 只报告，不发
python3 scripts/boss_hunt.py send  N                   # 真发
python3 scripts/boss_hunt.py inbox                     # 盯消息 + 分类 + 回面试
python3 scripts/boss_hunt.py run   [--send N] [--inbox]
```

配置文件查找顺序：`--config PATH` → `$BOSS_HUNT_CONFIG` → `~/.boss-hunt/config.json`。

## 配置要点

| 字段 | 作用 | 建议 |
|--|--|--|
| `user.years_experience` / `industry` / `highlights` | 招呼语素材 | 成绩必须带数字 |
| `user.greeting_template` | 招呼语，支持 `{years}{industry}{highlights}{job_name}{brand}` | 80–200 字，至少 2 个占位符 |
| `user.interview_reply` | 面试类自动回复 | 只表达愿意面 + 请对方给时段 |
| `targets[]` | 城市 + 关键词 + 每日配额 | `city_code` 见 `docs/boss-site-notes.md` |
| `filters.salary_min_k` | 薪资下限（K） | 先比心理价低 2K |
| `filters.title_allow` / `title_block` | 头衔白/黑名单（正则） | 命中黑名单且不含白名单 → 拒 |
| `filters.industry_block` / `brand_block` / `hr_block` | 行业/公司/HR 排除 | 加离职公司、竞业对手 |
| `filters.exclude_headhunter` / `exclude_proxy` | 排掉猎头、代招 | 默认开 |
| `filters.experience` | `job_min_years_max` / `job_max_years_min` | 排掉够不到和太初级 |
| `filters.districts_allow` | 通勤圈 | 空 = 不限 |
| `sending.daily_cap` / `min_gap_seconds` | 日上限 / 间隔 | 15–20 / ≥60s |
| `sending.verify_text` | 发送校验文本 | 默认取招呼语开头 8 字 |
| `inbox.interview_auto_reply` | 是否自动回面试类 | 开了也不会替用户定时 |
| `inbox.followup` | 已读不回 48h 补一句 | 默认关，先问用户 |
| `schedule.timezone` / `windows` / `per_window` | 何时跑、每跳几个 | 与系统时区无关 |

改完配置只要重跑 `validate` + `plan`，不用改代码。

## 报告与回复规则

- 一行摘要（本轮发了谁 + 今日累计 N/M + 异常），细节每条一行；列表最多 5 条，其余只报数量。
- 分类：`不合适/已招满` → 跳过；`面试/约时间` → 回「希望线上，请先给时段，我确认后答复」；
  `感兴趣/要简历` → 只报告，不擅自答应任何事。
- 系统消息（「附件简历已发送」「对方已同意」）不是 HR 说话，不要当招呼汇报。
- 用户只发一句「为什么」时，指的多半是他刚收到的播报：先看 `runs.log` 末尾和最近一次输出，
  不要从代码倒着猜。

## 风控 / 排障 / 二次开发

- 额度与触发点：`docs/risk-control.md`（含来源与日期）
- 页面结构、字段、城市码、桥返回格式：`docs/boss-site-notes.md`
- 故障：`docs/troubleshooting.md`
- 没有 huashu-chrome：`docs/agent-driven-mode.md`

## 交给别人之前

> 要把它真发到 GitHub 开源（README/LICENSE/仓库结构/清污/建仓推送），按 `github` 技能的
> 「发布本地项目/技能为公开开源仓库」参考文档走；本技能的 `README.md` + `LICENSE` + `.gitignore`
> 已经按那个流程备好，推送前把 README 里的 `<this-repo>` 换成真实地址。

1. 删掉 `~/.boss-hunt/data/`（`state.json`、`raw/`、`runs.log` 全是真实岗位和沟通记录）。
2. 确认 `config.json` 里没有真实姓名、手机号、现公司敏感信息；
   招呼语里的数字若是真实业绩，交付前问用户是否要保留。
3. 技能目录里不放个人模板、不放 cookie。
4. 交付时附一句：这是自动化辅助，平台规则可能变，账号风险自负；节奏按文档别调高。

## 只跑一套（迁移时怎么搬）

同一个 BOSS 账号只允许**一套实例**在跑：两套并行会互相抢额度，同一家公司/HR 也会收到两条招呼，
风控特征更明显。换版本或换机器时按下面的顺序搬：

1. **配置搬事实**：用户资料、城市与城市码、关键词、模板、每日上限 —— 手工搬进新 `config.json`，
   别整体复制，旧版字段名可能不同。
2. **状态必须交接受保护字段**：`sent`、`contacts`、`replied`、`inbox_seen`、`inbox_ts` 要一起搬，
   否则会给同一批 HR 重发招呼、同一批面试消息重复处理；`day` / `day_count` 也搬，
   否则当天额度凭空重置，会多投一轮。
3. **不要搬的**：`raw/` 只作历史快照（可以留着看，不参与筛选）、旧日志格式、旧代码目录。
4. 搬完按顺序验：`validate` → `template`（核对渲染出的招呼语）→ `plan`（候选数是否合理）
   → 跑**真实入口**（wrapper/定时脚本，而不是直接 `python3 脚本 run`）一次。
5. 确认新的入口跑通后，才停用旧实例（停 cron、归档旧目录），别两个都留着。
