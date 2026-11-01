# JM-Bot

QQ 中发送 `/jm <本子ID>`，机器人下载全部章节、确认完整后合成 PDF，并发回当前会话。使用独立 QQ 小号和独立 Docker 项目，保留 NAS 上原有 AstrBot。

**当前源码面向候选版 `1.1.0-rc.3`，镜像尚未发布。** 本版增加下载设置、进度、停止下载，以及运行在 NAS 上的 Mihomo 独立代理。下面 Compose 是 rc.3 的目标部署配置；请等发布和匿名拉取检查成功后再升级，不能用 rc.2 镜像代替 rc.3。此前版本的发布和 HTTPS 检查不代表 rc.3 已完成真实 NAS 验收。[版本验证记录](docs/verification.md)。

只需填写一个管理密码，在同一网页完成 QQ 扫码、选群、权限、测试 PDF 和下载线路设置。NAS 不需要安装 Python、pip、VPN 图形客户端或 Clash Verge；代理运行后也不需要电脑一直开机。

## 1. 在 UGOS 部署

1. 准备新 QQ 小号，用主号添加它为好友；需要群聊时，把小号加入目标群。
2. 在 UGOS 的 Docker 应用创建独立项目 `jm-bot`，选择独立项目目录。确认 NAS 端口 **16099** 空闲；有冲突只改 `16099:8080` 的左侧数字。
3. 在项目目录预建 `data`、`napcat-config`、`qq-data`、`downloads`、`proxy-data` 五个文件夹。
4. 复制以下完整配置，只把 `ADMIN_PASSWORD` 占位符换成自己的管理密码：**至少 12 个字符，建议随机英文字母和数字**。妥善保存，不要把含真实密码的 Compose 提交到 GitHub；字母数字也能避免 `$` 被 Compose 当作变量替换。
5. 确认 rc.3 已发布后部署，查看三个容器日志。默认直连，不导入订阅也能使用直连下载。

```yaml
name: jm-bot

services:
  jm-bot:
    image: ghcr.io/epiphany-leave/jm-bot:1.1.0-rc.3
    platform: linux/amd64
    restart: unless-stopped
    environment:
      TZ: Asia/Shanghai
      # 唯一需要填写的值：至少 12 个字符，建议使用随机英文字母和数字。
      ADMIN_PASSWORD: "REPLACE_WITH_YOUR_ADMIN_PASSWORD"
    ports:
      - "16099:8080"
    volumes:
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

只有 JM-Bot 管理页映射到 NAS。NapCat 的 `6099`、OneBot 的 `3001`，以及 Mihomo 的控制和代理端口都不对 NAS 开放。JM-Bot 先生成内部配置并启动网页，NapCat、Mihomo 在网页健康后启动；QQ 尚未登录不会阻止网页打开。

JM-Bot 与 NapCat 使用项目默认网络；JM-Bot 与 Mihomo 使用 `proxy` 网络，NapCat 不加入后者。代理只供本项目的 JM 下载与检测使用，不接管 QQ，也不改变 NAS 其他应用的网络。配置不使用 TUN、主机网络或 Docker socket。

本版面向 x86-64 NAS，JM-Bot 和 Mihomo 固定 `linux/amd64`；Mihomo 固定为 `metacubex/mihomo:v1.19.33`。`./` 指项目目录。UGOS 按钮名称可能随系统版本变化，参见[官方 Compose 指南](https://support.ugnas.com/detail/article/zh-CN/411)。

## 2. 在一个网页完成设置

1. 打开 `http://NAS的局域网IP:16099/`，用 Compose 中的管理密码登录。新版入口是根路径 `/`，旧 `/webui` 地址会跳转。
2. 打开“QQ 登录”，用小号手机 QQ 扫描二维码并确认；过期时刷新。核对页面账号正确且在线。
3. 打开“使用权限”，刷新群列表，勾选允许使用的群并保存。拉机器人入群不会自动授权。
4. 默认选中群内所有成员可用。若改为仅指定用户，调用者 QQ 号和群号都必须授权；私聊始终检查用户白名单，用户列表为空时禁止所有私聊。未选中的群始终禁止使用。
5. 回到概览，选择已授权的群或用户，发送普通测试 PDF，并在 QQ 中打开。这一步只验证 QQ 文件回传，不访问漫画站点。
6. 打开“下载设置”，先保留直连，填写自己有权访问的有效本子 ID，点击“检测下载线路”。只检查接口和一张完整图片，不下载整本、不发 QQ；检测使用**已保存**的线路，未保存的修改不会参与检测。
7. 检测成功后，在已授权会话发送 `/jm <ID>`，按下一节检查整本结果。

管理密码只从 Compose 读取，网页不提供改密码入口。修改时更新 `ADMIN_PASSWORD` 并重新创建 JM-Bot 容器；普通重启不会更新环境变量。内部 QQ token、代理口令和控制口令由程序生成并私下保存，用户不需要填写第二个密码或手改 NapCat 网络设置。

通过 UGOS 的 HTTPS 外网地址访问时，先完成 UGOS 本身的登录，再打开应用根路径并使用管理密码。不要使用 NapCat 日志里的 WebUI Token。网关应保留原始 Host（域名和端口）；程序不会盲目信任 `X-Forwarded-*` 请求头。

### 直连失败时使用 NAS 独立代理

1. 在“下载设置”展开“NAS 独立代理”，导入**自己的** Clash 节点订阅链接，或上传不超过 **2 MiB** 的 `.yaml`、`.yml`、`.txt` 文件，二选一。Clash 配置只提取节点，不套用文件里的端口、控制器、TUN 或规则；其他节点格式由固定版本 Mihomo 的原生解析器处理，并非所有 VPN 配置都支持。
2. 导入会实际测试代理连通性。显示连接正常后，可保持“自动”或选择具体节点，再点击“选择节点”。连通性测试通过不代表 JM 图片一定可用。
3. 将使用方式改为“NAS 独立代理”，点击“保存下载设置”。然后填写有效 ID，点击“检测下载线路”，确认接口、图片都通过后再发 `/jm`。
4. 订阅更新可点“刷新节点”；通过文件导入的配置没有更新链接，需要重新上传。订阅链接下载受阻时，可先在能访问订阅的设备导出节点文件，再上传到本页，不需要把电脑设置为代理服务器。

导入不会自动切换下载模式。代理故障时不会悄悄回退直连，可手动选择直连并保存。管理页不显示订阅凭据，链接导入成功后清空输入框；订阅内容和口令仍保存在 NAS 私有目录中。容器冷重启后代理状态可能暂待验证，点击“选择节点”重新应用节点，或在已保存代理模式下检测线路，可触发连接验证。

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

## 3. 验证下载与权限

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

### 从 rc.2 或 rc.1 升级到 rc.3

**不能只修改 JM-Bot 镜像标签。** 本版新增 Mihomo 服务、`proxy` 网络和 `./proxy-data` 挂载，需要完整替换 Compose。

1. 等本次任务结束，停止自己的 JM-Bot 项目；不要操作原有 AstrBot 项目。
2. 备份旧 Compose 和 `data`、`napcat-config`、`qq-data`、`downloads` 四个目录。保留原管理密码和项目目录。
3. 在同一项目目录新增 `proxy-data`，将 Compose **完整替换**为本文配置，再填回原 `ADMIN_PASSWORD`。保留端口 `16099:8080`（或此前选择的端口）及四个旧目录挂载，不删除旧数据。
4. 确认 rc.3 镜像已发布后，拉取并重新创建整个项目，核对三个服务。账号与权限应从原目录恢复，下载默认直连。
5. 登录网页核对 QQ 与权限，发送普通测试 PDF，再检测下载线路和整本文件。需要代理时，按上面的导入、测试、切换并保存流程设置。

从 1.0.x 升级也要完整替换 Compose，并新增 `data` 和 `proxy-data`；保留旧 `napcat-config`、`qq-data`、`downloads`。不要带入旧 `ONEBOT_TOKEN`、`WEBUI_TOKEN` 或白名单环境变量，在网页重新确认并保存群与用户权限。

| 项目目录 | 用途与备份要求 |
| --- | --- |
| `data/` | 权限、下载设置和程序生成的内部口令；私密备份 |
| `napcat-config/` | NapCat 配置，JM-Bot 与 NapCat 共用；私密备份 |
| `qq-data/` | 小号登录状态；私密备份 |
| `downloads/` | 在途图片、PDF；已生成但发送未确认的 PDF 需手动取回 |
| `proxy-data/` | Mihomo 配置、节点与订阅凭据；私密备份 |

重建容器时继续挂载这五个目录。不要手改 `data/config.json`，不要公开上传配置、订阅、口令或日志包；源码和部署包不会包含这些运行数据与日志。

发送未确认时，按页面或日志路径从 NAS 的 `downloads/` 取回 PDF。先确认 QQ 是否已收到，再决定是否手动重试；留存 PDF 不定时删除，取回后自行清理。缺页或停止的下载不会保留一份可发送的半本 PDF。

回退前停止项目，保留五个目录，使用升级前备份的 Compose 与配置恢复，不要把新版配置直接覆盖回旧版。删除项目或容器时不要勾选删除 NAS 数据。

## 排查

| 现象 | 先检查 |
| --- | --- |
| 镜像拉取 denied/not found | 本版是否已发布并通过匿名拉取；GHCR 包公开性及 NAS 网络 |
| 管理页打不开 | 根路径、端口冲突、JM-Bot 健康状态和日志 |
| ADMIN_PASSWORD 启动错误 | 替换占位符、至少 12 字符，保存后重新创建容器 |
| HTTPS 外网登录报 403 | 完成 UGOS 登录后访问根路径；检查网关保留 Host，可先用局域网入口排查 |
| 页面打开但 QQ 离线 | 等待 NapCat 启动，查看日志，刷新二维码并手机确认 |
| 群列表缺少目标群 | 小号已入群、账号在线，再刷新列表 |
| `/jm` 无回复 | 账号正确、页面在线、群及用户权限已保存 |
| 普通测试 PDF 发送失败 | QQ 状态、目录权限与上传确认；先检查是否已收到，避免重复发送 |
| 代理服务不可用 | 是否完整替换 Compose，Mihomo 是否启动，`proxy-data` 是否正确挂载 |
| 订阅链接导入失败 | 在可访问订阅的设备导出节点文件后上传；检查格式和 2 MiB 限制 |
| 重启后代理待验证 | 选择节点并应用，或在代理模式下检测线路；已保存不代表当前健康 |
| 代理正常但下载失败 | 用有效 ID 检测接口和图片；检查节点能否访问上游、图片源及磁盘空间 |
| 点停止后仍忙碌 | 底层请求或线程尚未退出；停止不是硬中断，不要反复发任务 |

NapCat 按 UID/GID `1000:1000` 运行。`napcat-config`、`qq-data` 要可写，`downloads` 及父目录要可遍历、PDF 要可读。任务目录为 `0755`、PDF 为 `0644`；NAS ACL 仍可能限制访问，必须实际发送测试 PDF。不要直接套用别人截图中的用户组或盲目修改其他项目权限。

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

Windows 已安装 Edge 时可运行 `python scripts/webui_browser.py --channel msedge`。浏览器测试保留登录、二维码、权限、PDF 和 HTTPS 网关流程，并检查下载设置、代理导入选点、图片检测、停止及移动布局；本地 HTTP、QQ 和 Mihomo 控制端替身不证明真实 NAS 或真实上游可用。

Linux 与本地 Docker 环境另检查真实 Mihomo 和成品镜像：

```text
python scripts/mihomo_smoke.py --image metacubex/mihomo:v1.19.33
docker build --platform linux/amd64 -t jm-bot:ci .
python scripts/container_smoke.py --image jm-bot:ci
```

`mihomo_smoke.py` 用真实核心与本地受控 HTTP 节点检查原生解析、自动/指定节点、失败回滚、重启恢复和不可用时拒绝代理请求，不使用私人订阅或访问真实漫画站点。成品容器检查覆盖离线网页启动、认证、保存恢复及只读文件访问；空闲内存不代表下载峰值，也不包含 QQ 与代理整体开销。

[GitHub Actions](https://github.com/Epiphany-Leave/jm-bot/actions) 在推送和手动运行时测试构建；版本标签用于发布候选镜像。发布后还需匿名拉取检查：仓库公开不代表镜像包公开，旧版检查不能代替新版。真实 NAS 的扫码、文件、下载及重建恢复验收完成前保持候选版。[GHCR 权限说明](https://docs.github.com/en/packages/working-with-a-github-packages-registry/working-with-the-container-registry)。

设计取舍见[独立服务决策](docs/decisions-standalone.md)，各版本证据见[验证记录](docs/verification.md)。
