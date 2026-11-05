## 2026-10-27 · Phase A 验收修正

- **修复**：`do_send` 每跳把整池候选逐个读 JD（实测 652 次/跳），定时任务会被超时杀掉。
  改为：粗标 C 直接跳过（不打开详情页）+ 单跳 JD 读取上限 `max_inspect_per_window`（默认 24）。
- **修复**：A 配额满后仍会为粗标 A? 的候选无限读 JD。改为 `hidden_b_probe`（默认 6）限额探测
  「自报互联网、实则门店运营」的隐藏 B，其余跳过。
- **修复**：`b_pool` 里的旧候选被粗标 C 误杀（粗标只看列表字段，不能否掉已按 JD 定级的 B）。
- **修复**：额度耗尽时 `do_send` 的返回结构缺 `quotas` 字段。
- **移除**：海外岗位押配额逻辑（`sending.overseas_regex` / `overseas_max_per_round`）—— 实测不好用，
  且不适合公开技能；要投海外方向改为在 `filters.title_hard_block` 里排除工程外派岗。
- **测试**：`tests/test_phase_a.py` 的配额用例给 B 类岗位补上品牌侧行业（原写法 45 个岗位行业全同，
  等于假设行业字段完全不可信），27 项单测全绿。

# 变更记录

本项目遵循 [语义化版本](https://semver.org/lang/zh-CN/)；格式参考
[Keep a Changelog](https://keepachangelog.com/zh-CN/1.1.0/)。

## [Unreleased]

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
