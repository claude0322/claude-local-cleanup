# Claude 本地清理助手

**先检查，再确认；清理有记录，恢复有依据。**

[![Tests](https://github.com/claude0322/claude-local-cleanup/actions/workflows/tests.yml/badge.svg)](https://github.com/claude0322/claude-local-cleanup/actions/workflows/tests.yml)
![Python 3.10+](https://img.shields.io/badge/Python-3.10%2B-blue)
[![MIT](https://img.shields.io/badge/License-MIT-green)](LICENSE)

用于 Claude 登录排障、卸载后的残留整理和本地隐私清理。提供中文 AI Skill、Python 标准库脚本、浏览器配置盘点与恢复命令，适用于 macOS 和原生 Windows。

**本地清理不能解除服务端封禁，也不承诺避免再次封号。** 项目不修改 IP、代理、时区、硬件标识或浏览器指纹。

**[下载完整技能包](https://github.com/claude0322/claude-local-cleanup/raw/refs/heads/main/dist/claude-local-cleanup.zip)** · [AI 执行入口](SKILL.md) · [浏览器指南](references/browser-guide.md) · [恢复说明](references/recovery.md) · [来源](references/sources.md)

## 能做什么

| 功能 | 方式与边界 |
| --- | --- |
| 只读检查 | 列出已知路径、大小与状态，不打印配置内容 |
| 缓存清理 | 默认只选 Claude Code 与 Desktop 的可再生缓存 |
| Code 登录字段 | 可选移除两个已知 JSON 账号键，保留其他配置 |
| Desktop 网站存储 | 可选处理 Cookies、Local Storage 等已知子目录，不整删应用目录 |
| 恢复 | 完整性校验、部分失败记录；独占移动与保留版本，检测到冲突时停止 |
| 浏览器 | 盘点 Chrome / Edge / Brave / Firefox 配置；Safari 使用原生设置；按指南逐配置清理 |
| CC Switch | 人工审阅流程，不自动改写混合数据库 |

默认保留项目、编程会话、文件历史、MCP、skills、plugins、设置、Desktop 独立会话/VM 目录、浏览器密码、书签和其他网站数据。网站存储本身可能包含离线页面数据，选择 `desktop-login` 前应检查计划。

不自动处理 macOS 钥匙串、Windows 凭据管理器、MSIX/未知安装布局、配置内部备份、自定义 `CLAUDE_CONFIG_DIR`、shell 环境变量、系统快照或第三方账号工具。“脚本执行完成”只表示计划内文件操作完成。

## 交给 AI 使用

1. 下载上方 ZIP，解压得到完整的 `claude-local-cleanup/` 文件夹。
2. 将文件夹放入 AI 工具实际配置的技能目录，或让有本机文件与终端权限的 AI 打开其中 `SKILL.md`。普通网页聊天本身不能操作电脑。
3. 发送：

> 请使用 claude-local-cleanup 检查我的 Claude 本地缓存、登录数据和浏览器配置。先列出具体路径、影响、保留内容与恢复位置，等我确认后再执行。保留项目、会话、MCP 和其他工具数据。

正在运行的 Claude Code 不应清理自己的资料；请交给独立工具或正常退出后的终端。

## 自己在终端使用

需要 **Python 3.10+**，无第三方依赖。进入下载后的项目目录；macOS 使用 `python3`，Windows PowerShell 将其替换为 `py -3`。

```bash
# 1. 只读检查，不创建恢复批次或执行删除
python3 scripts/cleanup.py audit
python3 scripts/browser_inventory.py

# 2. 生成缓存计划，输出文件不能已存在
python3 scripts/cleanup.py plan --scope cache --out ~/claude-cleanup-plan.json

# 3. 阅读清单，正常退出 Claude 和相关 Node.js 进程，再执行
python3 scripts/cleanup.py apply ~/claude-cleanup-plan.json
```

需要处理本地登录字段与 Desktop 网站存储时，在生成计划时明确选入：

```bash
python3 scripts/cleanup.py plan --scope cache --scope code-login --scope desktop-login --out ~/claude-login-plan.json
```

执行时在真实交互终端输入 `APPLY <屏幕显示的前12位计划编号>`。没有 `--yes`、`--force` 或跳过确认选项。文件变化后必须重新生成计划。

Code 登录范围只移除 `.claude.json` 的 `oauthAccount` 和 `.claude/.credentials.json` 的 `claudeAiOauth`。Mac 的认证可能仍保存在钥匙串，修改 JSON 不等于退出所有认证。优先使用 Claude Code 自带 `/logout` 再复核；浏览器按[指南](references/browser-guide.md)另行操作。

## 恢复与核验

每次操作创建 `~/.claude-local-cleanup/recovery/<完整计划编号>/`，`receipt.json` 记录实际完成情况。Windows 对应当前用户主目录下同名目录；它不是系统回收站。

使用脚本实际输出的路径：

```bash
python3 scripts/cleanup.py verify /完整路径/receipt.json
python3 scripts/cleanup.py restore /完整路径/receipt.json
```

恢复需输入 `RESTORE <前12位计划编号>`。原位置出现新数据时停止并保留双方资料，详见[恢复说明](references/recovery.md)。副本可能包含旧登录信息，留在本机，不要上传到仓库或 Issue。

1.0.1 加固了 macOS ACL、Windows 系统命令路径与并发文件操作。JSON 更新会保留操作前的原文件；若程序重新启动或持有旧文件句柄继续写入，变更可能留在恢复目录的保留版本中，需要人工比较。操作期间仍须保持客户端退出。旧版计划需重新生成，1.0.0 的恢复记录仍可读取和恢复。

## 维护与测试

```bash
python3 -m unittest discover -s tests -v
python3 scripts/package.py
```

测试使用临时目录和虚构凭据，覆盖确认拒绝、路径链接、目标变化、字段保留、备份失败、部分失败、恢复冲突和打包边界，并模拟检查后发生的并发写入。CI 配置为 macOS / Windows / Linux、Python 3.10 / 3.13；Linux 仅运行隔离测试，不启用客户端写入。原生测试另检查 macOS ACL 继承、Windows NTFS junction、恢复文件 ACL 与系统命令路径。CI Action 固定到完整提交哈希。

测试通过不表示覆盖每个客户端版本。反馈请提供系统、脚本版本、脱敏错误类型和复现步骤，不附凭据、Cookie、完整配置或恢复副本。

打包只包含固定源码清单，生成 ZIP 和 SHA-256 文件；重复构建结果一致。改动源码后重新打包并一并提交。

## 来源与许可

功能思路参考 [l2461090/claude-post-ban-cleanup](https://github.com/l2461090/claude-post-ban-cleanup)。本项目独立编写脚本、测试和文档，未复制参考项目实现或广告，详见[来源说明](references/sources.md)。

采用 [MIT License](LICENSE)，允许分享、修改和再发布，保留许可证即可。非 Anthropic 官方项目。
