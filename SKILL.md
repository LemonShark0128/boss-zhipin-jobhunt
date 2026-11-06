---
name: boss-zhipin-jobhunt
description: BOSS直聘求职自动化：访谈需求→登录→筛选→打招呼→盯回复。
---

# BOSS直聘 求职助手（可复用版）

> 公开仓库：<https://github.com/LemonShark0128/boss-zhipin-jobhunt>（MIT）。
> 本目录是技能本体，同时就是仓库工作副本；对外交付给链接即可。
>
> 这个 `SKILL.md` 不是 Hermes 专属：frontmatter 符合 Agent Skills 规范，Hermes 和
> Claude Code 用同一个文件直接装载；Codex / Cursor / 自建或厂商 harness 读仓库根的 `AGENTS.md`；
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
2. 不改简历，不伪造经历，不替用户答应面试时间；只回「希望线上，请先给时段，我确认后答复」。
3. 有平台风控风险，节奏按 `docs/risk-control.md`；不承诺「一定安全」。
4. 同一账号不要再跑第二套自动化（别的油猴脚本/工具），会双发同一条招呼。
5. 收尾只关本技能自己开的标签页，绝不动用户其他页面。

## 前置依赖

Chrome（已登录 BOSS）+ Python 3.9+ + Node ≥20（`npx`）+ [huashu-chrome](https://github.com/alchaincyf/huashu-chrome)
（MIT，运行时 `npx` 拉取，不打包）**及其 Chrome 扩展**。

招呼语用 `greeting.mode=llm` 时还要求本机跑着 Ollama + 一个中文小模型（默认 `qwen3.5:9b`）。
Ollama **直连启动**，别注入 `HTTPS_PROXY`（代理抖动会掐断 manifest 请求）；
`preflight` 会打印 `招呼语：mode=llm … ok:<模型>` 或 `down:…`。模型没起不阻断发送，
但会退回模板话术——想每岗现写就必须先把它起起来。

平台：macOS 为实测环境；`chrome_mcp.py` 的 Chrome 唤醒已按平台分支（macOS `open -a` /
Linux `google-chrome`｜`chromium` / Windows `start chrome`），非 macOS 分支未实测，
回报时要说明是「未验证」而不是「支持」。定时脚本里的 `caffeinate` 只在 macOS 有效。

扩展必须由用户手动装一次：商店
<https://chromewebstore.google.com/detail/foiljmaplphdfimfcnfdpekhdnfbgfbf>，
或 `npx -y huashu-chrome extension` 打印目录后在 `chrome://extensions` 加载已解压的扩展。
装完 `npx -y huashu-chrome doctor` 应显示「Chrome 扩展在线」。
不要替用户装（浏览器不允许脚本代装），也不要改上游代码。

## 一图看全流程（用户问「这技能怎么运作」时先给图）

用户要调整这个技能时，第一句常是「先给我个流程图」。建议在你的 Agent 产物目录备一张单页 HTML
流程图（如 `<agent 产物目录>/boss-hunt-flow.html`），被问到时直接交付。
改完代码就同步这张图，别让它停在旧分支上。

画图口径：**按代码真实顺序画，不按本文件的散文顺序画**——散文里 `title_block` 与 `title_hard_block`
读起来像并列，代码里 `hard` 先判且 allow 命中也拒；`send_one` 的五种返回、`do_send` 的五道守卫、
`inbox` 的判定链同理。写图前用 `grep -n "^def " scripts/boss_hunt.py` 定位，再读 `judge()` /
`score()` / `do_send()` / `send_one()` / `inbox()` 的分支，图才和实际行为对得上。

交付节奏：**先给图，再问「要改哪一段」**，不要在他没指方向前直接动 `config.json` 或代码。
需求横跨多处（分级 + 配额 + 排班 + 话术 + 汇报）时，先整理成一份需求文档：每条带数据依据
（用 `raw/` 回放或 `state.json` 统计），把待拍板的点编号列成问题，用**一次 clarify** 逐条确认，
确认完再动配置或代码。他会边看边加需求，边聊边改会白改一遍。
他的需求里常带自相矛盾（既要 A 类精准、又要 B 类只在 A 干涸时补位；既要统计回复率、又只给 3 条/天；
允许直接约线上时段、却没给可约时间表）。动工前先用池子存量、每日额度、两周样本量把每个选项的代价算成数字，
拿数字逼他选，选项里把自己的推荐放第一位 —— 照单实现等于把矛盾写进代码，验收时才发现就得重做。
他可能指四类之一，对应落点不同：筛选规则 → `filters`/`scoring`（改完 `validate` + `audit_filters.py`
回放 raw）；招呼语 → `RULES`/`facts`/闸门阈值（改完跑 `greet --times 6` 回归）；
发送守卫 → `sending`（额度/间隔/出海名额）；汇报口径 → 本文「报告与回复规则」。

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

输出 `✅ 已登录` 才继续；`🔑 未登录` 就等用户登完再跑。

登录态要三个信号一起看，单看「页面里有没有用户区」会假阳性——导航栏的「我要招聘 / 我要找工作」
块会被当成头像区。判据是 **存在「我的简历 / 退出登录」且页面没有「登录/注册」**；头像选择器只认
`.nav-figure` / `img[class*=avatar]`，不要用 `.user-nav` / `[class*=user-info]` 这类宽选择器。

### 3. 生成并校验配置

```bash
python3 scripts/boss_hunt.py init            # 生成 ~/.boss-hunt/config.json
# 编辑 user / targets / filters（字段见下表）
python3 scripts/boss_hunt.py validate        # 离线校验，有 ❌ 必须改
python3 scripts/boss_hunt.py template        # 预览模板招呼语 + 字数 + 校验标记
python3 scripts/boss_hunt.py greet --job-id <encryptJobId>   # 预览该岗位现写的招呼语
python3 scripts/boss_hunt.py greet --job-name 'KA经理' --brand '示例公司' --jd-file jd.txt --times 2
```

`template` 输出的字数要 80–200 字；>380 会被拦。校验标记（招呼语开头 8 个字）
是后续判断「到底发出去没有」的依据。`greet` 是 `mode=llm` 下的离线预演：不需要浏览器，
`--times 2` 连写两条看多样性，闸门（字数/纯文本/点名岗位/数字可溯源/去 AI 味/结尾完整）不过会标出来，
回退模板的行会打印 `回退原因`。

### 4. 干跑校准

```bash
python3 scripts/boss_hunt.py preflight           # 登录 + 招呼语开关 + 今日额度
python3 scripts/boss_hunt.py scan --pages 1      # 抓 1 页验证链路
python3 scripts/boss_hunt.py plan --top 15       # 让用户看名单，不对就调过滤
python3 scripts/boss_hunt.py send 2              # 首跑验证链路用 2：send 1 会被压成 A1/B0，B 档必然发不出
```

`preflight` 若报 `auto_greeting_on`，必须让用户关掉 BOSS 自带招呼语
（或 `preflight --fix`）：开着会让 HR 收到两条（默认话术 + 你的模板）。
报 `auto_greeting_unknown` 同样**不准发送**：那是「没拿到明确关闭证据」，不是「已关闭」。
页面真实结构（`input[type=hidden]` value 0/1，没有 `ui-switch-checked`/`aria-checked`）与真机取证配方
见 `references/login-and-verification-pitfalls.md` 第 10 条；改过开关/归属/按钮判定后必须先跑一次真机 `preflight`。

**验收证据**（缺一条就别说跑通了）：

| 步骤 | 成功标志 |
|--|--|
| validate | 无 ❌；`mode=llm` 时 facts 非空且带数字 |
| greet | `[1] … 闸门 通过`，且 `--times 2` 两条内容明显不同；`preflight` 里 `招呼语 … ok:<模型>` |
| scan | `抓取 N 页响应`，N≥1；`库内岗位 M` 与预期同量级 |
| plan | 前几条是用户真想要的方向；不合理就调 `title_allow`/`industry_block` |
| send | 有 `A/B 已发｜岗位｜公司｜薪资｜招呼语:llm N 字` 行，且 `今日累计 1/N`；需复核时另看消息页左侧会话列表是否新出现该公司条目（时间戳应对得上，见 `references/login-and-verification-pitfalls.md` 第 9 条） |
| inbox | `近 72 小时新消息 …（列表 N）`；只读，不点不发、不新增发送流水 |
| chat | 面试类只出现「线上 + 约时段」的话术，不出现答应线下到场 |
| grade / grades | JD 样本等级与信号可解释；粗标统计与 raw 一致，全程离线 |
| tick --dry-run | 窗口动作正确，不开浏览器、不发送、不记账 |
| rejects | kind / attribution / reason / grade / target 分组与样本一致 |
| summary | sent / by_grade / rejects / no_reply / pending 与本地记录一致 |
| 收尾 | `已关自己开的标签页 N 个`；用户自己的页面没动 |

**回归与验证边界**

- 改筛选规则后，别只看「候选从 93 变成 289」这种数量变化：把新规则跑在历史 `raw/`
  快照上，与旧结果做集合差，逐条看差异是不是想要的（新增的猎头/代招/经验超标过滤会
  体现为「旧有我无」）。数量只是线索，差异清单才是证据。
- 改 `grading` 的判级逻辑，用 `state.grade_cache` 里已存的真实 JD 正文**离线回放**验证：
  `for jid, v in cache.items(): grade_job(job, cfg, v["jd_text"])`，新旧等级逐条对照、数差异条数。
  比改完直接 `send` 快几十倍，也不产生真实投递。**不要用真发去验证判级改动** —— 发出去撤不回，
  误投的 HR 只会回「不合适」，还白白消耗额度与风控暴露。
- `load_state` 会从当天已发记录里反推 `day_by_grade`（防改 state 骗额度），所以把 `state.day`
  改成明天再跑也模拟不出「新的一天」：A 的已用量仍按今天的记录恢复，配额可能整轮是 0。
  要预演新一天就换 scratch workdir，不要手改真实 `state.json`。
- 真实发送属于用户决策，不要用「发几个试试」验证链路：读路径（`login-check` /
  `scan` / `plan` / `inbox` 只读）跑通 + 防重复守卫跑通（对已投岗位必须返回
  `skip_already_chatted` 且不点按钮）就够，真发先问用户。
- 改了代码要在**不发消息**的前提下验证：复制配置到 scratch 目录（`inbox` 现在只读、不发消息；
  别跑 `send` / `tick` / `chat`），把 `paths.workdir` 指到 scratch，再复制 `~/.boss-hunt/data/raw`。用 `cp -r raw/. <workdir>/raw/`（末尾 `/.`）；
  写成 `cp -r raw <workdir>/raw` 会嵌成 `raw/raw`，`load_jobs` 读到 0 条、candidates 空，
  看起来像过滤逻辑坏了。
- `send` 的写入路径用替身跑：`B.send_one = lambda *a, **k: "sent"`、`B.time.sleep = lambda *a, **k: None`，
  再调 `do_send(cfg, None, None, st, 2, out)`，检查返回结构（`sent` / `items`）与 `out` 文本行是否都符合预期。
  **替身要在一次性子进程里跑：** `B.time` 就是 stdlib `time` 模块本身，给它挂 `sleep` 会连你自己的
  轮询/重试循环一起变成空转（for 循环瞬间跑完，看起来像进程“秒退”）；且常驻内核的 `sys.modules`
  会缓存改代码前的旧模块，同一次会话里改完磁盘代码再用 `import boss_hunt` 验的是旧逻辑。
  正确做法：`subprocess.run(['/usr/bin/python3','-c', code])` 每次新解释器加载，或用 `time.sleep` 的本地别名。
  **跑之前必须把 `paths.workdir` 也改到 scratch**：只复制 config、不改 workdir 时 `save_state()` 会把假的
  `sent`/`contacts`/`day_count` 写进真实 `state.json`（实测一次污染了 10 条）。发现污染这样复原：按
  `greet_text` 是替身占位串筛出假记录 → 删 `sent` 条目、删尾部同数量 `contacts`、`day_count` 与
  `day_by_target` 一并回退，再跑 `preflight` 核对「今日已发 N」。

### 5. 交给定时任务（可选）

新配置推荐单入口 `tick`，由脚本按配置时区分派动作，先用 `tick --dry-run` 校准：

```bash
# wrapper 用 flock 文件锁（写法见仓库 `scripts/cron/boss-hunt-chat.sh`），不要自己 mkdir data/.lock：
# 那会把 python 的 flock 文件改成目录，每跳窗口内 tick 全灭（2026-10-28 实测）。
# 播报由 `boss-hunt-tick-fmt.py` / `boss-hunt-inbox-fmt.py` 渲染，格式坏了不会吞掉业务退出码。
#!/bin/bash
caffeinate -i /usr/bin/python3 <skill>/scripts/boss_hunt.py tick --json
# cron: */10 * * * *；tick 自己检查 schedule.timezone 和窗口。
```

**每个窗口都要投递**；`scan_windows` 在投递前补池（默认 3 个），`inbox_windows` 在投递后再收件箱，
`summary_window` 再追加总结。**不要把 inbox 窗口写成「只收件箱、不投递」** —— 那样 8 个窗口里只剩 1 个在发，
一天最多投 3 个，而且脚本正常退出、日志无报错、`state` 看着一切正常，只能靠核对配额发现。
改完用 `tick --dry-run` 逐窗口看 actions 里有没有 `send`，这是唯一判据。
不要每跳都用 run 扫描全部关键词。
老配置仍可使用 run/send/inbox 与原 wrapper，但不要在已有目录锁里面调用 tick。
`max_per_window` 放在 sending 中，quota 根据各级剩余额度和剩余窗口现算；失败不写扣账。
已成功完成的同日同窗口不重复执行，遗留锁保守跳过。

`caffeinate` 只是防睡眠，不杀子进程。tick 有 40 分钟业务预算，Unix alarm 兜底，
其它平台在循环边界检查；关键词多时仍应减少 scan_pages / 拆分关键词，不保证所有候选处理完。
桥/扩展必须预先在线，tick 不循环拉起 Chrome；不可用时快速退出并顺延未用额度。

**例外：离线会先唤醒一次（2026-10-29 用户批准）**：`connect(fast=True)` 抛 `bridge_offline` /
`extension_offline` 时，macOS 上会调 `CM.wake_extension(cfg)` → 等一小段 → 再 `connect(fast=True)` 一次，
成功就继续本跳（播报 `桥离线已唤醒 Chrome 并恢复`），仍失败才整跳失败。代价是 `open -a Chrome` 会把 Chrome
拉到前台抢焦点 —— 用户已明确接受，别再加二次确认。

**错过窗口补跑（`schedule.catch_up`，默认 false）**：开启后，不在窗口内的 tick 会把「今天已过 10–90 分钟
且不在 `ticks_done`、也不在 `ticks_failed`」的最近一个窗口补投一次（只做 send，不 scan/inbox/summary，
复用 `do_send`，补跑 key 就是那个窗口，保证同窗口只补一次）。`missed_windows` 仍然每次都报
`⏰ HH:MM 跳被吞了（晚 N 分钟…）`。补跑会真发，所以开关默认关，要用户点头再打开。

**三类「本来能回却被判需人工」的误拦（2026-11-01 真机取证，改闸门口径就看这条）**：一条真实招呼
「你好，看了你的简历，方便聊聊吗？我们主要做虚构品牌线下广告置换投放的，周末双休，不加班」被连拦三次，
每一层都在不同文件，只改一层仍然发不出去：

1. `conv_rules.PATTERNS['manual']` 里的裸 `置换` 命中了业务描述「广告置换投放」。改成 `置换.{0,4}(?:资源|客户|名单|货物|押金|费用)`，
   「需要自备客户资源置换」仍拦。同理「前置收费|自备|垫资|押金|加盟费」要保持强语境。
2. （历史：`reply.py` 已随 cleanup-02 移除，此条仅作口径参考）`reply._HUMAN_PAT` 里的 `双休|加班` 把福利描述「周末双休，不加班」当成排班条件。删掉 `双休`、`加班` 改 `(?<!不)加班`；
   `单休|大小周|夜班|倒班` 继续拦。
3. **BOSS 的「你与该职位竞争者PK情况」提示气泡在真机上带 `message-item item-friend` class，和 HR 真人消息同 class**，
   而且排在 HR 那句之后 —— `reply.generate()` 取 `history[-1]` 就拿到了系统文案，整条判「其他」。
   所以 `is_system_message` 除了 class 标记和精确文本，还要有**文案特征正则**（`conversation.system_patterns`，默认
   `你与该职位竞争者PK情况.{0,20}(?:共.{0,4}人投递|查看详细分析)`），且 `generate()` 要用过滤后的 `human` 取最后一条。
   **不要改成前缀匹配**：`test_system_texts_exact_match_and_config_override` 守的就是「系统文案 + 真人指令
   （『你与该职位竞争者PK情况，请发简历』）」必须留给真人流程。

当时的验收判据（旧链路，已移除）：`reply_eval.py` 32/32 且**误自动 0**；`python3 -m unittest discover -s tests` 全绿（`tests/test_system_patterns.py`
守着上面第 3 条）；真机 `inbox` 输出出现 `💬 回 [<HR>] → …` 且需人工数减一、`send_ledger.jsonl` 末尾 `status=sent`。

> 下面到「成功分支只做 …」为止，记录的是旧 inbox 自动回话链路（`reply.py` / `human.py` / `reply-to`）已于 2026-11-06 cleanup-02 移除之前的旧实现，保留作教训；
> 现行回 HR、发简历都走对话模块（`conversation.py` / `conv_actions.send_resume`）。

**系统气泡还留了一个洞**：LLM 回出「我整理一下发您」时 `state.resume_followups` 没登记（承诺落空没人去发），
查承诺要同时看 ledger 文案与 `resume_followups` 条数，别只看「发出去了」。

**第一轮请对方介绍工作；要简历直接发附件（2026-11-01 用户要求，两轮都改过）**：

· `build_invite_prompt` 的 invite 分支现在是「答应沟通，直接请对方详细介绍这个岗位的工作内容（做什么、团队、日常）；
  不要问区域 / 规模这类细节，不要说整理、发资料、发您任何东西」。**不要再改回「反问负责区域 / 团队规模 / 汇报对象」**——
  用户明确要第一轮就请对方把活说清楚，区域规模放到后面。
· 对方要简历：**不生成任何文字，直接点 BOSS 自带的「发附件简历」**。实现在 `boss_hunt.inbox` 自动回话循环：
  `gr['intent'] == 'resume'` → `conv_actions.send_resume(...)`，成功（`sent`/`pending_verification`）就记账并 `continue`。
  **只有发简历失败（点不到按钮 / 未验证）才退回 `R.generate` 的文字 + `resume_followups` 待办**，不能既没发出去又没吭声。
  `reply.RESUME_TEMPLATE` 保留「我整理一下发您」，因为它现在只当失败兜底，且 `tests/test_reply_gate.py`、
  `tests/test_reply_llm_20261028.py` 硬断言了这个词；**不要往 `check()` 里加全局「整理一下发您」拦截**。

验收：`tests/test_resume_followup_20261028.py` 的 `run_inbox(resume_result=...)` 一个参数切两条路径；
真机看 `📎 发简历 [<HR>] → sent` 且 `state.resume_followups` 不增加。

这条路径上有两个坑（真机各踩一次）：

· **意图正则要容忍中间夹修饰词**：`_RESUME_PAT` 原来要求「发份简历」连续，HR 的真实说法是「方便发份详细的简历过来吗」，
  整条被判 `other` 进「需人工」，按钮永远轮不到被点。改成 `发(?:份|一份)?[^，。？！?]{0,10}简历` 才命中。
  改完必须用**真实 HR 原话**跑一遍 `R.generate(...)` 看 `intent == 'resume'`，别只看单测 —— 单测样本是自己写的短句，
  天然按正则的正确形状写。
· **发完不要再补记 `remember_reply`**：`send_resume` 自己写 ledger（`cmd=conv_resume`），调用方再按 `auto_resume`
  补一条会撞 `hunt_state.remember_reply` 的 `missing_confirmed_reply_event` 直接抛异常，整次 inbox 只吐一行报错、
  别的会话全不处理。成功分支只做 `used += 1` + `save_state` + 播报。

**仓库外的定时 wrapper 别各拷一份播报脚本**：定时入口放在仓库外（例如 Agent 自己的 scripts 目录）时，
让它直接调用仓库里的 `scripts/boss-hunt-tick-fmt.py` / `scripts/cron/*-fmt.py`，或像
`scripts/cron/boss-hunt-inbox.sh` 那样只 `exec` 仓库脚本。若拷了一份到部署目录，只改仓库版本不会生效，
必须同步。验证：把 `{'window','today_sent','send'}` 的 json 管道喂给**定时任务实际调用的那份** fmt 看输出。

**（历史，旧 inbox 自动回话链路（`reply.py` / `human.py` / `reply-to`）已于 2026-11-06 cleanup-02 移除）自动回话不再静默（2026-10-29）**：`inbox()` 的自动回话循环原来有三个静默 `continue`
（打开会话失败 / 最后一句是我说的 / 归属不明），实测会出现「新招呼 40 个｜今日已回 0/3」而一行说明都没有。
现在固定输出 `自动回话：新招呼 N 个｜回 A｜需人工 M｜打开失败 X｜我最后说过 Y｜归属不明 Z`，
且 `open_by_index` 失败会同一行重试一次再计失败。**`dry` 语义与回话闸门一个字都不要动** ——
`dry=true` 时只预演（播报 `🧪 预演`），真发必须用户明确同意后把 `dry` 关掉。
**别凭记忆判断「现在是预演期」**：这个开关用户会自己切、另一套 agent 也可能改，实测在设成 `dry=true`
的当晚之后真发过一条自动回复（`send_ledger.jsonl` 里 15:03 一条 `auto_reply/sent`）。
每次汇报「现在是预演、不会真发」之前先 `json.load(~/.boss-hunt/config.json)["inbox"]["auto_reply"]` 回读现值，
改完配置也回读确认落盘；对不上就先按「可能在真发」处理，别继续拿旧设定做承诺。
**`auto_reply.enabled=false` 时 `inbox` 一行都不打印**：自动回话整段被静默跳过，连「新招呼 N 个｜已回 0」都不出现，
看起来像链路坏了。排查「今天一条都没回」先读 `enabled`，再读 `dry`。
**验证新话术不要临时把 `enabled` 打开在生产 state 上真跑**：在内存里 monkeypatch `HC.load_config` 改开关（不落盘）
确实一次就通，但一次真发就可能留下 `conv_resume` 的 `pending`，按上面的规则整台机器停机，还要花用户的时间去核对。
要预演文案用 `dry=true`，要跑真链路用独立配置 + scratch `paths.workdir`，别在生产 state 上碰真发。

**scan 失败不再整跳取消投递（2026-10-29）**：`scan_windows` 里 `scan_network_failed` / `scan_failed` /
`no_pages` 这几种网络类失败现在只记 `data["scan_error"]`，本跳继续用旧快照投（`do_send` 自己的
`stop_reason=network` 仍会兜住真断网）。只有 `not_logged_in` / `risk_*` / `captcha` / `TimeoutError`
仍然整跳退出 —— 那是登录、风控和预算信号，不能一边撞墙一边继续点按钮。
判据是 `tick --json` 里 `scan_error` 有值但 `send` 仍在、且 `ticks_done` 不写这一跳。

**晚触发不再静默（2026-10-29）**：`tick_plan` 只认 ±10 分钟容差，调度晚 41 分钟的那一跳会被判「不在窗口」
整跳跳过、fmt 一行不输出，用户看到的就是「没触发」（10-28 20:00 实测）。现在 `missed_windows()` 会把
「今天已过 10–90 分钟、又不在 `ticks_done` 里」的窗口报出来（`⏰ 20:00 跳被吞了（晚 41 分钟…）`），
并用 `state.missed_reported` 去重，每个错过窗口只报一次；**只报不补跑**，要不要补跑由用户定。

**失败跳留痕**：`run_tick` 的 except 分支写 `state.ticks_failed`（最近 64 条，`key/error/ts`），
纯审计、不参与任何判断；state 本身读不出来时不覆盖原文件（避免把损坏状态洗成空状态）。
排查「今天为什么没投」先看这个字段，不用再翻几百 KB 的 `runs.log`。

`scan_windows` 以外的补池失败例外：`inbox_windows` 的失败仍整跳退出，回消息那条路径不降级。

各 Agent 定时映射见 `docs/agent-adapters.md`。挂定时前先验证真实 wrapper 的 dry-run 入口；
真发必须另有用户授权。脚本不直接推送消息，外部 cron 侧读取 JSON 决定如何通知。

**播报丢了要有据可查（2026-10-29）**：投递失败发生在 tick 退出之后（推送渠道侧），脚本不可能知道这条发没发出去。
所以 `boss-hunt-tick-fmt.py` 渲染完会把同一条文本追加到 `~/.boss-hunt/data/broadcasts.log`
（`[时间] window=HH:MM` + 正文 + `---`，可用 `BOSS_BROADCAST_LOG` 改路径；写失败只进 stderr，不影响 stdout 与退出码）。
用户说「某点没收到播报」时：先看 `broadcasts.log` 有没有写出来（有 = 脚本侧正常，去查推送渠道的投递），
再看定时器自己的执行记录（如 Hermes 的 `cronjob_manage list` 里 `last_status` / `last_delivery_error`）。
推送渠道常见两类失败要分清：会话/授权失效（通常要用户在渠道里给 bot 发一句话重新激活）和代理/DNS 抖动（重试即可）。

每跳播报固定带「今日已发」清单（只在窗口内输出，窗口外的补跑仍静默），**必须一条一行写「公司 ｜ 职位」**，
如 `· 示例公司 ｜ 区域经理`；`(A)/(B)` 等级字母和 `＋3` 这种省略写法一律不要 —— 用户原话
「这样一段看不出有效信息」，他要看的是投了哪个岗。最多列 8 条，超出写「另有 N 个未列出」。
数据取 `hunt_schedule.tick_today_sent` 的 `items[].brand / name`（`name` 才是岗位名，`grade` 只用于内部配额），
`📮 HH:MM 跳｜发出 N｜今日 X/Y` 后面也不要再挂 `（A 2 · B 1）`。
所以漏掉一跳也能从下一跳看到当天累计；`stop_reason=inspect_cap` 时无论发出几条都会显示
`⏳ JD读取到上限，还有 N 个候选留下跳`。

## 招呼语：模板 vs 按岗位现写

`user.greeting.mode` 决定每条招呼语怎么来：

| 模式 | 怎么产出 | 什么时候用 |
|--|--|--|
| `template`（默认，兼容旧配置） | `user.greeting_template` + 占位符，一个岗位一套同样的话 | 不想依赖本地模型；首跑校准 |
| `llm` | 发送前读详情页 JD 正文，把 `user.greeting.facts` 交给本地 Ollama 现写一条 100–200 字、点名岗位、只挑该 JD 最看重的 2–3 条战绩 | 想让 HR 看出这条是专门写给他的 |

**llm 模式的四条硬约束**（代码在 `scripts/greet.py`）：

1. **事实只有一个来源**：`user.greeting.facts`。模型写出的每个阿拉伯数字都必须能在
   facts / JD / 岗位字段里找到，否则判为编造 → 带反馈重试一次 → 仍不过就回退模板。
   facts 要逐条写清「哪段经历 + 数字」，带「独家」「覆盖率」这类限定词的成绩要自带数字，
   否则模型会张冠李戴（实测把 A 段经历的「独家 N+」挂到 B 段经历的「M+」上）。
2. **失败不阻断发送**：Ollama 没起 / 超时 / 连续两轮不过闸门，都用模板渲染发出去，
   `runs.log` 和 JSON 里记 `template_fallback` + 原因。发送结果里的
   `greet_source` 只有 `llm` / `cache` / `template_fallback` 三种，看到 fallback 就该去修。
3. **话术同一岗位稳定**：结果按 `(jobId, JD+facts 指纹, PROMPT_VERSION)` 缓存到
   `data/greet_cache.json`；重跑不会换词。改了 prompt 就 `PROMPT_VERSION += 1`，
   否则旧缓存会一直发旧话术。升版本前先告诉用户：缓存里几百条历史话术会全部失效，
   之后发出去的与之前不同，且每条要多花一次模型调用。
4. **校验标记必须用实际文本**：llm 模式下招呼语每岗不同，`verify_marker(cfg, job, text)`
   要吃实际发出去的那条 text（此时忽略 `sending.verify_text`），否则每条都会被判 `unverified`。

### 去 AI 味（招呼语的核心质量门槛）

只约束"事实准"的 prompt 写出来是典型简历体：`具备…能力`、`沉淀 SOP`、三句排比、句句带 KPI，
用户看到的第一反应就是"AI 感很重"。下面五层约束**改 prompt 时一层都不要删**：

1. **骨架代替自由发挥**：①看到你们在招 X，我在这行做了 N 年 →②两条成绩（哪家公司/城市+做了什么+结果）
  →③用 JD 原词呼应"你们看重什么 + 我在 Y 做过"→④固定结尾句。骨架写死在 `RULES` 里。
2. **人称硬约束**：只准「我」指候选人、「您/你们」指招聘方；禁反问、禁"吧/呢/吗"收尾。
   （放开语气要求后模型会把候选人的经历写成"你做这行 N 年了吧"，必须显式禁。）
3. **固定结尾三选一**：`CLOSINGS` 由代码随机挑一句注入 prompt 并要求原样照抄，
   闸门 `bad_closing` 校验。不然模型会写出"想聊聊""简单聊聊吗。"这种半截话。
4. **AI 味词库分两级**：`STYLE_WORDS_HARD`（具备/拥有/沉淀/赋能/闭环/深耕/匹配度…）命中一个就重写；
   `STYLE_WORDS`（高效/深度/能够…）允许 1 个。用户可再用 `user.greeting.style_forbid` 追加。
5. **数字防走样**：`cn_numbers` 闸门禁中文数字（模型会把 85% 写成"加一五"、-30% 写成"三十天"，
   数字溯源闸门就失效了）；`min_numbers=2` / `max_numbers=6` 防止堆成 KPI 清单。

配套：facts 里别出现「沉淀」这类词（模型会照抄），改写措辞比事后拦更省事。

**用云端 API 而不是本地 Ollama（2026-10-24）：** 本机跑 qwen3.5:9b 写招呼语会把 CPU 顶到 90°+（用户实测多次高温预警），且 4–15 秒/条。切 `user.greeting.llm` 到 DeepSeek：
```json
{"provider":"deepseek","base_url":"https://api.deepseek.com/v1","model":"deepseek-chat",
 "api_key_env":"DEEPSEEK_API_KEY","num_predict":400,"timeout_s":45}
```
`greet.py` 已支持两种后端：`provider=ollama`（默认，走 `/api/generate`）或 `deepseek/openai/cloud`（走 `/chat/completions` + Bearer key，key 依次从环境变量、`llm.env_file` / `$BOSS_HUNT_ENV_FILE`、`~/.boss-hunt/.env`、`~/.hermes/.env`（Hermes 兼容）读，不回显）。实测 DeepSeek 1.1–1.5 秒/条、3/3 过闸门、字数 140–163（本地 9b 是 4–15 秒、4/4 还是 165–190）。切完用 `ollama stop <model>` 卸模型降温。

**口语化 + 压短（2026-10-24 实测）：** 用户嫌 AI 味重后加的两条约束——骨架①改「这行我做了 {years} 年」（不写死「某某行业这行」，否则投售前/SaaS 岗会错位）；RULES 里加「多用短句、少用四字词与书面连词、允许『这行/干了/跑了』」。
- **`length.max` 别设到 170 以下**：qwen3.5:9b 基本落在 165–190 字，max=150 时通过率只有 1/4（大量 `too_long` 回退模板），max=190 时 4/4 通过。提示词里写「目标 120–140 字」对 9b 无效，别指望它自己数字数。
- **回退模板必须同步改成口语短版**：回退是常态（约 1/4），模板若还是「既擅长…也熟悉…沉淀」的长篇简历体，回退等于没改。实测 149 字口语模板既能兜底又不难看。
- **③ 呼应段必须限死事实组合与符号**：不限的话模型会把意向城市（targets 里的城市）说成经历城市（「我在 A 和 B 城市的经验里都跑过」），还会把 `+500%` 写成「订单量 500%」（闸门拦不住，语义残缺）。

**改完必须做回归**：`greet --job-name … --jd-file … --no-cache --times 6`，用过闸门率
≥5/6 当底线（低于这个数说明新约束有漏洞，回退模板会变成常态），再逐条人读像不像真人发的。
回退行会打印 `回退原因 <reasons>`，照原因改 prompt 或阈值；`too_long` 就先砍一条成绩、
`ai_style_hard` 就补进词库、`cn_numbers` 就加重数字照抄。症状→机制的完整对照见
`references/greeting-prompt-tuning.md`。

`think` 必须显式 `false`：qwen3.5 这类思考模型默认先吐一大段 thinking，
`num_predict=260` 会被 thinking 吃光，正文是空字符串。
报错 `llm_empty(length)` 基本就是这个原因，不要再去调超时。

## 真机发送三坑（2026-10-28 实测，决定「能不能发出去」）

单测全绿不等于能发：连着 6 轮测试全绿，线上一条都没发出去，因为测试替身照着错误选择器建。三条根因：

1. **输入框不是 textarea**：聊天页是 `div.chat-input[contenteditable="true"]`，整页没有 textarea。
   直接 `textContent = text` + 合成 `InputEvent` 只改 DOM，Vue 模型不动，发送按钮一直是 `disabled`。
   必须 `focus()` → 全选选区 → `document.execCommand('insertText', false, text)`，才会解锁按钮；
   写完还要轮询等 `disabled` 消失（0.15s×3s）再点，别立刻判失败。
2. **JS `button.click()` 是 untrusted**：页面会先乐观渲染气泡、随后被服务端丢弃——看起来发了其实没发。
   要给按钮打唯一标记再走 CDP 可信点击；JS 点击只能当兜底。
3. **确认气泡的字段**：我方气泡 class 是 `item-myself`（不是 item-mine/item-self），消息 id 在 `data-mid`，
   正文在 `.text-content`（`.message-content` 混着「送达」前缀）。字段错就永远记 `unknown`，
   于是 quota 不涨、下一轮还可能重发。

配套真机事实：点「立即沟通」是**整页跳转** `/web/geek/chat`（不是弹窗）；聊天页异步渲染（URL 先变、面板后出、
头部文字再后出，三样都要轮询等）；公司名只能从 `document.title` 取（`.company-name` 指向侧栏「同类公司」）；
岗位名带内部编号 `(J13893)`，比对前要剥掉；招呼语开关线上是 `<input type="hidden" value="0">`，没有
`ui-switch-checked`/`aria-checked`。

改完这几处必须真机小量验（`preflight` → `send 2`），只看测试会重复踩同一个坑。

## 过滤的坑

**`title_allow` 会把技术岗放进来 — 用 `title_hard_block` 兜底（2026-10-25 实测）：** 判定逻辑是「命中 block 且未命中 allow 才拒」，所以只要加了宽泛的 allow 词（解决方案 / 海外 / 出海 / 驻外 / 外派），「售前解决方案架构师」「具身智能解决方案架构师」「弱电安防工程师（派驻马来西亚）」「幕墙工程现场工程师（外派新加坡）」全部会被放行 —— 实测候选池里漏进 120 个工程技术岗，已误投了解决方案架构师和产品负责人。

修法：`filters.title_hard_block`（硬排除，**allow 命中也不放行**，judge() 里最先判），候选词：工程师/架构师/技术/实施/施工/弱电/安防/幕墙/整椅/调试/运维/算法/开发/测试/采购/研发/产品经理/产品负责人/产品总监/嵌入式/结构设计/高阶岗位。加完回放 raw（`candidates(cfg)` 不联网）确认技术类残留 0。

**硬排除只写中文会整类漏掉英文标题的技术岗：** 实测 `Senior Infra Engineer (外派)` 靠 allow 的「外派」放行并真投了出去。补词时同时加英文正则（`Engineer|Infra|Developer|Architect|Analyst|Scientist`），判据是 `scripts/audit_filters.py` 回放后技术类残留为 0，不是「候选数降了」。

同理：给 `keywords` 加「驻外 / 外派 / 派驻」会同时抓回大量**工程外派岗**（弱电、幕墙、整椅、变压器）—— 要投海外方向就同步把这些词补进 `filters.title_hard_block`，判据同样是回放 `raw/` 后技术类残留为 0。

**组合加分 `scoring.combo_bonus`：** 单关键词加分区分不出「某行业 × 出海」这种双对口岗。配置形如 `[{"a":[行业词1,行业词2,连锁],"b":[海外,出海,驻外],"score":5}]`，`score()` 里 a 和 b 同时命中才加分（匹配 blob = 标题+技能+行业+标签）。实测给这条 +5 后一个双对口的海外岗直接从中间升到第 3。

**用户说「不要全是某个方向」时，光调排序分不够 —— 要加每轮硬名额。** 池子本身偏向那个方向时
（关键词、组合加分、历史投递三件事互相强化），排序怎么调，头部还是那个方向。做配平要用三件套：
城市加权（`scoring.city_bonus`）+ 本地方向加分（`scoring.blob_bonus`）+ **每轮硬名额**
（`sending.overseas_regex` / `sending.overseas_max_per_round`，`do_send` 里按标题匹配计数，
满额后同方向岗位直接跳过、记 `skip_overseas_quota`，且**不写 contacts**，下一轮仍可投）。
只加名额不加权，名单会变成「凑数的低分岗」；只加权不加名额，一轮 10 个里仍可能 8 个同向。
改完用替身跑一轮 `do_send` 数配比（`B.send_one = lambda *a, **k: "sent"`），别靠肉眼看 `plan --top 20`。

## A/B 分级与投递顺序（Phase A）

新配置两段判级：列表按 `grading.roles` 筛职能，按行业/公司弱信号标 A?/B?，发送前读 JD，
按各 track 的 `jd_signals` 命中条数定最终等级。**无任何档位达标时按 `grading.no_signal`
（默认 C，不投）**；只有多档同分才用 `unknown_side` 兜底。C 和缺 JD 都不发送。
所有行业/公司/JD 词表来自配置。side/label 只解释，不含隐藏词表。

**B 类信号词必须「专属」，否则 B 就是垃圾桶。** 「库存」「陈列」「选址」「装修」在任何渠道经理
JD 里都写（旅游 OTA 写库存、化妆品写选址），拿它们当门店运营信号等于没筛；实测一批真实 JD 回放，
原判 B 的里面七成是这么进来的。判据只留「门店运营 / 加盟商 / 督导 / 店长 / 直营门店 / 巡店 /
门店业绩 / 门店管理 / 连锁门店 / 店员管理」。同理 A 类去掉「GMV」这类全行业销售岗都写的词，
并用 `tracks.*.min_hits` 抬终判门槛（A 要 2 个、B 要 1 个强信号）：`min_hits` **只在终判生效**，
粗标阶段仍按列表弱信号走，否则列表字段凑不出 2 个，候选直接清零。

判不出来的岗位宁可不投：`no_signal=C` 的代价是 B 池变小（一天可能只凑出 1–3 个 B），
换来的是不再把渠道经理当城市经理投。给用户汇报时要主动说这个代价，别只报「已修好」。
`scoring.min_score` 仍控制是否进入候选池，分数只排同级；旧配置不含 grading 时沿用 a_min。

`grade_quota` 是固定日配额；先 A 后 B，B 本跳额度满则入 b_pool，后跳先取池中 B，
不重复读 JD。grade_cache 分开保存 JD/规则指纹：仅规则变化时用缓存正文离线重判，
快照更新时间、bossOnline 等波动字段不会触发重读。`grading.jd_cache_ttl_days` 默认 7，
有效期内复用 JD，0 关闭复用；岗位关键字段变化或缓存过期后重新读取。
`sending.transient_retry_max` 的 max=N 表示首次读取后最多再试 N 次，max=0 即失败一次当天终态。
远端 JD 静默修改无法在免请求时发现。C 始终不发，新增 track 需配置同名额度才能发送。

**粗标阶段（纯函数、不联网）必须挡住 C，别让它去读 JD。** `do_send` 主循环用 `grade_job(job, cfg)`
先筛一层：职能不命中 `grading.roles` 的直接跳过，只有 A?/B? 才 `inspect_job` 打开详情页。
少这一层，每跳会把整池候选逐个 inspect（实测 652 次/跳，40 分钟预算当场打爆，风控特征也明显）。
配合 `max_inspect_per_window` 封顶，并且 **b_pool 里已判过 B 的岗位要豁免粗标 C 检查** —— 粗标只看
列表字段，拿它否掉已按 JD 定级的旧候选，池子会只进不出。

**配额要按比例压进单跳预算，不能让 A 先发吃光。** `quotas_for_now` 算出的是「本跳应发 A/B」，
而 `max_per_window` 限制的是「本跳总数」；A 先发时若 A 配额 ≥ 上限，B 一条都发不出去（实测 22:00
A8/B8 + 上限 8 → 发成 8A0B，跨行岗永远轮不上）。`do_send` 里用 `shrink_quotas` 按比例压缩，
同一场景变 4A4B。改配额逻辑后必须验证 `by_grade` 而不是只看 `sent` 总数。

**`send 1` 结构性发不出 B 档，别拿它判断发送链路坏了。** 预算 1 时 `shrink_quotas` 按最大余数把唯一名额
分给 A，B 配额变 0；B 档岗位在 `deliver` 里撞 `used[B] >= quotas[B]` 直接 return，**一行日志都不打印**。
所以出现「本轮发出 0」、跳过统计里也只有 `skip_same_hr`/`skip_grade`、当轮又没有 A 档时，瓶颈是配额不是链路。
验证发送链路用 `send 2`（压缩后 A1+B1）；要单发一条 B 才需要临时配置把预算放大，别改真配置。

`quotas_for_now` 按 ceil(剩余额度 / 含当前的剩余窗口数) 顺延，max_per_window 同时限制
每级与本跳总数；send N 只是总数上限，不覆盖日/窗口配额。carry_over=false 改用固定 per_window。
额度仅 sent 后写账，跨日只重置日计数，保留 sent/contacts/池和缓存。

**`state.checked_jobs`（2026-10-28 加）：** 记「当天已读过 JD 并定级过」的 `jobId → 指纹`，跨日清空。
`do_send` 每跳先跳过指纹相同的条目，所以不会再出现「每跳都把同一批头部候选重读一遍、后面的永远轮不到」；
`max_inspect_per_window` 只对**真正发起的 JD 请求**计数，缓存命中不占额度。
排查「一轮 0 发出」时先看静默跳过里的 `今日已检查 N` —— 这个数很大说明今天这个方向的池子确实读完了，
该扩容（加城市/关键词），而不是继续调额度或放宽分级。

**用户提「每轮 N 个 / 日上限 M」时，三个数要一起对齐。** 实际能发多少 =
min(`sending.max_per_window`，本跳 `grade_quota` 压缩后的 A+B，`daily_cap` 剩余)。用户要「每轮 10、
日上限 40」而配置是 `max_per_window=8`、`grade_quota A22/B8`、`daily_cap=30` 时，实测每跳只发 8、全天封顶 30 ——
他试完会以为额度没生效。改之前先把这三个值打印出来核一遍，改完用 `tick --dry-run --json` 看 `budget` 与 `quotas`（只读 state/ledger，不创建浏览器或锁），
别只改 `daily_cap` 就回报「已提到 40」。

单 cron 用 `tick`，不要外层再持 data/.lock；它内部用同一锁、成功窗口去重，失败不消耗未用额度。
scan_windows 扫描后投递，inbox_windows 投递后收件箱，其他窗口只投递，summary_window 出本地总结。
专用窗口均需在 windows 中，±10 分钟容差（不跨日）。桥或登录不可用即退出，40 分钟业务预算。
Unix 有 alarm，其他平台仅循环间检查。遗留锁不自动删除，避免抢占别的实例。

`grades --by-src` 严格只读，仅回放缓存 JD，**不校验 TTL / `jd_fp`**，可能使用过期或与当前岗位不符的正文。
`open` / 顶层 `open_unique` 保持快照口径，执行硬过滤、`min_score`、职能与终态过滤；
`open_with_bpool` 另补 tick 会恢复的 B 池岗位（包括不在快照中的岗位，并沿用池中职能豁免）。
这些都是当前状态下的候选数，仍受当天 `checked_jobs` 影响，不代表本跳必发数量。
多来源岗位每行各计一次，**行内 open 不去重**，顶层 `open_unique` 才是去重后的快照 open 数。
`no_jd` 单列 open 中缺正文的岗位；`sr` = `(final_A+final_B)/(final_A+final_B+final_C)`，无已判岗位时为 0。
零产出警告只在已判数量大于 0，且 `final_A+final_B+sent+b_pool` 为 0 时触发；
历史 `sent` / `b_pool` 只按快照或保存的 job 的 `_src` 归属；有 job 但缺来源时记 `unknown`，无 job 时无法归属，不猜关键词。
这些产出计数可重叠，仅用于判断是否有产出，不应相加当作去重岗位数。
无交集警告只读判级实际使用的 `grading.roles`；显式档位 roles 与其不同会另给警告。

`rejects` / `summary` 完全本地；明确拒绝与 72h 无回复从 inbox 采集。**消息方只认会话气泡的
`.item-mine`**：列表接口 `fromId` 不可靠，「文本像招呼语」和「距投递 10 分钟内」都不算证据 ——
后者会把 HR 收到招呼一分钟后的「请给几个面试时段」判成我方，然后在 `inbox` 里 `continue` 掉，
整段十分钟区间的回信永远不处理。判不出归属就记 `unknown` 进待核对：既不算已回复，也不入无回复库。
查不到会话不算无回复。待确认仅统计已同步、未处理的面试消息，可能陈旧。
summary.activity=false 时 cron 可不推送；有待确认仍展示。脚本不直接推送消息。

Phase B 的旧回复引擎 `scripts/reply.py` 已于 2026-11-06 移除（cleanup-02），回 HR 只走对话模块（`chat`）。
归属判定靠会话气泡的 `.item-mine`（列表接口的 `fromId` 实测不可靠，别用）这条仍然适用。
旧设计与已知坑见 `references/inbound-chat-design.md`（历史文档）。

**回消息闸门的判定顺序不能反，而且必须有一套离线测试集守着。** `self_contained` / `judge` 的顺序是：
① 整段先过「拒绝 / 人工模式」→ ② 必须有一个「邀约核心句」→ ③ 其余短句逐个检查是否安全。
顺序反了就会把拒信当邀约：「不太合适哈，后续有机会再沟通一下」「已经招到人了，可以看看机会」
整句里都有邀约词，自动回出去等于在拒信上追问「这个岗位负责哪块区域」。拒绝模式
（不太|不是很|暂时不|招到|已关闭|下次再|另有安排|祝好）要写在邀约词之前判；`base|常驻|办公地|驻点`
这类地点陈述也算人工。反向的坑是要求「每个短句都整句匹配」——「你好，看到你的简历，有兴趣聊聊吗」
这种带客套的真邀约会被判需人工而漏回，改成「一个邀约核心句 + 其余短句只许安全」。

判据始终是 **误自动必须 0**（发错话撤不回），漏自动只是少回一家。旧链路时期由 `reply_eval.py` +
`tests/fixtures/reply_cases.jsonl` 守（已随 cleanup-02 移除）；对话模块的同类红线由
`tests/test_conversation_adversarial.py` 等测试守。

**对话跳（`conversation.py` + `conv_rules.py`）必须先辨意图再开口，不能默认推进轮次。** 只按「轮次模板」
发问时，HR 问「有相关工作经验吗」、说条件（前置收费 / 置换 / 自备 / 垫资）、要简历，都会被当成「对上一轮的补充回答」，
一律回出 `round2`（「面试大概有几轮？可以安排线上面试吗？」）—— 三条里两条一字不差，等于答非所问。修法两条：
① `PATTERNS['manual']` 要覆盖经验/能力问句（`(?:有没有|是否有|有|做过|接触过|熟悉|了解).{0,8}(?:相关)?(?:工作)?(?:经验|经历|资源|客户|团队)[^。！\n]{0,12}[？?吗呢]`）
和风险条件词（`前置收费|置换|自备|垫资|押金|加盟费`）；② `decide()` 里加窄口径守卫：**已经问过问题
（`asked_round1_ts`/`asked_round2_ts` 非空）+ 本条 `classify()=='other'` + 带提问标记** → `finish('manual')`，
不拿模板硬推下一轮。
**守卫必须收窄到 `intent=='other'`**：写成「只要带 吗/呢/？ 就转人工」会把 `greet`（「方便聊聊吗？」）
和正常回答一起拦掉，实测一次红 28 个既有用例；而「底薪…，」这类宽条件词也会吃掉真实回答（「底薪加奖金，面试两轮，可以线上」）。
改这一层跑 `tests/test_chat_intent_20261101.py`（真实话术分类 + 人工类不得发 `round2`）与全量 unittest，
判据是两条都绿，不是「新用例绿了」而已。

自动回里任何「我整理一份发您」都是承诺：模板与 LLM 两条路径必须同一口径（都允许答应、
都禁出现「简历」「附件」字样），否则承诺落空没人去发（旧链路时期靠 `state.resume_followups` 提醒，已随 cleanup-02 移除）。
模板选择用 `zlib.crc32(job_id)`，不要用 `hash()` —— Python 每次进程哈希随机，同一岗位会轮流换话术。

**「谁在等谁」追踪（`scripts/conv_state.py` + `unanswered`）**：每个会话存
`last_side / last_ts / status`，四种状态 `waiting_me` / `waiting_them` / `stale` / `unknown`；
我方最后一条超 `inbox.stale_hours`（默认 72）对方没回 → 写 `rejects.json` 的 `stale_no_reply`
（reason「我已回复后72h无回应」），与「投递后对方从未回」的 `no_reply` 分开计数。
**用户手动回的也算已回**，判定只看气泡归属、不看脚本记录——所以人工回复不需要登记。
只要 `conv_state` 空着 `unanswered` 就全是 0，那不是坏了，是还没跑过扫描。
这一节只讲机制；**判据依据、配额比例怎么定、换行业 / 换用户该改哪几项**见
`references/grading-and-quota.md` —— 用户再次质疑分级、或要把技能交给别人配置时先读它。

## 常用命令

```bash
python3 scripts/boss_hunt.py init|validate|template|login-check|preflight
python3 scripts/boss_hunt.py regrade [--alt-config PATH] [--top 20] [--near-miss-threshold 5] [--pool-threshold 45] [--json]  # 只读离线回放，不写日志/状态
python3 scripts/boss_hunt.py greet --job-id <id> [--times 2] [--no-cache]   # 离线预览现写的招呼语
python3 scripts/boss_hunt.py scan  [--target 名称] [--keyword 词] [--pages 3]
python3 scripts/boss_hunt.py plan  [--top 20]          # 只报告，不发
python3 scripts/boss_hunt.py send  N                   # 真发
python3 scripts/boss_hunt.py inbox                     # 盯消息 + 分类（只读；回 HR 走 chat）
python3 scripts/boss_hunt.py unanswered [--hours 72] [--limit 20] [--json]  # 离线：谁在等谁（不开浏览器）
python3 scripts/boss_hunt.py run   [--send N] [--inbox]   # 手动一键，定时任务用 tick
python3 scripts/boss_hunt.py grade --jd-file jd.txt [--job-name 职位] [--brand 公司] [--industry 行业]
python3 scripts/boss_hunt.py grades [--top 20] [--json]
python3 scripts/boss_hunt.py tick [--dry-run] [--json]
python3 scripts/boss_hunt.py rejects [--days 30] [--json]
python3 scripts/boss_hunt.py ledger-repair [--dry-run|--apply]   # 隔离发送流水损坏尾部，不改前缀
python3 scripts/boss_hunt.py ledger-ack --list | <event_id> --sent --conv-key <key> --text-sha256 <hash>   # 人工核对 pending_verification；hash 从 --list 的 text_sha256 取，落成 --not-sent 同形
python3 scripts/boss_hunt.py summary [--json]
python3 scripts/dump_conversations.py [-o conv.json]   # 只读导出会话列表（谁回了什么），见 round-audit 第 6 节
```

**给 harness / 脚本调用时加 `--json`**：stdout 只有一个 JSON 对象（`schema` / `cmd` / `ok` +
命令专用字段），人类文本仍写 `runs.log`。字段表见 README「JSON 输出」，或直接
`python3 scripts/boss_hunt.py plan --json | jq .summary`。

**会话里不要整份读 `data/state.json`（几百 KB）或 `data/runs.log`（几百 KB）**：一次就把上下文吃掉，
而脚本自己 `json.load` 这些文件是**零 token** 的。要查数一律走 `--json` 摘要或单条查询。
成本模型、各文件增长上限与沉淀话术的治理见 `references/cost-and-context-budget.md`。

`ok=true` 只代表命令跑通了，**不代表招呼发出去了** —— 发送结果看 `items[].result`
（`sent` / `skip_already_chatted` / `unverified`）。别把 `ok` 当成功回报给用户。

配置文件查找顺序：`--config PATH` → `$BOSS_HUNT_CONFIG` → `~/.boss-hunt/config.json`。
`--config` 是**全局参数，必须写在子命令前面**（`boss_hunt.py --config X inbox`）；写成
`inbox --config X` 会报 `unrecognized arguments`。一次性只读巡检用 `BOSS_HUNT_CONFIG=<path>`
环境变量最省事，也更容易把 `paths.workdir` 指向 scratch。

## 配置要点

| 字段 | 作用 | 建议 |
|--|--|--|
| `user.years_experience` / `industry` / `highlights` | 招呼语素材 | 成绩必须带数字 |
| `user.greeting_template` | 招呼语，支持 `{years}{industry}{highlights}{job_name}{brand}` | 80–200 字，至少 2 个占位符；`mode=llm` 时只作回退 |
| `user.greeting.mode` | `llm`=按岗位 JD 现写 / `template`=渲染上面的模板 | 想每岗不同就 `llm` |
| `user.greeting.facts` | 现写时唯一可引用的事实清单（逐条、带数字、写清哪段经历） | 9–12 条最合适，太多会稀释重点 |
| `user.greeting.length` | `min`/`max` 字数闸门 | 100–200；>220 手机上看不完 |
| `user.greeting.min_numbers`/`max_numbers` | 阿拉伯数字个数区间 | 2 / 6；少于 2 像空话，多于 6 变 KPI 清单 |
| `user.greeting.style_forbid` | 追加要禁的 AI 味词（并进内置词库） | `["匹配度","欢迎"]` |
| `user.greeting.semicolon_max` | 分号上限 | 1 |
| `user.greeting.closings` | 覆盖结尾候选句（空=内置三句） | 可换成你自己习惯的说法 |
| `user.greeting.llm` | `base_url`/`model`/`think`/`temperature`/`num_predict`/`timeout_s`/`retries`/`max_jd_chars`/`cache` | `think` 必须 `false` |
| `user.interview_reply` | 面试类自动回复 | 要线上时段；对方只给线下的先争取线上，不答应到场 |
| `targets[]` | 城市 + 关键词 + 每日配额 | `city_code` 见 `docs/boss-site-notes.md` |
| `filters.salary_min_k` | 薪资下限（K） | 先比心理价低 2K |
| `filters.title_allow` / `title_block` | 头衔白/黑名单（正则） | 命中黑名单且不含白名单 → 拒 |
| `filters.industry_block` / `brand_block` / `hr_block` | 行业/公司/HR 排除 | 加离职公司、竞业对手 |
| `filters.exclude_headhunter` / `exclude_proxy` | 排掉猎头、代招 | 默认开 |
| `filters.experience` | `job_min_years_max` / `job_max_years_min` | 排掉够不到和太初级 |
| `filters.districts_allow` | 通勤圈 | 空 = 不限 |
| `sending.daily_cap` / `min_gap_seconds` | 日上限 / 间隔 | 20–30 / ≥60s；带多轮对话时 90s |
| `sending.max_inspect_per_window` | 单跳最多打开几个详情页读 JD | 默认 24；调小更省请求，但额度可能发不满 |
| `sending.hidden_b_probe` | A 额度满后，还允许读几个粗标 A? 的 JD（捞「自报互联网、实则门店」的隐藏 B） | 默认 6；行业字段不准就调大 |
| `sending.overseas_regex` / `overseas_max_per_round` | 某个方向每轮最多几个（按 `jobName` 正则计数） | 混投时用；被跳过的岗位不写 contacts，下轮可投 |
| `scoring.city_bonus` | 按 `cityName` 给本地岗加分，如 `{"示例城市": 2}` | 只影响同档排序；旧配置仍需明显小于 a_min |
| `scoring.blob_bonus` | 单边加分：命中任一 term 就加（`[{terms:[...],score:3}]`） | 门店类、城市经理类各一条 |
| `sending.verify_text` | 发送校验文本 | 默认取招呼语开头 8 字；`mode=llm` 时忽略，改用实际话术开头 |
| `grading.*` | roles、tracks 词表、unknown_side、require_role_for_ab | 用户确认 A/B 标准后填写，不把公司行业当 JD |
| `sending.grade_quota` / `carry_over` / `max_per_window` | 固定日配额、顺延、窗口上限 | 示例 A22/B8；max_per_window 同时限制本跳总发送量 |
| `schedule.timezone` / `windows` / `per_window` | 何时跑、固定模式每跳总数 | carry_over=false 才用 per_window；0 表示不发 |
| `schedule.scan_windows` / `inbox_windows` / `summary_window` / `scan_pages` | tick 动作分派 | 专用窗口需在 windows；扫描默认 2 页 |

改完**筛选规则**（`filters` / `scoring` / `sending`）重跑 `validate` + `plan` 即可，不用改代码。

改 `targets[].keywords` 是另一回事：`plan` 只读 `raw/` 快照、**不联网抓新词**——加完关键词必须跑一次
`scan` 才有新岗位，直接 `plan` 会看到旧候选，容易误判成「加了没效果」。

## 扩方向 / 加关键词（实测顺序）

用户说「再加几个方向，不替换原来的」时，只改配置、不动代码：

1. `cp ~/.boss-hunt/config.json config.json.bak-$(date +%Y%m%d-%H%M%S)`。
2. 往 `targets[].keywords` **追加**新词，旧词一个别删（「不替换」就是真的追加）。
3. 宽词（`AI商业化`、`外企`、`出海`）会连带抓出技术/产品/运营岗：同一轮把噪声头衔补进
   `filters.title_block`（`产品经理`、`产品线`、`工程师`、`算法`、`开发`、`架构师`、`策划`、`合伙人`），
   否则候选从 251 涨到 435，一半不是要投的岗。
4. `validate` → `template`（核对渲染后字数与校验标记）→ **`scan`**（真抓）→ `plan --top 20` 看头部。
5. 汇报给用户的是「加之前 / 加之后的候选数、可投数、新方向分布」，不是「我改了配置」。

扫描成本：每个「城市 × 关键词」串行抓 3 页，约 1 分钟/组；10 词 × 2 城市 ≈ 25–35 分钟。
定时 wrapper 的 `caffeinate -t 900` 会先到期——关键词一多就分批 `scan --keyword`，或把 wrapper 时长放长，
别让定时任务在扫描中途被砍。

**池子投光时先加城市/关键词，别先放宽去重 —— 但「同 HR 能挡多少」必须现算，别照搬旧数字。** 同 HR 去重
（`brand|bossName`）能挡掉的量随池子结构变化很大：早期样本里 905 个岗位只有 7 个 HR 挂多岗（放宽只多出个位数），
而同一个账号投了 169 个之后，1881 个岗位里「同 HR 已投、但本岗未投且 `judge` 通过」的有 **248 个**，
其中 6 个是平台侧优质岗（多为头部平台的 BD / 区域经理 / 城市经理类）—— 这时该动的是去重规则，不是加词。
三条现算命令（都在 `open_pool` 之外单独跑，不联网）：被挡且 `judge==pass` 的岗位数、其中平台侧品牌数、
这些 `contacts` 平均挂几个岗。岗位数大就改成「同 HR ＋ **不同岗位** ＋ 距上次投递 > N 天（建议 7）」放行，
别一刀切永久跳过 —— 同一个 HR 手上常有好几个岗，投另一个岗不算骚扰。

**用户问「我是不是更适合投某类头衔」时，用池内三列数据回答，不要给泛泛的匹配建议。** 对每个候选头衔算：
标题命中数、`judge==pass` 数、被「同 HR 已投」挡掉的数；再看职级是否与现职对得上（例如现职是经理级时，
「销售主管」这类低一档的标题即使命中也很少，且多为低底薪+提成，不值得进搜词）。示例：城市经理 117 命中/19 过筛、
区域经理 130/44、销售经理 96/33、销售主管仅 5/2 —— 结论只能是「城市经理＋区域经理是主线，销售主管不进搜词」，
而不是「城市经理更契合」这种没有数字的判断。

**调配额之前先算「可投池」，别直接调高每轮数量。** 可投 = `candidates()` 里既不在
`state.sent`、也不在 `state.contacts`（`brand|bossName`）的岗位数。候选 400 而可投只有 150 时，
把每轮从 7 提到 10 只是多跑几条「跳过(同 HR 已投)」，额度根本发不出去；反过来，可投充足还按
小额度跑就是在浪费已扫到的池子。向用户汇报配额时给「候选 N / 可投 M」两个数，不是只给候选数。

**`send N` 报「本轮发出 0」并且带「本跳 JD 读取已达上限」，先怀疑噪声吃光了 inspect 额度，
而不是发送链路坏了、也不是分级太严。** 粗标只看列表字段，行业填「互联网」的地推/返现岗
（「推广抖音买单、单台返现 2000+、欢迎小白」这类）照样进 A?，前 24 个详情页可能全是它们。
取证做法：把「未投且不同 HR、粗标非 C」的候选批量读 JD 复核一遍（36 个约 2–3 分钟），
输出「真 A / 真 B / 踩红线」三列；实测一次 36 个里 33 个终判 C（含 3 个踩 `text_block`）、
只剩 3 个 B、0 个 A —— 这时是池子干了，正确动作是加城市/关键词，**不要**调高
`max_inspect_per_window` 或放宽 `min_hits` 去凑数。复核脚本骨架：`grade_job(job, cfg)` 粗标筛 →
`inspect_job(br, tab, job, cfg)` 取 JD → `grade_job(job, cfg, jd)` 终判 + `any_match(text_block, jd)`。
真正发货前可用临时配置把 `max_inspect_per_window` 临时放大，走完噪声再落 B 档，但那份配置只用于验证。

**临时改一个开关、又要在真实 state 上跑时，用独立配置文件并显式指定 `paths.workdir`。**
`load_config` 的 workdir 兜底是 `dirname(config)/data`，复制到 `/tmp` 而不设 workdir 就会写到
一份空状态上，判级、额度、去重全是假的，看起来像「改了没效果」。做法：`json.load` 真配置 →
`cfg["paths"]["workdir"]="~/.boss-hunt/data"` 展开成绝对路径 → 改要试的字段 →
`python3 scripts/boss_hunt.py --config /tmp/cfg.json send 1`（`--config` 必须在子命令前）。
跑完把结果当验证结论，**不要**顺手把这临时配置当新配置；要改真配置先问用户。

`filters.text_block` 里的 JD 红线词（如 `外呼`、`扫街`）会连带挡掉相当一部分线下拓展类岗位，
因为那类 JD 本来就写这些词。加之前先把这个代价告诉用户、让他选保留还是只针对新方向；
用户说保留就不要再自作主张撤。`陌拜` 这类词算不算红线由用户决定；用户说不算，就别再自作主张加回去。

**补跑一轮发出 0 时的三步判定**（别先怀疑锁/浏览器）：
① `candidates − sent − contacts` 算「真可投」；② 拿 `state.grade_cache` 里本轮的 `reason` 做分布；
③ 看是否有 A?/B? 岗位根本没出现在 `runs.log`（不是 skip_grade 而是没被迭代到，才是代码问题）。
实测一轮 133 个真可投里 34 个被读 JD 判 C：29 条是 `no_signal=C`（无门店/区域强信号）、
5 条踩红线 —— 其中「合伙人」「创业」两个词会连带挡掉对口的区域/渠道经理岗（JD 里常写「城市合伙人」「创业团队」），
这两个词是否保留要让用户拍板，不要自己放宽 `min_hits` 去凑数。

**「池子枯了还是规则太紧」用 `regrade` 定性，不要凭感觉放宽阈值 —— 先排除第三种可能：池子被某条硬规则锁死。**
所以 `regrade` 得出 `pool_dry` 时，汇报前必须再算这三个数：可投（`open_role_matched`）、
被同 HR 挡掉且 judge 通过数、`checked_jobs` 当天已判数；三个加起来才解释得了「为什么 0 发出」。
打开同 HR 窗口后同一份数据变成 `open_role_matched` 35→226、`fresh_pool` 1→192，
`verdict` 从 `pool_dry` 变 `inconclusive` —— 只看 `pool_dry` 就去加城市加词，会把一条规则的代价误读成池子的天花板。 `regrade` 只读（不写 state/日志、
不开浏览器），输出 `matrix`（stored→current、current→alt）、`jd`（等级 + `cause` 分布）、
`diagnosis.verdict`（`pool_dry` / `rule_tight` / `both`）。判断规则那一半要自己造放宽配置：
`--alt-config` 各跑一遍，**每次只动一个维度**（`tracks.*.min_hits` / `jd_signals` / `filters.text_block`）。
一次把三样全放宽，结论会变成 `both`，看着「阈值紧、红线也紧」，实测拆开后 `min_hits` 2→1 是 **0 条变化**、
加同义词 +7 条、去掉两条红线仅 +1 条 —— 真正的杠杆是 `jd_signals` 覆盖不到 JD 的写法，`no_signal` 占绝大多数。
所以：**先扩同义词，再谈别的**（不降门槛、不动红线、无风控代价）；放宽 `min_hits` 在同池上几乎不产出，
因为 `min_hits` 只在终判生效，粗标阶段进不来的岗位根本轮不到它。
但扩词前必须先逐条读那批 `no_signal` 的标题与 JD、确认是「词表漏了」而不是「赛道不对」
（实测 34 条里大多数是家具/医疗/外贸/酒店的渠道销售）；只用高区分度业务词，并同步把该 track
的 `min_hits` 抬到 2，否则同义词会把不对口的岗放进来。做法与数字见
`references/grading-and-quota.md`「池子枯 / 规则卡死」。

**看诊断输出先确认不是工具自己的 bug。** `grades --by-src` 的行按 `final_A+final_B` 降序，
全 0 时退化成按 src 字母序（中文 src 排在 ASCII 之后）——前几十行可能全是 0，看着像命令坏了。
先对合计：各行 `open` 之和要等于 `regrade` 的 `open_role_matched`，或直接过滤 `open>0` 的行。
`validate` 的「零产出关键词」有两类已知误报：岗位没有缓存 JD（永远判不出等级）、该词扫回的 A/B
已经发出去（不在 open 里）。**据它删关键词之前先把这两类排掉**，否则删掉的是已经用完的好词。
`C` 里的 `no_signal` 与 `redline` 要分开报：前者是词表/方向问题，后者是红线词与目标行业天然冲突。

## 报告与回复规则

- **他要做选择时，把选项编号列出；他只回一个数字（如「1」「2」）时，就是选上一条里那个编号。**
  直接按编号执行并在回复里写明「按你说的 1 处理：…」，不要用 clarify 或反问拆一轮 —— 他常常只回一个字，
  追问会拖来一个超时的空回合，而且他回来时已经过了几个小时，那条待决事项就悬着了。
  上一条没给编号时，按最近一次列出的顺序理解，把假设写出来；同样别停下等确认。
- **一条信息一行，组之间空一行，绝不把多个事实串成一句。** 他明确说过「所有东西堆成一长句，完全让人无法理解」：
  「进程活着、Chrome 起过、名单抓完 2 条、正在转写第 1/2 条」这种复合成句必须拆成四行，每组之间留空行，
  重点行用 emoji 起头。数字与他给的路径/地址可以贴，但一行只放一个事实；列表超过 5 条只报前几条 + 其余数量。
  这条同样管多主题汇报（如「BOSS 投递 + BOSS 对话 + 回消息跳」出现在同一条消息里）：按主题分块，
  每块自带标题行，块间空行；不要把三个组件的结果并进同一段。
- **推送给用户的任何文本（IM / 手机通知等推送渠道）里都不能出现命令、路径、job id、任务编号、代码块。**
  用户明确说过「像这种完全没法看，你要用自然语言直接描述」—— 他收到的定时失败通知里带着
  命令、输出目录和随机任务号，整条就读不下去。所以：① 脚本播报的每一行都要是人话
  （核对发送就写「先确认那个 HR 有没有收到消息」，不要写「执行 ledger-ack --list」）；
  ② 系统通知里带代码的部分，转述成人话再发，不原样转发；
  ③ 「第几跳」「今日 6/18」这类业务数字可以留，路径和随机 id 不行。
- 一行摘要（本轮发了谁 + 今日累计 N/M + 异常），细节每条一行；列表最多 5 条，其余只报数量。
- **投递跳/回消息跳按用户要求「每跳回一行」**（2026-10-28）：`boss-hunt-tick.sh` 窗口内固定输出
  `📮 HH:MM 跳｜发出 N｜今日 X/30`，0 发出时第二行跟 `同HR已投 / 判级不过 / 无沟通按钮` 前 3 项；
  回消息跳固定输出 `💬 回消息跳｜约面试 N｜感兴趣 N｜待你回 N` 一行，约面试最多列 3 条原话
  （wrapper 与 fmt 在仓库 `scripts/cron/boss-hunt-inbox{.sh,-fmt.py}`，部署处的 wrapper
  只需 `exec` 它，和对话跳一样；旧的「已回/失败」已随旧回话链路去掉）。
  窗口外的补跑仍静默 —— 否则他看到的又是「任务在跑但一条没发」却没有任何播报，会当成坏了。
- （历史，旧自动回话已移除）**预演期（`inbox.auto_reply.dry=true`）播报要多两行**：`🧪 预演 品牌：本来要发的文案`（最多 3 条）
  和 `📎 待发资料 N 条：HR 名`。用户要靠这两行判断「文案像不像人话、答没答对题」，只给
  「已回 0」等于什么都不说。渲染在 `boss-hunt-inbox-fmt.py` 里按 `auto_replied[].source` 含 `:dry` 取行。
- **`cronjob_manage action=resume` 之后 `next_run_at` 常是过去时刻**：调度器会补跑一次，而 `tick`
  判「不在窗口」直接退出 → 输出 `silent (empty output)`。这不是没恢复，别据此又 pause 一遍；
  核对方式是 `runs.log` 末尾有那一段「不在窗口，跳过」+ 下一个整点在窗口内。
- 分类：`不合适/已招满` → 跳过；`面试/约时间` → **只承诺线上形式**，时间段一律请对方给 2–3 个可选、
  回来问用户确认后再答（他给不出可约时间表，替他把时段定死就会约到他有事的时间）；
  对方只给线下/电话的先争取线上；`感兴趣/要简历` → 只报告，不擅自答应任何事。
- 每天最后一跳的失败复盘按 `references/round-audit.md` 第 8 节 + `references/grading-and-quota.md`：
  失败样本 = **明确拒绝 + 投递满 72h 无回复**（两类分开计数、都进分母），先分归属（只有
  `our_apply` 算投放精准度），原因分类决定该动哪个配置；报当天新增 + 累计 + 原因分布，无新增不推。
- 系统消息（「附件简历已发送」「对方已同意」）不是 HR 说话，不要当招呼汇报。
- 报 `inbox` 结果必须分清「读到了」和「回了」：`inbox` 只读不发（2026-11-06 cleanup-02 起），回 HR 只走对话模块
  `chat`（:30 跳：问两轮、发简历、面试固定回复、回绝）。汇报时把「已自动回 N」「需你人工回 M（列品牌）」和「为什么不自动回」
  写在一起 —— 只报「近 72 小时新消息 155 条」，他会以为脚本坏了。
- **「收到好多招呼」和「我们发了多少」是两件事**：`inbox` 里的「新招呼 N 条」是 HR 主动发来的，
  我们投出去的量只看 `state.sent` 里当天的记录。用户问「上一轮是不是发了好多招呼」时，先把这两个数
  分开说，别拿收到的招呼数当投递量报给他。
- 用户问「上一轮发了多少 / 出了什么问题」时，按 `references/round-audit.md` 取证后再答：
  以 `state.sent` 的时间戳为准，别用「`runs.log` 里有没有那一段」判断发没发。
- 用户问「HR 回复说岗位不匹配，是不是投递方向错了」时：先用**当前规则重判 `state.sent` 的历史岗位**
  （只读本地、不清未读，见 `references/round-audit.md` 第 7 节），把「规则收紧前的存量误投」和
  「真的方向不对」分开；再看会话正文（`scripts/dump_conversations.py -o conv.json`，按第 6–7 节核归属），
  用「分方向回复率」回答——不要拿几条拒信倒推方向（多数「不匹配」是 HR
  主动招呼后自己否掉的，不是我们的投递）。报的时候要说清两件事：打开消息页会清未读；
  查不到会话的那批投递单列「无法判断」，不算作无回复。
- **暂停投递时，把对话跳（`chat`，:30）一起停。** 对话模块是独立的、也会真发消息的路径（问两轮 / 发简历 / 回绝），
  只停投递跳，脚本仍会替用户回人；回消息跳（`inbox`，:10）cleanup-02 起只读，可不停。两条一起 `cronjob_manage action=pause`，恢复时一起 `resume`，
  并用 `action=list` 核 `state`、`next_run_at`；暂停原因写进 `paused_reason`，免得下次忘了为什么停着。
  代码审查发现 P0 需要停投时，先停再修，别边修边发。
- 用户只发一句「为什么」时，指的多半是他刚收到的播报：先看 `runs.log` 末尾和最近一次输出，
  不要从代码倒着猜。
- **用户要求「结果推到手机 / 接着在 IM 里聊」时**：结论和证据整理成一条，经你的推送渠道发出
  （长文本走文件参数，别走命令行参数，避免被转义吃掉字符）；手机端按短句分行 + 空行分组 + emoji 起重点行，
  长表格、代码和日志留在桌面端。桌面端只留一句「已推送」，别把那条再复述一遍。
- **发送成功 ≠ 用户收到了。** 推送渠道的会话未就绪时可能静默失败、调用方照常返回成功，
  用户什么都没收到。投递类任务报数之前核一眼定时器/推送侧的投递错误记录
  （如 Hermes 的 `cronjob_manage action=list` 里 `last_delivery_error`），按渠道说明恢复会话。

## 聊天页的真实结构（2026-10-28 真机实测，改发送逻辑前必读）

- 点「立即沟通」是**整页跳转**到 `/web/geek/chat`，不是弹窗；跳转后 `document.title` 变成 `BOSS直聘`，
  岗位页的标题/公司名证据随之丢失 —— 身份必须在跳转前取到，或改用会话头部文本。
- 聊天页**输入框是 `div.chat-input[contenteditable="true"]`，整页没有任何 `textarea`**；
  发送按钮 `.btn-v2.btn-sure-v2.btn-send`，空输入时带 `disabled` class。
- 异步渲染三步：URL 先变 → 面板 `.chat-conversation` 后出现 → 头部文字（公司/岗位）再后出现。
  每一步都要轮询等，把「还没画出来」当成「身份不明」会白拦；面板 0 个记 `panel_not_ready`，
  有但不可见记 `panel_not_visible`，多于 1 个才是 `panel_not_unique`。
- 岗位名常带内部编号（`区域零售高级经理（华东大区）(J13893)`），页面不显示，比对前必须剥掉。
- 写 `contenteditable` 只赋 `textContent` + `InputEvent` 会**写进文本但不解锁按钮**（页面框架不认），
  表现是 `send_aborted:button_disabled`。正确做法：`el.focus()` + 全选选区 +
  `document.execCommand('insertText', false, text)`，并轮询等按钮去掉 `disabled`（≤3s）再点。
- 点击必须用 **CDP 可信点击**（`br.click`）：JS `button.click()` 是 untrusted，页面会先乐观渲染气泡、
  随后被服务端丢弃（消息列表预览不变）——看起来"发出去了"其实没发。守卫轮给按钮打 `#bh-send-btn` 再点。
- 我方气泡是 `div.message-item.item-myself`（不是 item-mine），消息 id 在 `data-mid`，
  正文在 `.text-content`（`.message-content` 混着「送达/已读」）；三处按真机写，否则发成功也只会记 `unknown`。
- `br.json()` 走 `parse_json_payload`，**裸字符串会被丢掉**；取 `location.pathname` 这类标量必须
  `JSON.stringify({path: location.pathname})` 再解析，否则永远判「没跳过去」。
发送原语集中在 `scripts/chat_send.py`（`type_into_chat` / `click_send_scoped` /
`confirm_new_bubble` / `chat_snapshot` / `chat_identity_evidence`）；首条招呼、自动回复、人工回复共用它。

**点击结果只能有三种，判过一次就别在各处再手写一遍：** `click_send_scoped` / `press_enter_scoped`
的返回值带 `attempted` 标记，统一走 `chat_send.click_outcome(clicked)`：

| 判定 | 含义 | 唯一允许的处理 |
|--|--|--|
| `aborted` | 点击**之前**就失败（`reason` 在 `PRECLICK_FAILURES` 白名单：identity_* / owner_unknown / stale_draft / button_disabled / panel_* / input_mismatch / fill_mismatch） | 记 `send_aborted:<reason>` → ledger `failed`，可再投 |
| `pending` | 可能已经点了，但没拿到回执（`attempted=True` 且 `ok!=True`、非 dict、白名单外的原因） | **只读**确认最多 3 次（每 2s，绝不重新点击/输入），出现新气泡才算 `sent`，否则 `pending_verification` |
| `clicked` | 回执成功 | 仍要核 `conv_key`，不一致照样 `pending_verification` |

为什么不能把「不确定」当 `aborted`：`aborted` 写 ledger `failed`，而 `ledger_guard` 只拦
`pending/unknown` —— 一次错判就是给同一个 HR 重发。新加失败原因时要么进白名单，要么落到 `pending`。
ledger 行的 `meta.via`（`trusted_click` / `enter` / `js_fallback` / `confirm_only`）记录这条是靠什么
确认的：`confirm_only` 占比高说明可信点击常丢回执，`js_fallback`/`enter` 出现说明可信点击不可用，
这两条路径的气泡可能是乐观渲染，真机首次发送后先看这个字段。

**一条 `pending` 是全系统停机，不是只停那一个岗位**：`run_tick` / `do_send` / `inbox` 都会在
`ledger_guard` 处抛出 `unresolved_send`，投递和面试回复一起停，直到用户核对真实会话后跑
`ledger-ack --list` 再 `--sent` / `--not-sent`。播报里看到 `⚠️ … unresolved_send` 就往这个方向查，
不要去重试发送。
**`conv_resume` 也同样算账**：点「发附件简历」后拿不到「附件简历已发送」气泡就记 `pending`，
`ledger-ack --list` 里是 `cmd=conv_resume` / `text="<resume>"` 那条，此时自动回话整个停摆。
**核对别靠翻会话列表**：`新招呼` / `全部` 标签只渲染一屏几十行、还会随会话状态漂移，pending 那条常常已经不在列表里，
滚动搜文本也搜不到。直接让用户在 BOSS 上打开这个 HR 的会话看一眼有没有「附件简历已发送」，拿到答复再 `--sent` / `--not-sent`。

## 同 HR 冷却窗口（2026-10-28）

`filters.same_hr_window_days` 仅控制候选过滤：缺省或 `null` 保持同一 `品牌|HR` 永久跳过；
`0` 关闭同 HR 冷却（仍拦住已投 jobId）；正数 N 表示距最近一次真实发送至少 N × 86400 秒才放行。
权威时间只取 `send_ledger.jsonl` 中 `status == "sent"` 的行，优先 `ts_epoch`，解析失败回退 `ts`；
不从缺少 HR 的 `state.sent` 猜时间。**`ts_epoch` 存的是毫秒**（`int(now*1000)`）：比较前必须 `/1000`，
并兼容历史秒级值（`>1e11` 视为毫秒）。把毫秒当秒会算出几万年后，`now-last` 恒为负，凡 ledger 有记录的 HR
反而被永久跳过；而测试夹具若写成秒，几百个测试全绿也照样测不出这条 —— **夹具必须和真实写入同单位**。
该 HR 无有效 sent 时间（`last is None`）时，**窗口 >0 要保守跳过**（跳过原因记 `skip_same_hr_unknown_ts`），
只有 `0` 才放行 ——「不知道上次何时联系过」不等于「很久没联系」。
ledger 缺失或不可读时，正数窗口保守保持旧行为。坏行跳过，解析结果仅在命令/单次 tick 内存缓存。
**开窗口前先算时间覆盖率**：实测 600 个 `contacts` 只有 55 个能算出上次联系时间（ledger 仅 28 行带 boss，
`state.sent` 又没有 boss 字段），按保守策略窗口打开只多放出 1 个候选 —— 改了等于没改。
先看 `len(contact_last_sent(st,cfg)) / len(st["contacts"])`，低于一半就先做历史回填或干脆不开。
同一跳里发完一条后必须**同步更新缓存**并维护本次已发 HR 集合，否则同一个 HR 会在几分钟内收到两个岗位的招呼。
`candidates()`、`open_pool()` 与**发货阶段 `do_send.allowed()`** 必须都调同一个
`same_hr_filter(st, cfg, job)`，放行项的候选 `flags` 标记 `same_hr_window_pass`，
窗口内仍为 `skip_same_hr`，岗位已投、judge 拒绝等其他条件仍生效。
**这三处任何一处漏改，都会复现「候选放行、发送拦截、本轮 0 发出」** —— 加过滤规则时
`grep -n "in st.get(\"contacts\")" scripts/*.py` 确认没有绕过 `same_hr_filter` 的裸判断；
codex 施工若写明「发送路径不动」，这一处要自己补并补一条「放行岗位能真的发出去」的断言。
判级路径不受窗口影响（同 HR 不是判级问题）。

风险：同一 HR 多岗时可能出现连续两次招呼；窗口只参照上次真实发送，不为同批候选预占时间。
回滚删除该配置项（或设 `null`），无需改 ledger 或 contacts。
`tick --dry-run --json` 的 `budget` 改为对象：`limit` 保留原数字预算，
`same_hr_window_days` 为配置值（未配置为 `null`）；实际 `tick --json` 的 `budget` 同样携带窗口值。

## 风控 / 排障 / 二次开发

- 额度与触发点：`docs/risk-control.md`（含来源与日期）
- 页面结构、字段、城市码、桥返回格式：`docs/boss-site-notes.md`
- 故障：`docs/troubleshooting.md`
- 没有 huashu-chrome：`docs/agent-driven-mode.md`
- **`本轮发出 0` 的排查顺序：登录态 → 验证码误判 → 按钮时序 → 筛选规则**，别倒着查。
  · 先跑 `login-check` 看真实登录态。未登录时点「立即沟通」只会弹登录框，消息不会真发出，
    若发送结果无法确认，程序保留待核对流水并阻止重发，须先核对真实会话。
  · 再查验证码误判：详情页底部藏着登录弹窗，正文里常带「发送验证码」，用整页 `innerText` 匹配
    「验证码」会把正常岗位全判成 `skip_captcha`（实测连续 4/4 误判）。判据只认 `document.title`
    里的「验证码中间页 / 安全验证」和 `[class*=captcha]` 容器，不看整页文本。
  · 最后才核按钮时序：`send_one` nav 后轮询 8×1.2s，等到 `.btn-startchat` / `.btn-continue-chat` /
    验证码 / 需跳 App 任一信号再判断。
- 发送点击回执缺失、点击后未取得确认气泡时，流水保留 `pending_verification`（`pending/unknown`），
  `ledger_guard` 会阻止重发。**不等于没发出去，不可直接重跑 `send`**；先由用户核对真实会话。
  明确未点击的原子校验失败返回 `send_aborted:<reason>`；身份、输入文本、面板或按钮不满足要求时不点击。
- `skip_ledger_corrupt` 表示发送流水损坏。先运行 `ledger-repair --dry-run` 查看损坏字节和待核对事件，
  确认后才用 `ledger-repair --apply` 隔离。完整前缀保留，损坏尾部写入 `send_ledger.corrupt-<ts>-<id>`。
  隔离不代表核对完成；未知发送与隔离标记仍阻止后续发送，不自动恢复定时任务。
- 看见「另一个实例在跑(pid unknown)：遗留目录锁 …/.lock」，不是真有实例在跑，是 `.lock` 被建成了一个**目录**（
  典型来自 wrapper 里的 `mkdir data/.lock`）：`open(lock,'a+')` 抛 IsADirectoryError。查 `ls -ld data/.lock`，
  是目录就 `rm -rf` 掉并改 wrapper 的锁名；同名的 wrapper 锁会让每一跳窗口内 tick 全灭，窗口外却正常，极易误判成「没到点」。
- `skip_no_button` 跟 `skip_already*` 走同一分支，会把 `brand|bossName` 追加进 `contacts` **永久拉黑**：
  某次页面没渲染出按钮的岗位此后一直报「同 HR 已投」。看到「没投过却报同 HR 已投」，先查这条并从
  `state.contacts` 里删掉对应条目，而不是去放宽其他过滤（同 reference 第 7 条）。
- 读 `runs.log` 别用 `tail -20`：一次 send 的段落几百行，结尾永远只有「跳过(同 HR 已投)」和
  「本轮发出 0」，真正的失败模式（`unverified` / `skip_captcha` / `skip_no_button`）在中间。
  按 `【` 切段再统计：`re.split(r'\n(?=【)', txt)[-1]` + `collections.Counter(re.findall(r'跳过\(([^)]+)\)', p))`。
- **`runs.log` 里整轮分段缺失 ≠ 什么都没发**：日志是收尾一次性落盘的，进程被杀（典型是被定时任务
  的超时上限干掉）时整段丢失，而 `state.sent` 早就逐条写过了。核「发了几个」一律以 `state.sent`
  的时间戳为准，日志只用来解释失败模式。取证与只读巡检的完整步骤见 `references/round-audit.md`。
- 改完 `filters` 用 `scripts/audit_filters.py` 回放历史 raw（不联网、不发消息）复核残留，
  技术/产品岗残留必须为 0；它比 `plan` 的输出更方便做集合差。
- `title_block` 的语义是「命中黑名单 **且** 没命中白名单才拒」。白名单只放岗位性质词
  （销售经理 / 售前 / 解决方案 / 客户成功 / 大客户），**不要放 `外企`、`AE`、`Global` 这类标签词**：
  带「外企」的技术岗会靠它豁免混进候选，`AE` 还会子串命中 CAE/AERO。
- llm 招呼语相关的排障顺序：`preflight` 看 `greet_llm` → `greet --job-id` 单岗复现 →
  看 `runs.log` 里的 `招呼语回退:<reason>`。常见原因：Ollama 没起（`llm_unreachable`）、
  `think` 没关（`llm_empty(length)`）、`numbers_not_in_facts`（facts 漏了这条成绩）、
  `no_job_or_brand`（岗位名是小象超市/摆摊这类怪名，模型不肯点名——这种岗本来也不该投）。
- 读 JD 的选择器是 `.job-sec-text` / `.job-detail-section` 依次回退，**只在点「立即沟通」之前读**：
  弹窗开着再读不到 JD，还会白等模型几秒。实测正常岗位能读到 290–900 字；读到 0 字说明
  详情页可能没渲染；启用 grading 时保守跳过，不以模板兜底绕过分级。

## 交给别人之前

> 要把它真发到 GitHub 开源（README/LICENSE/仓库结构/清污/建仓推送），按 `github` 技能的
> 「发布本地项目/技能为公开开源仓库」参考文档走；本技能的 `README.md` + `LICENSE` + `.gitignore`
> 已经按那个流程备好，推送前把 README 里的 `<this-repo>` 换成真实地址。

1. 发布包排除运行数据（`state.json`、`raw/`、`runs.log`、`rejects.json`），绝不删改用户的数据目录。
2. 确认 `config.json` 里没有真实姓名、手机号、现公司敏感信息；
   招呼语里的数字若是真实业绩，交付前问用户是否要保留。
3. 技能目录里不放个人模板、不放 cookie。
4. 交付时附一句：这是自动化辅助，平台规则可能变，账号风险自负；节奏按文档别调高。

## 只跑一套（迁移时怎么搬）

同一个 BOSS 账号只允许**一套实例**在跑：两套并行会互相抢额度，同一家公司/HR 也会收到两条招呼，
风控特征更明显。换版本或换机器时按下面的顺序搬：

1. **配置搬事实**：用户资料、城市与城市码、关键词、模板、每日上限 —— 手工搬进新 `config.json`，
   别整体复制，旧版字段名可能不同。
2. **状态必须交接受保护字段**：`sent`、`contacts`、`replied`、`inbox_seen` 要一起搬，
   否则会给同一批 HR 重发招呼、同一批面试消息重复处理；`day` / `day_count` 也搬，
   否则当天额度凭空重置，会多投一轮。
3. **不要搬的**：`raw/` 只作历史快照（可以留着看，不参与筛选）、旧日志格式、旧代码目录。
4. 搬完按顺序验：`validate` → `template`（核对渲染出的招呼语）→ `plan`（候选数是否合理）
   → 跑**真实入口**（wrapper/定时脚本，而不是直接 `python3 脚本 run`）一次。
5. **配置里的窗口列表和 cron 表达式必须同一轮改完。** `tick` 只认 `schedule.windows`；老 cron 的
   11/14/16/21 点在新配置里不存在时，它判「不在窗口，跳过」然后**静默停投** —— 脚本正常退出、
   日志无报错，看着像还没到点。改完立刻 `tick --dry-run` 逐窗口核对 actions，再用
   `cronjob_manage action=list` 确认 `next_run_at` 落在新窗口上，最后等第一跳查 `state.sent` 有没有新记录。
6. 确认新的入口跑通后，才停用旧实例（停 cron、归档旧目录），别两个都留着。

## 改安全相关代码的做法（发送 / 点击 / 闸门）

动发送、点击、闸门逻辑之前，先读最近一次审查结论里列出的失效路径——它们都是实测复现过的。
推荐固定分工：**一个 Agent 只读分析给方案 → 另一个实现 → 再只读审 diff → 你自己跑测试与真机验收**，
不要让同一个进程自查自修。审查报告、施工单这类过程文档放在仓库外，不提交。

1. 先暂停投递跳**和**对话跳（两条都会真发消息；回消息跳只读，可不停）。
2. 把缺陷清单 + 证据写成分析单（每条带 `file:line`、替身复现结论、要拍板的问题），只读产出方案：
   按「根因 / 改法（到函数与字段）/ 验收 / 风险与取舍」写，并明确「重发=账号风险、漏发=少投一家」这个不对称代价。
3. 把方案切成批次实现，要求为每条补「能复现原缺陷的测试」并贴出 unittest 真实输出。
4. 每批改完只读审 diff，问法固定在「能不能上机 / 有没有必须改」。**审查说能上机之前不恢复定时任务。**
   收尾复审（整批改动的最后一轮）用四问，比泛泛 review 有效得多：① 有没有引入
   「重发 / 多发 / 说错话」的路径（要 file:line + 触发条件）② 规则之间有没有互相矛盾
   （同一口径散在多处、闸门与发送层守卫冲突）③ 恢复定时前必须补的检查项 ④ 一句话能不能恢复。
   它只读代码没跑测试时会在结论里自陈，这类判断（尤其正则能不能拦）必须自己用真实字符串复跑一遍再采信。
5. 我自己复核：全新子进程跑全量 unittest、逐条抽样验证新语义（如白名单矩阵）、替身跑一跳、
   真实 wrapper 跑一次，最后看真机那一跳的输出与 ledger。每批单独 commit，便于回滚。
   **子代理自报的「全绿」不算验收**：它跑的是自己写的用例，会把盲区一起测绿。必须自己再补一批
   对抗性输入（混合句、拒绝句、带条件句）打同一个闸门 —— 实测它交付「25/25 全绿」，我临时补
   「聊聊？base 在杭州」当场抓到一条误放行。新加的用例要进 `tests/fixtures/` 长期守着。
   **codex 会自己加施工单里没写的规则，并且带着红测试交付。** 实测它报「已完成」时全量是 8 红、
   补了一遍更像的改动后变成 28 红（新加的「带问号就转人工」守卫把所有 greet/回答都拦了）。
   处置顺序固定：先 `git checkout -- <改动的文件>` 回到绿基线 → 自己按最小口子重做 → 再跑全量。
   **不要在它的半成品上继续叠补丁**，红用例会越修越多；也不要只看它贴的测试输出（那是它自己挑的用例）。
   它自加的规则要能从 `git diff` 逐条看出来，改完对着 diff 问一句「这条是我要的吗」，不是就直接删。

长任务用 `notify` 后台跑 + `pgrep -f` 轮询；**不要因为一两分钟没输出就杀掉重跑** ——
`claude -p` 的 text 模式全程缓冲、stream-json 也要等生成才有 delta，3–8 分钟出结果很正常。
后台启动 agent CLI 时带上 `< /dev/null` 关闭 stdin，否则它会等 3 秒（stdin 是常开的管道时可能更久）。
四条最费时间的坑：

- **同级判定被复制到多处时，修法必须是「收敛成唯一入口 + 测试断言旧写法已消失」**，不是逐处打补丁。
  归属解析曾散在读会话、状态扫描、历史构建、原子点击四个位置，修好一处，其余三处的「默认对方」
  仍在误判。验收写成 `grep` 不到的断言（旧模式字符串必须搜不到），比读十遍 diff 可靠；
  grep 要带 `--include=*.py`，否则会命中 `scripts/__pycache__/*.pyc` 的二进制匹配，把已清干净的
  代码误报成残留。
- **从 codex 日志里提报告要锚定结论句再切片，并核对字节数**：`log.rfind("审查结论")` 之后才是正文；
  用 `rfind("\n## ")` 会倒抓到 AGENTS.md/SKILL.md 的内容，静默写出几百 KB 的假报告。正常报告 3–15 KB，
  提取后先打印首 200 字确认，不要直接 `git add`。
- **codex 进程被外部终止时不要重跑整张施工单**：写一份续跑说明（放仓库外，别提交）只描述
  「当前哪几条测试红、哪条要求未落地、顺手产生的垃圾目录要清」，让它接着修。重跑施工单会把已改好的
  部分再改一遍，且新起的进程不知道上一轮的取舍。
- **不要 pipe codex 输出到 `tail`/`head`**（会吃掉退出时的缓冲），也不要让子代理替跑长任务：
  用 `notify` 后台跑 + `pgrep -f "codex exec"` 轮询 + `tail -c 2000` 看进度；子代理有执行上限，
  被砍时它的 codex 子进程一并终止，工作区留下半成品。
复审会一直产出新的边界问题（修完 9 个 P0 又出 3 个，再修完又出 2 个），所以**P0 清零 + 测试全绿就转小量实测**，
不要用「再审一轮」当作可以无限延后的验收。用户说「修完」时，就把当轮报告里的 P0+P1（外加便宜的 P2）一次修完，
别承诺再开一轮循环。

**测试全绿不等于页面点得中。** 加固后新增的点击/弹窗选择器（招呼弹窗、`.send-message`、`data-job-id` 一类）
是照旧页面结构写的，真实 DOM 未核验，线上可能成批返回 `send_aborted:identity_unknown`。
**最先要真机核的不是发送按钮，是「这一页是谁」**：公司名只能从 `document.title` 的
`_<公司>招聘-BOSS直聘` 段取，`.company-name` 读的是侧栏「同类公司」（真机实测会读到别家名字，
导致每个岗位都 `unknown_conversation`、一条也发不出去）。做法与取证脚本见
`references/login-and-verification-pitfalls.md` 第 11 条。
恢复定时任务前先手动跑一跳（**用 `send 2`，`send 1` 的结构性发不出 B**），确认结果是 `sent` 且有确认气泡，
再交回 cron；失败就把选择器核出来，别靠重试撞。
