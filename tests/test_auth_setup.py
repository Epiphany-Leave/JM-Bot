import asyncio
import io
import json
import os
from contextlib import redirect_stdout
from pathlib import Path
import tempfile
import threading
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from aiohttp.test_utils import TestClient, TestServer

from jm_bot.store import StateStore, password_credential, verify_password
from jm_bot.web import create_app


class AuthSetupTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.store = StateStore(self.root, env={})
        self.client = await self.open_app(self.store)

    async def open_app(self, store, password=""):
        client = TestClient(TestServer(create_app(SimpleNamespace(store=store), password, manage_runtime=False)))
        await client.start_server()
        self.addAsyncCleanup(client.close)
        return client

    async def post(self, path, body, **kwargs):
        return await self.client.post("/api/auth/" + path, json=body, **kwargs)

    async def setup_password(self, password="密"):
        response = await self.post("setup", {"password": password, "confirm_password": password})
        self.assertEqual(response.status, 200, await response.text())
        return response

    async def test_first_setup_short_unicode_restart_and_stored_priority(self):
        response = await self.client.get("/api/auth/session")
        self.assertEqual(await response.json(), {"authenticated": False, "setup_required": True})
        for path in ("/api/permissions", "/api/qq/qrcode", "/api/proxy/status"):
            response = await self.client.get(path)
            self.assertEqual(response.status, 401)
            self.assertEqual(response.headers["Cache-Control"], "no-store")
            self.assertIn("Content-Security-Policy", response.headers)
        before = dict(self.store.state)
        response = await self.setup_password()
        self.assertTrue(response.cookies["jm_session"]["httponly"])
        self.assertEqual(response.cookies["jm_session"]["samesite"], "Strict")
        credential = self.store.state["admin_credential"]
        self.assertEqual(credential["iterations"], 600000)
        self.assertEqual(len(bytes.fromhex(credential["salt"])), 16)
        self.assertNotIn("密", self.store.path.read_text(encoding="utf-8"))
        self.assertEqual({k: v for k, v in self.store.state.items() if k != "admin_credential"}, before)
        persisted = StateStore(self.root, env={"ADMIN_PASSWORD": "other"})
        self.assertEqual(persisted.state, self.store.state)
        restarted = await self.open_app(persisted, password="override")
        response = await restarted.post("/api/auth/login", json={"password": "密"})
        self.assertEqual(response.status, 200)
        response = await restarted.get("/api/auth/session")
        self.assertEqual(await response.json(), {"authenticated": True, "setup_required": False})
        for secret in credential.values():
            if isinstance(secret, str):
                self.assertNotIn(secret, await response.text())
        self.assertEqual((await self.post("setup", {"password": "x", "confirm_password": "x"})).status, 409)

    async def test_validation_and_setup_rate_limit(self):
        for body in ({"password": "", "confirm_password": ""},
                     {"password": " \n", "confirm_password": " \n"},
                     {"password": "a", "confirm_password": "b"},
                     {"password": "a" * 1025, "confirm_password": "a" * 1025}, []):
            self.assertEqual((await self.post("setup", body)).status, 400)
        self.assertEqual((await self.post("setup", {"password": "x", "confirm_password": "x"})).status, 429)
        self.assertNotIn("admin_credential", self.store.state)

    async def test_setup_origin_json_https_and_change_authentication(self):
        body = {"password": "x", "confirm_password": "x"}
        response = await self.post("setup", body, headers={"Origin": "https://evil.invalid"})
        self.assertEqual(response.status, 403)
        self.assertEqual(response.headers["Cache-Control"], "no-store")
        self.assertEqual((await self.post("setup", body, headers={"Sec-Fetch-Site": "cross-site"})).status, 403)
        response = await self.client.post("/api/auth/setup", data="{}")
        self.assertEqual(response.status, 415)
        self.assertEqual((await self.post("password", {})).status, 401)
        response = await self.post("setup", body, headers={"Host": "admin.example", "Origin": "https://admin.example"})
        self.assertEqual(response.status, 200)
        self.assertTrue(response.cookies["jm_session"]["secure"])

    async def test_concurrent_setup_and_kdf_preserve_other_state_without_blocking_loop(self):
        entered, release = threading.Event(), threading.Event()
        real_hash = password_credential

        def slow_hash(password):
            entered.set()
            if not release.wait(5):
                raise TimeoutError("hash worker stalled")
            return real_hash(password)

        with patch("jm_bot.web.password_credential", side_effect=slow_hash):
            first = asyncio.create_task(self.post("setup", {"password": "first", "confirm_password": "first"}))
            try:
                self.assertTrue(await asyncio.to_thread(entered.wait, 2))
                response = await asyncio.wait_for(self.client.get("/api/auth/session"), 1)
                self.assertTrue((await response.json())["setup_required"])
                acl = {"allowed_users": ["42"], "allowed_groups": [], "group_allow_all": False}
                self.store.save_permissions(acl)
                second = asyncio.create_task(self.post("setup", {"password": "second", "confirm_password": "second"}))
            finally:
                release.set()
            self.assertEqual((await first).status, 200)
            self.assertEqual((await second).status, 409)
        self.assertEqual(StateStore(self.root, env={}).get_permissions(), acl)
        self.assertTrue(await asyncio.to_thread(verify_password, "first", self.store.state["admin_credential"]))

    async def test_change_invalidates_all_sessions_and_old_password(self):
        response = await self.setup_password("x")
        first_cookie = response.cookies["jm_session"].value
        # A second browser shares the same application and session registry.
        response = await self.post("login", {"password": "x"})
        second_cookie = response.cookies["jm_session"].value
        body = {"current_password": "bad", "new_password": "新", "confirm_password": "新"}
        self.assertEqual((await self.post("password", body)).status, 400)
        body["current_password"] = "x"
        response = await self.post("password", body)
        self.assertEqual(response.status, 200)
        self.assertIn("重新登录", (await response.json())["message"])
        self.assertEqual(response.cookies["jm_session"]["max-age"], "0")
        for cookie in (first_cookie, second_cookie):
            response = await self.client.get("/api/permissions", headers={"Cookie": "jm_session=" + cookie})
            self.assertEqual(response.status, 401)
        self.assertEqual((await self.post("login", {"password": "x"})).status, 401)
        self.assertEqual((await self.post("login", {"password": "新"})).status, 200)
        self.assertTrue(await asyncio.to_thread(verify_password, "新", StateStore(self.root, env={}).state["admin_credential"]))

    async def test_failed_setup_and_change_save_keep_live_and_disk_credentials(self):
        before = self.store.path.read_bytes()
        with patch("jm_bot.store.atomic_json", side_effect=OSError("full")):
            response = await self.post("setup", {"password": "x", "confirm_password": "x"})
        self.assertEqual(response.status, 500)
        self.assertNotIn("admin_credential", self.store.state)
        self.assertEqual(self.store.path.read_bytes(), before)
        await self.setup_password("x")
        before = self.store.path.read_bytes()
        credential = self.store.state["admin_credential"]
        with patch("jm_bot.store.atomic_json", side_effect=OSError("full")):
            response = await self.post("password", {"current_password": "x", "new_password": "y", "confirm_password": "y"})
        self.assertEqual(response.status, 500)
        self.assertEqual(self.store.state["admin_credential"], credential)
        self.assertEqual(self.store.path.read_bytes(), before)
        self.assertTrue((await (await self.client.get("/api/auth/session")).json())["authenticated"])


class CredentialMigrationTests(unittest.TestCase):
    def test_offline_cli_reset_preserves_all_other_settings_and_failed_confirmation(self):
        from jm_bot.__main__ import main

        with patch("jm_bot.__main__.logging.basicConfig"):
            self._check_cli_reset(main)

    def _check_cli_reset(self, main):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            with (patch.dict(os.environ, {"DATA_DIR": folder}),
                  patch("sys.argv", ["jm_bot", "--reset-admin-password"]),
                  patch("jm_bot.__main__.getpass.getpass") as prompt,
                  patch("jm_bot.__main__.argparse.ArgumentParser.error", side_effect=ValueError)):
                with self.assertRaises(ValueError):
                    main()
                prompt.assert_not_called()
            self.assertFalse((root / "config.json").exists())
            store = StateStore(root, env={})
            store.save_permissions({"allowed_users": ["42"], "allowed_groups": [], "group_allow_all": False})
            before = dict(store.state)
            with (patch.dict(os.environ, {"DATA_DIR": folder}),
                  patch("sys.argv", ["jm_bot", "--reset-admin-password"]),
                  patch("jm_bot.__main__.getpass.getpass", side_effect=["密", "密"]),
                  patch("jm_bot.__main__.asyncio.run") as serve, redirect_stdout(io.StringIO())):
                main()
                serve.assert_not_called()
            updated = StateStore(root, env={})
            self.assertTrue(verify_password("密", updated.state["admin_credential"]))
            self.assertEqual({k: v for k, v in updated.state.items() if k != "admin_credential"}, before)
            before_file = store.path.read_bytes()
            with (patch.dict(os.environ, {"DATA_DIR": folder}),
                  patch("sys.argv", ["jm_bot", "--reset-admin-password"]),
                  patch("jm_bot.__main__.getpass.getpass", side_effect=["a", "b"]),
                  patch("jm_bot.__main__.argparse.ArgumentParser.error", side_effect=ValueError)):
                with self.assertRaises(ValueError):
                    main()
            self.assertEqual(store.path.read_bytes(), before_file)

    def test_legacy_import_placeholder_missing_and_corruption(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            for value in ("", "REPLACE_WITH_PASSWORD", "  "):
                store = StateStore(root, env={"ADMIN_PASSWORD": value})
                self.assertNotIn("admin_credential", store.state)
            before = dict(store.state)
            store = StateStore(root, env={"ADMIN_PASSWORD": "a"})
            self.assertTrue(verify_password("a", store.state["admin_credential"]))
            self.assertEqual({k: v for k, v in store.state.items() if k != "admin_credential"}, before)
            self.assertEqual(StateStore(root, env={"ADMIN_PASSWORD": "b"}).state, store.state)
            valid = store.state["admin_credential"]
            for invalid in (None, {}, {**valid, "iterations": 1}, {**valid, "salt": "broken"}, {**valid, "hash": "broken"}):
                store.path.write_text(json.dumps({**store.state, "admin_credential": invalid}), encoding="utf-8")
                before_file = store.path.read_bytes()
                with self.assertRaises(ValueError):
                    StateStore(root, env={"ADMIN_PASSWORD": "b"})
                self.assertEqual(store.path.read_bytes(), before_file)
