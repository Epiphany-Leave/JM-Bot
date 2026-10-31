import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from jm_bot.store import StateStore


class StoreTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.data = self.root / "data"

    def test_first_init_stable_secrets_and_old_environment_only_used_once(self):
        first = StateStore(self.data, {"ALLOWED_USERS": "2,1", "ALLOWED_GROUPS": "10", "GROUP_ALLOW_ALL": "false"})
        self.assertEqual(first.get_permissions(), {"allowed_users": ["1", "2"], "allowed_groups": ["10"], "group_allow_all": False})
        second = StateStore(self.data, {"ALLOWED_USERS": "99", "GROUP_ALLOW_ALL": "true"})
        self.assertEqual(second.state, first.state)
        self.assertGreaterEqual(len(first.state["onebot_token"]), 32)
        self.assertNotEqual(first.state["onebot_token"], first.state["napcat_token"])
        if os.name != "nt":
            self.assertEqual(self.data.stat().st_mode & 0o777, 0o700)
            self.assertEqual((self.data / "config.json").stat().st_mode & 0o777, 0o600)

    def test_saved_permissions_available_to_new_instance(self):
        store = StateStore(self.data, {})
        changed = {"allowed_users": ["002", "1", "2"], "allowed_groups": ["55"], "group_allow_all": False}
        store.save_permissions(changed)
        expected = {"allowed_users": ["1", "2"], "allowed_groups": ["55"], "group_allow_all": False}
        self.assertEqual(store.get_permissions(), expected)
        self.assertEqual(StateStore(self.data, {}).get_permissions(), expected)

    def test_failed_save_leaves_file_and_live_state_intact(self):
        store = StateStore(self.data, {})
        old_state = store.get_permissions()
        old_file = store.path.read_bytes()
        with patch("jm_bot.store.os.replace", side_effect=OSError("disk error")):
            with self.assertRaises(OSError):
                store.save_permissions({"allowed_users": ["1"], "allowed_groups": [], "group_allow_all": False})
        self.assertEqual(store.get_permissions(), old_state)
        self.assertEqual(store.path.read_bytes(), old_file)
        self.assertEqual(StateStore(self.data, {}).get_permissions(), old_state)

    def test_legacy_webui_backup_and_unrelated_fields_preserved(self):
        store = StateStore(self.data, {})
        directory = self.root / "napcat"
        directory.mkdir()
        path = directory / "webui.json"
        old = {"token": "old", "port": 9999, "theme": {"name": "custom"}, "enable2FA": False}
        path.write_text(json.dumps(old), encoding="utf-8")
        store.prepare_napcat(directory)
        self.assertEqual(json.loads((directory / "webui.json.before-jm-bot").read_text()), old)
        current = json.loads(path.read_text())
        self.assertEqual(current["theme"], old["theme"])
        self.assertEqual(current["token"], store.state["napcat_token"])
        store.prepare_napcat(directory)
        self.assertEqual(json.loads((directory / "webui.json.before-jm-bot").read_text()), old)

    def test_2fa_refuses_takeover_without_changing_configuration(self):
        store = StateStore(self.data, {})
        directory = self.root / "napcat"
        directory.mkdir()
        path = directory / "webui.json"
        path.write_text(json.dumps({"enable2FA": True, "totpSecret": "original", "token": "old"}), encoding="utf-8")
        previous = path.read_bytes()
        with self.assertRaises(ValueError):
            store.prepare_napcat(directory)
        self.assertEqual(path.read_bytes(), previous)

    def test_invalid_existing_state_is_never_overwritten_by_environment(self):
        original = StateStore(self.data, {}).state
        cases = [b"{invalid JSON", json.dumps({**original, "schema_version": 2}).encode(),
                 json.dumps({**original, "onebot_token": "short"}).encode(),
                 json.dumps({**original, "permissions": {"allowed_users": ["not-an-id"],
                            "allowed_groups": [], "group_allow_all": False}}).encode()]
        path = self.data / "config.json"
        for invalid in cases:
            with self.subTest(invalid=invalid):
                path.write_bytes(invalid)
                with self.assertRaises(ValueError):
                    StateStore(self.data, {"ALLOWED_USERS": "999"})
                self.assertEqual(path.read_bytes(), invalid)

    def test_invalid_existing_webui_is_not_overwritten(self):
        store = StateStore(self.data, {})
        directory = self.root / "napcat"
        directory.mkdir()
        path = directory / "webui.json"
        for invalid in (b"{invalid JSON", b"[]"):
            path.write_bytes(invalid)
            with self.assertRaises(ValueError):
                store.prepare_napcat(directory)
            self.assertEqual(path.read_bytes(), invalid)
