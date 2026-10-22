# 变更记录

本项目遵循 [语义化版本](https://semver.org/lang/zh-CN/)；格式参考
[Keep a Changelog](https://keepachangelog.com/zh-CN/1.1.0/)。

## [Unreleased]

## [0.1.2] - 2026-10-22

### Added
- `CHANGELOG.md`（本文件）
- `.github/ISSUE_TEMPLATE/bug_report.yml`：选择器失效 / 发送失败的问题模板
- README 首屏加「powered by huashu-chrome」徽章，致谢段落补 npm 包名

## [0.1.1] - 2026-10-22

### Added
- README「依赖与前置」：Chrome / Python 3.9+ / Node ≥20 / huashu-chrome 及其扩展，
  含商店一键装与 `chrome://extensions` 手动加载两条路径、`huashu-chrome doctor` 验证
- README「依赖与致谢」：说明上游提供 MCP server + 本地桥 + 扩展，本项目不打包、不修改，
  仅运行时 `npx` 调用
- `docs/troubleshooting.md` 前置排查：`NO_EXTENSION` 先确认扩展是否装过
- `SKILL.md`「前置依赖」段

### Fixed
- 原 README 只写「需要 npx」，新用户会先卡在扩展未安装的 `NO_EXTENSION` 上

## [0.1.0] - 2026-10-22

### Added
- 首个公开版本
- 配置驱动的筛选：薪资 / 经验 / 学历 / 行业 / 公司 / HR / 区域 / 猎头 / 代招
- 个性化招呼语渲染；发送后回读弹窗按校验文本确认
- 收件箱分类：感兴趣 / 约面试 / 不合适；面试类只回「请先给时段，我确认后答复」
- 幂等记账：按岗位 ID 与 `brandName|bossName` 去重，额度按配置时区翻日
- 定时运行：`run --send N --inbox`，macOS 用 `caffeinate` 包住，桥断自愈
- 文档：访谈清单、页面选择器与城市码、风控阈值与来源、故障排查、无桥代理模式

### Known limitations
- 唤醒扩展（`open -a`）与 `caffeinate` 为 macOS 命令，其他平台需替换
- 选择器基于 2026-10 网页版，改版后需按 `docs/boss-site-notes.md` 更新
