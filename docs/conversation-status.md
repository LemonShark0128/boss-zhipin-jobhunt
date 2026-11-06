# 会话状态与第三轮安全修复

## 离线查看

```bash
python3 scripts/boss_hunt.py unanswered
python3 scripts/boss_hunt.py unanswered --hours 72 --limit 20 --json
```

命令只读配置和 state.json，不创建浏览器、不写日志、不发送。默认每类列出 5 条；counts 是完整计数，items 是限额后的明细。默认阈值 72 小时，`--hours` 仅改变本次离线报告口径，不修改状态或 rejects。

## 状态更新

`inbox` 的只读状态扫描写入 `state.conv_state[conv_key]`。（旧自动回话链路已于 cleanup-02 移除，`inbox` 只读。）默认 `inbox.status_scan_max=20`，`inbox.stale_hours=72`。只检查当前可定位的已渲染会话，不把虚拟滚动列表视为全量历史。

顺序为待我回、等对方、其余；前两类按最久未检查优先，其余按最后消息时间倒序。稳定键、头部身份和气泡正文均必须通过验证；归属来自 `.message-item.item-mine`，不使用 fromId 推断。消息时间优先取 DOM 的明确时间戳，缺失时允许使用精确会话身份匹配的快照 `msgTime/lastTS`；两者都缺失时保守保留旧状态，不用当前时间冒充消息时间。正文缺失、身份不匹配或归属未知均不会触发发送。

每个会话连续失败 3 次后跳过，JSON 和人类摘要均报告跳过数量。达到 deadline 时保留已完成状态、返回部分结果，tick 不将此窗口标为完成。扫描本身不生成话术、不发送；原有发送路径仍单独受身份、承诺、额度和流水保护。

状态机时间只向前；unknown 检查仅增加 checks；stale_since 只在本轮等待首次进入 stale 时设置。用户在浏览器手动发出的消息与脚本消息使用相同 DOM 归属规则。脚本确认发送后也立即更新本地会话状态。

## 超时库

`stale_no_reply` 使用 `我已回复后72h无回应`；旧的投递无回复规则继续使用 `no_reply`，新入库原因为 `投递后72h无回应`。通过 conv_key 合并同会话记录，no_reply 可升级为 stale_no_reply 或 explicit，explicit 永远优先。写入共用数据目录的锁、fsync 和原子替换；损坏 JSON 或错误顶层/行结构拒绝覆盖。

## 发送规则

- 招呼语、缓存和模板共用候选人事实来源。数字分句采用保守的受控短句匹配，完整保留指标和团队等主体，不从岗位薪资借数字。事实短句改写可能回退模板；模板也不合规则不发送。
- 历史销售额可引用 facts；面试、到岗、薪资承诺，以及出差、驻外、外派、异地等个人意愿转人工。
- 点击首次沟通前必须明确读取到平台招呼语关闭；开启、空结果、缺少布尔状态均中止，不自动调整设置。
- （历史）`inbox.auto_reply.daily_cap` 曾是 auto_reply、interview_reply、followup 共用的流水额度；这三条发送路径已于 cleanup-02 移除，`send_safety` 里的额度分支暂未清理。
- RPC 顶层 error 抛出包含错误码和消息的异常。关闭标签只累计确认成功项；失败保留 ID、抛出明确错误，客户端仍执行关闭。连接、导出和业务异常均进入清理。
