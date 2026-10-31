# 独立 JM-Bot 的交付决策

1.1.0-rc.1 增加统一管理页。用户在 NAS 只粘贴 Compose 并填写一个 `ADMIN_PASSWORD`，随后在同一网页完成 QQ 扫码、群选择、权限保存和普通 PDF 测试。保留原有 AstrBot 项目；不安装或迁移 AstrBot 插件。

| 决策 | 原因与边界 |
| --- | --- |
| 一个项目、两个容器 | JM-Bot 提供管理页并处理 `/jm`；NapCat 连接 QQ，无需模型和额外数据库 |
| 仅映射 `16099:8080` | 浏览器只访问统一入口；NapCat 6099 和 OneBot 3001 留在项目网络 |
| JM-Bot 先健康，NapCat 再启动 | 管理页启动前写好内部配置；`/healthz` 仅反映网页可用，不依赖 QQ 登录或 NapCat 在线，避免循环等待 |
| Compose 只要求一个管理密码 | 内部 token 自动生成并持久化，不要求用户操作 NapCat 网络页；密码至少 12 字符，修改后重建 JM-Bot 容器 |
| `data/config.json` 存权限与内部口令 | 网页保存后重建可恢复；管理密码从 Compose 读取，不在网页改密码；配置不公开打包 |
| 默认指定群全员可用 | 仅已选择的群生效，空群名单拒绝全部群；可关闭全员模式要求用户与群同时授权；私聊始终检查用户 |
| `python:3.12-slim`、`linux/amd64` | 面向目标 x86-64 NAS；固定直接依赖，不要求在 NAS 安装 pip 或编译 |
| NapCat `mlikiowa/napcat-docker:v4.18.42` | 使用官方镜像，MODE=ws；内部 OneBot 地址 `ws://napcat:3001/` |
| 四个目录持久化 | `data`、`napcat-config`、`qq-data`、`downloads`；JM-Bot 可写 NapCat 配置，NapCat 对 downloads 只读 |
| 全局单任务、完整下载才合成 | 不排队、不发缺页 PDF；收到上传成功确认后清理 |
| 失败或断线留文件 | 不自动重发，避免发送结果未确认时重复；重启不续传 |
| 任务目录 0755、PDF 0644 | NapCat UID/GID1000 可读取；NAS ACL 仍需实际发送 PDF 验证 |

直接依赖固定为 `jmcomic==2.7.10`、`img2pdf==0.6.3`、`websockets==17.4`、`aiohttp==3.14.5`、`qrcode==8.2`。HTML/CSS/JS 随镜像提供，没有前端构建服务。Playwright 只用于开发和 CI，不进入运行镜像。普通测试文件固定打包到 `/app/samples/test.pdf`。

源码和部署包使用公开文件白名单，不打包 `data`、QQ 登录状态、NapCat 配置或下载目录。基础镜像和传递依赖未做全量哈希锁定，重新构建可能有补丁或体积差异；NAS 使用固定发布版本。

CI 保留真实本机 WebSocket 的 OneBot 替身集成测试，新增管理页 API 与浏览器操作验证。Linux 容器检查独立验证成品的离线网页启动、登录、权限保存、重建恢复和只读权限；截图只上传普通测试场景的 PNG，不上传运行配置或口令。

RC 表示候选版本。只有镜像发布及匿名拉取检查成功后才可宣布可拉取；只有用户 NAS 的扫码、群选择、私聊/群文件、真实下载和重建恢复验收成功后，才可标为稳定版。旧版的测试或公开状态不能代替新版验收。

依据：[NapCat 官方 WS 示例](https://github.com/NapNeko/NapCat-Docker/blob/main/compose/ws.yml)、[NapCat 初始化逻辑](https://github.com/NapNeko/NapCat-Docker/blob/main/entrypoint.sh)、[GHCR 发布与可见性](https://docs.github.com/en/packages/working-with-a-github-packages-registry/working-with-the-container-registry)。
