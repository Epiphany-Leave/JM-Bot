import asyncio
from collections import OrderedDict
import logging
from pathlib import Path
import shutil
import tempfile
import time

from .config import Settings, positive_id
from .downloader import download_pdf
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
    def __init__(self, settings: Settings, client=None):
        self.settings = settings
        self.client = client or OneBotClient(settings.ws_url, settings.token, self.handle)
        self.data_dir = settings.download_dir
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self._lock = asyncio.Lock()
        self._handlers = set()
        self._closing = False
        self._seen = OrderedDict()

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
        if not text or text.split()[0] != "/jm":
            return
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
            await self._reply(target, generation, "未授权使用 /jm，请联系机器人管理员配置白名单。")
            return
        args = text.split()
        try:
            if len(args) != 2:
                raise ValueError
            album_id = positive_id(args[1])
        except ValueError:
            await self._reply(target, generation, "用法：/jm <一个正整数ID>，例如 /jm 11451。")
            return
        if self._lock.locked():
            await self._reply(target, generation, "正在处理，请稍后重试。")
            return

        async with self._lock:
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
                    worker = asyncio.get_running_loop().run_in_executor(None, download_pdf, album_id, job)
                    try:
                        pdf = await asyncio.shield(worker)
                    except asyncio.CancelledError:
                        # Keep the lock and files until the thread really stops, even after repeated cancellation.
                        while True:
                            try:
                                await asyncio.shield(worker)
                                retain_pdf = True
                                break
                            except asyncio.CancelledError:
                                continue
                            except Exception:
                                log.exception("取消时等待下载线程结束失败")
                                break
                        raise
                except Exception:
                    log.exception("下载或 PDF 合成失败")
                    await self._reply(target, generation, "下载或 PDF 合成失败，未发送文件；请查看 jm-bot 日志。")
                    return

                retain_pdf = True
                if not self._available(generation):
                    log.warning("连接已变化，不自动上传；PDF 保留于 %s", pdf)
                    return
                try:
                    action = "upload_group_file" if group else "upload_private_file"
                    await self.client.call(action, **target, file=str(pdf.resolve()), name=pdf.name, upload_file=True)
                except Exception:
                    log.exception("上传结果未确认，PDF 保留于 %s", pdf)
                    await self._reply(target, generation,
                                      "PDF 发送结果未确认，请先检查 QQ，避免重复发送。\n"
                                      f"PDF 已保留，NAS 文件（相对项目目录）：downloads/{job.name}/{pdf.name}")
                    return
                retain_pdf = False
                await self._reply(target, generation, "PDF 已上传。")
            finally:
                if job is not None:
                    try:
                        await asyncio.to_thread(shutil.rmtree, job / "images" if retain_pdf else job)
                    except FileNotFoundError:
                        pass
                    except OSError:
                        log.exception("临时文件清理失败：%s", job)

    async def close(self) -> None:
        self._closing = True
        await self.client.close()
        if self._handlers:
            await asyncio.gather(*self._handlers, return_exceptions=True)
