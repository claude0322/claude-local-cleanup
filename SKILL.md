---
name: claude-local-cleanup
description: 检查并经用户确认清理 macOS 或 Windows 上的 Claude 缓存、选定登录字段和 Desktop 网站存储，盘点浏览器配置并提供恢复。适用于登录排障、卸载残留和本地隐私整理；不能解除服务端封禁。
---

# Claude 本地清理助手

使用随附脚本整理本机数据。安装或阅读本技能不等于同意清理；不得声称本地清理能解封或避免再次封号。

## 先检查

1. 确认需求是缓存、登录重置还是浏览器网站数据；需求明确时直接只读检查，不默认扩大到全部范围。
2. 运行 `python3 scripts/cleanup.py audit` 与 `python3 scripts/browser_inventory.py`，路径指向本技能。Windows 使用 `py -3`。不输出 token、Cookie、账号 JSON、环境变量值或带凭据的进程参数。
3. 报告实际发现与覆盖边界。MSIX、自定义目录、钥匙串、系统凭据、配置内部备份、环境变量、CC Switch 和浏览器删除均不由客户端脚本自动处理。

## 计划与执行

默认缓存计划：

```bash
python3 scripts/cleanup.py plan --scope cache --out ~/claude-cleanup-plan.json
```

只有用户需要重置相应登录时才追加 `--scope code-login`、`--scope desktop-login`。Code 仅处理两个已知账号键，Desktop 仅选已知网站存储，不整删配置或应用数据根目录。

展示绝对路径、影响、计划编号、保留内容及恢复目录。Desktop 网站存储可能含离线数据；恢复副本可能保存旧凭据，应留在本机。默认保留项目、会话、MCP、skills、plugins、设置、书签、密码和其他网站数据。

取得对此清单的明确确认后运行：

```bash
python3 scripts/cleanup.py apply ~/claude-cleanup-plan.json
```

在交互终端传递已获用户授权的 `APPLY <前12位计划编号>`。不能编造同意、管道代答或修改脚本绕过检查。已有明确授权无需重复询问同一计划；范围或内容变化后重新展示并确认。

正常退出 Claude；脚本保守拒绝 Claude 与 Node.js 进程，不强制结束未保存工作。执行者若是 Claude Code，交给独立工具或用户终端。权限、路径链接、文件变化或检测失败时停止并报告。

## 浏览器与 CC Switch

- 浏览器处理前读取 [浏览器指南](references/browser-guide.md)，逐个实际配置通过原生设置处理获批域名。盘点成功不等于已清理。
- 用户要求处理 CC Switch 时读取 [CC Switch 参考](references/cc-switch.md)，不整删混合数据库。
- macOS 优先通过 Claude Code `/logout` 退出。额外钥匙串删除需列出确切条目、说明不可自动恢复并取得授权，不显示密码值。

## 核验与恢复

使用实际返回的 `receipt.json` 运行 `verify`，分别报告客户端和各浏览器配置已完成、未执行、受阻项。不能把脚本成功写成所有痕迹已清除。

恢复前读取 [恢复说明](references/recovery.md)，展示还原路径并取得授权后执行 `restore`，输入 `RESTORE <前12位计划编号>`。有新数据时不强行覆盖。脚本不提供永久删除副本或清空回收站的功能。

来源见 [sources.md](references/sources.md)。不修改网络、硬件标识、浏览器指纹或服务端账号状态。
