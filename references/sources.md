# 来源与实现范围

审阅日期：2026-10-30。

## 功能参考

- [l2461090/claude-post-ban-cleanup](https://github.com/l2461090/claude-post-ban-cleanup)
- 审阅提交：[`56c022fe9d79a19e892f8fff8b26d96d889038d2`](https://github.com/l2461090/claude-post-ban-cleanup/tree/56c022fe9d79a19e892f8fff8b26d96d889038d2)

参考其先检查、展示范围、确认后处理和保护工作资料的功能思路。本项目独立实现计划校验、文件操作、恢复、浏览器盘点、测试和文档，没有复制参考仓库的实现、测试、图片或广告。

审阅时参考仓库没有根目录 LICENSE；其中 `references/upstream-license.txt` 属于更早的 Suyuan 项目。本项目不把该文件当成参考仓库新增代码的整体授权，也不自称其官方 fork。本仓库 MIT 许可覆盖独立编写内容。

## 官方资料

- [Claude Code Authentication](https://code.claude.com/docs/en/authentication)：认证、退出登录和自定义配置目录；本版本遇到 `CLAUDE_CONFIG_DIR` 停止自动修改。
- [Electron app.getPath](https://www.electronjs.org/docs/latest/api/app#appgetpathname)：用户与会话数据路径的含义；Claude 具体布局仍需现场核对。
- [Deploy Claude Desktop for Windows](https://support.claude.com/en/articles/12622703-deploy-claude-desktop-for-windows)：MSIX、Cowork 部署差异；本版本未实现 MSIX 清理。

不验证封号原因，不承诺改变服务端账号状态。隔离测试不能证明覆盖全部客户端版本；新版本的未知字段和路径保留并报告。
