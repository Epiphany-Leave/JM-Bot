import asyncio
from collections import OrderedDict
import logging
from pathlib import Path
import shutil
import tempfile
import time
from dataclasses import replace
from functools import partial

from .config import Settings, positive_id
from .downloader import DownloadFailure, DownloadSession, detect_download, download_pdf, query_album_info
from .download_settings import download_settings
from .proxy import ProxyError
from .onebot import OneBotClient


log = logging.getLogger(__name__)


def command_text(message, self_id: str) -> str | None:
    """Accept text and one leading mention of this bot, never embedded media/CQ code."""
    if not isinstance(message, list):
        return None
    text = ""
    mentioned = False
    for segment in message:
        if not isinstance(segment, dict) or not isinstance(segment.get("data"), dict):
            return None
        data = segment["data"]
        if segment.get("type") == "text" and isinstance(data.get("text"), str):
            text += data["text"]
        elif segment.get("type") == "at" and str(data.get("qq")) == self_id and not text.strip() and not mentioned:
            mentioned = True
        else:
            return None
    return text.strip()


class JMBot:
    def __init__(self, settings: Settings, client=None, download_options=None, proxy=None):
        self.settings = settings
        self.client = client or OneBotClient(settings.ws_url, settings.token, self.handle)
        self.data_dir = settings.download_dir
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self._lock = asyncio.Lock()
        self._handlers = set()
        self._closing = False
        self._seen = OrderedDict()
        self.current_task = None
        self.last_result = None
        self.download_options = download_options or download_settings
        self.proxy = proxy
        self.download_session = None
        self._last_download_progress = {}

    @property
    def busy(self) -> bool:
        return self._lock.locked()

    def task_status(self):
        if self.current_task is None:
            return None
        result = dict(self.current_task)
        if self.download_session is not None:
            result.update(self.download_session.snapshot())
            result["cancellable"] = result.get("stage") in ("metadata", "images") and not result.get("stop_reason")
        else:
            result["cancellable"] = False
        return result

    def stop_download(self) -> None:
        if self.download_session is None or not self.task_status().get("cancellable"):
            raise ValueError("当前没有可停止的下载任务")
        self.download_session.cancel("用户停止下载")

    async def _new_session(self):
        options = self.download_options()
        proxy_url = None
        if options["mode"] == "mihomo":
            if self.proxy is None:
                raise DownloadFailure("proxy_unavailable", "内置代理未就绪，请先导入订阅并测试连接。")
            try:
                async with asyncio.timeout(20):
                    await self.proxy.ensure_ready()
                proxy_url = self.proxy.proxy_url
            except (ProxyError, TimeoutError):
                raise DownloadFailure("proxy_unavailable", "内置代理连接验证失败，请在下载设置中检查节点。") from None
        return DownloadSession(options, proxy_url=proxy_url)

    async def _download_worker(self, function, album_id, job):
        self._last_download_progress = {}
        session = await self._new_session()
        self.download_session = session
        worker = asyncio.get_running_loop().run_in_executor(None, partial(function, album_id, job, session=session))
        try:
            return await asyncio.shield(worker)
        except asyncio.CancelledError:
            session.cancel("任务已取消")
            while True:
                try:
                    await asyncio.shield(worker)
                    break
                except asyncio.CancelledError:
                    continue
                except Exception:
                    break
            raise
        finally:
            self._last_download_progress = session.snapshot()
            self.download_session = None

    def _failure_message(self, error):
        progress = self._last_download_progress
        total = progress.get("total_pages", 0)
        suffix = f"（已完成 {progress.get('completed_pages', 0)}/{total} 页）" if total else ""
        return error.message + suffix

    async def detect_download(self, album_id: str) -> dict:
        album_id = positive_id(album_id)
        if self.busy:
            raise ValueError("正在处理其他任务，请结束后再检测线路")
        async with self._lock:
            self.current_task = {"album_id": album_id, "stage": "检测下载线路"}
            job = Path(tempfile.mkdtemp(prefix="probe_", dir=self.data_dir))
            try:
                result = await self._download_worker(detect_download, album_id, job)
                self.last_result = {"status": "success" if result.get("api_ok") and result.get("image_ok") else "error",
                                    "message": result["message"]}
                return result
            except DownloadFailure as exc:
                self.last_result = {"status": "error", "message": self._failure_message(exc), "code": exc.code}
                raise
            finally:
                self.current_task = None
                await asyncio.to_thread(shutil.rmtree, job, True)

    def update_permissions(self, value: dict) -> None:
        self.settings = replace(self.settings, allowed_users=frozenset(value["allowed_users"]),
                                allowed_groups=frozenset(value["allowed_groups"]),
                                group_allow_all=value["group_allow_all"])

    def _available(self, generation: int) -> bool:
        return not self._closing and self.client.connected and self.client.generation == generation

    async def _reply(self, target: dict, generation: int, text: str) -> None:
        if self._available(generation):
            action = "send_group_msg" if "group_id" in target else "send_private_msg"
            await self.client.call(action, **target, message=[{"type": "text", "data": {"text": text}}])

    def _duplicate(self, key: tuple) -> bool:
        now = time.monotonic()
        while self._seen and next(iter(self._seen.values())) < now - 600:
            self._seen.popitem(last=False)
        if key in self._seen:
            return True
        # shortcut: deduplication lasts for this process; add persistent storage if restart replay is needed.
        self._seen[key] = now
        if len(self._seen) > 2048:
            self._seen.popitem(last=False)
        return False

    async def handle(self, event: dict, generation: int) -> None:
        task = asyncio.current_task()
        self._handlers.add(task)
        try:
            await self._handle(event, generation)
        finally:
            self._handlers.discard(task)

    async def _handle(self, event: dict, generation: int) -> None:
        if not self._available(generation) or event.get("post_type") != "message":
            return
        kind = event.get("message_type")
        if kind not in ("private", "group"):
            return
        try:
            sender = positive_id(event.get("user_id"))
            self_id = positive_id(event.get("self_id"))
            group = positive_id(event.get("group_id")) if kind == "group" else None
        except ValueError:
            return
        if sender == self_id:
            return
        text = command_text(event.get("message"), self_id)
        if not text or text.split()[0] not in ("/jm", "/jm-s"):
            return
        command = text.split()[0]
        message_id = event.get("message_id")
        if isinstance(message_id, bool) or not isinstance(message_id, (str, int)):
            return
        if self._duplicate((self_id, kind, group or sender, str(message_id))):
            return
        target = {"group_id": group} if group else {"user_id": sender}
        authorized = sender in self.settings.allowed_users
        if group:
            authorized = group in self.settings.allowed_groups and (self.settings.group_allow_all or authorized)
        if not authorized:
            await self._reply(target, generation, f"未授权使用 {command}，请联系机器人管理员配置白名单。")
            return
        args = text.split()
        try:
            if len(args) != 2:
                raise ValueError
            album_id = positive_id(args[1])
        except ValueError:
            await self._reply(target, generation, f"用法：{command} <一个正整数ID>，例如 {command} 11451。")
            return
        if self._lock.locked():
            await self._reply(target, generation, "正在处理，请稍后重试。")
            return

        async with self._lock:
            self.current_task = {"album_id": album_id, "stage": "查询漫画信息" if command == "/jm-s" else "下载并生成 PDF"}
            if command == "/jm-s":
                self.current_task["kind"] = "info"
            try:
                if command == "/jm-s":
                    await self._query_info(album_id, target, generation)
                else:
                    await self._process(album_id, group, target, generation)
            finally:
                self.current_task = None

    async def _query_info(self, album_id, target, generation):
        await self._reply(target, generation, f"正在查询 JM {album_id} 的信息。")
        if not self._available(generation):
            return
        try:
            text = await self._download_worker(query_album_info, album_id, self.data_dir)
        except DownloadFailure as exc:
            self.last_result = {"status": "error", "message": exc.message, "code": exc.code}
            await self._reply(target, generation, exc.message)
            return
        except Exception:
            log.error("Unexpected album information query failure", exc_info=False)
            self.last_result = {"status": "error", "message": "漫画信息查询失败，请检查下载设置后重试。"}
            await self._reply(target, generation, self.last_result["message"])
            return
        if not self._available(generation):
            self.last_result = {"status": "error", "message": "查询期间连接中断，未自动补发；请重新查询。"}
            return
        try:
            await self._reply(target, generation, text)
        except Exception:
            log.warning("Album information reply result unconfirmed", exc_info=False)
            self.last_result = {"status": "error", "message": "信息已查询，但回复结果未确认，请检查QQ后重试。"}
            return
        self.last_result = {"status": "success", "message": f"JM {album_id} 信息查询完成。"}

    async def _process(self, album_id, group, target, generation):
        await self._reply(target, generation, f"已开始处理 JM {album_id}，完成后发送 PDF。")
        if not self._available(generation):
            return
        job = None
        retain_pdf = False
        try:
            try:
                job = Path(tempfile.mkdtemp(prefix="job_", dir=self.data_dir))
                # mkdtemp defaults to 0700; NapCat needs to traverse this directory as another UID.
                job.chmod(0o755)
                try:
                    pdf = await self._download_worker(download_pdf, album_id, job)
                except asyncio.CancelledError:
                    retain_pdf = (job / f"jm_{album_id}.pdf").is_file()
                    raise
            except DownloadFailure as exc:
                message = self._failure_message(exc)
                log.warning("Download stopped (%s): %s", exc.code, message)
                self.last_result = {"status": "error", "message": message, "code": exc.code}
                await self._reply(target, generation, message + "\n未发送文件，请在 JM 管理台检查下载设置。")
                return
            except Exception:
                log.error("Unexpected download or PDF failure", exc_info=False)
                self.last_result = {"status": "error", "message": "下载或 PDF 合成失败，未发送文件；可检查网络后重试。"}
                await self._reply(target, generation, "下载或 PDF 合成失败，未发送文件；请查看 jm-bot 日志。")
                return

            retain_pdf = True
            self.last_result = {"status": "retained", "message": "PDF 已生成；若连接中断或发送结果不明，将保留文件。",
                                "path": f"downloads/{job.name}/{pdf.name}"}
            if not self._available(generation):
                self.last_result["message"] = "任务期间连接中断，未自动补发；请从项目目录取回 PDF。"
                log.warning("连接已变化，不自动上传；PDF 保留于 %s", pdf)
                return
            try:
                self.current_task["stage"] = "发送 PDF"
                action = "upload_group_file" if group else "upload_private_file"
                await self.client.call(action, **target, file=str(pdf.resolve()), name=pdf.name, upload_file=True)
            except Exception:
                log.exception("上传结果未确认，PDF 保留于 %s", pdf)
                self.last_result["message"] = "发送结果未确认；请先检查 QQ，避免重复发送。PDF 已保留。"
                await self._reply(target, generation,
                                  "PDF 发送结果未确认，请先检查 QQ，避免重复发送。\n"
                                  f"PDF 已保留，项目目录中的文件位置：downloads/{job.name}/{pdf.name}")
                return
            retain_pdf = False
            self.last_result = {"status": "success", "message": f"JM {album_id} PDF 已上传，临时文件已清理。"}
            await self._reply(target, generation, "PDF 已上传。")
        finally:
            if job is not None:
                try:
                    await asyncio.to_thread(shutil.rmtree, job / "images" if retain_pdf else job)
                except FileNotFoundError:
                    pass
                except OSError:
                    log.exception("临时文件清理失败：%s", job)
                    if self.last_result is not None:
                        self.last_result["message"] += " 临时文件清理失败，请在项目目录检查残留文件。"
                        if (job / f"jm_{album_id}.pdf").exists():
                            self.last_result["path"] = f"downloads/{job.name}/jm_{album_id}.pdf"

    async def send_test_file(self, kind: str, target_id: str, source: Path) -> dict:
        target_id = positive_id(target_id)
        if kind not in ("private", "group"):
            raise ValueError("请选择群聊或私聊")
        allowed = self.settings.allowed_groups if kind == "group" else self.settings.allowed_users
        if target_id not in allowed:
            raise ValueError("请先在使用权限中允许这个群或用户，并保存设置")
        if self.busy:
            raise ValueError("正在处理其他任务，请稍后重试")
        if not self.client.connected:
            raise ValueError("QQ 连接尚未就绪，请先查看概览中的提示")
        async with self._lock:
            self.current_task = {"album_id": "测试文件", "stage": "发送普通测试 PDF"}
            job = None
            acknowledged = False
            try:
                job = Path(tempfile.mkdtemp(prefix="test_", dir=self.data_dir))
                job.chmod(0o755)
                pdf = job / "jm_bot_test.pdf"
                await asyncio.to_thread(shutil.copyfile, source, pdf)
                pdf.chmod(0o644)
                self.last_result = {"status": "retained", "message": "测试 PDF 发送中；若发送结果不明将保留文件。",
                                    "path": f"downloads/{job.name}/{pdf.name}"}
                try:
                    await self.client.call("upload_group_file" if kind == "group" else "upload_private_file",
                                           **{"group_id" if kind == "group" else "user_id": target_id},
                                           file=str(pdf.resolve()), name=pdf.name, upload_file=True)
                    acknowledged = True
                    result = {"status": "success", "message": "测试 PDF 已上传，请在 QQ 中确认可以打开。"}
                except Exception:
                    result = {"status": "retained", "message": "发送结果未确认，请先检查 QQ，避免重复发送。",
                              "path": f"downloads/{job.name}/{pdf.name}"}
                self.last_result = result
                return result
            finally:
                self.current_task = None
                if job is not None and acknowledged:
                    try:
                        await asyncio.to_thread(shutil.rmtree, job)
                    except OSError:
                        self.last_result["message"] += " 临时文件清理失败，请在项目目录检查残留。"
                        if pdf.exists():
                            self.last_result["path"] = f"downloads/{job.name}/{pdf.name}"

    async def close(self) -> None:
        self._closing = True
        if self.download_session is not None:
            self.download_session.cancel("程序正在关闭")
        await self.client.close()
        if self._handlers:
            await asyncio.gather(*self._handlers, return_exceptions=True)
