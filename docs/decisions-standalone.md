# 独立 JM-Bot 的交付决策

用户已确认使用独立 Python 服务，公开仓库 `Epiphany-Leave/jm-bot`，并通过 GitHub Actions 发布 GHCR 镜像。用户在 NAS 只粘贴 Compose；不上传源码、不运行 pip、不在 NAS 编译。

| 决策 | 原因与边界 |
| --- | --- |
| 一个 `jm-bot` 项目、两个容器 | NapCat 连接 QQ；Python 仅处理 `/jm`，无需 AstrBot 和大模型 |
| `python:3.12-slim`，预装固定直接依赖 | 尽量少的运行环境；不加入数据库、Web 管理页、下载微服务 |
| `linux/amd64`，版本 `1.0.0` | 面向目标 x86-64 NAS；不隐含承诺 ARM 支持 |
| NapCat `mlikiowa/napcat-docker:v4.18.42` | 使用官方 Docker 项目提供的镜像；不自制 QQ 镜像 |
| 正向 WS `ws://napcat:3001/` | 仅映射管理网页 `16099:6099`；OneBot 不暴露到 NAS 端口 |
| 网页扫码、设置一次 OneBot token | 官方 `MODE=ws` 初始化模板不接收本项目的 `ONEBOT_TOKEN`；不声称全自动免配置 |
| 用户与群白名单同时检查 | 私聊仅查用户；群聊两者都必须允许，空名单默认拒绝对应入口 |
| 全局单任务、完整清单才能合成 | 不排队、不发缺页 PDF；仅成功上传确认后清理 |
| 失败/断线留文件，不自动重发 | 发送结果可能不确定，避免重复发；重启不续传 |
| root 写任务目录 `0755`、PDF `0644` | NapCat 默认 UID/GID `1000:1000` 可读取；NAS ACL 仍需现场验证 |
| 三个目录持久化 | `napcat-config`、`qq-data`、`downloads`；共享下载目录在 NapCat 为只读 |

直接依赖固定为 `jmcomic==2.7.10`、`img2pdf==0.6.3`、`websockets==17.4`。基础镜像及传递依赖未做全量哈希锁定，重新构建可能有体积或补丁差异；运行部署固定使用发布版本。

CI 先跑标准库 unittest，再构建镜像，检查容器导入、离线 OneBot 连接与闲置 RSS，并记录镜像大小。QQ 与站点不会在 CI 中登录或访问，因此现场收发、下载和 NAS 权限仍单独验收。

发布使用仓库的 `GITHUB_TOKEN`，不需要个人 PAT。首次 GHCR 包默认私有；设成公开且匿名拉取成功，才可承诺复制 Compose 即可拉取镜像。目前发布未验证，禁止将候选版本描述成已部署成功。

旧部署尚未验收成功。本次失败残留只停止并移除 `jm-bot` 自己的容器，保留数据目录；其他 AstrBot 项目不动。不迁移旧插件、不额外兼容旧配置。

依据：[NapCat 官方 WS Compose](https://github.com/NapNeko/NapCat-Docker/blob/main/compose/ws.yml)、[启动配置初始化](https://github.com/NapNeko/NapCat-Docker/blob/main/entrypoint.sh)、[GHCR 发布与可见性](https://docs.github.com/en/packages/working-with-a-github-packages-registry/working-with-the-container-registry)。Actions 使用已核查的 [checkout v7.0.0](https://github.com/actions/checkout/releases/tag/v7.0.0) 和 [setup-python v7.0.0](https://github.com/actions/setup-python/releases/tag/v7.0.0)，固定提交 SHA。
