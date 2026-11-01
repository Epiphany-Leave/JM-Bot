import asyncio
import itertools
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import patch

import img2pdf
import pikepdf
from PIL import Image

from jm_bot import bot as module
from jm_bot.config import Settings
from jm_bot.onebot import OneBotDisconnected


ids = itertools.count(1)


def event(text="/jm 11451", user=10001, group=None, **extra):
    return {"post_type": "message", "message_type": "group" if group else "private",
            "user_id": user, "self_id": 99999, "group_id": group,
            "message_id": next(ids), "message": [{"type": "text", "data": {"text": text}}], **extra}


def make_pdf(album_id, job, *, session=None):
    images = job / "images"
    images.mkdir()
    image = images / "test.png"
    Image.new("RGB", (120, 180), "white").save(image)
    pdf = job / f"jm_{album_id}.pdf"
    with pdf.open("wb") as stream:
        img2pdf.convert(str(image), outputstream=stream)
    pdf.chmod(0o644)
    return pdf


class LocalClient:
    def __init__(self):
        self.connected = True
        self.generation = 1
        self.calls = []
        self.upload_error = None
        self.upload_hook = None
        self.fail_final_notice = False

    async def call(self, action, **params):
        self.calls.append((action, params))
        if action.startswith("upload_"):
            with pikepdf.open(params["file"]) as document:
                assert len(document.pages) == 1
            if self.upload_hook:
                await self.upload_hook(params)
            if self.upload_error:
                raise self.upload_error
        elif self.fail_final_notice and params["message"][0]["data"]["text"] == "PDF 已上传。":
            raise RuntimeError("final notice rejected")
        return None

    async def close(self):
        self.connected = False


class BotTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.root = Path(self.directory.name)
        self.client = LocalClient()
        self.bot = self.new_bot()
        self.worker = patch.object(module, "download_pdf", side_effect=make_pdf)
        self.worker.start()
        self.addCleanup(self.worker.stop)

    async def asyncTearDown(self):
        await self.bot.close()
        self.directory.cleanup()

    def new_bot(self, users=frozenset({"10001"}), groups=frozenset({"20001"}), group_all=False):
        return module.JMBot(Settings(token="test", allowed_users=users, allowed_groups=groups,
                                     download_dir=self.root, group_allow_all=group_all), self.client)

    async def test_all_members_of_allowed_group_can_download_without_user_list(self):
        self.bot = self.new_bot(users=frozenset(), group_all=True)
        for user in (10002, 10003):
            await self.bot.handle(event(user=user, group=20001), 1)
        self.assertEqual(len(self.uploads()), 2)
        self.assertTrue(all(action == "upload_group_file" for action, _ in self.uploads()))

    async def test_group_all_mode_never_opens_other_groups_or_private_messages(self):
        self.bot = self.new_bot(users=frozenset(), group_all=True)
        for user, group in ((10001, 20002), (10002, None)):
            await self.bot.handle(event(user=user, group=group), 1)
        self.assertFalse(self.uploads())
        self.assertTrue(all("未授权" in text for text in self.messages()))
        self.bot = self.new_bot(users=frozenset(), groups=frozenset(), group_all=True)
        await self.bot.handle(event(group=20001), 1)
        self.assertFalse(self.uploads())

    async def test_group_all_mode_can_be_disabled_to_restore_user_restrictions(self):
        self.bot = self.new_bot(group_all=False)
        await self.bot.handle(event(user=10002, group=20001), 1)
        self.assertFalse(self.uploads())
        await self.bot.handle(event(user=10001, group=20001), 1)
        self.assertEqual(len(self.uploads()), 1)

    def uploads(self):
        return [(action, params) for action, params in self.client.calls if action.startswith("upload_")]

    def messages(self):
        return [params["message"][0]["data"]["text"] for action, params in self.client.calls if action.startswith("send_")]

    async def test_private_and_group_files_go_to_origin_and_clean_after_ack(self):
        for group in (None, 20001):
            with self.subTest(group=group):
                self.client.calls.clear()
                await self.bot.handle(event(group=group), 1)
                action, params = self.uploads()[0]
                self.assertEqual(action, "upload_group_file" if group else "upload_private_file")
                self.assertEqual(params["group_id" if group else "user_id"], str(group or 10001))
                self.assertTrue(params["upload_file"])
                self.assertEqual(params["name"], "jm_11451.pdf")
                self.assertFalse(list(self.root.iterdir()))

    async def test_group_checks_user_and_group_but_private_only_checks_user(self):
        for user, group in ((10002, None), (10002, 20001), (10001, 20002)):
            await self.bot.handle(event(user=user, group=group), 1)
        self.assertFalse(self.uploads())
        self.assertTrue(all("未授权" in text for text in self.messages()))
        self.bot = self.new_bot(groups=frozenset())
        await self.bot.handle(event(group=20001), 1)
        self.assertFalse(self.uploads())
        await self.bot.handle(event(), 1)
        self.assertEqual(len(self.uploads()), 1)

    async def test_empty_user_whitelist_denies_all_downloads(self):
        self.bot = self.new_bot(users=frozenset())
        await self.bot.handle(event(), 1)
        self.assertFalse(self.uploads())
        self.assertFalse(list(self.root.iterdir()))

    async def test_wrong_parameters_reply_with_usage_and_do_not_download(self):
        for text in ("/jm", "/jm 0", "/jm -1", "/jm +1", "/jm abc", "/jm 1.5", "/jm １２３", "/jm 12 34", "/jm ../12"):
            await self.bot.handle(event(text), 1)
        self.assertFalse(self.uploads())
        self.assertTrue(all("用法" in text for text in self.messages()))
        self.assertFalse(list(self.root.iterdir()))

    async def test_unrelated_messages_media_self_messages_and_wrong_mentions_are_ignored(self):
        for payload in (event("hello"), event("/jmfoo 12"), event(user=99999),
                        event(post_type="message_sent"), event(message_type="notice"),
                        event(message="[CQ:at,qq=99999] /jm 12"),
                        event(message=[{"type": "image", "data": {"file": "anything"}}]),
                        event(message=[{"type": "at", "data": {"qq": "10002"}},
                                       {"type": "text", "data": {"text": "/jm 12"}}])):
            await self.bot.handle(payload, 1)
        self.assertFalse(self.client.calls)

    async def test_leading_mention_of_this_bot_and_leading_zeros_are_accepted(self):
        payload = event(group=20001, message=[{"type": "at", "data": {"qq": "99999"}},
                                             {"type": "text", "data": {"text": " /jm 0011451"}}])
        await self.bot.handle(payload, 1)
        self.assertEqual(self.uploads()[0][1]["name"], "jm_11451.pdf")

    async def test_duplicate_message_id_is_processed_only_once(self):
        payload = event()
        await self.bot.handle(payload, 1)
        await self.bot.handle(payload, 1)
        self.assertEqual(len(self.uploads()), 1)

    async def test_download_error_removes_partial_files_and_never_uploads(self):
        def fail(album_id, job, *, session=None):
            (job / "partial").write_bytes(b"incomplete")
            raise RuntimeError("download failed")
        with patch.object(module, "download_pdf", side_effect=fail):
            await self.bot.handle(event(), 1)
        self.assertFalse(self.uploads())
        self.assertTrue(any("合成失败" in text for text in self.messages()))
        self.assertFalse(list(self.root.iterdir()))

    async def test_upload_rejection_or_timeout_retains_pdf_and_cleans_images(self):
        for failure in (RuntimeError("QQ rejected"), TimeoutError("uncertain upload")):
            self.client.upload_error = failure
            await self.bot.handle(event(), 1)
        self.assertEqual(len(self.uploads()), 2)
        self.assertEqual(len(list(self.root.rglob("*.pdf"))), 2)
        self.assertFalse(list(self.root.rglob("*.png")))
        self.assertTrue(any("结果未确认" in text for text in self.messages()))

    async def test_reconnection_during_download_does_not_upload_old_job(self):
        def disconnect(album_id, job, *, session=None):
            pdf = make_pdf(album_id, job)
            self.client.generation = 2
            return pdf
        with patch.object(module, "download_pdf", side_effect=disconnect):
            await self.bot.handle(event(), 1)
        self.assertFalse(self.uploads())
        self.assertEqual(len(list(self.root.rglob("*.pdf"))), 1)
        self.assertFalse(list(self.root.rglob("*.png")))
        self.client.calls.clear()
        await self.bot.handle(event(), 1)
        self.assertFalse(self.client.calls)

    async def test_lost_upload_ack_does_not_retry_after_reconnection(self):
        async def disconnect(params):
            self.client.generation = 2
            raise OneBotDisconnected("lost after send")
        self.client.upload_hook = disconnect
        await self.bot.handle(event(), 1)
        self.assertEqual(len(self.uploads()), 1)
        self.assertEqual(len(list(self.root.rglob("*.pdf"))), 1)

    async def test_final_notice_failure_cleans_confirmed_upload_without_misreporting_download(self):
        self.client.fail_final_notice = True
        with self.assertRaises(RuntimeError):
            await self.bot.handle(event(), 1)
        self.assertEqual(len(self.uploads()), 1)
        self.assertFalse(list(self.root.iterdir()))
        self.assertFalse(any("合成失败" in text for text in self.messages()))

    async def test_busy_reply_does_not_wait_for_background_download(self):
        entered, release = threading.Event(), threading.Event()
        def slow(album_id, job, *, session=None):
            entered.set()
            if not release.wait(5):
                raise TimeoutError("test did not release worker")
            return make_pdf(album_id, job)
        with patch.object(module, "download_pdf", side_effect=slow):
            active = asyncio.create_task(self.bot.handle(event(), 1))
            try:
                self.assertTrue(await asyncio.to_thread(entered.wait, 2))
                await asyncio.wait_for(self.bot.handle(event("/jm 12"), 1), 1)
                self.assertTrue(any("稍后" in text for text in self.messages()))
            finally:
                release.set()
                await active
        self.assertEqual(len(self.uploads()), 1)

    async def test_repeated_cancellation_waits_for_worker_and_preserves_completed_pdf(self):
        entered, release = threading.Event(), threading.Event()
        def slow(album_id, job, *, session=None):
            entered.set()
            if not release.wait(5):
                raise TimeoutError("test did not release worker")
            return make_pdf(album_id, job)
        with patch.object(module, "download_pdf", side_effect=slow):
            active = asyncio.create_task(self.bot.handle(event(), 1))
            try:
                self.assertTrue(await asyncio.to_thread(entered.wait, 2))
                for _ in range(2):
                    active.cancel()
                    await asyncio.sleep(0)
                await self.bot.handle(event("/jm 12"), 1)
                self.assertTrue(any("稍后" in text for text in self.messages()))
                self.assertTrue(list(self.root.iterdir()))
            finally:
                release.set()
                with self.assertRaises(asyncio.CancelledError):
                    await active
        self.assertFalse(self.uploads())
        self.assertEqual(len(list(self.root.rglob("*.pdf"))), 1)
