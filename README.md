# JM-Bot

QQ 中发送 `/jm <本子ID>`，机器人下载全部章节、确认完整后合成 PDF，并发回当前会话。使用独立 QQ 小号和两个容器，保留 NAS 上原有 AstrBot。

**1.1.0-rc.1 是统一管理页的候选版本。** 新版只需在 Compose 填一个管理密码，随后在同一个网页完成 QQ 扫码、选群、保存权限和测试 PDF。内部连接口令由程序生成，无需手填。候选版的检查和发布状态见[验证记录](docs/verification.md)；完成 NAS、真实 QQ 收发及下载验收前不标为稳定版。

## 1. 在 UGOS 部署

NAS 只运行已经打包好依赖的镜像，不需要安装 Python、pip、插件或上传源码。镜像必须已发布并通过匿名拉取验证；候选镜像尚未发布时，等待发布后再部署。

1. 准备新 QQ 小号，用主号添加它为好友；需要群聊时，把小号加入目标群。
2. 在 UGOS 的 Docker 应用创建独立项目 `jm-bot`，选择独立项目目录。确认 NAS 端口 **16099** 空闲，有冲突只改配置中 `16099:8080` 的左侧数字。
3. 在项目目录预建 `data`、`napcat-config`、`qq-data`、`downloads` 四个文件夹。
4. 复制以下配置，只把 `ADMIN_PASSWORD` 的占位符换成自己的管理密码：**至少 12 个字符，建议使用密码管理器生成的随机英文字母和数字**。妥善保存，不要把含真实密码的 Compose 提交到 GitHub。使用字母数字也能避免 `$` 被 Compose 当作变量替换。
5. 粘贴到项目的 Compose 配置区并部署，查看两个容器日志。

```yaml
name: jm-bot

services:
  jm-bot:
    image: ghcr.io/epiphany-leave/jm-bot:1.1.0-rc.1
    platform: linux/amd64
    restart: unless-stopped
    environment:
      TZ: Asia/Shanghai
      ADMIN_PASSWORD: "REPLACE_WITH_YOUR_ADMIN_PASSWORD"
    ports:
      - "16099:8080"
    volumes:
      - ./data:/data
      - ./napcat-config:/napcat-config
      - ./downloads:/downloads
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
```

只有 JM-Bot 的管理页端口映射到 NAS。NapCat 的 `6099`、OneBot 的 `3001` 只在项目内部使用，无需额外开放。JM-Bot 先准备内部连接配置并启动管理页，NapCat 在该页面健康后启动；QQ 尚未登录不会阻止管理页打开。

本版面向 x86-64 NAS，镜像平台是 `linux/amd64`。`./` 指所选的项目目录，两个容器共用项目自己的网络，服务名不与其他项目冲突。UGOS 按钮名称可能随系统版本变化，参见[官方 Compose 指南](https://support.ugnas.com/detail/article/zh-CN/411)。

## 2. 在一个网页完成设置

1. 打开 `http://NAS的局域网IP:16099/`，使用 Compose 中的 `ADMIN_PASSWORD` 登录。新版地址是根路径，不是旧的 `/webui`。管理页保留局域网访问即可。
2. 在 QQ 登录区域显示并扫描二维码，使用新小号完成手机确认；二维码过期时刷新。确认页面显示的 QQ 号正确且在线。
3. 刷新群列表，勾选允许使用机器人的群，保存权限。拉机器人入群不会自动授权，勾选并保存后才生效。
4. 默认使用“指定群内所有成员可用”。需要逐人限制时，在页面关闭群全员模式，并填写允许的用户 QQ 号。私聊始终检查用户白名单；未填写用户时禁止私聊。没有选中的群时，所有群都禁止下载。
5. 选择已授权的群，在页面发送普通测试 PDF。需要私聊时，先保存对应用户白名单，再向该用户发送测试 PDF。这一步不访问漫画站点，成功后在 QQ 打开文件。
6. 在已授权会话中发送 `/jm`，应收到用法提示，然后按下一节测试真实下载。

管理密码从 Compose 读取，页面不提供改密码入口。需要修改时，在 Compose 更新 `ADMIN_PASSWORD` 并重新创建 JM-Bot 容器，再用新密码登录；普通重启不会更新容器环境变量。不要手填内部 token 或修改 NapCat 网络配置。

## 3. 验证下载与权限

用自己有权访问的有效 ID 发送 `/jm <ID>`，不要把尖括号一起发出。`/jm 11451` 仅展示格式，不保证这个 ID 可下载。

| 场景 | 预期 |
| --- | --- |
| 群全员模式下，成员在已选群使用 | 完整下载后把 `jm_<ID>.pdf` 发到当前群 |
| 关闭群全员模式 | 群号和调用者 QQ 号都必须已授权 |
| 白名单用户私聊 | PDF 发回该用户 |
| 未选中的群、未授权私聊用户 | 不开始下载 |
| 空 ID、非数字、零、多个 ID | 回复用法，不开始下载 |
| 正在下载、合成或上传时再次调用 | 回复忙碌，不排队 |
| 缺页、下载或合成失败 | 报错，不发送不完整 PDF |
| 上传收到成功确认 | 清理本次图片和 PDF |
| 上传失败、超时或连接中断 | 留存文件，不自动重发 |

收到 PDF 后检查它能打开、章节及页码顺序正确、页数完整。网络请求有超时和有限重试，整项任务没有强制时间限制。重启会中断在途任务，不自动续传或重发；请等任务结束再更新、重建或修改相关配置。

## 升级、备份与恢复

从 1.0.x 升级到统一管理页时：

1. 在 UGOS 停止旧的 **JM-Bot 项目**，释放它占用的 `16099`；不要停止或修改原有 AstrBot 项目。
2. 用文件管理备份旧 Compose，以及项目里的 `napcat-config`、`qq-data`、`downloads`。这些备份可能包含账号登录状态和连接口令，保存在自己的 NAS 中。
3. 在同一项目目录新增 `data` 文件夹。将 Compose **完整替换**为新版并填写 `ADMIN_PASSWORD`，保留原来的三个数据目录；不要把旧 `ONEBOT_TOKEN`、`WEBUI_TOKEN`、群号环境变量继续粘入新版。
4. 重新创建项目容器，打开统一管理页，核对账号、重新选择允许的群和用户，再做普通 PDF、权限与下载验收。新版 Compose 不继承旧容器中的白名单，请在网页重新确认并保存。

| 项目目录内文件夹 | 用途 |
| --- | --- |
| `data/` | 网页保存的权限及程序生成的内部连接配置 |
| `napcat-config/` | NapCat 配置，两个容器共用 |
| `qq-data/` | 小号登录状态 |
| `downloads/` | 任务图片、PDF；失败或上传未确认时留存 |

网页中的保存操作会写入持久化目录，重建容器时继续挂载这四个文件夹。不要手改 `data/config.json`，也不要把它上传到公开仓库或作为排查附件公开。

失败后按回复或日志里的路径，在 UGOS 文件管理的 `downloads/` 对应任务目录取回 PDF。断线时可能收不到错误回复，应查看日志和任务文件。先在 QQ 确认文件是否已经到达，再考虑手动重新执行；留存文件取回后自行删除，不定时清除。

回退时停止本次 JM-Bot 项目，保留四个目录；需要恢复旧版时使用事先备份的 Compose 与配置。删除项目或容器时不要勾选删除 NAS 数据。

## 排查

| 现象 | 先检查 |
| --- | --- |
| 镜像拉取 denied/not found | 候选版本是否已发布、GHCR 包是否公开、NAS 能否联网拉取 |
| 管理页打不开 | 访问根路径、端口冲突、JM-Bot 容器健康状态和日志 |
| ADMIN_PASSWORD 启动错误 | 替换占位符、至少 12 字符、保存 Compose 后重新创建容器 |
| 登录失败 | 使用 Compose 中的管理密码，旧 NapCat 网页密码不用于新版入口 |
| 网页已打开但 QQ 离线 | 等待 NapCat 启动，检查其日志，再刷新二维码并确认手机登录 |
| 群列表没有目标群 | 确认小号已入群、账号在线，然后刷新列表 |
| `/jm` 无回复 | 指令发给正确账号、页面在线状态、群选择及用户权限已保存 |
| PDF 发送失败 | 先用页面的普通 PDF 测试，再检查目录权限和 QQ 返回错误 |
| 下载失败 | JM-Bot 日志、容器联网、上游站点和磁盘空间 |

NapCat 按 UID/GID `1000:1000` 运行。`napcat-config`、`qq-data` 要可写，`downloads` 和父目录要可遍历、PDF 要可读。JM-Bot 创建的任务目录为 `0755`、PDF 为 `0644`；NAS 的文件夹 ACL 仍可能额外限制访问，必须实际发送测试 PDF。不要直接套用别人截图中的用户组 `10`，或盲目修改其他项目的权限。

## 开发与发布

开发机使用 Python 3.12。浏览器检查只在开发和 CI 安装 Playwright，不进入运行镜像：

```text
python -m pip install -r requirements.txt
python -m unittest discover -s tests -v
python -m pip install playwright==1.64.1
python -m playwright install chromium
python scripts/webui_browser.py
python scripts/package.py
```

Linux 与本地 Docker 环境下检查成品镜像：

```text
docker build --platform linux/amd64 -t jm-bot:ci .
python scripts/container_smoke.py --image jm-bot:ci
```

自动测试使用本机 NapCat/OneBot 替身，不登录真实 QQ、不访问漫画站点。成品容器检查在没有 NapCat 的情况下验证网页健康、静态资源、认证、权限保存及重建恢复，另检查 UID1000 只读文件访问并记录镜像大小和空闲内存。空闲数据不代表下载峰值，也不包括 NapCat/QQ。

[GitHub Actions](https://github.com/Epiphany-Leave/jm-bot/actions) 在 `main` 推送和手动运行时测试构建；版本标签可发布 `v1.1.0-rc.1` 这样的候选版。发布后还会在未登录 GHCR 的环境匿名拉取并检查成品镜像。仓库公开不等于镜像包公开；匿名拉取成功后才能宣布可直接部署。[GitHub 镜像权限说明](https://docs.github.com/en/packages/working-with-a-github-packages-registry/working-with-the-container-registry)。

源代码包和部署包只包含公开代码、示例配置及普通测试 PDF，不包含 NAS 运行数据。设计取舍见[独立服务决策](docs/decisions-standalone.md)，各版本证据见[验证记录](docs/verification.md)。
