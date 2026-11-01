# JM-Bot

QQ 中发送 `/jm <本子ID>`，机器人下载全部章节、确认完整后合成 PDF，并发回当前会话。使用独立 QQ 小号和独立 Docker Compose 项目，可与原有 AstrBot 并存。

本文面向待发布候选版 **`1.1.0-rc.4`**：简化跨平台部署，并改进订阅获取及错误提示。镜像是否已发布、可匿名拉取，以[版本验证记录](docs/verification.md)为准。实际订阅、QQ 收发和漫画下载仍需在自己的环境验收，通过前不标记稳定版。

只需填写一个 `ADMIN_PASSWORD`，在同一网页完成 QQ 扫码、选群、权限、测试 PDF 和下载线路设置。运行环境不需要安装 Python、pip、VPN 图形客户端或 Clash Verge；内置代理与机器人运行在同一部署主机上。

## Linux 部署

1. 按发行版的[官方 Docker Engine 安装说明](https://docs.docker.com/engine/install/)安装 Docker Engine 与 Compose 插件，启动 Docker，确认 `docker compose version` 可用。本项目发布镜像面向 x86-64 / `linux/amd64`。
2. 选择一个有写权限的独立项目目录，将下方完整配置保存为 `compose.yaml`。只修改 `ADMIN_PASSWORD`：**至少 12 个字符，建议随机英文字母和数字**；保管好密码，不要把填好密码的文件提交到 GitHub。
3. 在该项目目录运行 `docker compose up -d`。缺失的五个数据目录会自动创建，无需手动新建。若端口 **16099** 已被占用，只修改 `16099:8080` 左侧数字。
4. 在本机打开 [http://localhost:16099/](http://localhost:16099/)，从其他设备访问时使用 `http://部署主机IP:16099/`。按下方共用网页流程完成设置。

首次拉取需等待目标版本发布。默认使用直连，不导入订阅也可检测和下载。

## Windows 部署

1. 安装并启动 [Docker Desktop for Windows](https://docs.docker.com/desktop/setup/install/windows-install/)，推荐使用 WSL 2 后端，选择 **Linux 容器**。按官方要求启用 WSL 2 和虚拟化，确认 `docker compose version` 可用。
2. 在有写权限的独立项目目录保存下方完整配置为 `compose.yaml`，填入自己的 `ADMIN_PASSWORD`。不要保存成 `compose.yaml.txt`。
3. 在该目录打开 PowerShell 或终端，运行 `docker compose up -d`。不需要手动创建五个数据目录，也不需要在 Windows 安装 Python 或代理客户端。
4. 打开 [http://localhost:16099/](http://localhost:16099/)，按共用网页流程完成设置。需要其他设备访问时使用 Windows 主机 IP，并允许该端口通过主机防火墙。

Docker Desktop 和三个容器必须保持运行；这台主机关闭后，机器人和内置代理也会停止。

## 共用 Compose 配置

以下配置与仓库 [compose.yaml](compose.yaml) 一致。两种系统均只需保存文件、填写密码并启动，无需分别创建三个项目。

```yaml
name: jm-bot

services:
  jm-bot:
    image: ghcr.io/epiphany-leave/jm-bot:1.1.0-rc.4
    platform: linux/amd64
    restart: unless-stopped
    environment:
      TZ: Asia/Shanghai
      # 唯一需要填写的值：至少 12 个字符，建议使用随机英文字母和数字。
      ADMIN_PASSWORD: "REPLACE_WITH_YOUR_ADMIN_PASSWORD"
    ports:
      - "16099:8080"
    volumes:
      # 缺失的相对目录由 Compose 自动创建，无需手动新建；已有目录直接复用。
      - ./data:/data
      - ./napcat-config:/napcat-config
      - ./downloads:/downloads
      - ./proxy-data:/proxy-data
    networks:
      - default
      - proxy
    healthcheck:
      test: ["CMD", "python", "-c", "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8080/healthz', timeout=3).read()"]
      interval: 10s
      timeout: 5s
      retries: 3
      start_period: 10s

  napcat:
    image: mlikiowa/napcat-docker:v4.18.42
    restart: unless-stopped
    depends_on:
      jm-bot:
        condition: service_healthy
    environment:
      TZ: Asia/Shanghai
      MODE: ws
      NAPCAT_UID: "1000"
      NAPCAT_GID: "1000"
    volumes:
      - ./napcat-config:/app/napcat/config
      - ./qq-data:/app/.config/QQ
      - ./downloads:/downloads:ro

  mihomo:
    image: metacubex/mihomo:v1.19.33
    platform: linux/amd64
    restart: unless-stopped
    depends_on:
      jm-bot:
        condition: service_healthy
    command: ["-d", "/proxy-data", "-f", "/proxy-data/config.yaml"]
    volumes:
      - ./proxy-data:/proxy-data
    networks:
      - proxy

networks:
  proxy:
    driver: bridge
```

只有 JM-Bot 管理页向部署主机发布端口。NapCat 的 `6099`、OneBot 的 `3001`，以及 Mihomo 的控制和代理端口都不对宿主机开放。JM-Bot 先生成内部配置并启动网页，NapCat、Mihomo 在网页健康后启动；QQ 尚未登录不会阻止网页打开。

JM-Bot 与 NapCat 使用项目默认网络；JM-Bot 与 Mihomo 使用 `proxy` 网络，NapCat 不加入后者。内置代理供本项目使用，不接管 QQ，也不改变宿主机其他应用的网络。配置不使用 TUN、主机网络或 Docker socket。

NapCat 固定 `v4.18.42`，Mihomo 固定 `v1.19.33`，JM-Bot 和 Mihomo 使用 `linux/amd64`。`./` 指保存 Compose 的项目目录；短格式 bind 挂载会[自动创建不存在的宿主目录](https://docs.docker.com/reference/compose-file/services/#short-syntax-5)。五个路径仍是 `./data`、`./napcat-config`、`./qq-data`、`./downloads`、`./proxy-data`，不要用新的空目录替换已有数据。

## 共用网页设置

准备独立 QQ 小号，用主号添加它为好友；需要群聊时，先把小号加入目标群。

1. 打开 [http://localhost:16099/](http://localhost:16099/) 或 `http://部署主机IP:16099/`，用 Compose 中的管理密码登录。根路径 `/` 是概览，`/#permissions` 可直接进入使用权限；旧 `/webui` 地址兼容跳转。
2. 打开“QQ 登录”，用小号手机 QQ 扫描二维码并确认；过期时刷新。核对页面账号正确且在线。
3. 打开“使用权限”，刷新群列表，勾选允许使用的群并保存。拉机器人入群不会自动授权。
4. 默认选中群内所有成员可用。若改为仅指定用户，调用者 QQ 号和群号都必须授权；私聊始终检查用户白名单，用户列表为空时禁止所有私聊。未选中的群始终禁止使用。
5. 回到概览，选择已授权的群或用户，发送普通测试 PDF，并在 QQ 中打开。这一步只验证 QQ 文件回传，不访问漫画站点。
6. 打开“下载设置”，先保留直连，填写自己有权访问的有效本子 ID，点击“检测下载线路”。只检查接口和一张完整图片，不下载整本、不发 QQ；检测使用**已保存**的线路，未保存的修改不会参与检测。
7. 检测成功后，在已授权会话发送 `/jm <ID>`，按下一节检查整本结果。

管理密码只从 Compose 读取，网页不提供改密码入口。修改时更新 `ADMIN_PASSWORD` 并重新创建 JM-Bot 容器；普通重启不会更新环境变量。内部 QQ token、代理口令和控制口令由程序生成并私下保存，用户不需要填写第二个密码或手改 NapCat 网络设置。

通过 HTTPS 网关访问时，先完成网关要求的登录，再打开应用根路径并使用管理密码。不要使用 NapCat 日志里的 WebUI Token。网关应保留原始 Host（域名和端口）；程序不会盲目信任 `X-Forwarded-*` 请求头。

### 直连失败时使用内置代理

1. 在“下载设置”展开“内置代理”，导入**自己的** Clash 节点订阅链接，或上传不超过 **2 MiB** 的 `.yaml`、`.yml`、`.txt` 文件，二选一。Clash 配置只提取节点，不套用文件里的端口、控制器、TUN 或规则；其他节点格式由固定版本 Mihomo 的原生解析器处理，并非所有 VPN 配置都支持。
2. 导入会实际测试代理连通性。显示连接正常后，可保持“自动”或选择具体节点，再点击“选择节点”。连通性测试通过不代表 JM 图片一定可用。
3. 将使用方式改为“内置代理”，点击“保存下载设置”。然后填写有效 ID，点击“检测下载线路”，确认接口、图片都通过后再发 `/jm`。
4. 订阅更新可点“刷新节点”；通过文件导入的配置没有更新链接，需要重新上传。订阅链接下载受阻时，可先在能访问订阅的设备导出节点文件，再上传到本页，不需要把电脑设置为代理服务器。

订阅链接获取使用固定的 `mihomo/1.19.33` User-Agent，先直连尝试一次，最多 20 秒。失败后，只有已有成功保存的节点时才先验证旧代理，再经它尝试一次，最多 20 秒；整个获取流程（含旧代理验证）总计最多 60 秒。没有可用旧节点时可直接上传文件。页面会区分 HTTP 状态、超时、连接、TLS 或返回 HTML 等错误，并给出下一步提示，不显示完整订阅链接、代理口令或底层敏感异常。

导入失败保留原配置；导入成功不会自动切换漫画下载模式。订阅获取的直连与旧代理重试只服务于本次获取，不更改 QQ 或漫画下载的网络方式。已保存代理模式发生故障时不会悄悄回退直连，可手动选择直连并保存。

管理页不显示订阅凭据，链接导入成功后清空输入框；订阅内容和口令仍保存在项目目录的私有文件中。容器冷重启后代理状态可能暂待验证，点击“选择节点”重新应用节点，或在已保存代理模式下检测线路，可触发连接验证。

节点很多或大部分失效时，自动选择也可能超时。可以上传只保留少量已知可用节点的配置；已有订阅时，也可改选具体节点后重试。

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

概览和下载设置页显示已完成页数、总页数、图片源、阶段及停止原因。下载网络阶段可以点“停止下载”；PDF 合成、上传、普通测试 PDF 和配置操作不提供停止。停止和软时限需要等待已启动的下载线程退出，不能保证立即中断底层 I/O；退出前仍忙碌，不接下一项任务。代理、下载设置保存和检测应在空闲时操作；使用权限可照常保存，立即对后续请求生效。

## 验证下载与权限

用自己有权访问的有效 ID 发送 `/jm <ID>`，不要发送尖括号。`/jm 11451` 仅展示格式，不保证该 ID 可下载。

| 场景 | 预期 |
| --- | --- |
| 群全员模式下，成员在已选群调用 | 完整下载后把 `jm_<ID>.pdf` 发到当前群 |
| 仅指定群内用户模式 | 群号和调用者 QQ 号都必须授权 |
| 白名单用户私聊 | PDF 发回该用户 |
| 未授权群或用户 | 不开始下载 |
| 空 ID、非数字、零、多个 ID | 回复用法，不开始下载 |
| 正在下载、合成或上传时再次调用 | 回复忙碌，不排队 |
| 缺页、下载失败、主动停止或超时 | 不发送不完整 PDF，清理本次图片 |
| PDF 合成失败 | 不发送损坏或未完成的 PDF |
| 上传收到成功确认 | 清理本次图片和 PDF |
| PDF 已生成但上传失败、超时或连接中断 | 保留 PDF，不自动重发 |

收到 PDF 后检查能打开、章节和页码顺序正确、页数完整。重启会中断在途任务，不自动续传，不跨任务复用进度，也不自动重发；请等任务结束再升级、重建或修改部署配置。

## 升级、备份与恢复

### 从 rc.3 升级到 rc.4

在原项目目录升级，保留原管理密码和五个数据目录；无需新增或迁移目录。

1. 等当前任务结束，备份旧 Compose 和五个目录；可运行 `docker compose stop` 后备份，以免配置正在写入。
2. 在原目录将 Compose **完整替换**为本文配置，填回原 `ADMIN_PASSWORD`，保留原端口设置和五个相对挂载路径。不要删除数据或换成新的空目录。
3. 确认 rc.4 已发布并可匿名拉取后，在原项目目录运行：

   ```text
   docker compose pull
   docker compose up -d --force-recreate
   ```

4. 登录网页，核对 QQ、权限、下载方式和已保存节点，发送普通测试 PDF，再检测下载线路及整本文件。升级不会主动把已保存的下载方式改为直连。

从 rc.2、rc.1 或 1.0.x 升级同样完整替换 Compose，保留已有目录、密码和原端口；缺少的 `data` 或 `proxy-data` 会自动创建。不要带入 1.0.x 的旧 `ONEBOT_TOKEN`、`WEBUI_TOKEN` 或白名单环境变量，在网页重新确认并保存群与用户权限。

| 项目目录 | 用途与备份要求 |
| --- | --- |
| `data/` | 权限、下载设置和程序生成的内部口令；私密备份 |
| `napcat-config/` | NapCat 配置，JM-Bot 与 NapCat 共用；私密备份 |
| `qq-data/` | 小号登录状态；私密备份 |
| `downloads/` | 在途图片、PDF；已生成但发送未确认的 PDF 需手动取回 |
| `proxy-data/` | Mihomo 配置、节点与订阅凭据；私密备份 |

重建容器时继续挂载这五个目录。不要手改 `data/config.json`，不要公开上传配置、订阅、口令或日志包；源码和部署包不会包含这些运行数据与日志。

发送未确认时，按页面或日志显示的文件位置，从项目目录的 `downloads/` 取回 PDF。先确认 QQ 是否已收到，再决定是否手动重试；留存 PDF 不定时删除，取回后自行清理。缺页或停止的下载不会保留一份可发送的半本 PDF。

回退前停止项目，保留五个目录，恢复升级前备份的 Compose 和原密码，再拉取并重新创建服务。若旧版不兼容新配置，从备份恢复对应配置，不要删除运行数据。删除项目或容器时不要选择删除宿主机数据。

## 排查

| 现象 | 先检查 |
| --- | --- |
| 镜像拉取 denied/not found | 本版是否已发布并通过匿名拉取；GHCR 包公开性及部署主机网络 |
| 管理页打不开 | 根路径、端口冲突、JM-Bot 健康状态和日志 |
| ADMIN_PASSWORD 启动错误 | 替换占位符、至少 12 字符，保存后重新创建容器 |
| HTTPS 外网登录报 403 | 完成网关登录后访问根路径；检查网关保留 Host，可先用本机或局域网入口排查 |
| 页面打开但 QQ 离线 | 等待 NapCat 启动，查看日志，刷新二维码并手机确认 |
| 群列表缺少目标群 | 小号已入群、账号在线，再刷新列表 |
| `/jm` 无回复 | 账号正确、页面在线、群及用户权限已保存 |
| 普通测试 PDF 发送失败 | QQ 状态、目录权限与上传确认；先检查是否已收到，避免重复发送 |
| 代理服务不可用 | 是否完整替换 Compose，Mihomo 是否启动，`proxy-data` 是否正确挂载 |
| 订阅链接导入失败 | 按页面具体错误排查 HTTP 状态、DNS、TLS、连接或超时；没有可用旧节点时先上传文件，检查格式和 2 MiB 限制 |
| 重启后代理待验证 | 选择节点并应用，或在代理模式下检测线路；已保存不代表当前健康 |
| 代理正常但下载失败 | 用有效 ID 检测接口和图片；检查节点能否访问上游、图片源及磁盘空间 |
| 点停止后仍忙碌 | 底层请求或线程尚未退出；停止不是硬中断，不要反复发任务 |

NapCat 按 UID/GID `1000:1000` 运行。`napcat-config`、`qq-data` 要可写，`downloads` 及父目录要可遍历、PDF 要可读。任务目录为 `0755`、PDF 为 `0644`；宿主机 ACL 或 Docker Desktop 文件访问设置仍可能限制访问，必须实际发送测试 PDF。先选择有写权限的项目目录并检查具体报错，不要使用 `chmod 777` 或盲目修改其他项目权限。

### 可以在 UGOS 等系统的 Docker 管理界面部署吗？

可以使用兼容 Compose 的项目功能：在有写权限的独立项目目录保存完整配置，填写管理密码后启动。界面可能把启动称为“部署”或“创建项目”，相对路径仍以该项目目录为基准；缺失的五个目录由 bind 挂载自动创建。UGOS 的按钮和 HTTPS 网关登录方式可能随系统版本变化，主流程不依赖特定厂商。

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

[GitHub Actions](https://github.com/Epiphany-Leave/jm-bot/actions) 在推送和手动运行时测试构建；版本标签用于发布候选镜像。发布后还需匿名拉取检查：仓库公开不代表镜像包公开，旧版检查不能代替新版。用户部署环境的扫码、文件、下载及重建恢复验收完成前保持候选版。[GHCR 权限说明](https://docs.github.com/en/packages/working-with-a-github-packages-registry/working-with-the-container-registry)。

设计取舍见[独立服务决策](docs/decisions-standalone.md)，各版本证据见[验证记录](docs/verification.md)。
