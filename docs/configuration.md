# 配置说明

配置文件默认在 `~/.boss-hunt/config.json`（`init` 生成，查找顺序：`--config PATH` → `$BOSS_HUNT_CONFIG` → 默认路径）。
改完先跑 `python3 scripts/boss_hunt.py validate`。本页是字段参考；上手流程见仓库根的 [README](../README.md)。

## 配置文件

完整示例见 [`assets/config.example.json`](../assets/config.example.json)。

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
    { "name": "主方向", "city": "示例城市", "city_code": "101020100",
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
  "sending": { "daily_cap": 20, "min_gap_seconds": 60 },
  "inbox":   { "lookback_hours": 72, "max_listed": 5 },
  "schedule": { "timezone": "Asia/Shanghai", "windows": ["11:00", "15:00", "21:00"], "per_window": 5 }
}
```

| 字段 | 作用 |
|--|--|
| `user.greeting.mode` | `llm` = 每个岗位按 JD 现写；`template` = 渲染 `greeting_template`（默认，兼容旧配置） |
| `user.greeting.facts` | llm 模式下唯一可引用的事实清单：逐条写清「哪段经历 + 数字」，模型编不出事实外的数字 |
| `user.greeting.length` / `llm` | 字数闸门（建议 100–200）与本地模型参数（`think` 必须 `false`，否则思考模型只吐 thinking、正文为空） |
| `user.greeting_template` | 模板模式的话术，也是 llm 模式失败时的回退；≥2 个占位符（80–200 字最佳，>380 会被拦） |
| `user.interview_reply` | 面试类固定回复：只表达愿意面 + 请对方给时段 |
| `targets[].city_code` | BOSS 数字城市码，已核对的城市见 [`docs/boss-site-notes.md`](boss-site-notes.md) |
| `targets[].daily_share` | 每个方向的每日配额，防止额度全被高分方向吃掉 |
| `filters.title_allow/block` | 头衔白/黑名单（正则）；命中黑名单且不含白名单 → 拒 |
| `filters.exclude_headhunter/exclude_proxy` | 排掉猎头职位与代招岗位 |
| `filters.districts_allow` | 通勤圈；留空 = 不限 |
| `sending.daily_cap` / `min_gap_seconds` | 每日上限 / 发送间隔；建议 15–20 与 ≥60s（`min_gap_seconds` 不写时投递、对话、账本闸门统一按 60） |
| `inbox.lookback_hours` / `max_listed` / `stale_hours` | 收件箱（只读）回看时长 / 播报列几条 / 超时未回阈值 |
| `inbox.patterns.{reject,interview,interested,system}` | 收件箱归类正则（可选，`validate` 会检查能否编译） |
| `notify.channel` | 定时结果推给谁，见下方「推送」；示例配置是 `none` |

## 推送（定时结果推给谁）

定时任务由系统 cron 跑 wrapper，跑的时候没有 Agent 在场。wrapper 渲染完播报后调 `notify --stdin`，
按 `notify.channel` 推到你选的渠道；推送失败只记进 `runs.log`，不影响投递，也不改 wrapper 的退出码。
`--dry-run` 不推，只在最后一行报告推送配置是否就绪。

```jsonc
"notify": { "channel": "wecom" }
// email 另写：
"notify": { "channel": "email",
            "email": { "smtp_host": "smtp.qq.com", "smtp_port": 465, "user": "me@qq.com", "to": "me@qq.com" } }
// command 另写（argv 列表，不经过 shell；正文从 stdin 传入，标题在环境变量 BOSS_NOTIFY_TITLE）：
"notify": { "channel": "command", "command": ["/path/to/my-push.sh"] }
```

**凭据不写进 `config.json`**，写进 `~/.boss-hunt/.env`（一行一个 `变量名=值`，行尾可写 ` # 备注`，值里有空格或 `#` 就加引号；也认环境变量、`notify.env_file`、
`$BOSS_HUNT_ENV_FILE`、`~/.hermes/.env`，与模型 key 同一套查找顺序）。配好后：

```bash
python3 scripts/boss_hunt.py notify --check   # 不联网：渠道与凭据是否就绪
python3 scripts/boss_hunt.py notify --test    # 真发一条测试，确认手机上收到
```

| `channel` | 推到哪 | `.env` 里的凭据 | 要点 | 接口核实（2026-11-06） |
|--|--|--|--|--|
| `none` | 不由脚本推 | — | 用 Hermes 等会转发 wrapper 输出的调度器时选它，否则会收到两份；也适合只看 `runs.log` | — |
| `serverchan` | 个人微信（Server酱 服务号） | `BOSS_NOTIFY_SERVERCHAN_KEY`（SendKey） | `SCT` 开头走 Turbo，`sctp` 开头走 Server酱³ | Server酱³ 已核实（官方 API 文档）；Turbo 地址与字段依据作者官方示例代码，成功码 `code=0` 未核实 |
| `pushplus` | 个人微信（PushPlus 公众号） | `BOSS_NOTIFY_PUSHPLUS_TOKEN` | 以纯文本模板发送 | 已核实（官方接口文档 V1.18）；`code=200` 只表示服务器收下，不保证送达，以 `--test` 实收为准 |
| `wecom` | 企业微信群（群机器人） | `BOSS_NOTIFY_WECOM_WEBHOOK`（完整 webhook 地址） | 建一个只有自己的群再加机器人；单条超过 2048 字节会截断；每个机器人 20 条/分钟 | 已核实格式与限制；成功码 `errcode=0` 未在页面写明 |
| `feishu` | 飞书群（自定义机器人） | `BOSS_NOTIFY_FEISHU_WEBHOOK`；开了签名校验再加 `BOSS_NOTIFY_FEISHU_SECRET` | 安全设置选关键词时加 `BOSS`（每条都带） | 已核实（含签名算法） |
| `dingtalk` | 钉钉群（自定义机器人） | `BOSS_NOTIFY_DINGTALK_WEBHOOK`；加签再加 `BOSS_NOTIFY_DINGTALK_SECRET` | 安全设置选关键词时加 `BOSS`；每个机器人 20 条/分钟，超了限流 10 分钟 | 已核实（官方「自定义机器人安全设置 / 发送群消息」页，含加签算法与 `errcode=0`；`310000` = 关键词、签名或时间戳校验没过）；官方未写正文长度上限，脚本按 18000 字节自截 |
| `bark` | iPhone 通知（Bark App） | `BOSS_NOTIFY_BARK_KEY` | 自建服务器写 `notify.server` | 部分核实：官方 README 写明 `POST /:key` 与 `title` / `body`；成功码 `code=200` 未核实 |
| `ntfy` | 安卓 / iPhone / 桌面通知（ntfy App） | `BOSS_NOTIFY_NTFY_TOPIC` | topic 谁知道谁能订阅，起一个长随机名；自建服务器写 `notify.server`；正文超 4096 字节截断 | 已核实（标题按 RFC 2047 编码） |
| `telegram` | Telegram 机器人私聊 | `BOSS_NOTIFY_TELEGRAM_TOKEN`、`BOSS_NOTIFY_TELEGRAM_CHAT_ID` | 国内网络需要代理；超 4096 字符截断 | 已核实端点与 `ok` 字段；长度上限部分核实 |
| `email` | 邮箱 | `BOSS_NOTIFY_SMTP_PASSWORD` | QQ / 163 等邮箱填「授权码」，不是登录密码；465 走 SSL，587 走 STARTTLS；多个收件人 `"to"` 用逗号分隔或写成列表 | 标准 SMTP（Python 标准库） |
| `command` | 你自己的脚本 | — | 任何上面没有的渠道都能接；60 秒超时，非 0 退出算失败 | — |

## 对话模块（`conversation.*`，`chat` 子命令，回 HR 的唯一通道）

| 字段 | 作用 |
|--|--|
| `enabled` / `dry` | 总开关 / 预演（只出计划不发；预演会打开会话、清未读） |
| `hours` | 允许跑的整点（配置时区），默认 9–21 |
| `per_hop` / `daily_cap` / `lookback_hours` | 每跳最多处理几个会话 / 每日最多回几个会话 / 只看多少小时内有新消息的会话 |
| `templates` | 两轮问句等话术覆盖（键见 `conv_rules.DEFAULT_TEMPLATES`，如 `experience_tail`） |
| `opener_fallback` | 承接句生成失败时的兜底 |
| `auto_accept_resume_request` / `resume_request_card` | 过筛会话里 HR 发「要附件简历」卡片时自动点同意 / 卡片文字 |
| `resume_sent_pattern` | 简历回执正则；**一般不要写**，用代码默认（含附件卡片「点击预览附件简历」） |
| `system_texts` / `system_patterns` | 额外当系统消息的原文 / 正则（不是 HR 说话） |
| `patterns` | 意图判定正则覆盖（`validate` 检查能否编译） |
| `decline_industries` / `decline_text` / `decline_not_interested` | 黑名单行业（只看职责段）/ 回绝语 / 回绝后点「不感兴趣」 |
| `pin_manual` / `pin_max_per_hop` | 需人工的会话在 BOSS 置顶（取消置顶 = 处理完）/ 每跳最多置顶几个 |

需人工的会话之后对方又发消息，会推「🙋 需人工·新消息」并重新置顶（每跳最多 3 条，只看列表快照、不回复）。

## A/B 分级是怎么判的

列表没有 JD 正文，先按 `grading.roles` 筛职能，再参考行业/公司弱信号标 `A?` / `B?`；
职能不对口标 C。发送前读 JD，按每个 track 的 `jd_signals` 命中条数定最终 A/B，
平分或无命中用 `unknown_side`（默认 B）。C 和缺 JD 不发送，分数只决定同档顺序。
词表支持正则；`side` 和 `label` 是解释性元数据，不触发隐藏规则。

老配置缺少 `grading` 时保留 `scoring.a_min` 定级，缺 `grade_quota` 保留总日上限，不强制新增字段。
新配置 `grade_quota={"A":15,"B":5}`（示例配置的值，合计等于 `daily_cap=20`）是固定日配额，`send N` 的 N 只是总数上限。
每跳按 `ceil(各级剩余额度 / 剩余窗口数)` 分配，受 `sending.max_per_window` 限制；
为避免末跳突发，该字段也限制本跳总发送数。跳过或失败不扣额度，不跨天累积。
`carry_over=false` 使用 `schedule.per_window` 固定总数，仍受各级日配额约束。
B 本跳额度满时进入 `state.b_pool`，后续 B 阶段先取旧池；A 阶段优先。

判级缓存保存 JD、哈希及规则/列表字段指纹。同一快照重复运行不再读取 JD；规则、列表字段或快照文件更新时间变化后重读，
新 JD 哈希变化即重判。无法免请求检测网站远端 JD 的静默修改，调参后建议先重新 scan。

## 换行业怎么改

只改 `grading` + `filters` + `targets`，不改代码：替换职能词、各侧行业/公司与 JD 信号，
再配置硬排除、搜索城市和关键词。示例中的「示例行业A/B」「示例城市」都是占位符，
`100000000` 也要换为目标城市的真实代码。A/B 词表与个人经历独立，切勿照搬示例业绩。
可增加第三、第四个 track；C 始终不发送，其余新增 track 只有显式设置同名日配额才可发送。

```bash
python3 scripts/boss_hunt.py grade --jd-file jd.txt --job-name '城市经理' --industry '示例行业A'
python3 scripts/boss_hunt.py grades --top 20 --json
python3 scripts/boss_hunt.py tick --dry-run
python3 scripts/boss_hunt.py rejects --days 30 --json
python3 scripts/boss_hunt.py summary --json
```

`grade` 默认岗位名取配置的第一个 role；调试真实岗位请显式传 `--job-name`。
`grades` 统计全部 raw 快照（包括被其它过滤器挡住的岗位），已投数按 job_id 对应。

## 单入口排班与拒绝复盘

cron 在窗口整点调用 `tick`（也可以每 10 分钟调，但开了对话模块就别这样，会和 :30 对话跳抢锁），由 `schedule.timezone` 和 `windows` 判窗口（同日 ±10 分钟）：
每个窗口都投递：`scan_windows` 先补池再投递，`inbox_windows` 投递后再读收件箱（只读），
`summary_window` 投递后追加当天总结。各专用窗口须列入 `windows`（以 `tick --dry-run` 的 `actions` 为准）。
同日同窗口成功后不会重复执行；失败保留未用额度，后跳重新摊分。

`tick` 与现有 wrapper 共用 `data/.lock`。外层 wrapper 不要再持同一把锁调用 tick，
否则内层会跳过；遗留锁保守跳过，不自动抢锁。tick 要求本地桥/扩展已在线，
不可连接时快速退出，不在定时跳中循环唤醒。40 分钟后停止业务，留时间清理自身标签；
Unix 使用 alarm，其他平台在业务循环检查预算，不能保证正在阻塞的调用立即停止。

`data/rejects.json` 保存明确拒绝和满 72h 的无回复。含糊说法不计；会话缺失、末条为空或
公司匹配有歧义不算无回复。归属只按已发记录/可见会话判断，不以 `fromId` 判消息方。
同岗位幂等，no_reply 可升级 explicit 并保留首次 detected_ts；explicit_ts 表示升级日期。
`inbox.reject_reasons` 可覆盖原因正则映射；方向统计用投递时的 target。

`summary` 完全离线，输出当天投递、当天新增拒绝/无回复、累计与待确认面试。
待确认面试来自最近 inbox 的本地 pending，成功回复或明确拒绝会清除；未再次同步前可能陈旧。
无新增活动且无待确认时 `activity=false`，文字「今天没有活动」；cron 依据 JSON 的 activity 判空。
有待确认时仍显示，以免遗漏。`summary` 本身不推送；定时推送见上方「推送」。

| 新配置 | 含义 |
|--|--|
| `grading.*` | 职能词、tracks 的词表与未知归属处理 |
| `sending.grade_quota` / `carry_over` / `max_per_window` | 日配额、失败顺延、每跳上限 |
| `schedule.*_windows` / `summary_window` / `scan_pages` | 扫描/收件箱/总结窗口与扫描页数 |

## 招呼语：模板与模型现写

- `user.greeting.mode=template`（默认）：用 `greeting_template` 渲染，所有岗位同一套话术。
- `user.greeting.mode=llm`：发送前读详情页 JD，用模型和你的 `greeting.facts` 现写一条。三道护栏：
  ① 招呼语里的数字必须能在 facts 或 JD 里找到；② 字数、纯文本、点名岗位不过闸门就带反馈重写一次；
  ③ 仍不通过或模型不可用，回退 `greeting_template`。结果按岗位缓存，同一岗位重跑不会换词。
- 本地模型实测 `qwen3.5:9b`（MLX）单条 3–9 秒，`num_predict≈360` 够用；**`think` 必须为 `false`**，
  否则思考模型把输出额度花在 thinking 上，正文为空（`llm_empty(length)`）。
- 云端服务商（DeepSeek / Kimi / MiniMax 等 OpenAI 兼容接口）的配置见 [agent-adapters.md](agent-adapters.md) 第 7 节。

## 关于消息未读数

打开 BOSS 消息页会把未读数清零，这是页面自身行为。收件箱与对话模块用「消息时间 + 发送方是否是自己」判断新消息，不依赖未读数。
