# 用 codex 给这套代码做多轮加固（操作手册）

适用：发送/点击/闸门这类「改错了会真发错消息」的代码。目标不是审出完美代码，
而是**把 P0 清零、测试全绿、再转小量实测**，别把「再审一轮」当成可以无限延后的验收。

## 一轮的固定顺序

1. **先停发送**：`cronjob_manage action=pause` 停投递跳与回消息跳（两条都停，自动回复也会真发），
   `paused_reason` 写清是代码审查。
2. **写施工单** `docs/codex-fix-prompt-round<N>.md`，每条固定三段：
   - **现状**：贴出错的代码行/行为，说明为什么错（有的写 `file:line`）
   - **要求**：改完的语义边界，穷举「不满足就停」的分支
   - **验收测试**：写成可断言的形态——返回值、点击次数、状态字面量、`grep` 不到的旧模式
   末尾统一写「先写能复现原缺陷的测试（先红后绿）、不得删测试或放宽断言、全绿后停止不要提交」。
3. **跑**（前台会超时，一律 `notify` 后台）：
   ```bash
   cd <skill_dir> && rm -f /tmp/codex-r<N>.log && \
   codex exec --sandbox workspace-write "读 docs/codex-fix-prompt-round<N>.md 并按要求修复，每条先写能复现原缺陷的测试，修到 python3 -m unittest discover -s tests 全绿；不要跑复审、不要提交" > /tmp/codex-r<N>.log 2>&1
   ```
   轮询 `pgrep -f "codex exec"`，看进度用 `tail -c 2000`，绝不 pipe 到 `tail`/`head`。
   单轮 20–50 分钟；超过 ~75 分钟考虑终止并转断点续跑。
4. **自己验，不采信自评**：`git diff --stat` 只看允许的目录 → 自己跑全量 unittest →
   `validate` → 新命令的 `--json`/`--help` 都能跑 → 抽查两条关键语义（用测试或内存替身复现）。
5. **清垃圾再提交**：`git status --short` 看未跟踪项，codex 会顺手建 `docs/superpowers/` 之类目录，删掉。
   分开提交：修复一个 commit、复审报告一个 commit。不 `git push`。
6. **复审**（可选，见下）：`codex exec --sandbox read-only "读 docs/codex-review-prompt.md 并按要求执行代码审查"`。

## 三方分工（用户指定）：Claude 出方案 → Codex 实现 → Claude 审 → 我验收

- Claude 只读（`--allowedTools "Read,Grep,Glob,Bash(git diff:*)"`），产出方案或复审，**永远不改文件**；
  Codex 用 `--sandbox workspace-write` 写代码。**同一时刻只让一个 agent 写 `scripts/boss_hunt.py`**，
  两个并发写同一文件必冲突，且后来的那个不知道前一个的取舍。
- 每个 agent 的边界在 prompt 里写死：不联网、不开浏览器、不读 `~/.boss-hunt/`（真实数据）、
  不动 `~/.hermes/scripts/`（运行入口在仓库外）、不 commit（提交由我做，一批一个 commit 便于回滚）。
- 施工单开头写**基线 commit**，并注明「行号会漂移，按函数名/子命令名定位」；Codex 照 `file:line`
  找会改到别处。审 diff 时让它明确回答「能不能上机 / 有没有必须改」，结论存 `docs/claude-review-*.md`。
- 复审提的「建议改」不一定全做：按「会不会导致多发 / 会不会导致假报」筛，其余记进下一批，
  否则每轮都收不完（实测两轮各冒出 4–6 条低风险建议，做完还要再审一轮）。

## 上下文过长时：写交接包，别硬撑

一轮多批次 + 多 agent 之后，主会话上下文会到顶。用户的说法是「打包开启新会话」。
做法：在仓库 `docs/HANDOFF-<日期>-FINAL.md` 写一份**自包含**的交接包，新会话第一句
「读 docs/HANDOFF-…-FINAL.md，从下一步继续」就能接上。必须包含：

1. 30 秒上手命令（cd 仓库 / 跑全量测试 / 跑只读 `regrade`）
2. 当前状态：哪些 cron 停了、今日发出量、测试数、关键 commit 链
3. 已定性的结论（带数字，别只写「池子干了」）
4. 下一步的**有序**清单（含哪一步是下一步的前置）
5. 流水线命令（claude / codex 的完整一行，含 `< /dev/null`）与边界
6. 铁律与坑（重发>漏发、锁名、代理环境变量…）
7. 恢复定时任务的前置条件（测试绿 + 复审通过 + dry-run + 真机一跳）

写交接包时**别用 write_file 覆盖上一份**（内容已被别的会话改过会被拒），新建一个带 `-FINAL` 或时间的
文件名，并在头部指向上一份。交接包与施工单各自单独 commit。

## Claude 侧的实际命令与取结果

```bash
HTTP_PROXY=http://127.0.0.1:8890 HTTPS_PROXY=http://127.0.0.1:8890 NO_PROXY=localhost,127.0.0.1,::1 \
  claude -p "<prompt>" --allowedTools "Read,Grep,Glob,Bash(git diff:*),Bash(git show:*)" \
  --max-turns 40 --no-session-persistence --output-format stream-json --verbose \
  < /dev/null > /tmp/claude-x.jsonl 2>&1
```

- **`< /dev/null` 是必需的**：少了它后台进程会卡在等 stdin，看着像「跑不动」。
- CLI 读环境变量代理，不读 macOS 系统代理；不导出 `HTTP_PROXY/HTTPS_PROXY` 时它连不上 API。
- 输出是 jsonl，正文不在最后一行：扫 `type == "result"` 那条的 `result` 字段。

```python
import json
txt = ""
for line in open("/tmp/claude-x.jsonl", encoding="utf-8", errors="replace"):
    try: ev = json.loads(line)
    except Exception: continue
    if ev.get("type") == "result": txt = ev.get("result") or ""
print(len(txt)); print(txt[:300])          # 先看长度与开头，再落盘
```

- 它比 codex 快（3–7 分钟），但仍先输出思考再给正文；不要用「日志没长」判死，用 `pgrep -f` 看进程。
- 只读审查不要给 `Edit`/`Write`/`Bash(git commit:*)`；prompt 里再写一遍「不改文件、不 commit、不联网、
  不读 `~/.boss-hunt`」——工具白名单与文字约束是两道独立的闸。

## 提取复审报告

日志 400–500 KB，报告只在最后。锚定结论句，别锚定 Markdown 标题：

```python
log = open("/tmp/codex-review.log", encoding="utf-8", errors="replace").read()
i = log.rfind("审查结论")
body = log[i:].strip()          # 3–15 KB 属正常
print(len(body), body[:200])    # 先看首 200 字再落盘
```

`rfind("\n## ")` 会倒抓到读过的 AGENTS.md / SKILL.md 正文，静默写出几百 KB 假报告
（一次写成 387 KB 才发现）。落盘后核对字节数正常再加 header、再 `git add`。

## 进程被终止时的断点续跑

codex 被杀后工作区留着半成品：部分文件已改、新测试文件在、全量测试红。
**不要重跑整张施工单**——它会重做已改好的部分并丢掉上一轮的取舍。写 `docs/codex-resume-<N>.md`：

- 当前 `python3 -m unittest discover -s tests` 的失败数（连失败断言原文一起贴）
- 哪几条要求尚未落地（按施工单条目点名）
- 要清的垃圾目录
- 约束复述一遍（不驱动浏览器、不读真实数据、不 push、不提交）

然后 `codex exec --sandbox workspace-write "读 docs/codex-resume-<N>.md 并按顺序继续，把测试修到全绿"`。
实测一轮断点续跑 15 分钟左右收尾，比重新生成施工单快一倍。

## 修什么形状的问题

- **同一个判定逻辑散落多处**（归属解析、发送确认、额度记账）：要求收敛成唯一入口，
  并加「旧写法搜索不到」的断言。逐处打补丁的结果是修好一处、别处继续错。
- **「未能识别」被当成通过/关闭**：凡是布尔判定，都要有第三态（`unknown`）且 `unknown` 一律停手。
  把「属性缺失」映射成 `false` 是最常见的一类误发。
- **两种检查分两次调用**（先校验身份、再点全局按钮）：要求在一次页面脚本内完成校验+点击。
  分两次调用中间页面会重渲染或切换会话，消息就发给了别人。
- **冲突的两条记录类型**（`no_reply` vs `stale_no_reply`）：统一结构再分别计数，别只加新类型不改统计。
- **施工单里写「不动某路径」时，那条路径里该改的那一处也会被跳过。** agent 会严格守边界，
  并在报告「偏差 / 未做」里主动点出来（典型：「发送阶段原有守卫仍可能拦住候选」）；
  这一句就是它发现但不敢改的真缺陷 —— 每轮必读那一段，逐条判断要不要自己补。
  实测加同 HR 窗口时，候选与 `open_pool` 都放行了，发货阶段的 `allowed()` 仍按旧条件拦，
  不补就是「候选 226 个、实发 0」。补完自己跑全量测试，并在 commit message 里写明这处是自己补的。

## 什么时候停

复审每轮都会产出新 P0（实测：9 → 3 → 2 → 1）。判据不是「审出 0」，而是：

- P0 清零、全量测试绿、`validate` 通过 → 停，转小量实测；
- 剩下的 P1/P2 属于设计取舍（主文件过大、模块未拆）→ 记录不修，写进复审报告的结论段；
- 用户说「修完」= 当轮报告里的 P0+P1（外加便宜的 P2）一次做完，不承诺再开新循环。

**测试全绿不等于页面点得中。** 加固期新写的选择器（招呼弹窗、发送按钮、`data-job-id` 一类）
照的是旧页面结构，真实 DOM 未核验。恢复定时任务前先手动跑一跳只发 1 个，
确认 `sent` 且有确认气泡，再交回 cron。
