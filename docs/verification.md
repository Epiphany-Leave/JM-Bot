# 1.0.0 验证记录

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
