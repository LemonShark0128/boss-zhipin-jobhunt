# BOSS直聘求职助手

[![Release](https://img.shields.io/github/v/release/LemonShark0128/boss-zhipin-jobhunt)](https://github.com/LemonShark0128/boss-zhipin-jobhunt/releases)
[![License: MIT](https://img.shields.io/badge/License-MIT-green.svg)](LICENSE)
[![Python](https://img.shields.io/badge/Python-3.9%2B-blue.svg)](https://www.python.org/)
[![Powered by huashu-chrome](https://img.shields.io/badge/powered%20by-huashu--chrome-cb3837?logo=npm&logoColor=white)](https://github.com/alchaincyf/huashu-chrome)

在你自己已登录的 Chrome 里，按一份配置文件筛选 BOSS 直聘（zhipin.com）岗位、发送招呼语、跟进 HR 消息的命令行工具。
可以单独在终端运行、挂定时任务，也可以作为 Agent 技能交给 Claude Code、Codex、Hermes、Kimi Code、MiniMax Code 等使用。

设计取向是「少而准」：默认每天 20 个、间隔 60 秒、分时段发送；不绕过验证码，不替你答应面试时间，不编造经历。

## 功能

- **按条件筛岗位**：薪资下限、经验与学历区间、行业 / 公司 / HR 黑名单、通勤区域、猎头与代招；读 JD 判定 A / B 档，按日配额投递。
- **招呼语**：用模板渲染，或读取岗位 JD 由模型现写（本地 Ollama，或 DeepSeek / Kimi / MiniMax 等 OpenAI 兼容服务）。
  模型写出的数字必须能在你给的事实清单或 JD 里找到，不通过就回退模板。
- **收件箱与对话**：收件箱只读分类；对话模块按轮次问清工作要求、薪资结构、面试轮数与能否线上，
  面试时间、期望薪资等需要你本人决定的问题一律转人工。
- **定时运行**：投递、对话、收件箱三条 cron 入口共用单实例锁，挂定时前都可以加 `--dry-run` 离线校准。
- **结构化输出**：所有命令支持 `--json`，便于脚本和 Agent 解析。

## 工作原理

```text
你已登录的 Chrome
      │  huashu-chrome（本地桥 127.0.0.1:8899 + 浏览器扩展）
      ▼
scripts/boss_hunt.py
  ├─ scan   打开搜索页，读取页面自己发出的岗位列表请求
  ├─ plan   按配置筛选、打分、分档，只报告不发送
  ├─ send   打开详情页 → 生成招呼语 → 发送 → 回读页面确认已发出
  ├─ inbox  读取消息列表并分类（只读）
  └─ chat   对话模块：按轮次回复 HR、发附件简历
```

所有动作都经由真实页面完成，带页面自己的签名和登录态；不直接请求接口（直接请求几次就会被判「环境异常」）。

## 环境要求

| 依赖 | 说明 |
|--|--|
| Chrome | 保持登录 BOSS 直聘 |
| Python | 3.9+，无第三方依赖 |
| Node.js | 20+，运行时通过 `npx` 拉取 [huashu-chrome](https://github.com/alchaincyf/huashu-chrome) |
| huashu-chrome 浏览器扩展 | 需手动安装一次：[Chrome 应用商店](https://chromewebstore.google.com/detail/foiljmaplphdfimfcnfdpekhdnfbgfbf)；装好后 `npx -y huashu-chrome doctor` 应显示「Chrome 扩展在线」 |
| 模型（可选） | 仅「模型现写招呼语」需要：本地 Ollama，或 OpenAI 兼容服务的 API key |

实测环境：macOS + Chrome + Python 3.9.6 + huashu-chrome 1.3.0。Linux 未实测；Windows 上定时 wrapper 不可用（依赖 `fcntl`）。

## 快速开始

```bash
git clone https://github.com/LemonShark0128/boss-zhipin-jobhunt.git && cd boss-zhipin-jobhunt
python3 scripts/boss_hunt.py init            # 生成 ~/.boss-hunt/config.json
```

1. **填配置**：编辑 `~/.boss-hunt/config.json` 的 `user` / `targets` / `filters`（字段见 [配置说明](docs/configuration.md)），然后校验：
   ```bash
   python3 scripts/boss_hunt.py validate
   python3 scripts/boss_hunt.py template      # 预览招呼语与字数
   ```
2. **登录并体检**：在 Chrome 里打开 <https://www.zhipin.com> 扫码登录，再运行：
   ```bash
   python3 scripts/boss_hunt.py login-check
   python3 scripts/boss_hunt.py preflight     # 登录态、BOSS 自带招呼语开关、今日额度
   ```
3. **抓取并查看名单**（不发送）：
   ```bash
   python3 scripts/boss_hunt.py scan --pages 1
   python3 scripts/boss_hunt.py plan --top 15
   ```
4. **先发 1 个**，确认 HR 收到、招呼语没有被截断：
   ```bash
   python3 scripts/boss_hunt.py send 1
   ```
5. **挂定时任务**（可选）：先用真实入口离线校准，确认无误后再写进 cron，cron 写法见 [agent-adapters.md](docs/agent-adapters.md#3-挂定时)。
   ```bash
   bash scripts/cron/boss-hunt-tick.sh --dry-run    # 投递：当前是否在窗口、本跳预算
   bash scripts/cron/boss-hunt-chat.sh --dry-run    # 对话：是否开启、是否在时段内
   bash scripts/cron/boss-hunt-inbox.sh --dry-run   # 收件箱：配置是否可用
   ```

## 作为 Agent 技能使用

| Agent | 入口 | 接入方式 |
|--|--|--|
| Hermes、Claude Code 等支持 Agent Skills 的 Agent | `SKILL.md` | 把整个目录放进技能目录，例如 `~/.claude/skills/boss-zhipin-jobhunt/` |
| Codex、Cursor、Kimi Code、MiniMax Code 等读 `AGENTS.md` 的 Agent | `AGENTS.md` | 在仓库根打开即可，无需安装 |
| 只能执行 shell 的脚本或自建 harness | `scripts/boss_hunt.py` | 直接调用命令，加 `--json` 解析输出 |

接入后 Agent 会按「访谈 → 登录 → 配置 → 干跑 → 定时」带你走一遍。MiniMax Code 已实测按 `AGENTS.md` 的指引自行跑通离线上手流程；
各 Agent 的装载目录、执行 shell 与定时能力（含 WorkBuddy、DeepSeek Harness 的核实情况）见 [agent-adapters.md](docs/agent-adapters.md)。

## 文档

| 文档 | 内容 |
|--|--|
| [configuration.md](docs/configuration.md) | 全部配置字段、对话模块、A / B 分级、排班与拒绝复盘 |
| [cli.md](docs/cli.md) | 子命令一览与 `--json` 输出字段 |
| [agent-adapters.md](docs/agent-adapters.md) | 各 Agent 的接入方式、定时写法、模型服务商配置 |
| [onboarding-interview.md](docs/onboarding-interview.md) | 首次接入的需求访谈清单 |
| [risk-control.md](docs/risk-control.md) | 发送额度与风控阈值（含来源） |
| [troubleshooting.md](docs/troubleshooting.md) | 本地桥、扩展、定时任务、收件箱的常见故障 |
| [boss-site-notes.md](docs/boss-site-notes.md) | 页面结构、接口字段、已核对的城市码 |
| [CHANGELOG.md](CHANGELOG.md) | 版本变更 |

## 安全与边界

- **额度**：平台不公布固定上限。本项目默认 20 个 / 天、间隔 ≥60 秒、分 2–4 个时段，依据见 [risk-control.md](docs/risk-control.md)。
- **不做的事**：绕过验证码或人机验证、代填账号密码、群发同一句话、伪造经历、代你答应面试时间、交换电话或微信。
- **单实例**：同一账号不要同时运行两套自动化，否则会对同一岗位重复打招呼。
- **风险自负**：平台规则随时变化，本项目不保证账号不被限制，也不对求职结果作任何承诺。

## 常见问题

**会被封号吗？**
没有工具能保证。本项目把频率压在人工水平、不直连接口、不绕过验证；遇到验证码会跳过并提示你手动处理。

**支持哪些城市？**
在配置里填 BOSS 的城市码 `city_code` 即可。已核对的城市码见 [boss-site-notes.md](docs/boss-site-notes.md)；其他城市在网页版切换一次城市，URL 里的 `city=` 就是。

**能全自动聊到拿 offer 吗？**
不能，也不打算做。打招呼和固定问题可以自动，涉及时间、薪资、经历的回复都会转给你。

**数据会上传到哪？**
运行数据（`state.json`、岗位快照、发送流水、对话记录）都保存在本机 `~/.boss-hunt/`，不会上传。
唯一例外是模型：用本地 Ollama 时不出本机；配成云端服务商时，岗位 JD 片段、你的事实清单和 HR 原话会作为提示词发给该服务商。

## 目录结构

```text
.
├── README.md
├── SKILL.md                  # Agent Skills 入口，也是完整操作手册
├── AGENTS.md                 # 通用 Agent / harness 入口
├── CHANGELOG.md
├── assets/config.example.json
├── scripts/
│   ├── boss_hunt.py          # 命令行入口
│   ├── hunt_*.py             # 配置、筛选与判级、排班配额、状态
│   ├── outbound.py           # 投递链路与发送执行
│   ├── conversation.py       # 对话模块（conv_*.py、chat_*.py 为其组件）
│   ├── greet.py              # 招呼语生成与校验
│   ├── chrome_mcp.py         # huashu-chrome 客户端
│   └── cron/                 # 投递 / 对话 / 收件箱三条定时入口及播报格式化
├── docs/                     # 见上方「文档」
├── references/               # 设计说明与经验记录
└── tests/                    # 离线单元测试（python3 -m unittest discover -s tests）
```

## 参与贡献

- 页面改版导致选择器失效时，先运行 `scan --pages 1` 定位，再对照 [boss-site-notes.md](docs/boss-site-notes.md) 修改。
- 新增或调整过滤规则时，请附上一个被误杀或漏放的真实岗位类型作为回归用例。
- 提交前运行 `python3 -m unittest discover -s tests`，并确认没有把个人配置带进改动。

## 致谢

浏览器能力来自 [huashu-chrome](https://github.com/alchaincyf/huashu-chrome)（作者 [花叔 / alchaincyf](https://github.com/alchaincyf)，MIT）。
它提供本地桥与 Chrome 扩展，让脚本直接使用你当前浏览器的登录态。本项目不打包、不修改它，只在运行时通过 `npx` 调用。

## License

[MIT](LICENSE)
