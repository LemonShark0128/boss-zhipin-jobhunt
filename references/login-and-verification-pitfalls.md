# 登录态与发送校验的坑（2026-10-23 实测）

## 1. 详情页「验证码」误判 -> 批量 skip_captcha

- BOSS 岗位详情页底部常驻一个隐藏登录框，正文里带「+86 发送验证码」。
- 原判定 `CAPTCHA_RE = 验证码|滑动验证|安全验证|人机验证|拼图` 用**整页 innerText** 匹配，于是每个正常岗位都命中，全部判 skip_captcha。
- 规则：验证码只看**结构信号**——`document.title` 命中 `验证码中间页|安全验证|人机验证`，或存在 `[class*=captcha],[id*=captcha],.nc_wrapper,.nc_container`。绝不对整页文本匹配「验证码」。
- 同理 `已沟通|继续沟通` 也别整页匹配（页面底部有历史文案），用 `.btn-continue-chat` 元素判定。

## 2. login-check 假阳性

- 症状：命令报「已登录」，实际所有操作只弹登录框。
- 原因：`avatar` 选择器含 `.user-nav`，匹配到了导航栏「我要招聘 / 我要找工作」区块。
- 规则：`avatar` 只认 `.nav-figure, img[class*=avatar], [class*=user-avatar]`；并交叉验证正文不含「登录/注册」。

## 3. 掉登录时的日志指纹

- 一轮 send 出现**大量 unverified**（如 129 条）且「本轮发出 0」，或大量 skip_no_button：
  先怀疑掉登录，而不是选择器/加载时机。
- 掉登录时消息**没有真的发出**（点击只弹登录框），所以额度没被消耗、也无风控风险——先 `login-check` 确认，再让用户扫码，不要急着改选择器。
- 恢复路径：打开 `https://www.zhipin.com/web/user/?ka=header-login` 留给用户在 huashu-chrome 窗口扫码；`screencapture` 在这台机器上会失败（无屏幕录制权限），别指望截二维码给他。
- 注意：登录**正常**时也会成批 unverified，那是另一个原因（见第 6 条），别把两种情况混成一个诊断。

## 4. 扩展搜索方向时的两个陷阱

- `title_allow` 里放**宽泛标签词**（如「外企」「AE」）等于开后门：任何一个含该词的测试/工程师岗都会被放行（`block and not allow` 逻辑）。方向词只放岗位职能词。
- BOSS 搜索「AI商业化」会返回大量技术/产品岗，需在 `title_block` 补：产品经理/产品线/工程师/算法/开发/架构师/策划/设计师/CFO/CTO/合伙人。

## 5. 改完配置必须实扫验证

- `plan` 只读 raw 缓存、**不会扫描**；新增关键词后必须跑一次 `scan` 才会进库，否则会误以为新方向没效果。
- 新增关键词后先 `plan --top 20` 人工看头部是否混入技术岗，再放 `send`。

## 6. 发送校验读不到文本 -> 全判 unverified（登录正常也会发生）

- 症状：登录态正常、每个岗位都点了「立即沟通」并填了话术，但每条都 `unverified`、`本轮发出 0`。
- 历史判断把 `.startchat-dialog`、正文成功提示或 `.btn-continue-chat` 当发送凭据；
  **这些判据已于 2026-10-28 按任务提供的真机证据废止**，不能据此补记账或重试。
- 当前事实：`/job_detail/<id>.html` 点击立即沟通后整页跳转 `/web/geek/chat`，没有弹窗；
  聊天输入为 `.chat-input[contenteditable="true"]`，写入后必须触发 input 并回读一致。
- `.btn-v2.btn-sure-v2.btn-send` 的 `disabled` 类表示不可发送，不能只看原生 disabled 属性。
- 当前确认：点击前后比较气泡 ID，仅新增 `.message-item.item-mine` 且文本哈希一致算发出。
  无证据则 `pending_verification`，流水 `unknown` / `unresolved_send` 保守阻断，人工核对后
  用 `ledger-ack` 处理，不能因页面上出现旧文本而补记为成功。

## 7. skip_no_button 会被写进 contacts，等于永久拉黑

- `do_send` 里 `skip_already*` 与 `skip_no_button` 走同一分支，会把 `brand|bossName` 追加进 `contacts`。
- 后果：某次页面没渲染出按钮的岗位，之后永远被「同 HR 已投」挡掉，看起来像「HR 投过了」，实际一次没投。
- 处理：`skip_no_button` 只应跳过本轮、不应写 contacts；发现这类误拉黑时，从 `state.contacts` 里删掉对应 `brand|bossName` 再重跑，而不是去放宽其他过滤。

## 8. 同一家公司同日连投多岗

- 去重键是 `brand|bossName`（同 HR），同一家公司的不同 HR 会被当成不同目标——实测一轮里连续投了同一家公司的 3 个岗位。
- 用户在意观感时加公司级节流：按 `brandName` 记当日计数，同公司每天只投 1 个；只在用户提出来时加，别默认改。

## 9. 会话建立与文本发送必须分开核对（2026-10-28 更新）

- 历史观察中列表出现公司和时间，只能说明建立会话；不能证明自定义招呼语已经发出，
  也不能证明 HR 已读。旧的列表时间戳判据不再用于自动确认发送。
- `/web/geek/chat` 身份证据只能读当前唯一可见 `.chat-conversation`；列表 `.chat-content`
  可以同时含多家公司，不能用它拼出“匹配身份”。无法独立确认当前面板时停止发送。
- 人工核对按 `docs/live-check-chat-send.md` 操作；没有新增气泡证据时保留未知流水，
  不自动重发、不手改真实 state。

## 10. 招呼语开关（自动招呼）的真实结构：只有 hidden input 说了算

路径 `https://www.zhipin.com/web/geek/notify-set?type=greetSet`。真机抓到的结构是：

```html
<span class="ui-switch" tabindex="0">
  <input type="hidden" value="0">          <!-- 0 = 关，1 = 开 -->
  <span class="ui-switch-inner"></span>
</span>
```

- **页面上没有 `ui-switch-checked`，也没有 `aria-checked`。** 只按 class 判会永远判不出开/关；
  早先那版「读到 off」其实是把「没读到证据」当成了关闭，属于蒙对，HR 收到的可能是平台默认招呼 + 模板招呼两条。
- 判据（按优先级取唯一明确证据）：`input[type=hidden]` 的 value（`0`/`1`）→ `aria-checked="true|false"`
  → class 含 `ui-switch-checked`（视为开）。两处证据冲突、结构缺失、或找不到含「招呼」的那一行 →
  一律 `auto_greeting_unknown`，**不得当作已关闭放行**（unknown 必须中止发送并请用户手动确认）。
- 把判定抽成纯函数 `switch_status(rows)`（入参就是页面上抓到的行数组），才能用真实行数据写离线回归；
  直接对 JS 表达式做端到端 mock 时，替身 DOM 里没有 `querySelector` 会让生产脚本抛异常而假红。
- **真机取证配方**（不要凭猜测改选择器）：
  ```python
  br = CM.Browser(cfg); br.connect(fast=True)
  tab = br.new_tab(url); br.nav(tab, url, wait=4.5)
  data = CM.parse_json_payload(br.js(tab, expr).get("text") or "", None)   # expr 里抓 outerHTML/class/aria/hidden
  br.close_own_tabs()   # 只关自己开的
  ```
  改任何「开关 / 归属 / 发送按钮」的判定后，都要跑一次 `preflight` 看它落成 `auto_greeting_on|off` 而不是
  `auto_greeting_unknown` —— 单测全绿完全看不出这一类失败，而 unknown 会把投递整片停掉。

## 11. 详情页公司名：`.company-name` 是侧栏「同类公司」，只能从 document.title 取

- 真机（2026-10-28 任务提供证据，`/job_detail/<id>.html`）上 `.company-name` 读到的是**侧栏同类公司**：实测「潮玩渠道经理」页读到
  「己示例科技」，而岗位归属「戊示例科技」；`.company-info a`、`.sider-company .company-info a` 都是空串。
- 后果：身份校验永远 `False` → 每个岗位都返回 `error: unknown_conversation`，「本轮发出 0」，
  而单测全绿（替身把 `.company-name` 想当然了）。离线替身必须覆盖侧栏公司与真实公司不同的情形；上线仍需真机核对。
- 正确来源是 `document.title`，形如 `「潮玩渠道经理招聘」_戊示例科技招聘-BOSS直聘`：
  取 `_` 之后、`招聘` 之前那一段 —— `((document.title||'').split('_')[1]||'').match(/^(.+?)招聘/)`。
  岗位名仍取 `.job-banner h1,.job-name,h1`，job_id 仍取路径 `/job_detail/([^/]+)\.html`。
- 取不到品牌必须返回 `identity_unknown` 并停在点击前，**不要回落到 `.company-name`**——回落等于允许拿
  别家公司的名字蒙对，比直接报错更危险。
- 单岗验证只要 30 秒，改任何「发到谁」相关的解析后先跑这个，再谈 `send`：
  ```python
  print(B.inspect_job(br, tab, job, cfg)[0])      # 期望 ready
  print(B.job_identity_matches(br, tab, job))     # 期望 True
  ```
