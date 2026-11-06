# Spec v2 · 分级 / 配额 / 顺延 / 拒绝复盘（Phase A）

> 给实现者（Codex / Claude Code / 任何 agent）：这是一份完整的实施规格。
> 目标仓库即本目录，MIT 开源项目 <https://github.com/LemonShark0128/boss-zhipin-jobhunt>。
> 先读 `SKILL.md`、`AGENTS.md`、`README.md`、`scripts/boss_hunt.py`、`assets/config.example.json`，
> 理解现有架构、命名习惯和「硬边界」，再动手。
>
> **Spec 与现有代码冲突时，以 Spec 为准**；Spec 没写的地方保持现有风格。
> **本阶段不涉及对话引擎**（HR 主动找我后的多轮问答属于 Phase B，不在本次范围）。

---

## 0. 一句话

把「按分数排序投递」升级为「**先按职能粗筛、读到 JD 再定 A/B/C、按固定比例配额投递、跳过的额度自动顺延**」，
并新增「**拒绝与无回复的采集和日报**」。所有词表必须可配置，换行业/换用户只改配置不改代码。

---

## 1. 硬约束（违反即失败）

1. **不真发消息**。任何自测都用替身（stub）或 dry-run，`B.send_one` 不许对真实浏览器调用。
2. **不写 `~/.boss-hunt/`**。自测一律在 `$TMPDIR` 建临时 config + workdir。
3. **不删改 `~/.boss-hunt/data/`**（那是真实投递记录）。
4. **不 git commit / push**。人类验收后统一提交。
5. **不引入新依赖**（只用 Python 标准库 + 现有 `chrome_mcp.py`）。
6. **向后兼容**：老 `config.json`（没有 `grading` / `grade_quota` 段）必须照旧能跑，
   缺字段时用代码内默认值，且 `validate` 不许因此报 ❌。
7. **不焊死任何个人/地域/行业数据到代码**：所有词表进 config；`assets/config.example.json`
   必须是通用示例（不能用真实城市、品牌、行业方向这类个人配置）。

---

## 2. 需求

### R1 · 三级（A/B/C）判定

#### R1.1 为什么必须两段判定

`scan` 抓的列表接口**没有 JD 正文**，`brandIndustry` 是公司自报（不可靠），`skills` 常为空。
因此：

- **列表阶段**只能判「职能是否对口」→ 标 `A?` / `B?` / `C`（带问号的粗标）
- **发送阶段**（`send_one` 本来就要打开详情页并 `read_jd`）用 JD 正文定最终等级 → `A` / `B` / `C`

#### R1.2 新增配置段 `grading`

```json
"grading": {
  "enabled": true,
  "roles": ["城市经理", "区域经理", "城市负责人", "区域负责人", "BDM", "BD经理",
            "商务拓展", "商户拓展", "市场拓展", "渠道经理", "KA", "大客户", "客户经理",
            "销售经理", "业务经理", "拓展经理", "国家经理"],
  "tracks": {
    "A": {
      "label": "同职能·平台侧",
      "side": "platform",
      "industries": ["互联网", "生活服务", "O2O", "电子商务", "企业服务", "软件", "计算机"],
      "brands": [],
      "jd_signals": ["商户拓展", "商家运营", "新签商户", "开城", "城市代理", "GMV",
                     "BD团队", "地推", "平台政策", "KA商家", "商户数", "订单量"]
    },
    "B": {
      "label": "同职能·品牌侧/跨行",
      "side": "merchant",
      "industries": ["餐饮", "食品", "饮料", "服装", "家居", "酒店", "体育",
                     "批发", "零售贸易", "进出口"],
      "brands": [],
      "jd_signals": ["门店运营", "加盟商", "督导", "店长", "选址", "装修",
                     "陈列", "库存", "直营门店", "店员"]
    }
  },
  "unknown_side": "B",
  "require_role_for_ab": true
}
```

要求：

- `roles` / `industries` / `brands` / `jd_signals` 全部是**配置驱动**，代码里不得出现行业专有词
  （唯一例外：`(?i)Engineer|Architect|Developer` 这类**通用技术词**可留在 `filters.title_hard_block` 默认值里）
- 用户可通过 config 增加第三、第四个 track（如 C 类「职能不对口」），代码按 `tracks` 遍历，不写死只有 A/B

#### R1.3 新函数

```python
def grade_job(job: dict, cfg: dict, jd_text: str = "") -> dict:
    """返回 {"grade": "A|B|C|A?|B?|C?", "reason": str, "scores": {...}, "final": bool}
    - jd_text 为空 → 粗标（带问号），依据 roles + industries/brands
    - jd_text 非空 → 终判（不带问号），依据 jd_signals 计分；两边分数相同用 unknown_side
    - 职能不命中 roles → C（require_role_for_ab=true 时）
    """
```

- 判级结果要能解释（`reason` 里写清命中哪条规则），报错时人看得懂
- **必须纯函数**：不读文件、不联网、不依赖 state，方便单测
- `candidates()` 里给每个候选加 `grade` / `grade_final` 字段；排序规则：`A > B > A? > B? > C`，同级按 score 降序

#### R1.4 新命令 `grade`（供其他用户调参，也是验收手段）

```bash
python3 scripts/boss_hunt.py grade --jd-file jd.txt --job-name '城市经理' --brand 某某 --industry 互联网
python3 scripts/boss_hunt.py grade --jd-file jd.txt --json
```

输出：等级 + 命中的规则 + 各信号计分。**不联网、不开浏览器。**

#### R1.5 新命令 `grades`（看粗标分布）

```bash
python3 scripts/boss_hunt.py grades [--top 20] [--json]
```

基于 `data/raw` 快照统计：`A?`/`B?`/`C` 各多少、已投多少。**不联网。**

---

### R2 · 配额与顺延

#### R2.1 配置

```json
"sending": {
  "daily_cap": 30,
  "grade_quota": {"A": 22, "B": 8},
  "carry_over": true,
  "max_per_window": 8,
  "min_gap_seconds": 60
},
"schedule": {
  "timezone": "Asia/Shanghai",
  "windows": ["08:00", "10:00", "12:00", "14:00", "16:00", "18:00", "20:00", "22:00"],
  "scan_windows": ["08:00", "14:00", "20:00"],
  "inbox_windows": ["10:00", "16:00", "22:00"],
  "summary_window": "22:00",
  "per_window": 0
}
```

#### R2.2 行为

- `do_send(cfg, br, tab, st, want, out)` 扩展为按 grade 配额发：
  - 先 A 后 B，各自不超过 `grade_quota` 与当日已用量
  - `want` 参数若显式传入（`send N`），作为**总数上限**，仍受 grade 配额约束
- **顺延**：`quota_for_now(cfg, st, now)` 返回本跳 (a_quota, b_quota)

  ```
  remain_a = grade_quota.A - st.day_by_grade.A
  remain_b = grade_quota.B - st.day_by_grade.B
  n_win    = 当前时间之后（含当前）还有几个 windows（至少 1）
  a_quota  = min(max_per_window, ceil(remain_a / n_win))
  b_quota  = min(max_per_window, ceil(remain_b / n_win))
  ```

  `carry_over=false` 时改用固定 `per_window`，保持老行为
- `state` 新增：`day_by_grade: {"A": n, "B": n}`、`b_pool: {jobId: {title, brand, reason, ts}}`
  - 白天读到 `grade=B` 的岗位若本跳 B 额度已满 → 写入 `b_pool`，不发送
  - 后续跳次优先从 `b_pool` 取 B（已判过级，**不重复读 JD**）
- JD 判定结果缓存进 `state.grade_cache: {jobId: {"grade": "A", "jd_hash": "..."}}`，
  `jd_hash` 变化则重判（避免 22 点重复打开详情页）
- **额度记账只在真发出时**（保持现状：`day_count` / `day_by_target` / `contacts` / `sent`）

---

### R3 · 排班入口 `tick`

新增子命令，供 cron 单个任务调用：

```bash
python3 scripts/boss_hunt.py tick [--json] [--dry-run]
```

行为（按 `schedule.timezone` 的当前时间判断）：

| 条件 | 动作 |
|--|--|
| 当前 HH:MM 命中了但不在任何 window（±10 分钟容差） | 打印「不在窗口，跳过」，exit 0 |
| 命中 `scan_windows` | `scan(pages=cfg.schedule.scan_pages 或 2)` + 投递 |
| 命中 `inbox_windows` | `inbox()`（拒绝采集在 R4） |
| 命中 `summary_window` | 出总结（R5） |
| 其它 window | 只投递（不 scan、不 inbox） |

要求：

- 一个 tick 的**总耗时目标 < 45 分钟**；`--dry-run` 只打印计划动作不执行
- 复用现有目录锁（`data/.lock`）与 `close_own_tabs()`，绝不碰用户自己的标签页
- 失败（未登录 / 网络断）要**快速退出**，不消耗额度，让下一次 tick 自然顺延

---

### R4 · 拒绝与无回复采集

#### R4.1 数据文件 `data/rejects.json`

```json
{
  "job_123": {
    "brand": "某某公司", "title": "城市经理", "grade": "A",
    "applied_ts": "2026-10-27 09:12",
    "detected_ts": "2026-10-27 15:12",
    "kind": "explicit | no_reply",
    "raw": "不好意思，需要有连锁门店经验",
    "attribution": "our_apply | hr_initiated | unknown",
    "reason": "无理由婉拒 | 经验不符 | 行业不符 | 学历 | 薪资 | 已招满 | 无回复"
  }
}
```

#### R4.2 采集规则

- **明确拒绝词**（可配置 `inbox.patterns.reject`，保持现有字段兼容）：
  `不合适|不匹配|不符合|已招满|招满了|暂不考虑|不太适合|有更合适的人选|已招到`
- 含糊说法（`再看看|有消息联系你|保持联系`）**不记录**
- `attribution`：
  - `job_id ∈ state.sent` → `our_apply`
  - 会话说存在但不在 `state.sent` → `hr_initiated`
  - 都查不到 → `unknown`
- **无回复**：`state.sent` 中以 `applied_ts` 起算满 72 小时，且会话最后一条仍是**我方**发出的
  → 记 `kind=no_reply`、`reason=无回复`。判定方法沿用 `references/round-audit.md` 第 6 节
  （**不要用 `fromId` 判归属**，用招呼语文本/时间比对）
- 幂等：同一 `job_id` 只记一次；`kind` 从 `no_reply` 升级为 `explicit` 时覆盖并保留原 `detected_ts`

#### R4.3 新命令 `rejects`

```bash
python3 scripts/boss_hunt.py rejects [--days 30] [--json]
```

输出：总数、按 `kind` / `attribution` / `reason` / `grade` / 方向 的分组计数。
**纯本地读文件，不联网。**

---

### R5 · 当天总结

`tick` 命中 `summary_window` 时，或手动 `boss_hunt.py summary [--json]`：

```
📊 今天小结
投递 29/30（A 22 · B 7）
被「不合适」4 个｜72h 无回复 3 个｜累计 39
原因：无理由婉拒 2、经验不符 1、已招满 1
待你确认 2 条
```

要求：

- 只用本地数据（`state.json` + `rejects.json` + `runs.log` 可选）
- `--json` 输出结构化字段（`sent` / `by_grade` / `rejects` / `no_reply` / `reason_breakdown` / `pending`）
- **不发微信**（脚本保持解耦；由 cron 侧拿 `--json` 结果交给推送渠道）
- 当天无投递也无拒绝 → 输出「今天没有活动」，仍然可被 cron 判空不推送

---

### R6 · 文档与示例（这是开源交付的核心）

必须同步：

1. `assets/config.example.json`
   - 新增 `grading` / `sending.grade_quota` / `schedule.*_windows`
   - **通用示例**：不出现个人城市、不出现个人求职方向的行业专有词、不出现具体品牌黑名单
   - 用「示例行业」加注释说明「换行业改这里」
2. `docs/onboarding-interview.md`
   - 新增一节「确认 A/B 标准」，必问：
     - 你的目标职能词（roles）
     - 你在平台侧还是品牌侧（决定 side 定义）
     - 跨行岗位要不要投、占多少比例（grade_quota）
     - 行业白名单 / 公司黑名单 / JD 红线词
     - 每天几个时间点、每跳几个、断网要不要顺延
   - 给出「同一句话在不同行业含义不同」的例子（如「城市经理」在平台 vs 品牌）
3. `README.md`
   - 新增「换行业怎么改」小节：只改 `grading` + `filters` + `targets`，不改代码
   - 新增「A/B 分级是怎么判的」一小段（两段判定：列表粗筛 → JD 定级）
4. `SKILL.md`
   - 命令表补 `grade` / `grades` / `tick` / `rejects` / `summary`
   - 验收证据表补新命令的成功标志
   - 「配置要点」表补 `grading.*` / `sending.grade_quota` / `schedule.*`
5. `CHANGELOG.md` 记一条 `feat: A/B/C 分级 + 固定配额 + 额度顺延 + 拒绝复盘`
6. `AGENTS.md` 的命令映射表补新命令（Hermes / Claude Code / Codex 通用）

---

## 3. 验收清单（必须全部跑通，把输出贴出来）

在临时目录建 config + workdir，**不要碰真实数据**：

```bash
S=$(mktemp -d); mkdir -p $S/data
cp assets/config.example.json $S/config.json
# 把 paths.workdir 指到 $S/data，targets 随便填一个城市
```

| # | 命令 | 通过标准 |
|--|--|--|
| 1 | `python3 -m py_compile scripts/*.py` | 无报错 |
| 2 | `boss_hunt.py --config $S/config.json validate` | 无 ❌ |
| 3 | `boss_hunt.py --config $S/config.json grade --jd-file <平台侧JD>` | 判 `A`，reason 说明命中「商户拓展/开城」等 |
| 4 | 同上换 `<品牌侧JD>`（含「门店运营/加盟商/督导」） | 判 `B` |
| 5 | `boss_hunt.py --config $S/config.json grades --top 20`（把 raw 复制进 $S/data/raw） | 输出 A?/B?/C 计数，不联网 |
| 6 | 替身跑 `do_send`：`B.send_one = lambda *a,**k: "sent"`、`B.time.sleep = lambda *a,**k: None`，workdir 指向 $S | 返回 `items` 里 A ≤22、B ≤8；`state.json` 只在 $S 里被写 |
| 7 | 顺延：手工把 `day_by_grade.A=18` 写进 $S state，连跑两次 `quota_for_now`（模拟 12:00 / 14:00） | 12:00 拿到的 A 配额 > 4（剩余额度被摊到更少跳次） |
| 8 | `boss_hunt.py rejects --days 30`（用构造的 rejects.json） | 分组计数正确 |
| 9 | `boss_hunt.py summary`（用构造 state） | 数字与构造一致 |
| 10 | 老配置兼容：删掉 `grading` 段跑 `validate` + `grades` | 不报错，用默认值 |
| 11 | `tick --dry-run`（改 `schedule.timezone` 造出不同时刻） | 正确打印「这跳做 scan/send/inbox/summary」 |

**额外要求**：以上每条都要在最终输出里贴出实际命令 + 关键输出行，便于人类复核。

---

## 4. 交付输出格式

完成后用一段结构化文字回报：

1. **改动文件清单**（新增/修改，逐条一句话）
2. **每个需求（R1–R6）的实现位置**：`scripts/boss_hunt.py:1234` 这种精确到行
3. **验收清单 11 项的实际输出**（关键行即可）
4. **遗留风险 / 未完成项**：诚实列出，不要粉饰
5. **给人类的复核建议**：哪几处最值得先看

不要写「已完成所有需求」这种一句话结论，人类要按清单逐项核对。
