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

定时只是「按 cron 跑一次 `run.sh`」，与 Agent 无关：

| Agent | 做法 |
|--|--|
| Hermes | `cronjob_manage(action='create', no_agent=true, ...)`，`no_agent` 表示纯脚本、不花 token |
| Claude Code | 写进系统 `crontab -e`，或 `launchd`（macOS）；也可用会话内的 `/loop`（但不适合无人值守） |
| Codex CLI | 系统 `crontab -e` / `launchd`，Codex 本身不提供常驻调度 |
| 通用 | 下面这段 wrapper + 任意 cron 实现 |

```bash
#!/bin/bash
# ~/.boss-hunt/run.sh
cd ~/.boss-hunt
caffeinate -i -t 900 /usr/bin/python3 /path/to/boss-zhipin-jobhunt/scripts/boss_hunt.py run --send 5 --inbox
```

```
0 11,15,21 * * *  /bin/bash ~/.boss-hunt/run.sh >> ~/.boss-hunt/data/cron.out 2>&1
```

时区以 `config.json` 的 `schedule.timezone` 为准（脚本内部按它算日期和额度），
系统 cron 的时区只决定「什么时候触发」，两者不一致时会被记录到 `runs.log`，
排查见 `docs/troubleshooting.md`。

## 4. 跨 Agent 迁移注意

同一个 BOSS 账号只允许**一套实例**在跑。换 Agent 不等于换账号，但换机器/换目录时：

- 配置搬**事实**（用户资料、城市码、关键词、模板、上限），别整体复制旧 JSON。
- 状态必须搬：`sent` / `contacts` / `replied` / `inbox_seen` / `inbox_ts` / `day` / `day_count`。
- 搬完按 `validate` → `template` → `plan` → 真实入口跑一次，确认新入口通了再停旧实例。
