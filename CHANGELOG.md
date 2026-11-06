# 变更记录

本项目遵循 [语义化版本](https://semver.org/lang/zh-CN/)；格式参考
[Keep a Changelog](https://keepachangelog.com/zh-CN/1.1.0/)。

## [Unreleased]

## [0.4.1] - 2026-11-06

### 修复
- `init` 生成的示例配置日上限改为 20（`grade_quota` A15 / B5，合计等于 `daily_cap`），与 README「默认 15–20/天」一致；
  新用户第一次 `validate` 不再报「daily_cap 偏高」。README、SKILL、访谈清单里的配额示例同步。

## [0.4.0] - 2026-11-06

### 新增 · 上手与换 Agent
- 三条定时 wrapper 都在仓库里：新增投递跳 `scripts/cron/boss-hunt-tick.sh`，与对话跳、回消息跳共用同一把 flock；
  额外参数原样转给子命令（`--dry-run` 不再被丢掉变成真跳）。
- `chat --dry-run`、`inbox --dry-run`：离线校准，只校验配置并说明真跳会做什么，不开浏览器、不拿锁、不写 state / 日志；
  挂定时前三条 wrapper 都能加 `--dry-run` 校准一遍。`chat --dry`（连浏览器预演）不变。
- `validate` 检查 `user.greeting.llm.provider`：只认 ollama / deepseek / openai / cloud（Kimi、MiniMax 等 OpenAI 兼容服务写 `openai`）；
  以前写成厂商名会被静默当成本地 Ollama。云端 provider 必须显式写 http(s) `base_url`；找不到 key 给警告。
  招呼语 llm 模式或对话模块开着时算错误，否则只提醒。
- `docs/agent-adapters.md` 新增 WorkBuddy / DeepSeek Harness / Kimi Code / MiniMax Code 的接法（逐项标已核实 / 未核实），
  以及 DeepSeek / Kimi / MiniMax 三段可直接粘贴的模型配置；README、SKILL、AGENTS、访谈清单补上新用户从零的完整顺序。

### 变更
- 配置校验更早、更严：`tick`（含干跑）与 `chat` 一进入就校验配置，有错本跳不跑并指名报错；校验器遇到类型不对的字段不再崩、
  会指出键名；对话配置补布尔 / 整数 / 列表类型校验；空串正则不再算合法；`conversation`、`decline_text`、`pin_manual`
  写 `null` 视同没写。
- 配置模板去掉收件箱旧正则；`run` 的顺延缺省与真发一致；`unanswered` 超时阈值默认跟 `inbox.stale_hours`。
- 定时写法改为挂在 `schedule.windows` 整点，不再建议每 10 分钟跑 tick（会和对话跳抢锁）。

### 修复
- `--json` 在配置损坏、配置路径是目录、配置段形状不对、连桥失败时也只输出一个 JSON；文本模式遇到非预期异常保留堆栈。
- 播报：对话跳推送不再带账本命令与 event_id；锁被占、流水损坏、读不到配置时推人话、不带本机路径；回消息跳失败不再跟一行全 0 概览；
  「约面试 N」只数还没回过的；待核对提示分清对话挂起 / 投递挂起 / 流水损坏；投递跳播报认配置错误与干跑，干跑不写 `broadcasts.log`；
  `broadcasts.log` 跟配置的 workdir 走、时间按配置时区；播报脚本容忍 JSON 前后的杂讯。
- 配置的系统消息正则在对话模块各处与发送闸门都生效。
- 招呼语缓存坏了不再整份覆盖（改名留证）；招呼语缓存或 `conversations.json` 坏了收件箱照常只读；自定义与默认面试话术都认。
- `.env` 里的空占位不再挡住后面文件的真 key，认 `export` 前缀。

## [0.3.0] - 2026-11-06

### 变更 · 收件箱只读（破坏性）
- `inbox` 只读不发：只做分类与播报，不再自动回话、跟进或发简历；回 HR 与发简历只走对话模块 `chat`。
- 移除旧回话链路：`scripts/reply.py`、`scripts/human.py`、`scripts/reply_eval.py` 与 `reply-to` 等子命令，
  以及 `inbox.auto_reply` / `inbox.followup` / `interview_auto_reply` 配置项。旧 JSON 字段
  （`replied_ok` / `auto_replied` 等）恒为空，仅为兼容保留。
- `init` 生成的配置不再写死数据目录和简历回执正则：数据目录跟随配置文件位置，回执正则走代码默认值。

### 新增 · 对话模块
- 需人工会话可自动在 BOSS 置顶（`conversation.pin_manual`，默认关），用户取消置顶即视为处理完；
  已处理的会话又来新的 HR 消息时提醒一次并重新置顶，取消置顶之前的消息算看过。
- 遵守 `filters.brand_block`：屏蔽公司的会话不打开、不揭示、不占名额，转人工推一次。
- 减少需人工：冷招呼按招呼流程走、确认语静等；在职状态与意向城市内的工作地点自动答一次
  （调动 / 外派 / 出差仍转人工）；红线补「互换电话」「离职原因」。
- 会话状态支持一次性放弃旧积压（`ignored`），有新消息自动恢复；会话身份改用 `conv_key` 校验。
- 对话候选改最旧优先，列表 40 行外只揭示本跳轮到的会话；40 行外的拒信直接结束、不占推送名额。

### 新增 · 定时与播报
- 回消息跳的 wrapper 与播报收进仓库：`scripts/cron/boss-hunt-inbox.sh` / `boss-hunt-inbox-fmt.py`，
  与投递跳、对话跳共用同一把 flock；播报去掉旧链路恒为 0 的「已回 / 失败」。
- 投递播报的「今日已发」改为一行一条「公司 ｜ 职位」，去掉等级字母；待核对提示改成人话。

### 变更 · 投递模块
- 发送账本未核实按会话隔离：对话模块的挂起只停它自己的会话，投递与其他会话照常。
- `tick --dry-run` 与真实投递共用同一套本跳预算 / 配额计算；窗口外干跑把各级配额也报 0。
- 开场白、承接句、答句与发送闸门拒绝自我否定句（「无…经验」「缺乏…背景」），不误伤「无责底薪」。

### 变更 · 安全、配置与命令行
- 收件箱不再被发送账本的待核对挡住：只提示一行、照常只读；投递与对话的闸门不变。
- 收件箱的拒信 / 系统消息判定与对话模块统一（`inbox.patterns` 显式覆盖仍生效）；
  对话模块回过面试固定话术的会话不再记「待你确认」。
- `sending.min_gap_seconds` 未写时投递、对话、账本闸门统一按 60 秒。
- `validate` 检查配置里的正则能否编译；`chat` 启动前先校验对话相关配置，有错不连浏览器。
- `ok=false` 时退出码为 1（`login-check` / `preflight` / `scan` / `greet` / `template`）；
  `send` / `inbox` JSON 带 `ok`，`unanswered --json` 带 `schema` / `cmd`。
- 模型 key 依次从环境变量、`llm.env_file` / `$BOSS_HUNT_ENV_FILE`、`~/.boss-hunt/.env` 读（Hermes 的 `.env` 兼容）。
- JD 读取抽成 `scripts/jd_page.py`，对话模块不再依赖投递模块。

### 修复
- 瞬时失败先重试一次：开标签、登录态检查、详情页导航（登录墙不重试）；招呼语开关读不到时同页等待重读，最多 3 次。
- 推送原话去掉 BOSS 的 `<copy>` 标记（含转义与截断残片）。
- 置顶改用页面自身接口并回查 DOM 确认。
- `dump_conversations.py` 从自身目录导入（不再依赖固定安装路径），消息时间按配置时区。
- 发简历诊断文件与招呼语缓存的时间按配置时区写；`greet` 的 `temperature=0` 原样传给模型。
- 配置文件不存在时给提示（`--json` 输出 `config_not_found`），不再抛堆栈。
- `runs.log` 头两行写真实的桥 / 扩展连接结果；收件箱概览括号里的数改叫「列表」；
  不再每跳重复打印「待核对 N」（计数仍在 JSON 里）。

### 移除
- 零调用方的死代码与只剩测试引用的旧函数（如 `quota_for_now`，统一用 `quotas_for_now`）。
- 发送闸门里旧 `auto_reply` / `interview_reply` / `followup` 的额度与去重分支。

## [0.2.0] - 2026-11-05

### 新增 · 对话模块（`chat`）
- 定时处理 HR 来信：分两轮问清**工作要求、薪资结构**和**面试轮数、能否线上面试**，问齐推一张汇总卡片；
  过筛岗位自动发预设附件简历（含点「同意」简历请求卡片），不过筛只问问题不发简历。
- 黑名单行业（只看 JD 职责段）自动礼貌回绝并标「不感兴趣」；面试时间、期望薪资、到岗等一律转人工，
  不替用户答应、不编信息、不交换电话/微信。
- 回复只走一条通道：LLM 只写 ≤40 字承接句或事实库答句，必须过原有发送闸门；同一模板送达 2 次即转人工。
- 发简历全程留诊断（`resume_trace.jsonl`），认确认气泡与附件卡片回执；发送流水挂起时 `ledger-ack` 人工核对解堵。
- 会话列表是虚拟列表：40 行外的会话先滚动揭示再按原流程处理（身份唯一匹配才点），揭示失败才推「需手动看」；
  首次读行等列表渲染完成。
- 推送去重：同一会话同一跳不重复推「需人工」与「不过筛但要简历」，推送去掉卡片按钮字。

### 变更 · 投递模块拆分
- 投递（筛选 → 按 JD 写招呼语 → 发出）拆成独立模块（`outbound` / `hunt_*` / `send_safety`），分层无反向依赖；
  补池分页、详情页读取失败重开、岗位名读取判级等稳健性修复。


### 修复 · 首条招呼真机发送（2026-10-28）
### 修复 · 分级投递验收（2026-10-27）
- `do_send` 不再每跳把整池候选逐个读 JD：粗标 C 直接跳过，单跳 JD 读取上限 `max_inspect_per_window`（默认 24）。
- A 配额满后不再为粗标 A? 无限读 JD：改为 `hidden_b_probe`（默认 6）限额探测隐藏 B。
- `b_pool` 里的旧候选不再被粗标 C 误杀；额度耗尽时 `do_send` 的返回结构补上 `quotas` 字段。


- **根因 1（输入）**：`type_into_chat` 用 `textContent` + 合成 `InputEvent('input')` 只改 DOM，
  页面框架的输入模型不动 → 发送按钮永远 disabled（线上 `send_aborted:button_disabled`）。
  改为 `el.focus()` + 全选选区 + `document.execCommand('insertText')`；execCommand 不可用时
  才退回「写值 + beforeinput/input/keyup」并校验读回值。
- **根因 2（点击）**：JS `button.click()` 是 untrusted，页面乐观渲染气泡后被服务端丢弃
  （列表预览不变）。改为守卫轮给目标按钮打 `#bh-send-btn`，再用 CDP 可信点击落点；
  没有可信点击能力时才用 JS 点击兜底。
- **根因 3（确认）**：真机我方气泡 class 是 `item-myself`（原有 marker 只有 item-mine/item-self），
  消息 id 在 `data-mid`，正文在 `.text-content`（`.message-content` 混着「送达」状态）。
  三处都按真机结构修正，否则发成功也只会记成 `unknown`。
- **等待**：写完轮询等按钮解锁（默认 3s / 0.15s 间隔），超时仍 disabled 才返回 `button_disabled`；
  仍 disabled 时依次兜底「扩展注入输入 → Enter 键发送」。
- **可观测**：`BOSS_TRACE=1` 打出输入方式、点击方式、前后气泡 id，真机排障不必再猜。
- **测试**：新增 `tests/test_round8.py`（9 项）复现上述三个缺陷；全套 208 项全绿。

### Added
- feat: A/B/C 分级 + 固定配额 + 额度顺延 + 拒绝复盘。新增 grade / grades / tick / rejects / summary，
  JD 判级缓存和 B 池，72h 无回复及明确拒绝采集；示例与访谈同步。
- JSON schema 升至 2：候选 grade 可带问号，send 增加 quotas/by_grade；新增本地统计和排班输出。
- **按岗位 JD 现写招呼语**（`user.greeting.mode=llm`）：发送前读详情页 JD 正文，用本地 Ollama
  （默认 `qwen3.5:9b`）+ `user.greeting.facts` 生成 100–200 字、点名岗位、只挑对口战绩的话术。
  过闸门（字数/纯文本/点名岗位/数字必须能在 facts 或 JD 里找到）才发，否则带反馈重试一次，
  再失败回退 `greeting_template`；结果按 `(jobId, JD+facts 指纹, PROMPT_VERSION)` 缓存
- `scripts/greet.py`：prompt 组装、质量闸门、缓存、Ollama 客户端（显式 `ProxyHandler({})`，
  绕开系统代理）、模板回退，全部可单独 import
- `greet` 子命令：离线预览某岗位现写的招呼语（`--job-id` / `--jd-file` / `--times` / `--no-cache`）
- `preflight` 增加 `greet_llm` / `greet_mode` / `greet_ok`；`send` 的行与 JSON 条目增加
  `greet_source` / `greet_chars` / `greet_secs` / `greet_text`；`state.sent` 记录实际话术便于复盘

### Fixed
- llm 模式下 `verify_marker()` 必须吃实际发出去的文本（并忽略 `sending.verify_text`），
  否则每条都会被误判 `unverified`
- 岗位名里带空格（`海外销售BDM(外派墨西哥)`）时点名判定恒假：比较前统一去空白
- `--json` 全局开关：所有子命令都能输出单个 JSON 对象（`schema`/`cmd`/`ok` + 命令专用字段），
  供 harness / 脚本直接解析；人类可读块仍写 `runs.log`。`plan`/`send`/`inbox` 的条目带
  `job_id`、`title`、`brand`、`salary`、`city`、`result` 等字段
- `AGENTS.md`：Codex / Cursor 等不读 `SKILL.md` 的 Agent 的入口（读文件顺序、三条硬边界、
  默认脚本路径、交付前验证、不该做的事）
- `docs/agent-adapters.md`：各 Agent 的工具映射表（技能装载路径、浏览器驱动方式、定时调度、
  跨 Agent 迁移）

### Changed
- `scripts/chrome_mcp.py` 的 Chrome 唤醒改为平台分支（macOS `open -a` / Linux `google-chrome`|
  `chromium` / Windows `start chrome`），不再只有 macOS 可用；`wake_extension` 复用 `open_chrome()`
- README 首屏补「不挑 Agent、不需要接口 key」，重写「作为 Agent 技能使用」为三层入口
  （SKILL.md / AGENTS.md / 纯 CLI），FAQ 的跨平台说明与实现对齐
- 跨 Agent 化：`SKILL.md` 里 Hermes 专属的 `browser_exec(local=True)` / `cronjob_manage`
  改为通用表述，Hermes 名放括号里；`docs/troubleshooting.md` 的定时排障同样通用化
- README「作为 Hermes Agent 技能使用」→「作为 Agent 技能使用」，补 Claude Code / Codex /
  Cursor 的装载方式与映射文档入口
- `SKILL.md` 原「与本机旧版的关系」改为通用的「只跑一套（迁移时怎么搬）」：明确 `sent`/`contacts`/`replied`/`inbox_seen`
  与 `day`/`day_count` 必须随迁移交接（原表述写成「不要复制 state.json」，与实际需要相反）
- `docs/troubleshooting.md` 补 macOS 睡眠掐跳一节（`pmset sleep 1`、`caffeinate` 边界、合盖无效）

## [0.1.2] - 2026-10-22

### Added
- `CHANGELOG.md`（本文件）
- `.github/ISSUE_TEMPLATE/bug_report.yml`：选择器失效 / 发送失败的问题模板
- README 首屏加「powered by huashu-chrome」徽章，致谢段落补 npm 包名

## [0.1.1] - 2026-10-22

### Added
- README「依赖与前置」：Chrome / Python 3.9+ / Node ≥20 / huashu-chrome 及其扩展，
  含商店一键装与 `chrome://extensions` 手动加载两条路径、`huashu-chrome doctor` 验证
- README「依赖与致谢」：说明上游提供 MCP server + 本地桥 + 扩展，本项目不打包、不修改，
  仅运行时 `npx` 调用
- `docs/troubleshooting.md` 前置排查：`NO_EXTENSION` 先确认扩展是否装过
- `SKILL.md`「前置依赖」段

### Fixed
- 原 README 只写「需要 npx」，新用户会先卡在扩展未安装的 `NO_EXTENSION` 上

## [0.1.0] - 2026-10-22

### Added
- 首个公开版本
- 配置驱动的筛选：薪资 / 经验 / 学历 / 行业 / 公司 / HR / 区域 / 猎头 / 代招
- 个性化招呼语渲染；发送后回读弹窗按校验文本确认
- 收件箱分类：感兴趣 / 约面试 / 不合适；面试类只回「请先给时段，我确认后答复」
- 幂等记账：按岗位 ID 与 `brandName|bossName` 去重，额度按配置时区翻日
- 定时运行：`run --send N --inbox`，macOS 用 `caffeinate` 包住，桥断自愈
- 文档：访谈清单、页面选择器与城市码、风控阈值与来源、故障排查、无桥代理模式

### Known limitations
- 唤醒扩展（`open -a`）与 `caffeinate` 为 macOS 命令，其他平台需替换
- 选择器基于 2026-10 网页版，改版后需按 `docs/boss-site-notes.md` 更新
