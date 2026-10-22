# BOSS直聘 求职助手

**把「筛岗位 + 发招呼 + 盯回复」变成一份可配置的脚本，而不是无脑海投。**

[![License: MIT](https://img.shields.io/badge/License-MIT-green.svg)](LICENSE)
[![Python](https://img.shields.io/badge/Python-3.9%2B-blue.svg)](https://www.python.org/)
[![Chrome](https://img.shields.io/badge/Chrome-required-yellow.svg)](https://www.google.com/chrome/)
[![Token](https://img.shields.io/badge/LLM%20token-0-lightgrey.svg)](#工作原理)
[![Powered by huashu-chrome](https://img.shields.io/badge/powered%20by-huashu--chrome-cb3837?logo=npm&logoColor=white)](https://github.com/alchaincyf/huashu-chrome)

基于**你自己已登录的 Chrome**，按配置筛选 BOSS 直聘（zhipin.com）上的岗位、
用你的招呼语打招呼、盯 HR 的新消息并按类别处理。所有个人化内容都在 `config.json`，
代码里没有写死任何人的经历、城市或关键词。

> 设计取向是「少而准」：默认每天 15–20 个、间隔 60s、跳过猎头/代招/需下载 App/验证码页，
> 同一 HR 只发一次。它不绕过任何人机验证，也不替你答应面试时间。

---

## 特性

- 🔍 **多维筛选**：薪资下限、经验区间、学历、行业黑名单、公司/HR 黑名单、通勤区域、猎头/代招
- 💬 **个性化招呼语**：`{years}` `{industry}` `{highlights}` `{job_name}` 占位符渲染，发送后回读页面确认真的发出去了
- 📬 **收件箱分类**：感兴趣 / 约面试 / 不合适；面试类只回「请先给时段，我确认后答复」，绝不自作主张
- 🗂 **幂等记账**：`state.json` 按岗位 ID + HR 去重，跨天保留额度
- ⏱ **零 token 运行**：纯 Python + 本地浏览器桥，可挂 cron 无人值守
- 🧩 **两种用法**：独立 CLI，或作为 [Hermes Agent](https://hermes-agent.nousresearch.com/) 技能（含访谈清单）
- 📦 **可复制**：整个目录交给别人就能用，改配置不改代码

## 工作原理

```text
用户登录的 Chrome
      │  huashu-chrome（本地桥，127.0.0.1:8899）
      ▼
boss_hunt.py
  ├─ scan   导航 /web/geek/jobs?query=…&city=… → 读页面自己发出的 joblist 响应
  ├─ filter 逐条判：薪资/经验/学历/行业/公司/HR/区域/猎头/代招 → 打分排序
  ├─ send   详情页 → 点「立即沟通」→ 填模板 → 发送 → 回读弹窗确认
  └─ inbox  /web/geek/chat → 读 friend/getG → 分类 → 面试类代回固定话术
```

**为什么不用接口直连？** 直接请求搜索接口几次就会返回 `code:37 环境存在异常`。
脚本走的是「页面导航 + 抓取页面自己发出的请求」，带签名和 cookie，行为与人工浏览一致。
同理，打开消息页会把未读清零，所以判「有没有新消息」用的是消息时间 + 发送方，不是未读数。

## 依赖与前置

| 依赖 | 版本 / 说明 | 必需 |
|--|--|--|
| Chrome | 保持登录 BOSS | ✅ |
| Python | 3.9+（用 `zoneinfo`） | ✅ |
| Node.js | ≥20，提供 `npx` | ✅ |
| [huashu-chrome](https://github.com/alchaincyf/huashu-chrome) | MIT，作者 [花叔](https://github.com/alchaincyf)；MCP server + 本地桥 | ✅ 运行时 `npx` 拉取，不打包 |
| huashu-chrome **Chrome 扩展** | 浏览器不允许脚本代装，必须你点一次 | ✅ |

装扩展只需一次：

- 商店一键装：<https://chromewebstore.google.com/detail/foiljmaplphdfimfcnfdpekhdnfbgfbf>
- 商店打不开时手动加载：`npx -y huashu-chrome extension` 会打印扩展目录，
  打开 `chrome://extensions` → 开发者模式 → 「加载已解压的扩展程序」选中该目录
- 验证：`npx -y huashu-chrome doctor` 应显示「Chrome 扩展在线」

> 本项目不调用 `huashu-chrome install`（那是给 Claude Code / Codex 写 MCP 配置的），
> 只需要扩展 + 运行时的 `npx`。如果 `doctor` 通了但脚本仍报 `NO_EXTENSION`，
> 见 [`docs/troubleshooting.md`](docs/troubleshooting.md)。

## 快速开始

**环境**：Chrome（保持登录 BOSS）、Node.js ≥20（提供 `npx`）、Python 3.9+，外加上面的浏览器扩展。
实测于 macOS + Chrome + Python 3.9.6 + huashu-chrome 1.3.0。

```bash
git clone https://github.com/LemonShark0128/boss-zhipin-jobhunt.git boss-hunt && cd boss-hunt
python3 scripts/boss_hunt.py init          # 生成 ~/.boss-hunt/config.json
```

1. **登录**：在自己的 Chrome 里打开 <https://www.zhipin.com>，手机扫码登录。
   ```bash
   python3 scripts/boss_hunt.py login-check   # 只读探测，输出 ✅ 已登录 再继续
   ```
2. **改配置**：编辑 `~/.boss-hunt/config.json` 的 `user` / `targets` / `filters`（字段见下）。
   ```bash
   python3 scripts/boss_hunt.py validate      # 离线校验
   python3 scripts/boss_hunt.py template      # 预览招呼语、字数、发送校验文本
   ```
3. **体检 + 干跑**（**先别发**）：
   ```bash
   python3 scripts/boss_hunt.py preflight            # 登录态 + BOSS 自带招呼语开关 + 今日额度
   python3 scripts/boss_hunt.py scan --pages 1       # 抓 1 页验证链路
   python3 scripts/boss_hunt.py plan --top 15        # 只报告会投哪些，不发
   ```
4. **首次只发 1 个**：
   ```bash
   python3 scripts/boss_hunt.py send 1
   ```
   看 HR 有没有回、招呼语有没有被截断，没问题再 `send 5`。
5. **交给定时任务**（可选）：
   ```bash
   # ~/.boss-hunt/run.sh
   #!/bin/bash
   cd ~/.boss-hunt
   caffeinate -i -t 900 /usr/bin/python3 /path/to/repo/scripts/boss_hunt.py run --send 5 --inbox
   ```
   `cron: 0 11,15,21 * * *`（以 `schedule.timezone` 求值）。macOS 上必须用 `caffeinate`
   包住，否则系统睡眠会冻住 Chrome、断开本地桥。

## 配置说明

完整示例见 [`assets/config.example.json`](assets/config.example.json)。

```jsonc
{
  "user": {
    "years_experience": 5,
    "industry": "SaaS 企业服务",
    "highlights": ["带 6 人团队做到年营收 1200 万", "新签中大型客户 80+ 家"],
    "greeting_template": "您好，我有 {years} 年{industry}经验，{highlights}。看到贵司在招「{job_name}」，方便的话想和您详细聊聊。",
    "interview_reply": "您好，方便安排线上面试吗？我这边时间需要先确认，您先给几个时段，我确认后再回复您。"
  },
  "targets": [
    { "name": "主方向-上海", "city": "上海", "city_code": "101020100",
      "keywords": ["商务拓展经理", "BD经理"], "daily_share": 10 }
  ],
  "filters": {
    "salary_min_k": 15,
    "title_allow": ["经理", "主管", "总监", "负责人", "BDM"],
    "industry_block": ["保险", "房产", "中介", "教育培训", "人力资源"],
    "exclude_headhunter": true,
    "exclude_proxy": true,
    "experience": { "job_min_years_max": 8, "job_max_years_min": 2 }
  },
  "sending": { "daily_cap": 15, "min_gap_seconds": 60 },
  "inbox":   { "lookback_hours": 72, "max_listed": 5, "interview_auto_reply": true },
  "schedule": { "timezone": "Asia/Shanghai", "windows": ["11:00", "15:00", "21:00"], "per_window": 5 }
}
```

| 字段 | 作用 |
|--|--|
| `user.greeting_template` | 招呼语；至少 2 个占位符，否则不同岗位话术一样、像群发（80–200 字最佳，>380 会被拦） |
| `user.interview_reply` | 面试类固定回复：只表达愿意面 + 请对方给时段 |
| `targets[].city_code` | BOSS 数字城市码，已核对的城市见 [`docs/boss-site-notes.md`](docs/boss-site-notes.md) |
| `targets[].daily_share` | 每个方向的每日配额，防止额度全被高分方向吃掉 |
| `filters.title_allow/block` | 头衔白/黑名单（正则）；命中黑名单且不含白名单 → 拒 |
| `filters.exclude_headhunter/exclude_proxy` | 排掉猎头职位与代招岗位 |
| `filters.districts_allow` | 通勤圈；留空 = 不限 |
| `sending.daily_cap` / `min_gap_seconds` | 每日上限 / 发送间隔；建议 15–20 与 ≥60s |
| `inbox.followup` | 已读不回 48h 补一句，默认关闭 |

## 命令行

```bash
python3 scripts/boss_hunt.py init|validate|template|login-check|preflight
python3 scripts/boss_hunt.py scan  [--target 名称] [--keyword 词] [--pages 3]
python3 scripts/boss_hunt.py plan  [--top 20]     # 干跑：只报告
python3 scripts/boss_hunt.py send  N              # 真发
python3 scripts/boss_hunt.py inbox                # 盯消息 + 分类 + 回面试
python3 scripts/boss_hunt.py run   [--send N] [--inbox]
```

配置查找顺序：`--config PATH` → `$BOSS_HUNT_CONFIG` → `~/.boss-hunt/config.json`。
运行数据（`config.json`、`state.json`、`raw/`、`runs.log`）默认在 `~/.boss-hunt/`，不会上传到任何地方。

输出示例：

```text
【2026-10-22 12:17 Shanghai】
- bridge:bridge_ok
- extension:extension_ok
- scan: +3 页
- 候选 42（A18/B24）
- A 已发｜商务拓展经理｜某某科技｜20-30K·14薪
- 本轮发出 5，今日累计 5/15
- 近 72 小时新消息 12 条（新招呼 12）｜对口 4｜感兴趣 2｜约面试 1
- 📅 约面试 某某公司｜区域经理 → 已回「先给时段，我确认后再答复」
- 已关自己开的标签页 2 个
```

## 风控、边界与免责

- **额度**：平台不公布固定数字，公开口径为免费号约 40–100 次/天、会员 50–200 次/天。
  本项目默认 **15–20 个/天**、间隔 **≥60s**、分 2–4 个时段发送；数字与来源见
  [`docs/risk-control.md`](docs/risk-control.md)。
- **不做**：绕过验证码/人机验证、代填账号密码、批量群发同一句话、伪造经历、代替你答应面试时间。
- **硬性要求**：同一账号不要同时运行两套自动化（别的油猴脚本 + 本项目），否则同一岗位会被
  打两次招呼，风控特征最明显。
- **账号风险自负**。平台规则随时变化，本项目不保证不被限制、也不对求职结果作任何承诺。
  建议先 `plan` 看名单、`send 1` 验证，再决定是否定时运行。

## 目录结构

```text
.
├── README.md                    # 面向使用者的说明（本文件）
├── SKILL.md                     # Hermes Agent 技能说明（也可当流程文档读）
├── CHANGELOG.md                 # 版本变更
├── assets/config.example.json   # 配置模板
├── scripts/boss_hunt.py         # 主程序：scan / filter / send / inbox / run
├── scripts/chrome_mcp.py        # huashu-chrome 的 stdio MCP 客户端 + 页面封装
├── .github/ISSUE_TEMPLATE/      # 问题模板（选择器失效 / 发送失败）
└── docs/
    ├── onboarding-interview.md  # 需求访谈清单（8 必问 + 10 个常被忘的点）
    ├── boss-site-notes.md       # 页面选择器、接口字段、城市码、桥返回格式
    ├── risk-control.md          # 额度与风控阈值（含来源与日期）
    ├── troubleshooting.md       # 桥/扩展/定时任务/收件箱故障
    └── agent-driven-mode.md     # 没有 huashu-chrome 时用 Agent 驱动同一套动作
```

## 作为 Hermes Agent 技能使用

```bash
cp -r . ~/.hermes/skills/productivity/boss-zhipin-jobhunt
```

之后 Agent 会按 `SKILL.md` 的「访谈 → 登录 → 配置 → 干跑 → 定时」流程带你走一遍，
包括问你没想起来的事（通勤圈、猎头、已投公司、多方向配额、已读不回跟进）。

## FAQ

**会被封号吗？**
没有任何工具能保证。本项目的做法是把频率压到人工水平（15–20/天、60s 间隔、分时段）、
不直连接口、不绕过验证、不并发多套脚本。撞到验证码会直接跳过并提示你手动处理。

**支持哪些城市？**
配置里填 `city_code` 即可。已核对的城市码在 [`docs/boss-site-notes.md`](docs/boss-site-notes.md)；
其他城市在网页版切一次城市，URL 里的 `city=` 就是。

**Windows / Linux 能用吗？**
代码本身是纯 Python + stdio MCP，没有平台绑定；但 `chrome_mcp.py` 里唤醒扩展用的是 macOS
的 `open -a`，`caffeinate` 也是 macOS 命令。其他平台需要把这两处换成本地等价命令（欢迎 PR）。

**为什么消息页的未读数永远是 0？**
打开消息页会把未读清零，这是页面行为。项目改用「消息时间 + 发送方是否是自己」判断新消息。

**能不能全自动聊到拿 offer？**
不能，也不打算做。招呼和面试安排是模板化的，其余只报告给你；任何可能承诺时间、薪资、
经历的回复都不会被自动发出。

**数据会上传到哪？**
不会上传。`state.json` / `raw/` 全在本地，仓库的 `.gitignore` 已排除这些路径。

## 贡献

- 选择器失效是这类项目最常见的维护点：改版后先跑 `scan --pages 1` 定位，再按
  [`docs/boss-site-notes.md`](docs/boss-site-notes.md) 更新，别凭猜改。
- 新增过滤规则请附带一个真实反例（哪种岗位被误杀/漏放），方便回归。
- 提交前跑 `python3 scripts/boss_hunt.py --help` 和 `validate`，确保没把个人配置带进 diff。

## 依赖与致谢

本项目的浏览器能力几乎全部来自 **[huashu-chrome](https://github.com/alchaincyf/huashu-chrome)**
（作者 [花叔 / alchaincyf](https://github.com/alchaincyf)，MIT，npm 包名同为 `huashu-chrome`）：

- 它提供 MCP server、127.0.0.1 上的本地桥和 Chrome 扩展，让脚本能用用户**当前浏览器的登录态**，
  不必传 cookie、不必重新登录；
- 本项目**不打包、不修改**它，只在运行时 `npx -y huashu-chrome` 调用；升级由 `npx` 自动取最新版；
- 它的扩展必须由用户手动安装一次（浏览器不允许脚本代装），步骤见上面的「依赖与前置」；
- 遇到 `NO_EXTENSION` / 桥连不上，先跑 `npx -y huashu-chrome doctor` 分清是本项目还是上游的问题；
- 如果你觉得上游好用，去它的仓库点个 star：<https://github.com/alchaincyf/huashu-chrome>

除了 huashu-chrome，本项目没有第三方 Python 依赖，也**没有任何接口 key**。

## License

[MIT](LICENSE)
