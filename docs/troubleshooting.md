# 故障排查

按「现象 → 先查什么 → 怎么修」组织。凡是静默失败，先看报告里有没有对应行，
再去 `~/.boss-hunt/data/runs.log` 尾部。

## macOS：机器睡觉会掐掉整跳

本机 `pmset sleep 1`（闲置 1 分钟就睡），靠 TokenBar 的电源断言才没睡。定时入口必须用
`caffeinate -i -t 900` 包住脚本（wrapper 里已有），否则系统一睡 Chrome 冻结 → 桥断 → 整跳失败。
**合盖时 caffeinate 也无效**，这点要如实告诉用户，不要提议 `sudo pmset -a sleep 0` 去改他的电源设置。

## 前置：扩展必须装过一次

`NO_EXTENSION` / 桥一直连不上的最常见原因不是本项目，而是 **huashu-chrome 的 Chrome 扩展没装**。
浏览器不允许脚本代装扩展，必须用户手动点一次：

1. 商店一键装：<https://chromewebstore.google.com/detail/foiljmaplphdfimfcnfdpekhdnfbgfbf>
2. 商店打不开：`npx -y huashu-chrome extension` 打印扩展目录 → `chrome://extensions` →
   开发者模式 → 「加载已解压的扩展程序」选该目录
3. 验证：`npx -y huashu-chrome doctor` 显示「Chrome 扩展在线」才算好

`doctor` 在线但本项目仍报错，才是本项目的问题（继续往下看）。

## 桥和扩展

| 现象 | 原因 | 处理 |
|--|--|--|
| 报告首行 `bridge:bridge_fail` | `npx` 冷启动超过 60s 探测窗口，**多数是误报** | 同一跳里有 `scan: +N 页` 或「已发」行就不是故障；连续两跳都 fail 才管 |
| 一直 `NO_EXTENSION` | Chrome 没开 / 扩展的 service worker 睡了 | `open -a "Google Chrome"`（开空白页），重试；**唤醒后必须重建 MCP client**，旧 client 缓存着离线状态 |
| 桥端口不通 | 桥空闲约 5h 会自退 | `npx -y huashu-chrome bridge` 手动拉起，等扩展重连 |
| `doctor` 说桥拉不起来 | 假阴性 | 手动起桥即可 |

⚠️ 不要替用户恢复他原来的页面/窗口：只允许「在他开着的 Chrome 里开新标签页」。
收尾只关**自己开的 tab id**；无差别全关会让 Chrome 退出、用户所有页面丢失。

## 抓不到岗位

- `scan` 输出「抓取 0 页响应」：先看 `login-check` 是否 `未登录`；再看是否撞了
  `code:37`（报告里会写 `risk_code_37`）→ 降频、隔 10 分钟再跑。
- 有页面但 `joblist.json` 解析为空：BOSS 改版。跑一次 `network` 看响应体字段名，
  按 `boss-site-notes.md` 更新解析。
- 关键词太窄：`query=BDM` 在有些城市没有结果。换一个能搜出岗位的词。

## 发送

| 返回 | 含义 | 处理 |
|--|--|--|
| `skip_captcha` | 触发验证码/人机验证 | 停当天任务，让用户手动过一次验证，隔天再跑 |
| `skip_need_app` | 岗位要求 App 内沟通 | 正常跳过，不必重试 |
| `skip_already_chatted` | 已经聊过 | 正常跳过；脚本会记入 `contacts` 防重 |
| `skip_no_button` | 详情页 404 / 已下线 / 改版 | 偶发就是下架；大面积出现说明选择器失效 |
| `unverified` | 发出去了但弹窗里没找到校验标记 | 让用户在消息页确认；频繁出现就改 `sending.verify_text` |
| `err_fill` | 输入框没找到 | 可能被验证码页拦了；看当时的 tab 再判断 |
| `err_template_too_long` | 招呼语 >380 字 | 砍内容，80–200 字最好 |

`unverified` 也要在 UI 里复核：宁可少发一个，别让用户收到两条重复招呼。

## 定时任务静默失败

- `no_agent` 脚本崩了不会有任何聊天消息，痕迹只在 cron 作业状态里；
  用 `cronjob_manage(action='list')` 看 `last_status` / `last_error`。
- 本地还要用**真实入口**跑一遍：`bash ~/.boss-hunt/run.sh`，别只测 `python3 boss_hunt.py`。
- 脚本里所有「沿路径累加的列表/计数器」必须在任何 early return 之前初始化，
  否则第一次走到该分支就 `UnboundLocalError`，整跳一条都发不出去。
- 用 `caffeinate -i -t 900` 包住长任务（macOS 一分钟就睡会冻住 Chrome、断桥）；
  合盖场景 `caffeinate` 也无效，要事先说明。**不要**去改系统电源设置。

## 日期与额度

- 额度按 `schedule.timezone` 的日期翻页。系统时区和用户时区不同时，用
  `time.strftime` 会算错日界（下午额度凭空重置）。
- `runs.log` 行首时间用配置时区；如果混入系统本地时间，别按日期前缀重新算额度，
  以「今日累计 N/M」为准。
- 上限要**两处同步**：`sending.daily_cap` 和定时脚本里每跳的 `--send N`
  （`run` 默认取 `schedule.per_window`）。只改一个会出现「上限写 20、实际只发 12」。

## 收件箱

- 数字不对：打开消息页会把未读清零，`unreadMsgCount` 永远读到 0。
  只能用 `lastMessageInfo.msgTime` + `fromId != myuid`。
- 回复发错人：`reply_in_chat()` 靠会话标题核对公司名，读不到标题就放弃（`wrong_conv`）。
  这是**有意的保险**：宁可这条没回、让用户手工补，也不要发给不相干的人。
- 系统消息（「您的附件简历已发送」「对方已同意」）不是 HR 说话，已被
  `inbox.patterns.system` 过滤；新出现的模板自己加进正则。

## 标签页

每次跑完看 `tabs action:list`：超时被打断的跑会留下自己开的标签页。
按 `opened` 列表逐个关，不要「关掉所有标签页」。
