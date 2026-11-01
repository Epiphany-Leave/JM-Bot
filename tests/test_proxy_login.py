from pathlib import Path
import tempfile
import unittest

import aiohttp

from tests.webui_fixture import PASSWORD, running_webui


class ProxyLoginTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.fixture = running_webui(Path(self.temp.name))
        self.url, self.runtime, _ = await self.fixture.__aenter__()
        self.session = aiohttp.ClientSession(cookie_jar=aiohttp.DummyCookieJar())

    async def asyncTearDown(self):
        await self.session.close()
        await self.fixture.__aexit__(None, None, None)
        self.temp.cleanup()

    async def test_direct_http_login_ignores_forwarded_headers(self):
        for origin in (None, self.url):
            with self.subTest(origin=origin):
                headers = {"X-Forwarded-Proto": "https",
                           "X-Forwarded-Host": "other.example.test",
                           "Forwarded": "proto=https;host=other.example.test"}
                if origin:
                    headers["Origin"] = origin
                response = await self.session.post(
                    self.url + "/api/auth/login", json={"password": PASSWORD}, headers=headers)
                self.assertEqual(response.status, 200, await response.text())
                self.assertFalse(response.cookies["jm_session"]["secure"])

    async def test_https_proxy_login_and_permission_update(self):
        headers = {"Host": "manage.example.test", "Origin": "https://manage.example.test",
                   "Sec-Fetch-Site": "same-origin"}
        response = await self.session.post(
            self.url + "/api/auth/login", json={"password": PASSWORD}, headers=headers)
        self.assertEqual(response.status, 200, await response.text())
        cookie = response.cookies["jm_session"]
        self.assertTrue(cookie["secure"])
        self.assertTrue(cookie["httponly"])
        self.assertEqual(cookie["samesite"], "Strict")
        # The fixture transport is HTTP; send the simulated HTTPS browser cookie explicitly.
        cookies = {"jm_session": cookie.value}
        permissions = {"allowed_users": ["10001"], "allowed_groups": [], "group_allow_all": True}
        response = await self.session.put(
            self.url + "/api/permissions", json=permissions, headers=headers, cookies=cookies)
        self.assertEqual(response.status, 200, await response.text())
        self.assertEqual(self.runtime.store.get_permissions(), permissions)
        response = await self.session.get(
            self.url + "/api/auth/session", headers=headers, cookies=cookies)
        self.assertTrue((await response.json())["authenticated"])
        response = await self.session.post(
            self.url + "/api/auth/logout", json={}, headers=headers, cookies=cookies)
        self.assertEqual(response.status, 200, await response.text())
        response = await self.session.get(
            self.url + "/api/auth/session", headers=headers, cookies=cookies)
        self.assertFalse((await response.json())["authenticated"])

    async def test_rejects_foreign_or_invalid_origins_and_cross_site_requests(self):
        origins = ("https://evil.example.test", "https://sub.manage.example.test",
                   "https://manage.example.test:444", "null",
                   "https://manage.example.test/path", "not-an-origin")
        for origin in origins:
            with self.subTest(origin=origin):
                response = await self.session.post(
                    self.url + "/api/auth/login", json={"password": PASSWORD}, headers={
                        "Host": "manage.example.test", "Origin": origin,
                        "X-Forwarded-Host": origin.removeprefix("https://"),
                        "X-Forwarded-Proto": "https", "Forwarded": f'proto=https;host="{origin}"'})
                self.assertEqual(response.status, 403)
        response = await self.session.post(
            self.url + "/api/auth/login", json={"password": PASSWORD}, headers={
                "Host": "manage.example.test", "Origin": "https://manage.example.test",
                "Sec-Fetch-Site": "cross-site"})
        self.assertEqual(response.status, 403)

    async def test_legacy_bookmarks_redirect_to_management_page(self):
        for path in ("/webui", "/webui/", "/webui/web_login"):
            with self.subTest(path=path):
                response = await self.session.get(self.url + path)
                self.assertEqual(response.status, 200)
                self.assertTrue(response.history)
                self.assertEqual(response.history[0].headers["Location"], "/")
                self.assertEqual(response.url.path, "/")
                self.assertIn('id="login-view"', await response.text())
