# 换 Agent 怎么接（工具映射表）

`scripts/boss_hunt.py` 是一个纯 Python CLI，只依赖 `npx huashu-chrome` 提供的本地桥，
**没有任何 Agent 专属代码**。所以「适配别的 Agent」只发生在两件事上：

1. 技能/流程文档放在哪个目录，Agent 才会自动加载；
2. 少数组装步骤（驱动浏览器、挂定时）各 Agent 的工具名不同。

脚本模式（推荐）在下面所有 Agent 上都**完全一致**，不需要任何改动。

## 1. 装载技能

| Agent | 放哪里 | 生效方式 |
|--|--|--|
| Hermes | `~/.hermes/skills/productivity/boss-zhipin-jobhunt/` | 会话开始自动列出，按 `description` 触发 |
| Claude Code | `~/.claude/skills/boss-zhipin-jobhunt/SKILL.md`（个人级）或项目内 `.claude/skills/…` | 自动发现，也可 `/boss-zhipin-jobhunt` 手动调 |
| Codex CLI | 不用装；在仓库根读 `AGENTS.md` | `AGENTS.md` 是 Codex 的默认约定，克隆到哪生效到哪 |
| Cursor / 其他 IDE Agent | 项目根 `AGENTS.md`（本仓库已提供） | 按各家对 `AGENTS.md` 的支持度 |
| 自建 / 厂商 harness | 读仓库根 `AGENTS.md`；只执行 shell 的也可以直接调 CLI | 两者都不需要装东西，不需要 MCP |
| WorkBuddy / DeepSeek Harness / Kimi Code / MiniMax Code | 见第 6 节（逐家标了已核实 / 未核实） | 能执行 shell 就走脚本模式 |
| 无 Agent（人或 shell 脚本） | 直接读 `SKILL.md` 当操作手册 | 跑 `python3 scripts/boss_hunt.py` 即可 |

`SKILL.md` 的 frontmatter 只有 `name` + `description`，符合 Agent Skills 规范，
Hermes 与 Claude Code 是同一个格式，**不需要维护两份**。

## 2. 驱动浏览器（只有「代理模式」需要）

脚本模式走 huashu-chrome 的 stdio bridge（`npx -y huashu-chrome mcp`），与 Agent 无关。
只有不装扩展、让 Agent 自己点页面时才需要下面的映射：

| Agent | 等价能力 | 备注 |
|--|--|--|
| Hermes | `browser_exec(local=True)` + `js(expr)` / `goto_url` / `capture_screenshot` | 直接复用用户本机 Chrome 的登录态 |
| Claude Code | Playwright MCP 或 chrome-devtools MCP（`claude mcp add`） | 需要 `--browser-channel chrome --persistent` 之类才带登录态，否则是新 profile |
| Codex CLI | 同一批 MCP server（写进 `~/.codex/config.toml` 的 `[mcp_servers]`） | huashu-chrome 自带 `npx -y huashu-chrome install` 可代写这两个 Agent 的 MCP 配置 |
| 其他 / 无 MCP | 用户手动操作，Agent 只出名单和话术 | 退化成「人工执行 + 脚本记账」 |

不管用哪个，`docs/agent-driven-mode.md` 里的选择器、点击顺序、发送校验标记都通用 ——
那是 BOSS 页面的事实，不是某个 Agent 的接口。

## 3. 挂定时

定时只是「按 cron 跑仓库里的 wrapper（`scripts/cron/*.sh`）」，与 Agent 无关：

| Agent | 做法 |
|--|--|
| Hermes | `cronjob_manage(action='create', no_agent=true, ...)`，`no_agent` 表示纯脚本、不花 token |
| Claude Code | 写进系统 `crontab -e`，或 `launchd`（macOS）；也可用会话内的 `/loop`（但不适合无人值守） |
| Codex CLI | 系统 `crontab -e` / `launchd`，Codex 本身不提供常驻调度 |
| WorkBuddy / DeepSeek Harness / Kimi Code / MiniMax Code | 系统 `crontab -e` / `launchd` 跑 wrapper；各家自带的定时都是「定时让模型跑一段提示词」（花 token、且多半绑会话），不适合当投递跳，见第 6 节 |
| 通用 | 下面这段 wrapper + 任意 cron 实现 |

三条定时，都用仓库里的 wrapper（时间按你的 `schedule.windows` 改；投递跳挂在窗口整点上，**别用 `*/10`**——
那样会和 :30 的对话跳撞同一把锁）：

```
0 8,10,12,14,16,18,20,22 * * *  /bin/bash /path/to/boss-zhipin-jobhunt/scripts/cron/boss-hunt-tick.sh   # 投递跳
30 9-21 * * *  /bin/bash /path/to/boss-zhipin-jobhunt/scripts/cron/boss-hunt-chat.sh    # 对话跳：回 HR 的唯一通道
10 10,16,22 * * *  /bin/bash /path/to/boss-zhipin-jobhunt/scripts/cron/boss-hunt-inbox.sh  # 回消息跳：只读播报
```

三个 wrapper 共用 `<workdir>/.wrapperlock`（上一跳没跑完时这一跳静默跳过，不会撞锁报错）、
保留业务退出码、在 macOS 上自带 `caffeinate` 防睡眠，stdout 是一段可直接推送的人话，并按 `notify` 推给用户本人；
配置路径默认 `~/.boss-hunt/config.json`，可用 `BOSS_HUNT_CONFIG` 覆盖。
没开对话模块（`conversation.enabled=false`）就不用挂对话跳。

时区以 `config.json` 的 `schedule.timezone` 为准（脚本内部按它算日期和额度），
系统 cron 的时区只决定「什么时候触发」；两者不一致时脚本不会报警，以配置时区为准判窗口，
排查见 `docs/troubleshooting.md`。tick 内部持有 data/.lock，外层 wrapper 不要重复持同一锁。
挂 cron 之前，三条 wrapper 都先用真实入口加 `--dry-run` 校准一遍（参数原样转给子命令；都不开浏览器、不发送、不写 state、不进播报日志）：

```bash
bash scripts/cron/boss-hunt-tick.sh --dry-run    # 现在是不是窗口、这跳做哪些动作、本跳预算
bash scripts/cron/boss-hunt-chat.sh --dry-run    # 对话模块开没开、现在在不在 conversation.hours、真跳会不会连浏览器
bash scripts/cron/boss-hunt-inbox.sh --dry-run   # 配置能不能过、真跳会只读哪段时间的消息
```

`chat --dry-run`（离线校准）和 `chat --dry`（预演）不是一回事：`--dry` 会真的连浏览器、打开会话（清未读），
只是不发送；新用户第一次校准用 `--dry-run`。

### 定时结果推给谁（每个 Agent 上手时都要问）

系统 cron 跑 wrapper 时**没有任何 Agent 在场**：wrapper 的 stdout 只有会转发输出的调度器（Hermes）能推到用户手里，
其余 Agent 都收不到。所以无论用哪个 Agent 接入，挂定时前都要按 `AGENTS.md`「定时结果推给谁」问用户、
配 `notify.channel`、跑 `notify --check`，再征得同意跑 `notify --test` 并等用户确认收到。渠道与凭据见
[configuration.md](configuration.md#推送定时结果推给谁)。

| Agent | 定时结果会不会自己回到 Agent / 用户手里 | `notify.channel` 建议 |
|--|--|--|
| Hermes | 会：`cronjob_manage` 把 wrapper 的 stdout 推到 Hermes 配的渠道（如微信） | `none`（再配一个渠道会收到两份） |
| Claude Code | 不会：系统 cron 的输出不进 Claude Code；`/loop` 绑会话 | 选一个渠道 |
| Codex CLI / Cursor | 不会：没有常驻调度 | 选一个渠道 |
| Kimi Code | 不会：`CronCreate` 绑会话，系统 cron 的输出不回 Kimi Code | 选一个渠道 |
| MiniMax Code | 不会：`mcode exec` 是一次性运行 | 选一个渠道 |
| DeepSeek Harness | 未核实（`schedule/` 包的行为没写），按「不会」处理 | 选一个渠道 |
| WorkBuddy | 未核实：「自动化」跑的是提示词，能否接收系统 cron 的输出没写，按「不会」处理 | 选一个渠道 |

用户在 Agent 里问「今天投了几个、谁回了」时，Agent 跑 `summary --json` / `unanswered --json` 回答即可；
推送解决的是「人不在电脑前时也能收到」。

## 4. 结构化输出（harness 集成）

不需要正则、不需要解析中文：任何命令加 `--json`，stdout 就是一个 JSON 对象。

```bash
python3 scripts/boss_hunt.py plan  --json --top 20 | jq -r '.items[] | "\(.grade)\t\(.title)\t\(.brand)"'
python3 scripts/boss_hunt.py inbox --json | jq '.interviews'
python3 scripts/boss_hunt.py send  --json 3 | jq '{sent, day_count, skipped: (.skipped|length)}'
```

- 固定字段 `schema`（当前 2）、`cmd`、`ok`；命令专用字段见 [cli.md](cli.md)「JSON 输出」。
- `ok` 是「命令跑通了」，不是「发出去了」：发送结果看 `items[].result`。
- 人类可读块仍然写进 `runs.log`，日志排查方式不变。
- `validate --json` 在配置有错时 `exit=1` 且 `errors[]` 非空 —— CI 里可以直接当检查用。

## 5. 跨 Agent 迁移注意

同一个 BOSS 账号只允许**一套实例**在跑。换 Agent 不等于换账号，但换机器/换目录时：

- 配置搬**事实**（用户资料、城市码、关键词、模板、上限），别整体复制旧 JSON。
- 状态必须搬：`sent` / `contacts` / `replied` / `inbox_seen` / `inbox_ts` / `day` / `day_count` / `day_by_grade` / `b_pool` / `grade_cache` / `pending` / `ticks_done`。
- 搬完按 `validate` → `template` → `plan` → 真实入口跑一次，确认新入口通了再停旧实例。

## 6. 具体 harness：WorkBuddy / DeepSeek Harness / Kimi Code / MiniMax Code

**先分清两种角色**：

- **作为 harness**：谁来读 `SKILL.md` / `AGENTS.md`、替用户敲命令、挂定时。本节讲这个。
- **作为招呼语 / 对话的模型服务商**：`user.greeting.llm` 调谁写招呼语和承接句。见第 7 节。

两者互不绑定：用 Kimi Code 当 harness，招呼语照样可以走本地 Ollama 或 DeepSeek；反过来也一样。
脚本运行时零 token，**harness 只在接入、校准、排障时花 token**，定时跳本身不经过任何 harness。

> 核实口径：2026-11-06 读各家官方仓库 / 官方文档页面。「已核实」= 官方页面原文写明；
> 「未核实」= 官方页面没写或只有第三方说法，**不要当事实用**，接入时先自己试一次。

| 能力 | WorkBuddy（腾讯） | DeepSeek Harness（`dsh`） | Kimi Code（`kimi`） | MiniMax Code（`mcode`） |
|--|--|--|--|--|
| 读项目 `AGENTS.md` | 未核实（WorkBuddy 文档未提；同门的 CodeBuddy Code 文档也未提） | 未核实（官方仓库的 `AGENTS.md` 是给开发 dsh 本身的贡献者看的，运行时是否加载项目 `AGENTS.md` 没写） | **已核实**：项目根 `AGENTS.md` 或 `.kimi-code/AGENTS.md`，另有 `~/.kimi-code/AGENTS.md`、`~/.agents/AGENTS.md` | **已核实**：`mcode init .` 生成 / 更新项目 `AGENTS.md` |
| 装载技能（`SKILL.md`，name + description） | 部分核实：WorkBuddy 可从面板「添加技能」、SkillHub 安装；目录位置未核实（CodeBuddy Code 是 `.codebuddy/skills/`、`~/.codebuddy/skills/`，WorkBuddy 是否同路径未核实） | 未核实（仓库有 `skill/` 包，格式与目录没写） | **已核实**：`~/.kimi-code/skills/<name>/SKILL.md`、`~/.agents/skills/`、项目 `.kimi-code/skills/` / `.agents/skills/`；**不读** `~/.claude/skills` | 部分核实：支持插件与内置技能；技能目录未核实 |
| 执行 shell | 未核实（WorkBuddy 文档未写；CodeBuddy Code 的技能可执行 shell） | 部分核实：仓库有 `shell/`（命令执行）包，暴露给模型的工具名未写 | **已核实**：内置 `Bash` 工具（默认需审批） | **已核实**：可跑 shell 命令与测试，带权限与沙箱 |
| 无人值守 / 非交互 | 未核实 | 部分核实：源码运行 `pnpm dsh --profile headless "task"`（需 `DEEPSEEK_API_KEY`）；仍是开发者预览、会有不兼容变更 | **已核实**：`kimi -p "<提示词>"`，可加 `--output-format stream-json` | **已核实**：`mcode exec`（面向脚本、CI、批处理） |
| 自带定时 | **已核实**：「自动化」面板填标题、提示词、频率、时间，可选模型与技能 —— 是定时跑提示词（花 token）；能否直接跑脚本未写 | 未核实（仓库有 `schedule/`「scheduled follow-ups」包，无 cron 语法） | **已核实但绑会话**：`CronCreate`/`CronList`/`CronDelete`，定时只在该会话里有效，新会话不继承 | 未核实 |
| MCP | 未核实 | 部分核实：有 `mcp/` 包，配置方式没写 | **已核实**：`~/.kimi-code/mcp.json`、项目 `.kimi-code/mcp.json` | 未核实 |

**结论与接法（四家相同）**：

1. **装载**：Kimi Code 把整个目录放进 `~/.kimi-code/skills/boss-zhipin-jobhunt/`（或 `~/.agents/skills/`），
   或者直接在仓库根打开，它会读 `AGENTS.md`。MiniMax Code 在仓库根打开（读 `AGENTS.md`）。
   WorkBuddy、DeepSeek Harness 的技能目录未核实：最稳的做法是在仓库根打开、让它先读 `AGENTS.md`，
   读不到就把 `AGENTS.md` 的内容贴给它。
2. **执行**：只要能跑 shell，就按 `AGENTS.md` 的「默认执行路径：脚本模式」走，命令和输出（`--json`）完全一样。
   不能跑 shell 的（WorkBuddy 手机端等），只能做访谈和出配置，命令由用户自己在终端跑。
3. **定时**：一律用系统 `crontab` / `launchd` 跑仓库 wrapper（第 3 节），**不要**用 harness 自带的定时：
   那是「定时让模型跑一段提示词」，每跳都花 token、模型可能自作主张多发或改参数，
   Kimi 的定时还绑在会话上、关掉就没了。
4. **接入后验收**：问清「定时结果推给谁」并配好推送（第 3 节末尾），让 harness 跑一遍第 3 节的三条 `--dry-run`，
   再按 `AGENTS.md`「交付前必须验证」逐条核对。
5. **三条红线不随 harness 变**：不替用户答应面试时间、不编用户没给的信息、不交换电话/微信；
   harness 只驱动用户自己已登录的浏览器，看到登录墙就停。

## 7. 招呼语 / 对话的模型服务商（DeepSeek / Kimi / MiniMax）

`user.greeting.llm` 同时供招呼语（`greeting.mode=llm`）和对话模块的承接句 / 答句使用。两种后端：

- `provider` 不写或 `"ollama"`：本地 Ollama，走 `<base_url>/api/generate`。
- `provider` 为 `"deepseek"` / `"openai"` / `"cloud"`：走 OpenAI 兼容 `<base_url>/chat/completions`，
  `Authorization: Bearer <key>`。**Kimi（Moonshot）、MiniMax 以及任何 OpenAI 兼容服务都写 `"openai"`**，
  再填它自己的 `base_url` 和 `api_key_env`。`validate` 会拦 `"kimi"` / `"minimax"` 这类厂商名
  （以前会被静默当成 Ollama，招呼语全部回退模板），也会拦云端 provider 没写 `base_url`。

key 不写进配置，只写环境变量名 `api_key_env`；查找顺序：环境变量 → `llm.env_file` / `$BOSS_HUNT_ENV_FILE`
→ `~/.boss-hunt/.env` → `~/.hermes/.env`（Hermes 兼容），不回显。cron 环境变量很少，建议把 key 写进
`~/.boss-hunt/.env`（`MOONSHOT_API_KEY=...`，可带 `export` 前缀）。`validate` 找不到 key 时只给警告。

下面三段可以直接替换 `user.greeting.llm`（仓库测试会逐段跑 `validate`）：

DeepSeek（已核实：官方文档 base_url `https://api.deepseek.com`、示例环境变量 `DEEPSEEK_API_KEY`、
当前列出的模型 `deepseek-flash` / `deepseek-v4-pro`；`/v1` 后缀与旧名 `deepseek-chat` 官方首页未写，是否仍可用**未核实**）：

```json
{"provider": "deepseek", "base_url": "https://api.deepseek.com", "model": "deepseek-flash",
 "api_key_env": "DEEPSEEK_API_KEY", "num_predict": 400, "timeout_s": 45}
```

Kimi / Moonshot（已核实：国内 base_url `https://api.moonshot.cn/v1`、示例环境变量 `MOONSHOT_API_KEY`、
示例模型 `kimi-k3.1-pro`；国际站 base_url **未核实**）：

```json
{"provider": "openai", "base_url": "https://api.moonshot.cn/v1", "model": "kimi-k3.1-pro",
 "api_key_env": "MOONSHOT_API_KEY", "num_predict": 400, "timeout_s": 45}
```

MiniMax（已核实：国际站 OpenAI 兼容 base_url `https://api.minimax.io/v1`、模型 `MiniMax-M3.1-Flash` / `MiniMax-M3`；
官方示例只写占位 `<MINIMAX_API_KEY>`，`MINIMAX_API_KEY` 是本项目自取的变量名；国内站 base_url **未核实**）：

```json
{"provider": "openai", "base_url": "https://api.minimax.io/v1", "model": "MiniMax-M3.1-Flash",
 "api_key_env": "MINIMAX_API_KEY", "num_predict": 600, "timeout_s": 60}
```

注意：

- **MiniMax 的思考不能关**（官方文档写明）。思考内容会不会混进 `content`、会不会吃掉 `max_tokens`
  导致正文为空，**未核实**；上面先把 `num_predict` 给到 600。切换前务必跑
  `greet --job-name … --jd-file … --no-cache --times 6`，过闸门率 ≥5/6 再启用，否则招呼语会大量回退模板。
- 换任何服务商都按同一条回归：`validate` → `greet --times 6` 看过闸门率 → `preflight` 看 `greet_llm`。
  模型名、地址以服务商控制台为准，本表是 2026-11-06 的快照。
- 配成云端服务商后，岗位 JD 片段、你的事实清单和 HR 原话会作为提示词发给该服务商（README FAQ「数据会上传到哪」）。
