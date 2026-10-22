# 没有 huashu-chrome 时的「代理驱动」模式

`boss_hunt.py` 依赖 huashu-chrome（npm）把本地 Chrome 暴露成 MCP 工具。
别的用户装不了/不想装时，可以让 **Agent 用自己的浏览器工具** 跑同一套动作
（Hermes 是 `browser_exec(local=True)`；Claude Code / Codex 挂 Playwright 或 chrome-devtools MCP，
映射见 [`agent-adapters.md`](agent-adapters.md)）：
用户自己的浏览器、自己的登录态，不传 cookie、不代填密码。

代价：每次运行消耗模型 token，适合 **10 个以内/天** 或调试；量大再装脚本模式。

## 0. 前置

- 你自己的浏览器工具：驱动用户本机 Chrome（含登录态）。Hermes 用 `browser_exec(local=True)`，
  Claude Code / Codex 用挂载的 Playwright / chrome-devtools MCP，选择器与点击顺序完全一致。
- 每次先 `ensure_real_tab()`，结束前关掉自己开的标签页（不要关用户的）。

## 1. 确认登录（只读，永不代填）

```js
(()=>{const t=document.body.innerText||'';
return JSON.stringify({url:location.href,
 login:/扫码登录|登录\/注册|手机号登录|密码登录|请登录/.test(t),
 avatar:!!document.querySelector('.nav-figure,.user-nav,.user-info,[class*=nav-figure]')})})()
```

`login=true` 且 `avatar=false` → 停下来，请用户**自己**扫码登录，登录完再继续。
任何情况下不要输入手机号、密码、短信码、验证码。

## 2. 取岗位列表（DOM 抓取）

导航 `https://www.zhipin.com/web/geek/jobs?query=<关键词>&city=<城市码>`，
滚到底 3–10 次后取卡片（2026-10 实测结构）：

| 内容 | 选择器 |
|--|--|
| 卡片 | `li.job-card-box`（也兼容 `.job-card-wrapper`） |
| 岗位名 + 详情链接 | `.job-name`（`href` = `/job_detail/<encryptJobId>.html`） |
| 公司 | `.boss-name` |
| 地点 | `.company-location`（如 `上海·普陀区·梅川路`） |
| 经验/学历标签 | `.tag-list li` |
| 薪资 | ⚠️ 卡片上的 `.job-salary` 文字被字体混淆，只显示 `-K` |

**薪资必须从详情页取**：`.salary`（如 `10-15K`），或直接抓接口响应体
（`joblist.json` 里有真实 `salaryDesc`）。

批量取用一段 JS：

```js
(()=>{const out=[...document.querySelectorAll('li.job-card-box')].map(c=>{
 const q=s=>{const e=c.querySelector(s);return e?(e.innerText||'').replace(/\s+/g,' ').trim():''};
 const a=c.querySelector('.job-name');
 return {name:q('.job-name'), company:q('.boss-name'), loc:q('.company-location'),
         tags:q('.tag-list'), href:a?a.getAttribute('href'):''}});
 return JSON.stringify(out)})()
```

## 3. 详情页（读薪资 / 判断能不能聊）

| 内容 | 选择器 |
|--|--|
| 岗位名 | `.name h1`（或 `h1`） |
| 薪资 | `.salary` |
| 公司 | `.company-info a` |
| 关键词 | `.job-keyword-list` |
| JD 正文 | `.job-sec-text`（只截前几百字看硬条件） |
| 可开聊 | `.btn-startchat` 存在 |
| 已聊过 | `.btn-continue-chat` 或正文含 `继续沟通`/`已沟通` |
| 验证码 | 正文含 `验证码/滑动验证/安全验证/人机验证/拼图` |
| 需 App | 正文含 `需下载/需跳转 App`、`去小程序` |

## 4. 发招呼（一步一步来）

1. 点 `.btn-startchat`
2. 往 `textarea.input-area` 填**渲染好的**招呼语
3. 点 `.send-message`
4. 读 `.startchat-dialog` 的 innerText，确认包含招呼语开头 8 个字 → 才算发出

## 5. 收件箱

消息页 `https://www.zhipin.com/web/geek/chat` 打开即清零未读，别依赖未读数。
用会话项的 `lastMessageInfo.msgTime` 判断「有新消息」，用 `fromId != 自己 uid`
判断「是对方说的」。回复：`.chat-input` 填字 + `.btn-v2.btn-sure-v2.btn-send`。

## 6. 节奏（和脚本模式一致）

- 间隔 ≥60s，一天 ≤15–20 个，分 2–4 个时段。
- 每次只处理 1 条会话/1 个岗位，别一口气连点，容易被判机械行为。
- 结束时关掉自己开的标签页；**不要**刷新或关闭用户自己的页面。

## 7. 什么时候必须停下来问用户

- 出现验证码 / 人机验证（不要尝试绕过）
- 页面提示需要下载 App
- HR 在聊天里约具体时间（只回「请先给时段，我确认后答复」）
- 招呼语被截断或发送结果不确定（`unverified`）
