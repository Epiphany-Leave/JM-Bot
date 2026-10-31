import copy
import hashlib
import json
import tempfile
import unittest
from pathlib import Path

import aiohttp
from aiohttp import web

from jm_bot.napcat import NapCat, NapCatError


class NapCatTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.calls = []
        self.login_count = 0
        self.reject_once = False
        self.reject_always = False
        self.two_factor = False
        self.bad_auth_code = False
        self.bad_set_code = False
        self.fail_set = False
        self.ignore_set = False
        self.config = {
            "network": {
                "httpServers": [{"name": "existing-http", "port": 3000}],
                "websocketServers": [{"name": "ws", "port": 3001, "extra": "keep"},
                                     {"name": "other", "port": 4001}],
                "websocketClients": [{"name": "existing-client", "url": "ws://example"}],
                "plugins": [{"name": "existing-plugin"}],
            },
            "musicSignUrl": "unchanged",
        }

        async def handler(request):
            path = request.match_info["path"]
            body = await request.json()
            self.calls.append((path, body, request.headers.get("Authorization")))
            if path == "auth/login":
                self.login_count += 1
                self.assertEqual(body, {"hash": hashlib.sha256(b"secret.napcat").hexdigest()})
                data = {"require2FA": True} if self.two_factor else {"Credential": f"credential-{self.login_count}"}
                return web.json_response({"code": False if self.bad_auth_code else 0, "message": "success", "data": data})
            if self.reject_always or self.reject_once:
                self.reject_once = False
                return web.json_response({"code": 1, "message": "Unauthorized"})
            self.assertEqual(request.headers.get("Authorization"), f"Bearer credential-{self.login_count}")
            if path == "OB11Config/GetConfig":
                data = self.config
            elif path == "OB11Config/SetConfig":
                if self.fail_set:
                    return web.json_response({"code": 1, "message": "Error: failed"})
                if self.bad_set_code:
                    return web.json_response({"code": False, "message": "success", "data": None})
                config = json.loads(body["config"])
                if not self.ignore_set:
                    self.config = config
                data = None
            elif path == "QQLogin/CheckLoginStatus":
                data = {"isLogin": False, "isOffline": True, "qrcodeurl": ""}
            elif path == "QQLogin/RefreshQRcode":
                data = {"qrcodeurl": "", "restarting": True}
            else:
                return web.json_response({"code": 1, "message": "Not Login"})
            return web.json_response({"code": 0, "message": "success", "data": data})

        app = web.Application()
        app.router.add_post("/api/{path:.*}", handler)
        runner = web.AppRunner(app)
        await runner.setup()
        self.addAsyncCleanup(runner.cleanup)
        site = web.TCPSite(runner, "127.0.0.1", 0)
        await site.start()
        port = site._server.sockets[0].getsockname()[1]
        session = aiohttp.ClientSession()
        self.addAsyncCleanup(session.close)
        self.client = NapCat(session, f"http://127.0.0.1:{port}", "secret", "onebot-secret", Path(self.temp.name))

    async def test_auth_and_status_over_http(self):
        status = await self.client.login_status()
        self.assertFalse(status["isLogin"])
        self.assertTrue(status["isOffline"])
        self.assertEqual(await self.client.refresh_qrcode(), {"qrcodeurl": "", "restarting": True})
        self.assertEqual(self.login_count, 1)

    async def test_expired_credential_reauthenticates_once(self):
        await self.client.login_status()
        self.reject_once = True
        self.assertFalse((await self.client.login_status())["isLogin"])
        self.assertEqual(self.login_count, 2)
        self.reject_always = True
        with self.assertRaises(NapCatError):
            await self.client.login_status()
        self.assertEqual(self.login_count, 3)

    async def test_error_response_and_2fa_cannot_look_successful(self):
        with self.assertRaises(NapCatError):
            await self.client.call("unknown")
        self.client.credential = None
        self.two_factor = True
        before = len(self.calls)
        with self.assertRaisesRegex(NapCatError, "2FA|二次验证"):
            await self.client.login_status()
        self.assertEqual([c[0] for c in self.calls[before:]], ["auth/login"])

    async def test_boolean_success_code_is_rejected_for_login_and_config_write(self):
        self.bad_auth_code = True
        with self.assertRaises(NapCatError):
            await self.client.login_status()
        self.assertFalse(any(path == "QQLogin/CheckLoginStatus" for path, _, _ in self.calls))
        self.bad_auth_code = False
        self.bad_set_code = True
        before = copy.deepcopy(self.config)
        with self.assertRaises(NapCatError):
            await self.client.ensure_onebot()
        self.assertEqual(self.config, before)

    async def test_adopt_default_ws_preserve_others_and_repeat_safely(self):
        original = copy.deepcopy(self.config)
        config_file = Path(self.temp.name) / "onebot11_123.json"
        config_file.write_text("original", encoding="utf-8")
        await self.client.ensure_onebot()
        network = self.config["network"]
        managed = next(s for s in network["websocketServers"] if s["name"] == "jm-bot")
        self.assertEqual(managed["extra"], "keep")
        self.assertEqual((managed["host"], managed["port"], managed["token"]), ("0.0.0.0", 3001, "onebot-secret"))
        self.assertIn({"name": "other", "port": 4001}, network["websocketServers"])
        for key in ("httpServers", "websocketClients", "plugins"):
            self.assertEqual(network[key], original["network"][key])
        self.assertEqual(self.config["musicSignUrl"], "unchanged")
        self.assertEqual(config_file.with_name(config_file.name + ".before-jm-bot").read_text(), "original")
        await self.client.ensure_onebot()
        self.assertEqual(sum(path == "OB11Config/SetConfig" for path, _, _ in self.calls), 1)

    async def test_conflicts_do_not_change_any_configuration(self):
        for servers in ([{"name": "foreign", "port": 3001}],
                        [{"name": "jm-bot", "port": 3001}, {"name": "jm-bot", "port": 4000}],
                        [{"name": "ws", "port": 3001}, {"name": "foreign", "port": 3001}]):
            self.config["network"]["websocketServers"] = servers
            before = copy.deepcopy(self.config)
            with self.assertRaises(NapCatError):
                await self.client.ensure_onebot()
            self.assertEqual(self.config, before)
        self.assertFalse(any(path == "OB11Config/SetConfig" for path, _, _ in self.calls))

    async def test_failed_set_and_unpersisted_set_are_errors(self):
        before = copy.deepcopy(self.config)
        self.fail_set = True
        with self.assertRaises(NapCatError):
            await self.client.ensure_onebot()
        self.assertEqual(self.config, before)
        self.fail_set = False
        self.ignore_set = True
        with self.assertRaises(NapCatError):
            await self.client.ensure_onebot()
        self.assertEqual(self.config, before)
