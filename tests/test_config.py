import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from jm_bot.config import Settings


class ConfigTests(unittest.TestCase):
    def settings(self, **values):
        with patch.dict(os.environ, {"ONEBOT_TOKEN": "local-test-token", **values}, clear=True):
            return Settings.from_env()

    def test_defaults_deny_everyone_and_use_internal_connection(self):
        config = self.settings()
        self.assertEqual(config.ws_url, "ws://napcat:3001/")
        self.assertFalse(config.allowed_users)
        self.assertFalse(config.allowed_groups)

    def test_normalizes_ids_and_resolves_output_path(self):
        with tempfile.TemporaryDirectory() as folder:
            config = self.settings(ALLOWED_USERS=" 0010001,10002,10001 ",
                                   ALLOWED_GROUPS="020001", DOWNLOAD_DIR=folder)
        self.assertEqual(config.allowed_users, frozenset({"10001", "10002"}))
        self.assertEqual(config.allowed_groups, frozenset({"20001"}))
        self.assertEqual(config.download_dir, Path(folder).resolve())

    def test_invalid_allowlists_fail_closed_at_startup(self):
        for value in ("*", "all", "10001,", "10001,,10002", "0", "-1", "１２３", "abc"):
            with self.subTest(value=value), self.assertRaises(ValueError):
                self.settings(ALLOWED_USERS=value)

    def test_missing_placeholder_or_multiline_tokens_are_rejected(self):
        for token in ("", "REPLACE_WITH_YOUR_TOKEN", "test\r\nHeader: injected"):
            with self.subTest(token=token), self.assertRaises(ValueError):
                self.settings(ONEBOT_TOKEN=token)

    def test_only_ws_urls_without_embedded_credentials_are_accepted(self):
        for url in ("http://napcat:3001/", "ws:///", "ws://user:secret@napcat/", "ws://napcat/?access_token=secret", "ws://napcat:wrong/", "ws://napcat:65536/"):
            with self.subTest(url=url), self.assertRaises(ValueError):
                self.settings(ONEBOT_WS_URL=url)
