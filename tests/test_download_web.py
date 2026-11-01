import asyncio
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import patch

import aiohttp

from jm_bot.downloader import DownloadFailure
from jm_bot.store import StateStore
from tests.test_bot import event
from tests.webui_fixture import PASSWORD, running_webui


class DownloadWebTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.fixture = running_webui(self.root)
        self.url, self.runtime, self.peer = await self.fixture.__aenter__()
        self.session = aiohttp.ClientSession(cookie_jar=aiohttp.CookieJar(unsafe=True))
        response = await self.session.post(self.url + "/api/auth/login", json={"password": PASSWORD})
        self.assertEqual(response.status, 200)

    async def asyncTearDown(self):
        await self.session.close()
        await self.fixture.__aexit__(None, None, None)
        self.temp.cleanup()

    async def test_network_settings_persist_and_unready_proxy_does_not_enable(self):
        options = await (await self.session.get(self.url + "/api/download-settings")).json()
        self.assertEqual(options["image_timeout"], 45)
        self.assertEqual(options["max_sources"], 3)
        response = await self.session.put(self.url + "/api/download-settings", json={**options, "mode": "mihomo"})
        self.assertEqual(response.status, 503)
        self.assertEqual(self.runtime.store.get_download_settings()["mode"], "direct")
        response = await self.session.put(self.url + "/api/download-settings", json={**options, "concurrency": 2})
        self.assertEqual(response.status, 200)
        restored = StateStore(self.root / "data", env={})
        self.assertEqual(restored.get_download_settings()["concurrency"], 2)
        response = await self.session.put(self.url + "/api/download-settings", json={**options, "max_sources": True})
        self.assertEqual(response.status, 400)

    async def test_proxy_import_selection_mode_and_secret_exclusion(self):
        content = "proxies:\n- {name: alpha, type: http, server: localhost, port: 8080, password: PRIVATE_NODE}\n- {name: beta, type: http, server: localhost, port: 8081}\n"
        response = await self.session.post(self.url + "/api/proxy/import", json={"content": content})
        text = await response.text()
        self.assertEqual(response.status, 200, text)
        self.assertNotIn("PRIVATE_NODE", text)
        self.assertEqual((await response.json())["node"], "AUTO")
        response = await self.session.put(self.url + "/api/proxy/node", json={"name": "beta"})
        self.assertEqual(response.status, 200)
        self.assertEqual((await response.json())["node"], "beta")
        options = self.runtime.store.get_download_settings()
        response = await self.session.put(self.url + "/api/download-settings", json={**options, "mode": "mihomo"})
        self.assertEqual(response.status, 200)
        self.peer.proxy_core.fail_probe = True
        with self.assertRaises(DownloadFailure) as caught:
            await self.runtime.bot._new_session()
        self.assertEqual(caught.exception.code, "proxy_unavailable")
        self.assertEqual(self.runtime.store.get_download_settings()["mode"], "mihomo")
        text = await (await self.session.get(self.url + "/api/status")).text()
        for key in ("proxy_password", "proxy_control_token"):
            self.assertNotIn(self.runtime.store.state[key], text)

    async def test_probe_partial_failure_is_not_reported_as_success_and_cleans(self):
        def probe(album, directory, *, session):
            (directory / "test.tmp").write_bytes(b"partial")
            return {"api_ok": True, "image_ok": False, "source": "test.invalid", "message": "测试图片下载失败"}
        with patch("jm_bot.bot.detect_download", side_effect=probe):
            response = await self.session.post(self.url + "/api/download/test", json={"album_id": "114514"})
        self.assertEqual(response.status, 200)
        self.assertFalse((await response.json())["image_ok"])
        self.assertEqual(self.runtime.bot.last_result["status"], "error")
        self.assertFalse(list((self.root / "downloads").iterdir()))
        self.assertFalse(self.peer.uploads)

    async def test_cancel_holds_slot_and_blocks_network_changes_until_worker_exits(self):
        await self.session.put(self.url + "/api/permissions", json={
            "allowed_users": [], "allowed_groups": ["20001"], "group_allow_all": True})
        entered, release = threading.Event(), threading.Event()
        def waiting(album, directory, *, session):
            session.update(stage="images", total_pages=27)
            entered.set()
            if not release.wait(5):
                raise AssertionError("test did not release worker")
            session.check()
            raise AssertionError("cancelled task must not create a PDF")
        with patch("jm_bot.bot.download_pdf", side_effect=waiting):
            await self.peer.emit(event(group=20001))
            try:
                self.assertTrue(await asyncio.to_thread(entered.wait, 2))
                response = await self.session.post(self.url + "/api/task/cancel", json={})
                self.assertEqual(response.status, 200)
                self.assertTrue(self.runtime.bot.busy)
                for method, path, payload in (
                    ("PUT", "/api/download-settings", self.runtime.store.get_download_settings()),
                    ("POST", "/api/proxy/import", {"content": "anything"}),
                    ("POST", "/api/proxy/refresh", {}),
                    ("PUT", "/api/proxy/node", {"name": "AUTO"}),
                    ("POST", "/api/download/test", {"album_id": "114514"}),
                ):
                    response = await self.session.request(method, self.url + path, json=payload)
                    self.assertEqual(response.status, 409, path)
                response = await self.session.get(self.url + "/api/status")
                self.assertTrue((await response.json())["busy"])
                self.assertFalse(self.peer.uploads)
            finally:
                release.set()
            async with asyncio.timeout(3):
                while self.runtime.bot.busy:
                    await asyncio.sleep(0.01)
        self.assertEqual(self.runtime.bot.last_result["code"], "cancelled")
        self.assertIn("0/27", self.runtime.bot.last_result["message"])
        self.assertFalse(self.peer.uploads)

    async def test_proxy_operations_hold_same_slot_as_downloads(self):
        entered, release = asyncio.Event(), asyncio.Event()
        async def blocking_import(**kwargs):
            entered.set()
            await release.wait()
            return {"available": True, "configured": False, "healthy": False, "nodes": [], "node": ""}
        with patch.object(self.runtime.proxy, "import_subscription", side_effect=blocking_import):
            request = asyncio.create_task(self.session.post(self.url + "/api/proxy/import", json={"content": "test"}))
            try:
                await asyncio.wait_for(entered.wait(), 1)
                self.assertTrue(self.runtime.bot.busy)
                response = await self.session.post(self.url + "/api/download/test", json={"album_id": "114514"})
                self.assertEqual(response.status, 409)
            finally:
                release.set()
                await request

    async def test_new_management_endpoints_require_login(self):
        await self.session.post(self.url + "/api/auth/logout", json={})
        for method, path, payload in (("GET", "/api/download-settings", None),
                                      ("GET", "/api/proxy/status", None),
                                      ("POST", "/api/proxy/import", {"content": "secret"}),
                                      ("POST", "/api/download/test", {"album_id": "114514"}),
                                      ("POST", "/api/task/cancel", {})):
            response = await self.session.request(method, self.url + path, json=payload)
            self.assertEqual(response.status, 401)
