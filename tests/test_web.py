import asyncio
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import patch

import aiohttp

from jm_bot.store import StateStore
from jm_bot import __version__
from jm_bot.proxy import ProxyError
from tests.test_bot import event, make_pdf
from tests.webui_fixture import running_webui, PASSWORD


class WebTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.fixture = running_webui(self.root)
        self.url, self.runtime, self.peer = await self.fixture.__aenter__()
        self.session = aiohttp.ClientSession(cookie_jar=aiohttp.CookieJar(unsafe=True))

    async def asyncTearDown(self):
        await self.session.close()
        await self.fixture.__aexit__(None, None, None)
        self.temp.cleanup()

    async def login(self):
        response = await self.session.post(self.url + "/api/auth/login", json={"password": PASSWORD})
        self.assertEqual(response.status, 200)
        self.assertTrue(response.cookies["jm_session"]["httponly"])
        self.assertEqual(response.cookies["jm_session"]["samesite"], "Strict")

    async def allow(self, users=None, groups=None, all_members=True):
        response = await self.session.put(self.url + "/api/permissions", json={
            "allowed_users": users or [], "allowed_groups": groups or [], "group_allow_all": all_members})
        self.assertEqual(response.status, 200, await response.text())

    async def test_auth_rate_limit_source_check_logout_and_health_without_qq(self):
        response = await self.session.get(self.url + "/healthz")
        self.assertEqual(response.status, 200)
        self.assertEqual(await response.json(), {"ok": True, "service": "jm-bot", "version": __version__})
        self.assertEqual(response.headers["Cache-Control"], "no-store")
        text = await response.text()
        for key in ("onebot_token", "napcat_token", "proxy_password", "proxy_control_token"):
            self.assertNotIn(self.runtime.store.state[key], text)
        self.assertNotIn(PASSWORD, text)
        response = await self.session.get(self.url + "/api/auth/session")
        self.assertFalse((await response.json())["authenticated"])
        self.assertEqual((await self.session.get(self.url + "/api/permissions")).status, 401)
        self.assertEqual((await self.session.get(self.url + "/api/qq/qrcode")).status, 401)
        response = await self.session.post(self.url + "/api/auth/login", json={"password": PASSWORD},
                                           headers={"Origin": "http://evil.invalid"})
        self.assertEqual(response.status, 403)
        await self.login()
        response = await self.session.get(self.url + "/api/auth/session")
        self.assertTrue((await response.json())["authenticated"])
        response = await self.session.get(self.url + "/api/status")
        self.assertEqual((await response.json())["version"], __version__)
        text = await response.text()
        self.assertNotIn(self.runtime.store.state["onebot_token"], text)
        self.assertNotIn(self.runtime.store.state["napcat_token"], text)
        self.assertNotIn(PASSWORD, text)
        self.assertEqual((await self.session.post(self.url + "/api/auth/logout", json={})).status, 200)
        self.assertEqual((await self.session.get(self.url + "/api/status")).status, 401)
        for _ in range(5):
            response = await self.session.post(self.url + "/api/auth/login", json={"password": "bad"})
            self.assertEqual(response.status, 401)
        response = await self.session.post(self.url + "/api/auth/login", json={"password": PASSWORD})
        self.assertEqual(response.status, 429)

    async def test_proxy_error_metadata_is_safe_and_optional_http_status(self):
        await self.login()
        for http_status in (None, 403):
            error = ProxyError("订阅下载失败", code="subscription_http", stage="subscription_fetch_direct",
                               next_step="请检查订阅或上传文件。", http_status=http_status)
            with patch.object(self.runtime.proxy, "import_subscription", side_effect=error):
                response = await self.session.post(self.url + "/api/proxy/import", json={"url": "https://private.invalid/TOKEN"})
            self.assertEqual(response.status, 503)
            payload = await response.json()
            self.assertEqual(payload["error"], "订阅下载失败")
            self.assertEqual(payload["next_step"], error.next_step)
            self.assertEqual(payload["code"], error.code)
            self.assertEqual(payload["stage"], error.stage)
            self.assertNotIn("TOKEN", await response.text())
            self.assertEqual(payload.get("http_status"), http_status)
            self.assertEqual("http_status" in payload, http_status is not None)

    async def test_qr_groups_live_permissions_and_persisted_restart(self):
        await self.login()
        response = await self.session.get(self.url + "/api/qq/qrcode")
        self.assertEqual(response.content_type, "image/svg+xml")
        self.assertIn(b"<svg", await response.read())
        self.assertEqual(response.headers["Cache-Control"], "no-store")
        self.assertEqual((await self.session.post(self.url + "/api/qq/refresh", json={})).status, 200)
        self.peer.logged_in = True
        await self.runtime.poll()
        self.assertFalse(self.runtime.status()["ready"])
        response = await self.session.get(self.url + "/api/groups")
        self.assertEqual(len((await response.json())["groups"]), 2)
        await self.allow(groups=["20001"])
        self.assertTrue(self.runtime.status()["ready"])
        self.assertEqual(self.runtime.bot.settings.allowed_groups, frozenset({"20001"}))
        self.assertEqual(StateStore(self.root / "data", env={}).get_permissions()["allowed_groups"], ["20001"])
        await self.runtime.poll()
        self.assertEqual(self.peer.config_writes, 1)

    async def test_test_pdf_success_failure_and_permission_denial(self):
        await self.login()
        response = await self.session.post(self.url + "/api/test-file", json={"kind": "group", "target_id": "20001"})
        self.assertEqual(response.status, 400)
        self.assertFalse(self.peer.uploads)
        await self.allow(users=["10001"], groups=["20001"])
        for kind, target in (("private", "10001"), ("group", "20001")):
            response = await self.session.post(self.url + "/api/test-file", json={"kind": kind, "target_id": target})
            self.assertEqual((await response.json())["status"], "success")
            self.assertFalse(list((self.root / "downloads").iterdir()))
        self.peer.reject_upload = True
        response = await self.session.post(self.url + "/api/test-file", json={"kind": "group", "target_id": "20001"})
        result = await response.json()
        self.assertEqual(result["status"], "retained")
        self.assertTrue((self.root / result["path"]).exists())
        self.assertEqual(len(self.peer.uploads), 3)

    async def test_permissions_save_failure_is_not_applied_and_invalid_ids_rejected(self):
        await self.login()
        await self.allow(groups=["20001"])
        with patch("jm_bot.store.atomic_json", side_effect=OSError("full disk")):
            response = await self.session.put(self.url + "/api/permissions", json={
                "allowed_users": [], "allowed_groups": [], "group_allow_all": True})
        self.assertEqual(response.status, 500)
        self.assertIn("20001", self.runtime.bot.settings.allowed_groups)
        response = await self.session.put(self.url + "/api/permissions", json={
            "allowed_users": [True], "allowed_groups": [], "group_allow_all": True})
        self.assertEqual(response.status, 400)

    async def test_missing_upload_ack_keeps_pdf_and_does_not_retry(self):
        await self.login()
        await self.allow(groups=["20001"])
        self.peer.drop_upload_ack = True
        self.runtime.bot.client.api_timeout = 0.05
        response = await self.session.post(self.url + "/api/test-file", json={"kind": "group", "target_id": "20001"})
        result = await response.json()
        self.assertEqual(result["status"], "retained")
        self.assertTrue((self.root / result["path"]).is_file())
        await asyncio.sleep(0.1)
        self.assertEqual(len(self.peer.uploads), 1)

    async def test_cleanup_error_does_not_turn_confirmed_upload_into_failure(self):
        await self.login()
        await self.allow(groups=["20001"])
        with patch("jm_bot.bot.shutil.rmtree", side_effect=PermissionError("ACL")):
            response = await self.session.post(self.url + "/api/test-file", json={"kind": "group", "target_id": "20001"})
            result = await response.json()
        self.assertEqual(response.status, 200)
        self.assertEqual(result["status"], "success")
        self.assertIn("清理失败", result["message"])
        self.assertTrue((self.root / result["path"]).exists())
        self.assertEqual(len(self.peer.uploads), 1)

    async def test_download_does_not_block_web_and_permissions_change_next_request(self):
        await self.login()
        await self.allow(groups=["20001"])
        entered, release = threading.Event(), threading.Event()
        def slow_download(album, folder, *, session=None):
            entered.set()
            if not release.wait(5):
                raise TimeoutError("test worker stalled")
            return make_pdf(album, folder)
        with patch("jm_bot.bot.download_pdf", side_effect=slow_download):
            await self.peer.emit(event(user=10003, group=20001))
            try:
                self.assertTrue(await asyncio.to_thread(entered.wait, 2))
                response = await asyncio.wait_for(self.session.get(self.url + "/api/status"), 1)
                self.assertTrue((await response.json())["busy"])
                await self.allow(groups=[])
                await self.peer.emit(event(user=10004, group=20001))
                async with asyncio.timeout(2):
                    while not any("未授权" in text for text in self.peer.messages):
                        await asyncio.sleep(0.01)
            finally:
                release.set()
            async with asyncio.timeout(3):
                while self.runtime.bot.busy:
                    await asyncio.sleep(0.01)
        self.assertEqual(len(self.peer.uploads), 1)
        self.assertEqual(self.runtime.bot.last_result["status"], "success")
