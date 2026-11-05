# 运维与开发

日常部署和 QQ 命令见 [README](../README.md)。当前发布状态与验收范围以[验证记录](verification.md)为准。

## 升级、备份与恢复

1. 等当前任务结束，在原项目目录运行 `docker compose stop`，备份 `compose.yaml` 和 `data/`、`napcat-config/`、`qq-data/`、`downloads/`、`proxy-data/` 五个目录。备份包含登录状态、口令和订阅凭据，应私密保存。
2. 确认目标版本已发布且可以拉取，将 Compose 完整替换为目标版配置，保留原端口和五个挂载路径；新版 Compose 无需填写管理密码，不要用空目录替换已有数据。
3. 在原目录运行 `docker compose pull`，再运行 `docker compose up -d --force-recreate`。
4. 登录网页，核对 QQ、权限、下载方式和节点，发送普通测试 PDF，再检测下载线路及完整下载。升级不会主动把已保存的下载方式改为直连。

重建容器继续挂载原五个目录。管理密码以独立随机盐和哈希私密保存在持久目录的 `data/config.json` 中，不存明文。以后修改密码直接在已登录网页的侧栏打开“修改密码”，填写旧密码、新密码和确认密码；保存后所有会话退出，使用新密码重新登录，无需改 Compose 或重建容器。密码只要求非空，不强制 12 位，允许短密码或中文；建议使用难猜的密码。

兼容旧部署：仅当 `data/config.json` 尚未保存管理密码凭据时，旧 Compose 中非空、非占位符的 `ADMIN_PASSWORD` 才会自动导入并保存为盐和哈希。已有凭据后，环境变量不会覆盖网页设置的密码。使用新版 Compose 挂载旧数据时，若旧数据尚未保存凭据且不再携带旧环境变量，首次打开网页设置密码即可；原 QQ 登录、权限和其他配置文件保留不动。

不要手改 `data/config.json` 中的凭据或内部 QQ、代理口令，也不要公开上传运行配置和日志包。回退前停止项目，恢复升级前备份的 Compose 和对应五个目录，再拉取并重新创建服务；不要删除运行数据。删除项目或容器时不要选择删除宿主机数据。

重启会中断在途任务，不自动续传、不跨任务复用进度，也不自动重发。PDF 已生成但发送失败或未确认时，按页面或日志提示从 `downloads/` 取回，先确认 QQ 是否已收到再决定是否重试；留存文件需自行清理。缺页或停止的下载不会保留可发送的半本 PDF。

### 忘记管理密码

在原项目目录依次运行以下命令。第二条命令会交互要求输入新密码并确认，输入不会显示；不要把密码放进命令行。

```text
docker compose stop jm-bot
docker compose run --rm --no-deps jm-bot python -m jm_bot --reset-admin-password
docker compose up -d
```

重置只更新持久配置中的管理密码凭据，保留 QQ 登录、权限和代理配置。启动后使用新密码登录；不要为重置密码删除 `data/` 或其他数据目录。

## 代理与网络排查

只有 JM-Bot 管理页向部署主机发布端口；NapCat、OneBot 和 Mihomo 的控制、代理端口不对宿主机开放。JM-Bot 生成内部配置并启动网页，NapCat、Mihomo 在网页健康后启动；QQ 未登录不妨碍网页打开。

JM-Bot 与 NapCat 使用默认网络，与 Mihomo 使用 `proxy` 网络。内置代理不接管 QQ 或宿主机其他应用；不使用 TUN、主机网络或 Docker socket。

通过 HTTPS 网关访问时，先完成网关登录，再访问应用根路径并使用管理密码。网关应保留原始 Host（域名和端口），应用不盲目信任 `X-Forwarded-*`。遇到 403 可先用本机或局域网入口排查，不要使用 NapCat 日志里的 WebUI Token。

订阅获取使用 `mihomo/1.19.33` User-Agent，先直连尝试一次（最多 20 秒）；失败后只有已有成功保存的节点时，才先验证旧代理并经它重试一次（最多 20 秒）。整个获取流程含旧代理验证最多 60 秒。没有可用旧节点时，可在能访问订阅的设备导出节点文件再上传。

导入失败保留原配置；成功不自动切换下载方式。Clash 配置只提取节点，不套用文件中的端口、控制器、TUN 和规则；其他格式由固定版 Mihomo 原生解析器处理，并非所有 VPN 配置都支持。代理模式故障不会自动回退直连，需要手动选择直连并保存。

冷重启后代理状态可能待验证，可点击“选择节点”重新应用，或在已保存代理模式下检测线路。节点很多或多数失效时，自动选择也可能超时，可上传少量已知可用节点或指定节点重试。订阅连通性通过不保证漫画接口和图片可访问。

NapCat 以 UID/GID `1000:1000` 运行。`napcat-config`、`qq-data` 要可写，`downloads` 及父目录要可遍历、PDF 要可读；任务目录为 `0755`、PDF 为 `0644`。宿主机 ACL 或 Docker Desktop 文件访问设置仍可能限制访问，应实际发送测试 PDF。按具体报错检查，不要使用 `chmod 777` 或修改其他项目权限。

### 下载等待与停止

默认值兼顾速度和等待时间，高级设置默认折叠；“恢复推荐值”只改网页草稿，点击保存才生效。

| 设置 | 推荐值 | 可保存范围 |
| --- | --- | --- |
| 并发图片数 | 3 | 1–5 |
| 连接等待 | 8 秒 | 1–60 秒 |
| 单次图片总等待，包含连接 | 45 秒 | 1–300 秒，不能小于连接等待 |
| 每页尝试不同图片源 | 3 个 | 1–6 个，每个候选源只试一次 |
| 无图片完成进展时停止 | 150 秒 | 1–1800 秒 |
| 网络下载软时限 | 30 分钟 | 1–7200 秒，网页以分钟填写并换算到整数秒；不计入 PDF 合成和上传 |

漫画信息阶段另有 60 秒总预算；单次 API 请求最多 20 秒，同一 API 请求最多尝试两个域名。图片会优先使用已成功的图片源，再尝试有限的其他来源，不会无限重试。

概览和下载设置页显示当前任务、阶段及停止原因；下载另显示页数和图片源。信息查询或下载的网络请求阶段可以停止任务；PDF 合成、上传、普通测试 PDF 和配置操作不提供停止。停止和软时限需要等待已启动的线程退出，不能保证立即中断底层 I/O；退出前仍忙碌，不接下一项任务。代理、下载设置保存和检测应在空闲时操作；使用权限可照常保存，立即对后续请求生效。

## 开发与发布

开发机使用 Python 3.12。Playwright 只用于开发和 CI，不进入运行镜像：

```text
python -m pip install -r requirements.txt
python -m unittest discover -s tests -v
python -m pip install playwright==1.64.1
python -m playwright install chromium
python scripts/webui_browser.py
python scripts/package.py
```

Windows 已安装 Edge 时可运行 `python scripts/webui_browser.py --channel msedge`。浏览器测试保留登录、二维码、权限、PDF 和 HTTPS 网关流程，并检查下载设置、代理导入选点、图片检测、停止及移动布局；本地 HTTP、QQ 和 Mihomo 控制端替身不证明真实部署环境或真实上游可用。

Linux 与本地 Docker 环境另检查真实 Mihomo 和成品镜像：

```text
python scripts/mihomo_smoke.py --image metacubex/mihomo:v1.19.33
docker build --platform linux/amd64 -t jm-bot:ci .
python scripts/container_smoke.py --image jm-bot:ci
```

`mihomo_smoke.py` 用真实核心与本地受控 HTTP 节点检查原生解析、自动/指定节点、失败回滚、重启恢复和不可用时拒绝代理请求，不使用私人订阅或访问真实漫画站点。成品容器检查覆盖离线网页启动、认证、保存恢复及只读文件访问；空闲内存不代表下载峰值，也不包含 QQ 与代理整体开销。

[GitHub Actions](https://github.com/Epiphany-Leave/jm-bot/actions) 在推送和手动运行时测试构建；版本标签用于发布对应版本镜像。`1.1.1` 当前待发布，不能据版本号推定已验收稳定。发布后还需匿名拉取检查：仓库公开不代表镜像包公开，旧版检查不能代替新版。用户部署环境的扫码、文件、下载及重建恢复验收单独记录，实际范围见[验证记录](verification.md)。[GHCR 权限说明](https://docs.github.com/en/packages/working-with-a-github-packages-registry/working-with-the-container-registry)。

