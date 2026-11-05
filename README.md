# JM-Bot

QQ 中发送 `/jm <ID>`，下载全部章节、确认完整后合成 PDF 并发回当前会话；`/jm-s <ID>` 查询标题、作者和简介。使用独立 QQ 小号，通过 Docker Compose 部署。

当前 **`1.1.0-rc.5` 为待发布候选版**，能否拉取及现场验收范围以[验证记录](https://github.com/Epiphany-Leave/jm-bot/blob/main/docs/verification.md)为准。只填一个管理密码；网页完成扫码、权限、测试 PDF 和线路设置，无需安装 Python 或代理客户端。镜像面向 x86-64 / `linux/amd64`。

## 初次部署

**Linux**：按[官方说明](https://docs.docker.com/engine/install/)安装并启动 Docker Engine 与 Compose 插件，确认 `docker compose version` 可用。

**Windows**：安装并启动 [Docker Desktop](https://docs.docker.com/desktop/setup/install/windows-install/)，按官方要求启用 WSL 2 和虚拟化，选择 **Linux 容器**，确认 `docker compose version` 可用。使用期间保持 Docker Desktop 运行。

1. 选择有写权限的独立项目目录，将下方完整配置保存为 `compose.yaml`（Windows 不要存成 `compose.yaml.txt`）。
2. 只替换 `ADMIN_PASSWORD`：**至少 12 个字符**，建议随机英文字母和数字；保管好密码，不公开上传填好密码的配置。
3. 在该目录打开终端（Windows 可用 PowerShell），运行 `docker compose up -d`；缺失的五个数据目录自动创建，无需手动新建。
4. 打开 [http://localhost:16099/](http://localhost:16099/)。其他设备用 `http://部署主机IP:16099/`，并允许端口通过主机防火墙。端口冲突时只改 `16099:8080` 左侧数字。

两种系统共用这一份 [compose.yaml](compose.yaml)，不需要分别创建三个项目；首次启动需等待镜像拉取完成，目标版本未发布时无法拉取。

```yaml
name: jm-bot

services:
  jm-bot:
    image: ghcr.io/epiphany-leave/jm-bot:1.1.0-rc.5
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

## 网页设置

准备独立 QQ 小号，用主号添加为好友，需要群聊时先将小号加入目标群。

1. 用 Compose 中的管理密码登录根路径 `/`，打开“QQ 登录”，用小号手机 QQ 扫码并确认，检查账号正确且在线。
2. 打开“使用权限”，刷新列表、勾选允许的群并保存。默认选中群的所有成员可用；改为仅指定用户时，群号和调用者 QQ 号均须授权。私聊始终检查用户白名单，列表为空时禁止私聊；未选群始终禁止使用。
3. 回概览选已授权群或用户，发送普通测试 PDF，并在 QQ 打开；这一步只验证 QQ 文件回传，不访问漫画站点。
4. 在“下载设置”先保留直连，用自己有权访问的有效 ID“检测下载线路”。检测使用**已保存**设置，只查接口和一张完整图片，不下载整本、不发 QQ。
5. 直连失败时，展开“内置代理”，导入自己的 Clash 节点订阅链接，或上传不超过 **2 MiB** 的 `.yaml`、`.yml`、`.txt` 文件；选择节点并应用，将使用方式设为“内置代理”后**保存下载设置**，再检测线路。导入成功不会自动切换下载方式；链接导入失败可改用文件，文件导入后需重新上传来更新。
6. 线路检测通过后，在已授权 QQ 会话发送下方命令。

## QQ 命令

`ID` 是一个正整数，实际发送时不要写尖括号。

| 命令 | 用途 |
| --- | --- |
| `/jm <ID>` | 下载全部章节，完整后发送 `jm_<ID>.pdf` 到当前群或私聊 |
| `/jm-s <ID>` | 查询标题、作者、简介；未提供的信息会标明缺失，不补造内容 |

两种命令都遵守网页权限，并共用一个任务位置；忙碌时提示稍后重试，不排队。缺页、失败或主动停止不发送不完整 PDF。网络请求阶段可在网页停止，需等待已启动请求退出。上传未确认时保留 PDF，不自动重发；先确认 QQ 是否已收到，再从 `downloads/` 取回。

## 数据与排查

`./` 指保存 Compose 的项目目录：`data/` 存权限、设置和内部口令，`napcat-config/` 存 NapCat 配置，`qq-data/` 存 QQ 登录状态，`downloads/` 存图片和 PDF，`proxy-data/` 存节点和订阅凭据。不要公开这些数据，也不要用空目录替换已有目录。内置代理只用于本项目的网络请求，不接管 QQ 或宿主机其他应用。

| 现象 | 先检查 |
| --- | --- |
| 镜像 denied/not found | 目标候选版是否已发布、GHCR 可访问；查看验证记录 |
| 网页打不开 | Docker 和容器是否运行、端口是否冲突；在项目目录运行 `docker compose ps`、`docker compose logs --tail=100` |
| 密码启动错误 | 已替换占位符且至少 12 字符；改 Compose 后需重新创建容器 |
| QQ 离线或命令无回复 | 刷新二维码并手机确认，检查账号在线、已入群及网页权限已保存 |
| 测试 PDF 失败 | QQ 状态与项目目录读写权限；先确认是否已收到，避免重复发送 |
| 代理可连但下载失败 | 用有效 ID 检测接口和图片，检查节点、磁盘空间及页面具体错误 |

更多说明见[维护文档](docs/maintenance.md)。
