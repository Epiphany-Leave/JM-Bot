# JM-Bot

只接收 `/jm <本子ID>` 的 QQ 机器人：下载全部章节、确认完整后合成 PDF，发回当前私聊或群聊。独立 Python 服务 + NapCat（连接 QQ 的程序），没有 AstrBot、模型配置或插件安装。

> **1.0.1 发布验证中。** 此版本增加“指定群内所有成员可用”，并细分口令启动错误。1.0.0 已公开并验证；1.0.1 须通过构建和匿名拉取后才能按下文部署。[验证记录](docs/verification.md)。真实 QQ 收发、NAS 权限和站点下载仍需现场验收。

## 1. 填配置并部署

Compose 是一次管理两个容器的配置。容器使用已经打包好依赖的镜像；NAS 只需粘贴配置，不用上传源码、安装 Python/pip 或编译。

1. 准备 QQ 小号，用主号添加它为好友；需要群聊时，把小号加进目标群。
2. 在 UGOS 的 Docker 应用创建独立项目 `jm-bot`，选择独立项目目录。确认 NAS 端口 `16099` 没被其他服务占用，有冲突只改下面 `16099:6099` 的左侧数字。
3. 在项目目录预建 `napcat-config`、`qq-data`、`downloads` 三个文件夹。
4. 复制下列完整配置。自行生成两段不同的随机口令（建议密码管理器生成 32 位字母数字）：分别替换 `ONEBOT_TOKEN`、`WEBUI_TOKEN`。前者用于两个程序之间的连接，后者用于 NapCat 网页登录。口令不要公开或提交到 GitHub。
5. 本示例启用 `GROUP_ALLOW_ALL: "true"`，将 `ALLOWED_GROUPS` 填为目标群的实际群号，即允许该群所有成员使用，无需逐个填 QQ 号。空群名单仍禁止所有群。`ALLOWED_USERS` 控制私聊，暂时留空即可禁止私聊。
6. 粘贴到项目的 Compose 配置区并部署，检查两个容器日志。

```yaml
name: jm-bot

services:
  jm-bot:
    image: ghcr.io/epiphany-leave/jm-bot:1.0.1
    platform: linux/amd64
    restart: unless-stopped
    depends_on:
      - napcat
    environment:
      TZ: Asia/Shanghai
      ONEBOT_WS_URL: ws://napcat:3001/
      ONEBOT_TOKEN: "REPLACE_WITH_YOUR_ONEBOT_TOKEN"
      GROUP_ALLOW_ALL: "true"
      ALLOWED_USERS: ""
      ALLOWED_GROUPS: ""
      DOWNLOAD_DIR: /downloads
    volumes:
      - ./downloads:/downloads

  napcat:
    image: mlikiowa/napcat-docker:v4.18.42
    restart: unless-stopped
    environment:
      TZ: Asia/Shanghai
      MODE: ws
      NAPCAT_UID: "1000"
      NAPCAT_GID: "1000"
      WEBUI_TOKEN: "REPLACE_WITH_YOUR_WEBUI_TOKEN"
    ports:
      - "16099:6099"
    volumes:
      - ./napcat-config:/app/napcat/config
      - ./qq-data:/app/.config/QQ
      - ./downloads:/downloads:ro
```

本版面向 x86-64 NAS（镜像平台 `linux/amd64`）。只有 NapCat 网页端口映射到 NAS；不要额外开放 `3001`。两个容器通过项目自己的内部网络通信，`napcat` 是服务名。`./` 指项目目录，不要复用其他机器人的数据目录。UGOS 按钮可能随系统版本变化，参见[官方 Compose 指南](https://support.ugnas.com/detail/article/zh-CN/411)。

### 群聊全员模式与以后收紧权限

例如目标群号为 `987654321`（请替换为自己的实际群号）：

```yaml
GROUP_ALLOW_ALL: "true"
ALLOWED_GROUPS: "987654321"
ALLOWED_USERS: ""
```

该群内任何成员都可发送 `/jm`；其他群、私聊仍不能使用。拉机器人入群不会自动把群号写入名单。多个群号用英文逗号分隔。需要私聊时，在 `ALLOWED_USERS` 填主号，例如 `"123456789"`。

以后要限制群内调用者，将 `GROUP_ALLOW_ALL` 改为 `"false"`，并在 `ALLOWED_USERS` 中填写允许的用户 QQ 号；群号保留在 `ALLOWED_GROUPS` 中，此时群聊同时检查两份名单。程序未设置 `GROUP_ALLOW_ALL` 时默认也是 `false`，兼容 1.0.0 的权限规则。修改后重新创建 `jm-bot` 容器，使环境变量生效。

## 2. 扫码登录 QQ

打开 `http://NAS的局域网IP:16099/webui`，用刚填的 `WEBUI_TOKEN` 登录 NapCat，按页面扫码登录小号。核对显示的账号并确认在线。管理网页保留局域网访问即可。

`WEBUI_TOKEN` 只在缺少网页配置文件时初始化口令；已有 `napcat-config/webui.json` 时，应在网页修改口令，修改 Compose 不会覆盖旧配置。[NapCat Docker 启动逻辑](https://github.com/NapNeko/NapCat-Docker/blob/main/entrypoint.sh)。

## 3. 配置一次连接口令

OneBot 是两个程序交换 QQ 消息和上传请求的接口；WebSocket（简称 WS）是保持连接的方式。本项目由 JM-Bot 主动连接 NapCat 的 WS 服务。

1. NapCat 网页进入 **网络配置**，编辑已有 **WebSocket 服务器/服务端**；没有时新建一个，避免重复启用同端口服务。
2. 启用服务，监听地址填 `0.0.0.0`、端口填 `3001`，token（连接口令）填 Compose 中的 `ONEBOT_TOKEN`，保存并按页面提示重启连接配置。
3. JM-Bot 的连接地址保持 `ws://napcat:3001/`。不要填 NAS IP、`localhost`，也不要选择反向 WS 客户端。
4. 查看 `jm-bot` 容器日志确认已连接。在已配置的目标群内发 `/jm`，应收到用法提示；这一步不下载内容。全员模式下，群内无需逐人登记；群外仍不能触发下载。私聊仅对 `ALLOWED_USERS` 中的用户开放。

`MODE=ws` 只初始化缺失的 OneBot 配置，**不会把 Compose 的 `ONEBOT_TOKEN` 自动填进 NapCat**，所以首次仍需上述一次网页操作。初次部署时连接失败并重试属于待配置状态；保存正确 token 后应恢复。[官方 WS 示例](https://github.com/NapNeko/NapCat-Docker/blob/main/compose/ws.yml)。

## 4. 先用普通 PDF 检查上传

这一步不下载漫画，先验证共享目录与 QQ 文件通道。

1. 从仓库下载普通的 [test.pdf](samples/test.pdf)，用 UGOS 文件管理放到项目目录 `downloads/test.pdf`。不需要上传源码或插件。
2. NapCat 网页打开 **调试 → HTTP 调试**（网页自带接口测试页，无需增加 HTTP 服务或开放端口），选 `upload_private_file`。
3. 将示例 `user_id` 换成已加好友的主 QQ 号，发送一次：

   ```json
   {"user_id": 123456789, "file": "/downloads/test.pdf", "name": "test.pdf", "upload_file": true}
   ```

4. 检查接口成功，并在主 QQ 打开 PDF。需要群聊时，再选 `upload_group_file`，用 `group_id` 替换 `user_id` 并填写目标群号。
5. 成功后手动删除 `downloads/test.pdf`。网页调试不经过机器人的白名单，测试对象需自行核对。

两个容器读取的同一文件路径都是 `/downloads/test.pdf`；NapCat 的只读挂载允许读取上传。[NapCat 文件上传实现](https://github.com/NapNeko/NapCatQQ/blob/v4.18.42/packages/napcat-onebot/action/go-cqhttp/UploadPrivateFile.ts)。

## 5. 测试真实下载

用自己有权访问的有效 ID 发送 `/jm <ID>`，不要把尖括号一起发出。例如 `/jm 11451` 仅展示格式，不保证这个 ID 可下载。

| 场景 | 预期 |
| --- | --- |
| 白名单用户私聊 | 下载全部章节，完整后返回 `jm_<ID>.pdf` |
| 全员模式下，任意成员在白名单群 | PDF 发到当前群 |
| 关闭全员模式，白名单用户在白名单群 | PDF 发到当前群 |
| 群不在名单，或逐人模式下用户不在名单 | 不开始下载 |
| 空 ID、非数字、零、多个 ID | 回复用法，不开始下载 |
| 正在下载、合成或上传时又调用 | 回复忙碌，不排队 |
| 缺页、下载失败或合成失败 | 报错，不发送不完整 PDF |
| 上传收到成功确认 | 清理本次图片和 PDF |
| 上传失败、超时或连接中断 | 保留本次文件，不自动重发 |

打开收到的 PDF 检查页数、章节和页序。网络请求有超时和有限重试，整项任务没有强制时间限制。单任务运行，暂不提供取消、队列、搜索、批量或续传。重启会中断在途任务，不会自动恢复或重发。

## 排查、取回与恢复

| 项目目录内文件夹 | 用途 |
| --- | --- |
| `napcat-config/` | NapCat 网页及 OneBot 连接配置 |
| `qq-data/` | 小号登录状态 |
| `downloads/` | 任务图片、PDF；失败或上传未确认时留存 |

失败后按回复或 `jm-bot` 日志里的路径，在 UGOS 文件管理进入 `downloads/` 对应任务目录取回 PDF。断线时可能收不到错误回复，应检查日志和任务文件。先在 QQ 确认文件是否已经到达，再考虑手动重新执行，避免重复上传。留存文件不定时清除，取回后自行删除。

UID/GID 是 Linux 识别用户/用户组的数字。NapCat 默认用 `1000:1000` 运行；`napcat-config`、`qq-data` 要可写，`downloads` 及其父目录要可遍历，PDF 要可读。JM-Bot 以容器内 root 写文件，任务目录权限为 `0755`、PDF 为 `0644`，让 NapCat 能读。NAS 的 ACL（文件夹访问规则）仍可能额外限制访问，必须用第 4 步实测。不要直接套用别人截图中的用户组 `10`，也不要盲目修改其他项目权限。

| 现象 | 检查 |
| --- | --- |
| 镜像拉取 denied/not found | 发布是否完成、GHCR 包是否公开、版本是否存在；不要因此在 NAS 安装 pip |
| 启动报 ONEBOT_TOKEN 未设置/占位符/换行 | 检查 `jm-bot → environment → ONEBOT_TOKEN`，填写实际单行口令；保存 Compose 后重新创建容器，普通重启沿用旧环境变量。该错误发生在连接 NapCat 前，无需发送口令给他人排查 |
| NapCat 网页打不开 | 两容器运行状态、NAS IP、端口冲突和 NapCat 日志 |
| 401、鉴权失败、一直重连 | WS 服务端已启用，监听 `0.0.0.0:3001`，两端 token 完全一致 |
| `/jm` 无回复 | 小号在线、JM-Bot 连接成功、指令发给正确账号、用户/群白名单 |
| permission denied、PDF 上传失败 | 先做普通 PDF 测试，再核对相同挂载路径、目录权限与 QQ 返回错误 |
| 站点下载失败 | `jm-bot` 日志、容器联网、上游站点可用性与磁盘空间 |

改白名单或连接配置后，在 UGOS 应用 Compose 配置并重建 `jm-bot` 容器；普通重启不会载入新的环境变量。操作前等当前任务完成。

恢复时先停止本次 `jm-bot` 项目；如有本次失败的残留容器，只删除这个项目的容器并**保留三个挂载目录**，不要改 NAS 上其他 AstrBot 项目。用同一目录重新部署后，依赖已在镜像中，不需要重新安装；核对 QQ 登录、WS 连接，重做普通 PDF 与真实下载测试。删除容器时不要勾选删除 NAS 数据。原 AstrBot 插件数据不自动迁移。

## 开发、发布与验收记录

开发机使用 Python 3.12；下列容器检查需要 Linux 和本地 Docker（CI 也使用这个环境），运行：

```text
python -m pip install -r requirements.txt
python -m unittest discover -s tests -v
docker build --platform linux/amd64 -t jm-bot:ci .
python scripts/container_smoke.py --image jm-bot:ci
```

测试使用本机替身模拟 OneBot，不登录真实 QQ。容器启动检查记录镜像大小和闲置 RSS（进程实际占用的物理内存），这些数字不等于下载期间峰值或整个 NapCat + QQ 的内存。

[GitHub Actions](https://github.com/Epiphany-Leave/jm-bot/actions) 是仓库的自动测试与构建页面。推送 `main` 或手动运行会测试并构建 `linux/amd64` 镜像；推送 `v1.0.1` 标签才发布 `ghcr.io/epiphany-leave/jm-bot:1.0.1`。工作流先通过测试、容器导入与离线启动检查，再用 GitHub 自动提供的 `GITHUB_TOKEN` 和 `packages: write` 权限发布，无需另配个人访问口令。

首次发布的 GHCR（GitHub 镜像仓库）包默认私有。维护者必须在 GitHub 个人主页 **Packages → jm-bot → Package settings → Change visibility** 设为 Public，然后在没有 GHCR 登录凭据的环境运行：

```text
docker pull --platform linux/amd64 ghcr.io/epiphany-leave/jm-bot:1.0.1
```

标签工作流中的 `verify-published` 会用全新、未登录的 Docker 配置拉取成品镜像，再验证实际容器。如果首次因包仍为私有而失败，设为 Public 后重新运行这个失败任务即可。记录成功摘要后才能把本文顶部状态改成“已公开并匿名拉取验证”。仓库公开不代表镜像也公开。[GitHub 镜像仓库权限说明](https://docs.github.com/en/packages/working-with-a-github-packages-registry/working-with-the-container-registry)。

当前验收状态：1.0.1 本地 41 项测试通过，待 Linux 构建和匿名拉取；1.0.0 的容器与匿名拉取检查已通过。[完整证据与数据口径](docs/verification.md)。NAS 目录权限、QQ 私聊/群文件、真实下载、NAS 重建恢复待现场验收。设计取舍见 [独立服务决策](docs/decisions-standalone.md)。
