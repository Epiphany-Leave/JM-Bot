# 验证记录

## 1.1.0-rc.2 候选版

日期：2026-11-01（Asia/Shanghai）。修复 HTTPS 网关向内部 HTTP 转发时的同站来源判断，并兼容旧登录书签。

- 修复前用真实本地 HTTP 往返复现：HTTPS 来源登录返回 403，三个旧登录路径返回 404；原有跨站请求拒绝检查通过。
- 修复后 Windows Python 3.12 共 66 项测试通过。同域名同端口的 HTTPS 转发登录、Secure/HttpOnly/SameSite Cookie、权限保存、会话和退出通过；不同域名、端口、无效来源、跨站请求和伪造转发头仍受限制。
- 新增真实 TLS 终止代理浏览器场景：从 HTTPS 网页经 HTTP 后端登录，验证旧地址跳转、浏览器安全 Cookie、权限保存、刷新与退出。代理只在测试中运行，临时自签名证书不进入源码包或运行镜像。Linux 和镜像发布结果以完成后的 CI 记录为准。
- 尚未通过用户的 UGOS 外网网关实际登录验证；此前只读访问被网关重定向到 UGOS 登录页，没有取得用户会话或任何口令。

## 1.1.0-rc.1 候选版

日期：2026-11-01（Asia/Shanghai）。本节不沿用 1.0.x 的验证结论。

- 新入口：JM-Bot 管理页 `16099:8080`，NapCat 的 6099/3001 不映射到 NAS。
- 验证目标：管理密码、未登录访问限制、QQ 二维码、群选择、权限保存与恢复、普通 PDF 发送、原有 `/jm` 下载及发送行为。
- CI 增加 Chromium 网页操作及截图；成品容器检查在没有 NapCat 时验证 `/healthz`、静态资源、登录和重建后的权限恢复。
- Windows Python 3.12：使用真实管理页进程、临时持久化目录及离线 NapCat 地址，复用 `scripts/container_smoke.py` 的 HTTP 检查函数；健康检查、HTML/JS/CSS、未登录 401、登录、权限保存与进程重建后的恢复通过。这不是 Linux 容器验收。
- Compose 的单入口、单密码、健康依赖及 README 示例一致性检查通过；源码 ZIP 包含全部静态文件且排除运行数据，部署 ZIP 完整性检查通过。
- Windows Python 3.12：`python -m unittest discover -s tests -q`，62 项通过。包含真实本地 HTTP/WebSocket、临时磁盘读写、原子保存失败、旧配置备份、权限热更新、后台下载响应、普通测试 PDF 的成功/拒绝/丢失回执及清理失败处理。
- 真实 Edge 无头浏览器：`python scripts/webui_browser.py --channel msedge` 通过。完成登录、二维码显示/刷新、模拟手机确认后选群、保存权限、轮询不覆盖未保存输入、测试文件成功/保留、手机布局、会话恢复和退出；控制台错误 0。截图保存在 `.cache/webui-browser/`（仅本地或 CI artifact，不进入镜像/源码包）。QQ 服务由本地替身提供，未真实扫码登录。
- Linux Python 3.12：62 项测试和真实 Chromium 网页流程通过。`linux/amd64` 成品镜像构建、依赖与测试 PDF 导入、离线网页健康、认证、权限持久化、UID1000 读取私有配置与 PDF、只读挂载和容器重建后口令/权限/文件保留检查通过。[构建检查](https://github.com/Epiphany-Leave/JM-Bot/actions/runs/41218805945)。
- 候选镜像 `ghcr.io/epiphany-leave/jm-bot:1.1.0-rc.1` 已推送；使用空的 `DOCKER_CONFIG` 免登录拉取成功，并重新运行成品容器检查通过。[发布及匿名验证](https://github.com/Epiphany-Leave/JM-Bot/actions/runs/41218940636)。源码提交 `1fdbe16cd416c4057a45d4e39fdb4b0caa32580b`，标签 `v1.1.0-rc.1`，镜像摘要 `sha256:0f3951ee3aff2fcba947dfc274e6d26151a27ceb73c18bb079cbf1875fa84643`。

匿名拉取后实际运行的候选镜像数据：

| 项目 | 结果 |
| --- | --- |
| 镜像本地大小 | 244,925,098 字节（约 233.6 MiB） |
| 空闲容器内存，docker stats | 44.39 MiB |
| PID 1 空闲 RSS | 69,348 KiB（约 67.7 MiB） |

测量只包含 JM-Bot，不包含 NapCat/QQ，也不代表下载峰值或镜像网络下载量。新代码的编译与打包、Windows/ Linux 模拟验收均不能替代下面的真实 NAS 验收。

尚未验证：用户 NAS 权限、真实 QQ 登录、群选择、私聊/群文件、实际站点下载和 NAS 重建恢复。上述完成前仅为候选版，不标记稳定版。

## 1.0.1

源码标签 `v1.0.1`，提交 `093744cb402a8d2426145606b7f5b0d397665907`。本地和 Linux 41 项测试通过，新增覆盖：指定群全员下载、群外与私聊隔离、切回逐人白名单、配置开关严格解析、口令错误原因提示且不回显口令。

Linux/amd64 镜像构建及全新 Docker 配置下匿名拉取通过；拉取后再次完成真实 WebSocket 与本机替身消息收发、UID1000 只读权限、容器重建保留文件检查。[发布与匿名验证证据](https://github.com/Epiphany-Leave/JM-Bot/actions/runs/41213700150)。镜像摘要 `sha256:dba9a9f82b2529aa145f13dc8ca47eeb112600f498c658ddd2057970a538f875`。

## 1.0.0

日期：2026-10-31（Asia/Shanghai）。源码标签：`v1.0.0`，提交 `ef07c299c21c4e94382f2c998c22f69192197582`。

- Windows Python 3.12 本地测试：36 项通过。Linux CI Python 3.12：36 项通过。
- Linux/amd64 镜像构建、运行时依赖导入通过。
- 使用真实 WebSocket、本机 OneBot 替身验证认证和消息收发，不登录 QQ。
- 容器内 root 创建的 0755 目录、0644 文件，可由 UID/GID 1000 通过只读挂载读取。
- 容器移除并重建后，挂载的输出文件仍可读取。

匿名拉取后实际运行的成品镜像数据：

| 项目 | 结果 |
| --- | --- |
| 镜像本地大小 | 230,773,787 字节（约 220 MiB） |
| 空闲容器内存，docker stats | 31.47 MiB |
| PID 1 空闲 RSS | 51,148 KiB（约 50 MiB） |
| 已推送镜像摘要 | `sha256:7b005fbbec61fe5611baa36454f11e8068b33a9db0e4714fc430a04ea5924abf` |

测量仅针对 JM-Bot 下载程序，不含 NapCat/QQ；空闲数据不代表下载和 PDF 转换的峰值。镜像本地大小也不等于网络下载量。

证据：[Linux 构建检查](https://github.com/Epiphany-Leave/JM-Bot/actions/runs/41210802460)、[版本发布及匿名验证](https://github.com/Epiphany-Leave/JM-Bot/actions/runs/41210889960)。

匿名拉取：**通过**。`verify-published` 任务使用空的 `DOCKER_CONFIG`，成功拉取 `ghcr.io/epiphany-leave/jm-bot:1.0.0`，摘要与发布一致；随后启动拉取到的镜像，再次验证消息收发、UID1000 只读权限和重建文件保留。该任务与构建发布任务均成功。

尚未验证：用户 NAS 的网络与 ACL、真实 QQ 登录及私聊/群文件发送、实际站点下载、NAS 重建恢复。上述均需 README 中的现场验收，模拟测试不能替代。
