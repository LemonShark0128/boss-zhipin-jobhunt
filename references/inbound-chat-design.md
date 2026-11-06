# HR 主动找我：自动对话设计

> **历史文档**：本文描述的旧回复引擎（`scripts/reply.py`、`reply_eval.py`、`inbox.auto_reply.*`）已于 2026-11-06 cleanup-02 移除；
> 现行回 HR 只走对话模块（`conversation.py` + `conv_*`，`chat` 子命令）。下文保留作设计背景与教训。

> 状态：分级与配额、回复引擎、会话定位都已上线，`inbox.auto_reply.enabled` 可开（`dry: true` 先预演）。
> 实施前先读 SKILL.md「A/B 分级与投递顺序」与本文「回复闸门与离线评测」。
> 主动投递（扫岗 → 筛选 → 按 JD 写招呼语）保持原样，本文只覆盖「对方主动来打招呼」这条线。

## 入口与分级

- 入口是 `inbox` 的 `interested` 分支（对方主动打招呼 / 表达兴趣）。`reject` 不回；`interview` 见下「约面试」。
- 分级用「职能 × 归属方」二维，不用分数阈值（分数阈值下 B 会变 0 条）。
- 归属方（平台侧 / 品牌侧）**只能读 JD 判**：列表接口没有 JD 正文、`skills` 常空、
  `brandIndustry` 是公司自报，纯字段规则实测两版都错（宽判把品牌方全放进 A，严判把
  美团 / 货拉拉 / 转转打到 B）。判不出来**按 C 处理（不回）**，不要按 B 兜底 ——
  兜底的代价是把渠道经理、总助、化妆品拓展全当城市经理回，实测一批真实 JD 里七成 B 是这么来的。

| 级别 | 判据 | 自动回？ | 首轮动作 |
|--|--|--|--|
| A | 同职能 + **平台侧**（JD 写商户拓展 / 商家运营 / 开城 / BD团队 / 平台政策 / 商户数，**要命中 2 条**） | 回 | 直接问工作内容 |
| B | 同职能 + **品牌侧**（JD 写门店运营 / 加盟商 / 督导 / 店长 / 直营门店 / 巡店，命中 1 条即可）或跨行 | 回 | 先用 `greeting.facts` 挑与该 JD 最贴的 1–2 条经历写跨行开场，问「贵司是否接受跨行」；肯定才进三问，否定则 `closed_cross_industry` 收尾 |
| C | 职能不对口（技术/产品/售前/客户成功/店长/纯运营），或**两条判据都没命中** | 不回 | 只记录，供用户自己翻 |

信号词要专属：`库存` `陈列` `选址` `装修` `GMV` 这类词在任何渠道/销售 JD 里都写，不能当判据。

## 三问（A 类；B 类通过跨行确认后相同）

1. **工作内容**：团队规模、汇报对象、KPI、是否出差/外派、岗位是新增还是替换
2. **薪资构成**：底薪区间、提成比例与算法、绩效周期、年终、补贴、发薪日
3. **风险项**：试用期长度与薪资比例、社保基数、是否外包/代招、办公地点

每问必须结合 JD 原文与对方已说的话改写，不复读同一句 —— 同质话术在 BOSS 上一眼能看出机器人。

## 状态机（`data/chat_state.json`）

```
job_id → {brand, title, grade, stage, asked[], turns, last_msg_ts, pending_question, notified}
stage: asked_content | asked_salary | asked_risk | wait_user | interview | booked | closed
```

- `asked` 去重：同一问题不问第二遍
- 单会话自动回复 ≤4 轮；每轮间隔 ≥90s；同一 HR 一天 ≤3 次；对方 24h 未回不催
- 跨行被否 → `closed_cross_industry`，不再追

## 答不上来 → 推送给用户并挂起

触发：问到简历里没有的事实（期望薪资底线、到岗时间、离职原因、学历/证书、背调授权、长期出差）。

1. 推送一条：公司·岗位 / 对方问什么 / 我的草稿 / 「回我一句我接着发」
2. `stage = wait_user`，该会话停止自动回复
3. 用户在推送渠道回一句 → 发出去并恢复 stage

⚠️ 纯 cron `no_agent` 无法等待；这条链只有 Agent 在线时才通，别把它写成无人值守。

## 约面试

- 判形式：消息含 电话/电话沟通/电话聊 → 电话；视频/线上/腾讯会议/飞书/Zoom → 线上
- **线上可直接约定**并通知用户；电话/线下先争取改线上
- 约定后推送汇报：时间、形式与链接、对方称呼、工作内容 3 行内、薪资构成
- 用户已把旧规则「时间必须本人确认」放宽为「线上可直接约」，不要再按旧规则拒绝或空等确认

## 投递配额与排班（与分级配套）

- `daily_cap=30`；`grade_quota={A:30, B:10}`；`b_fill_last_round_only=true`
- 8 个时间点：`08/10/12/14/16/18/20/22`，**一个** cron 任务按当前小时分支（scan / send / inbox / summary），
  共用一个 `data/.lock`，别建 8 个任务
- 抓取 3 次（08/14/20，`run --pages 2`），查消息 3 次（10/16/22），其余时间点只 `send N`
- 每跳配额 = `ceil(剩余额度 / 剩余跳数)`，`max_per_window=8` 封顶，跳过的时间点自动顺延
- 白天只发 A；读到 B 跳过、记 `b_pool`；末轮（22 点）用 B 补到 `daily_cap`，B 每日 ≤10；C 永不投
- 比固定 20A+10B 好：A 池够时一个 B 都不投，不会为了凑名额投跨行岗
- 末轮同时出当天总结（投递数 + 拒绝复盘 + 待确认），推送给用户

## v1 实现（`scripts/reply.py`，已写但默认关闭）

- `judge(history)`：按**整段对话**判，不只看最后一句。以下一律转人工，不自动回：薪资/待遇/社保/
  上下班时间（facts 里没有，不能编）、追问具体经历（「之前做什么类目」「有没有 XX 经验」）、
  要简历（发附件是另一个动作，靠嘴答应等于空诺）、面试邀约（走原有话术）、拒绝、含糊且需上文的
  （「以上内容能接受吗」）。
- `check(text)` 闸门：8–100 字、禁「您好/感谢/贵司/本人/该岗位」、**禁阿拉伯数字**（防编造）、
  必须带反问。不过就走模板兜底，两条都不过就不发。
- prompt 只要三件事：口语、答应沟通 + 反问一个岗位细节（区域 / 团队规模 / 汇报对象三选一）、
  不复述简历。
- 额度：`inbox.auto_reply.daily_cap`（默认 3）+ `max_tries`（一次最多试几个会话，防跑满 40 分钟）。
  `dry: true` 只生成不发、`🧪 预演回 …` 打出来，改完话术先这么看。
- 记账：`state.auto_replies[job_id] = {ts, text, source, hr}`，同会话不重复回；
  失败/转人工的集中列在 `manual_reply` 里输出，带 HR 原话，方便用户直接手动接。

## 回复闸门与离线评测

闸门（`scripts/reply.py` 的 `self_contained` / `judge`）按三段固定顺序判，顺序不能反：

| 序 | 判什么 | 命中即 manual 的模式（举例） |
|--|--|--|
| ① | 整段先过「拒绝 / 人工」 | 薪资待遇社保、面试时间、追问经历、要上文（以上内容）、拒绝（不太合适／暂时不考虑／已招到／岗位已关闭／下次再／另有安排／祝好）、地点陈述（base／常驻／办公地／驻点）、联系方式（微信／加个v／vx） |
| ② | 必须有一个邀约核心句 | 聊聊／沟通一下／有兴趣／考虑吗／在看机会／发简历 |
| ③ | 其余短句只许「安全」 | 不命中 ①、不含数字、不含条件词 |

反例（顺序错的代价）：`不太合适哈，后续有机会再沟通一下` 整句含邀约词，若先判邀约就会自动回
「可以的，方便说下这个岗位负责的区域吗」—— 在拒信上追问，撤不回。
反向的坑是要求「每个短句都整句 fullmatch」：`你好，看到你的简历，有兴趣聊聊吗`、`有兴趣了解一下吗`
都会漏回，改成「核心句命中即可 + 余句安全」。

离线评测（不联网、不开浏览器、不写 state）：

```bash
python3 scripts/reply_eval.py        # 只看闸门，秒级
python3 scripts/reply_eval.py --llm  # 自动回的用例再跑真实生成，看文案（需 DEEPSEEK_API_KEY）
```

- 用例在 `tests/fixtures/reply_cases.jsonl`，字段 `id / hr / expect(auto|manual) / why`；
  分类覆盖：泛邀约、要简历、追问经历、薪资、条件、面试时间、需上文、拒绝、混合句。
- 判据优先级：**误自动 = 0**（说错话撤不回）> 答非所问 = 0（文案是否对题）> 漏自动（少回一家）。
- 断言别写死条数，用 `len(E.load_cases())`；`tests/test_reply_gate.py` 只守零误自动。
- 在配置里加人物要点后，`--llm` 才会真正调模型；无 brief 的泛邀约只有开 `inbox.auto_reply.use_llm`
  才走 LLM，否则一律模板 —— 排查「文案千篇一律」先看这个开关，而不是去改模板。

## 上线开关与预演顺序（别跳过预演直接真发）

1. `inbox.auto_reply.use_llm` 在代码里默认 **false**，上线时才在用户配置里显式打开；改配置前
   `cp ~/.boss-hunt/config.json ~/.boss-hunt/config.json.bak-$(date +%H%M%S)`，回滚就是把那个键改回 false。
2. 打开后先 `dry: true` 预演一轮（dry 行也计入 `used`，所以一晚最多 `daily_cap` 条）：
   播报里的 `🧪 预演 品牌：文案` 就是「本来会发出去的原话」，人工看完再 `dry: false`。
3. 预演期需要更多样本时，用**临时配置**把 `daily_cap` / `max_tries` 放大并显式 `paths.workdir`，
   别改真配置，也别把它当新配置留着。

## 拿真实 HR 消息做预演（比只跑 fixtures 有说服力）

`inbox --json` 的 `unanswered.items.unknown[]` 里带 `last_text`，是真实 HR 原话，可以直接过闸门：

```python
ok, why = R.self_contained(item["last_text"])          # 判定
R.generate(cfg, {"encryptJobId": item["job_id"]}, text)  # 只生成，不发送、不写 state
```

用处：证明「拒信 / 岗位广告 / 薪资条件」这类真实文本会被拦住，而真实邀约会被回。
已知局限：`unknown` 每桶最多 100 条且多数 `last_text` 为空（老会话正文抓不到），实测只得 5 条可用 ——
样本永远是薄的，覆盖面仍然靠 `tests/fixtures/reply_cases.jsonl`，别拿 5 条当验收。

## 承诺必须落成待办

自动回里出现「我整理一份发您」就是承诺。模板与 LLM 两条路径的承诺口径必须一致（都允许答应，
都禁出现「简历」「附件」字样），且要写进 `state.resume_followups`（同一会话只留最新、最多 30 条），
播报里提醒用户去发。只在 `auto_ok` 里记一条成功、没人跟进，等于对 HR 空口承诺。

模板选择用 `zlib.crc32(job_id.encode()) % len(templates)`，不要用 `hash()`：Python 每次进程哈希随机，
同一岗位会轮流换话术，看起来像「模板不稳定」。

## 已探明的页面事实（可直接引用，别重探）

- **`friend/getG` 基本拿不到消息正文**：`lastMessageInfo` 在大多数条目是 null（实测 114 条里只有
  3 条有 `showText`）。所以 `inbox` 长期只能数「新招呼 N 条」却读不到 HR 说了什么 ——
  「HR 打了招呼脚本没反应」的真因在这里，不是分类规则写错。
- `/wapi/zprelation/friend/getGeekFriendList.json` 是真正的会话列表接口，一次回 15 条，
  但 `lastMsg` 也是 null。列表接口给不了正文，正文只能点开会话后读。
- 会话项 DOM：`span.last-msg-text < div.gray.last-msg < div.text < div.friend-content
  < div.friend-content-warp < li < ul < div.user-list-content < div.user-list < div.chat-content`。
  列表项正文常只有「您正在与Boss 某某 沟通」，所以**匹配公司名会落空，要用 HR 姓名再试一次**。
- 右侧对话：`.message-item`，自己发的带 `item-mine`，对方的不带（`item-friend`）；
  正文取 `.message-content` 的 innerText（外层 `innerText` 混了时间和 `[送达]/[已读]`）。
  头部会话名取 `.chat-title` / `.title-box` / `[class*=chat-title]`。
- 消息列表是**虚拟滚动**，只渲染可视区十几项，老的会话不在 DOM 里。
- 探接口的可靠办法：`performance.getEntriesByType('resource')` 取全部 URL，再按
  `msg|chat|friend|geek` 过滤去重 —— 比猜接口名快得多。

## 卡点：会话定位（历史记录，已解决）

早期点击列表项不切换会话（`el.click()` / CDP click / `scrollIntoView` 都试过无效），一度只能读默认会话。
现已通过 `chat_send` 的会话定位打通，`inbox` 能读、能回。**兜底硬规则仍然保留**：发送前必须校验
`.chat-title` 头部含目标公司名或 HR 名前 2–3 个字，不匹配立刻中止并把实际头部写进 `wrong_conv(头部文本)` ——
只校验「点没点成功」而不校验头部，就会把回复发进错的会话，发出去撤不回。
## 其余缺口

- 定时单独一跳（如 `0 13,16,19,22 * * *`），与投递跳、inbox 跳共用 `data/.lock`
- 分三期：①分级 + 配额（已上线）→ ②回复引擎（v1 已写，等会话定位）→ ③A 类三问 + 状态机 + 转人工推送

## 风控

- 首批 3 个会话/天试水，稳定两周再放到每轮 ≤8、每天 ≤20
- 22 点后不回消息；「秒回 + 长对话 + 高频」的组合最容易被判机器
