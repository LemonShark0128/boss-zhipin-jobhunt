# AGENTS.md — 给任意 Agent 的入口

本仓库是一个**可直接运行的 CLI 项目**（Python 3.9+，运行时零 LLM token），
不依赖任何特定 Agent。Codex、Claude Code、Cursor、Copilot、Hermes、自建或厂商 harness
（只要能读本文件、或能执行 shell），走的都是同一套流程 —— 差别只在“用哪个工具点页面”和
“用什么挂定时”，映射见 `docs/agent-adapters.md`。

## 读文件的顺序

| 顺序 | 文件 | 什么时候读 |
|--|--|--|
| 1 | [`README.md`](README.md) | 第一次接触：能力、依赖、风控取向 |
| 2 | [`SKILL.md`](SKILL.md) | 要带用户走完整流程时：访谈 → 登录 → 配置 → 干跑 → 定时 |
| 3 | [`docs/onboarding-interview.md`](docs/onboarding-interview.md) | 用户资料不全，需要一次性问完（8 必问 + 10 常被忘） |
| 4 | [`docs/risk-control.md`](docs/risk-control.md) | 任何调整频率、上限、时段之前 |
| 5 | [`docs/troubleshooting.md`](docs/troubleshooting.md) | 报 `NO_EXTENSION` / `unverified` / 定时任务没跑 |
| 6 | [`docs/agent-adapters.md`](docs/agent-adapters.md) | 要换成你自己的浏览器或定时能力 |

`SKILL.md` 的 frontmatter 符合 Agent Skills 规范（`name` + `description`），
Claude Code（`~/.claude/skills/<name>/`）和 Hermes（`~/.hermes/skills/<category>/<name>/`）
都能直接当技能装载；其他 Agent 把它当流程文档读就行，内容一样有效。

## 三条硬边界（不要越）

1. 只驱动用户**自己已登录**的浏览器。绝不代填手机号、密码、短信码、验证码 ——
   看到登录墙就停下，请用户自己扫码。
2. 不替用户答应面试时间、不改简历、不伪造经历。HR 约时间只回
   「希望线上，请先给时段，我确认后答复」。
3. 收尾只关本任务自己开的标签页，绝不动用户的其他页面。

## 默认执行路径：脚本模式

不要一上手就用你的浏览器工具替用户点页面。先判断脚本能不能跑：

```bash
python3 scripts/boss_hunt.py login-check     # 只读，确认登录态
python3 scripts/boss_hunt.py init            # 生成 ~/.boss-hunt/config.json
python3 scripts/boss_hunt.py validate
python3 scripts/boss_hunt.py template        # 招呼语渲染 + 字数
python3 scripts/boss_hunt.py plan --top 15   # 只报告，不发
python3 scripts/boss_hunt.py send 1          # 首跑只发 1 个
python3 scripts/boss_hunt.py inbox           # 盯消息
```

脚本模式需要 huashu-chrome 扩展（用户手动装一次）。装不了或用户不愿意装，
才走「代理模式」：用你自己的浏览器工具按 `docs/agent-adapters.md` 的映射
驱动同一个已登录浏览器，代价是每步吃 token，适合 ≤10 个/天或调试。

## 交付前必须验证

- `plan` 的前几条是用户真想要的方向，不是关键词硬匹配出来的噪声。
- 防重复守卫有效：对已投过的岗位必须返回 `skip_already_chatted` 且不点按钮。
- 真发属于用户决策 —— 读路径跑通即可，发几个试试之前先问用户。
- 挂定时任务后，用**真实入口**（wrapper 脚本）跑一次，别只测 `python3 boss_hunt.py`。

## 不该做的事

- 不要并发跑第二套实例：同一 BOSS 账号两套并行会双发同一条招呼、抢额度、风控特征更明显。
- 不要提高默认节奏（15–20/天、≥60s 间隔）来「验证效果」。
- 不要改上游 huashu-chrome 的代码，也不要替用户装浏览器扩展（浏览器不允许脚本代装）。
- 不要碰 `~/.boss-hunt/data/` 里的真实沟通记录往外发。
