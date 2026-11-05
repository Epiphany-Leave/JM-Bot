# 验证记录

## 1.1.0-rc.5 候选版

日期：2026-11-05（Asia/Shanghai）。新增 `/jm-s <ID>` 元数据查询，整理管理页布局，并将维护内容移出 README。

- Windows Python 3.12：120 项测试通过。本机加密 HTTP 回包经过原生 API 解密、适配器和 `JmAlbumDetail`，验证标题、多人作者、简介、缺失值、截断、无效 ID、代理传输；查询不下载图片、不生成 PDF。命令验证覆盖原有授权、格式、群聊提及、去重、忙碌、取消等待线程退出、断线不补发，以及回复结果未确认时不标记成功。
- 真实 Edge 浏览器：登录、扫码、权限、测试 PDF、订阅导入和安全错误、代理选择、下载设置、检测与停止、HTTPS 转发通过。另检查四个页面的桌面 / 390px 手机布局、导航选中、键盘焦点、动态版本和查询任务的元数据进度；无横向溢出。故意触发的订阅 HTTP 503 单独记录，其他控制台错误和 JS 异常为 0。
- 界面截图位于 `.cache/webui-browser/`，包括 `05-ready.png`、`10-download-settings.png` 和 `16-mobile-overview.png`。主按钮白字与背景对比度约 5.19:1；资源随镜像提供，没有新增外部字体或 CDN。
- README 保留安装、网页设置、命令和必要排错；维护文档独立。部署 ZIP 新增 `docs/maintenance.md` 和 `docs/verification.md`，源码和部署包均使用公开文件白名单，不包含运行数据。
- [Linux CI](https://github.com/Epiphany-Leave/JM-Bot/actions/runs/41974525704) 的 120 项测试、Chromium 桌面 / 手机布局、真实 Mihomo、镜像构建及 Compose 目录 / 共享读取 / 重建检查均通过。
- 候选镜像 `ghcr.io/epiphany-leave/jm-bot:1.1.0-rc.5` 已发布。独立任务使用空 `DOCKER_CONFIG` 匿名拉取，并再次通过目录自动创建、认证、共享 PDF 读取和重建恢复。[发布与匿名验证](https://github.com/Epiphany-Leave/JM-Bot/actions/runs/41974861608)。源码提交 `f26e33fbabc0ff314590f0c067913bff43db3752`，标签 `v1.1.0-rc.5`，推送与拉取摘要一致：`sha256:e929a0b44a862d743532d97cfc20d25f43215cb8265193e11931d1fa19087c7e`。

尚未验证真实 QQ 和上游元数据 / 下载服务、用户主机网络，以及 Windows Docker Desktop 实机；当前开发机没有 Docker CLI 或 Docker Desktop。自动化中的 QQ、元数据和节点均为受控测试服务，不能替代现场验收。

## 1.1.0-rc.4 候选版

日期：2026-11-01（Asia/Shanghai）。本版保留三个服务，修复订阅链接获取、旧入口响应缓存和版本识别，并将 README 改为 Linux / Windows 部署说明。

- Windows Python 3.12：108 项测试通过。真实本机 HTTP 服务覆盖固定 Mihomo User-Agent、重定向、YAML / URI / Base64 获取、HTTP 状态码、HTML、UTF-8、2 MiB、连接 / TLS / DNS / 超时诊断；验证直连失败后通过已保存代理获取、两路失败保留旧配置，以及代理就绪失败后恢复手动节点。
- 真实 Edge 浏览器：根路径、权限深链接、旧地址跳转、登录前后动态版本、订阅安全错误、文件导入、节点与下载设置、进度和停止、二维码、权限、普通 PDF、移动布局均通过。故意触发的订阅错误产生 1 条预期 HTTP 503 资源诊断，其他控制台错误和 JS 异常为 0；HTTPS 转发流程控制台错误为 0。截图 `.cache/webui-browser/01-login.png`、`09-https-proxy.png`、`15-subscription-safe-error.png`。
- [最终 Linux CI](https://github.com/Epiphany-Leave/JM-Bot/actions/runs/41309801129) 通过 108 项测试、Chromium、真实 Mihomo 及成品镜像检查。Compose 检查从五个数据目录均不存在的临时项目开始，读取正式 Compose 挂载并验证自动创建、准备配置共享、UID1000 读取 PDF、重建保留权限 / 内部口令 / 文件。QQ 与代理服务在目录检查中使用受控文件检查进程；另行执行的 Mihomo 检查使用真实固定版本核心，不是真实 QQ 登录。
- README 的 Compose 与仓库文件一致；新部署不要求手动创建五个目录，已有目录原地复用。
- 候选镜像 `ghcr.io/epiphany-leave/jm-bot:1.1.0-rc.4` 已发布。独立任务使用空 `DOCKER_CONFIG` 匿名拉取成功，并重新通过 Compose 首次目录自动创建、认证、共享 PDF 读取及重建恢复。[发布与匿名验证](https://github.com/Epiphany-Leave/JM-Bot/actions/runs/41309991183)。源码提交 `2818ff137842d60240e7fbbd1bdf740e34812840`，标签 `v1.1.0-rc.4`；推送与拉取摘要一致：`sha256:1161c3bd245d58a56eacd67e83701e2894533658cb8ce3d80b01c6b42cecb801`。
- 匿名拉取后镜像本地大小 244,989,604 字节，空闲内存 44.88 MiB，PID1 RSS 69,920 KiB。真实 Mihomo 检查中镜像大小 102,916,352 字节、空闲内存 8.918 MiB。上述不含 NapCat/QQ，也不代表 Windows、用户主机、实际订阅规模或下载峰值；没有承诺合并容器或资源下降。

未完成：当前 Windows 开发机没有 Docker CLI 或 Docker Desktop，因此未进行 Windows Docker Desktop 的首次部署与重建实机验收。未访问用户真实订阅、下载源或登录后的 UGOS 网关；这些现场结果不能由本机替身或 CI 代替。

## 1.1.0-rc.3 候选版

日期：2026-11-01（Asia/Shanghai）。修复图片源实际切换与下载预算，新增下载设置、停止任务和 NAS 独立 Mihomo 管理。

- Windows Python 3.12 共 98 项测试通过；PDF 合成不会错误触发已结束的网络下载预算。
- 本机 HTTP 验证：原图片源失败后请求真实不同的备用 Host，路径和查询参数保持一致；覆盖 404、截断图片、HTTP200错误内容、传输超时、真实图片解扰与 PDF 页序。已完成页只获取一次，缺页不产生 PDF。
- 本机网络验证：连接/图片/首次元数据/无进展/整本预算生效；原生下载线程在取消后完全退出才解除忙碌，排队的库内图片任务不再发起请求。显式直连忽略环境代理，代理只属于当前下载会话。
- 网页验证：真实 Edge 通过下载设置保存、未保存输入保护、文件与链接导入、刷新与节点选择、检测一张完整本机测试图片、停止下载且等待工作线程退出、停止后状态提示清除、手机布局，以及原 HTTPS 登录回归；控制台错误 0。截图 `.cache/webui-browser/10-download-settings.png` 至 `14-mobile-download.png`。
- 权限及配置验证：下载/代理接口需要登录；下载与代理配置操作共用单任务锁；旧持久化配置自动补充默认值且不更换 QQ/OneBot 口令，保存失败不改变当前设置；缺失代理节点文件时管理网页仍可用于恢复。大订阅健康检查最多等待 5 秒，超时仍须通过实际 HTTP 204 验证；认证和配置错误不被当作超时忽略。
- 真实 Mihomo 容器的原生解析、冷启动自动切换、回滚和重启恢复通过；包括首节点返回 HTTP 502 后使用第二个有效节点、URI/Base64 解析、停止核心后拒绝代理请求。[最终 Linux 检查](https://github.com/Epiphany-Leave/JM-Bot/actions/runs/41294628792)，含 98 项测试、Chromium 与 HTTPS 浏览器流程、成品镜像构建及重建持久化检查。
- 候选镜像 `ghcr.io/epiphany-leave/jm-bot:1.1.0-rc.3` 已发布。独立任务使用空 `DOCKER_CONFIG` 匿名拉取成功，并重新验证网页认证、UID1000 只读文件访问、容器重建后的配置与内部口令保留。[发布与匿名验证](https://github.com/Epiphany-Leave/JM-Bot/actions/runs/41294757058)。源码提交 `7b0d0438df9c442c189b9cf6a410b1d2d92aae09`，标签 `v1.1.0-rc.3`，推送与拉取摘要一致：`sha256:09780d3ff25c6bcd1be1083a214bcff1cc03fd52da3768cb769848cc6b3ecee4`。

本次 Linux CI 的实际测量：

| 服务 | 镜像本地大小 | 空闲容器内存 |
| --- | --- | --- |
| JM-Bot（匿名拉取后） | 244,981,934 字节 | 44.65 MiB；PID1 RSS 69,940 KiB |
| Mihomo v1.19.33（受控测试节点） | 102,916,352 字节 | 8.527 MiB |

测量不含 NapCat/QQ，不代表 NAS 实际内存、真实订阅规模或下载峰值；镜像本地大小也不等于网络下载量。

所有自动化下载与代理测试使用受控本机服务和测试节点，没有使用用户日志中的真实下载地址或任何用户订阅。用户 NAS 的实际订阅、JM 接口和图片可达性仍需现场验收。

## 1.1.0-rc.2 候选版

日期：2026-11-01（Asia/Shanghai）。修复 HTTPS 网关向内部 HTTP 转发时的同站来源判断，并兼容旧登录书签。

- 修复前用真实本地 HTTP 往返复现：HTTPS 来源登录返回 403，三个旧登录路径返回 404；原有跨站请求拒绝检查通过。
- 修复后 Windows Python 3.12 共 66 项测试通过。同域名同端口的 HTTPS 转发登录、Secure/HttpOnly/SameSite Cookie、权限保存、会话和退出通过；不同域名、端口、无效来源、跨站请求和伪造转发头仍受限制。
- 新增真实 TLS 终止代理浏览器场景：从 HTTPS 网页经 HTTP 后端登录，验证旧地址跳转、浏览器安全 Cookie、权限保存、刷新与退出。代理只在测试中运行，临时自签名证书不进入源码包或运行镜像。Linux 和镜像发布结果以完成后的 CI 记录为准。
- Windows Edge 与 Linux Chromium 的普通 HTTP、真实 HTTPS 转发浏览器流程均通过，控制台错误 0。Windows 本地截图 `.cache/webui-browser/09-https-proxy.png`。
- 候选镜像 `ghcr.io/epiphany-leave/jm-bot:1.1.0-rc.2` 已发布，Linux 66 项测试、构建、私有配置 UID1000 访问、只读文件权限、容器重建恢复和空 `DOCKER_CONFIG` 匿名拉取检查全部通过。[发布与匿名验证](https://github.com/Epiphany-Leave/JM-Bot/actions/runs/41275576332)。源码提交 `f1f25efc54d5fd91671dd83ad4e0196dc7b463e4`，镜像摘要 `sha256:4a231d55a15f9c3c13352370dc62a76bc5504012dd0746bf9cd523377cc06756`。
- 匿名拉取后成品镜像本地大小 244,925,604 字节，空闲容器内存 43.8 MiB，PID1 RSS 69,000 KiB。只包含 JM-Bot，不含 NapCat/QQ 和下载峰值。
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
