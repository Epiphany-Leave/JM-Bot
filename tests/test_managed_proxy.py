import tempfile
from pathlib import Path
import unittest

import aiohttp
from aiohttp import web
import yaml

from jm_bot.proxy import GROUP, ManagedProxy, ProxyError


class Store:
    def __init__(self):
        self.state = {"proxy_control_token": "controller-secret", "proxy_password": "proxy-secret", "proxy": {}}

    def save_proxy(self, value):
        self.state["proxy"] = dict(value)


class ManagedProxyTests(unittest.IsolatedAsyncioTestCase):
    async def test_missing_saved_provider_keeps_recovery_seed_and_reports_error(self):
        self.store.save_proxy({"provider": "provider-missing.yaml", "node": "AUTO", "subscription_url": ""})
        self.proxy.prepare()
        config = yaml.safe_load((self.root / "config.yaml").read_text(encoding="utf-8"))
        self.assertNotIn("proxy-providers", config)
        self.assertEqual(config["proxy-groups"][0]["proxies"], ["REJECT"])
        with self.assertRaisesRegex(ProxyError, "缺失"):
            await self.proxy.ensure_ready()
        self.assertEqual(self.store.state["proxy"]["provider"], "provider-missing.yaml")

    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.store = Store()
        self.loaded = {}
        self.selected = "REJECT"
        self.fail_probe = False
        self.ambiguous_apply = False
        self.health_checks = 0
        self.auto_ready = False
        self.source = "proxies:\n- {name: alpha, type: http, server: 127.0.0.1, port: 8080}\n"
        app = web.Application()
        app.router.add_route("*", "/{tail:.*}", self.serve)
        self.runner = web.AppRunner(app)
        await self.runner.setup()
        site = web.TCPSite(self.runner, "127.0.0.1", 0)
        await site.start()
        self.base = f"http://127.0.0.1:{site._server.sockets[0].getsockname()[1]}"
        self.session = aiohttp.ClientSession(trust_env=False)
        self.proxy = ManagedProxy(self.store, self.root, self.session, api_url=self.base,
                                  server_url=self.base, test_url="http://probe.invalid/generate_204")
        self.proxy.prepare()

    async def asyncTearDown(self):
        await self.session.close()
        await self.runner.cleanup()
        self.temp.cleanup()

    async def serve(self, request):
        if request.path == "/generate_204":
            self.assertEqual(request.headers.get("Proxy-Authorization"), "Basic am06cHJveHktc2VjcmV0")
            if self.selected == "AUTO":
                self.assertTrue(self.auto_ready, "AUTO probe ran before synchronous health checks finished")
            return web.Response(status=502 if self.fail_probe else 204)
        if request.path == "/subscription":
            return web.Response(text=self.source)
        self.assertEqual(request.headers.get("Authorization"), "Bearer controller-secret")
        if request.path == "/version":
            return web.json_response({"version": "test"})
        if request.path == "/configs":
            candidate = yaml.safe_load((await request.json())["payload"])
            self.loaded = candidate
            self.selected = "REJECT"
            self.auto_ready = False
            if self.ambiguous_apply:
                self.ambiguous_apply = False
                return web.Response(status=500, text="secret node data must never escape")
            return web.Response(status=204)
        if request.path == "/providers/proxies/subscription/healthcheck":
            self.health_checks += 1
            self.auto_ready = True
            return web.Response(status=204)
        if request.path == "/providers/proxies/subscription":
            provider = self.loaded.get("proxy-providers", {}).get("subscription")
            if not provider:
                return web.Response(status=404)
            source = yaml.safe_load(Path(provider["path"]).read_text(encoding="utf-8"))
            if not isinstance(source, dict):
                return web.Response(status=500)
            return web.json_response({"proxies": source["proxies"]})
        if request.path == f"/proxies/{GROUP}":
            if request.method == "PUT":
                self.selected = (await request.json())["name"]
                return web.Response(status=204)
            return web.json_response({"now": self.selected})
        return web.Response(status=404)

    async def test_owned_config_restart_and_secret_redaction(self):
        result = await self.proxy.import_subscription(content=self.source + "mixed-port: 1\nexternal-controller: evil:9\nrules: [MATCH,DIRECT]\n")
        self.assertTrue(result["healthy"])
        self.assertEqual(result["nodes"], ["alpha"])
        self.assertEqual(self.health_checks, 1)
        self.assertEqual(self.loaded["proxy-providers"]["subscription"]["health-check"]["expected-status"], "204")
        self.assertEqual(self.loaded["mixed-port"], 7890)
        self.assertEqual(self.loaded["rules"], ["MATCH,JM_PROXY"])
        source = Path(self.loaded["proxy-providers"]["subscription"]["path"]).read_text(encoding="utf-8")
        self.assertEqual(set(yaml.safe_load(source)), {"proxies"})
        self.assertNotIn("secret", str(result))
        self.proxy.prepare()
        self.selected = "REJECT"
        await self.proxy.ensure_ready()
        self.assertEqual(self.selected, "AUTO")

    async def test_failed_import_preserves_last_good_disk_state_and_core(self):
        await self.proxy.import_subscription(content=self.source)
        previous = dict(self.store.state["proxy"])
        config = (self.root / "config.yaml").read_bytes()
        self.fail_probe = True
        with self.assertRaises(ProxyError):
            await self.proxy.import_subscription(content=self.source.replace("alpha", "beta"))
        self.assertEqual(self.store.state["proxy"], previous)
        self.assertEqual((self.root / "config.yaml").read_bytes(), config)
        self.assertEqual(self.selected, "AUTO")
        self.assertIn(previous["provider"], self.loaded["proxy-providers"]["subscription"]["path"])
        self.fail_probe = False
        await self.proxy.ensure_ready()

    async def test_ambiguous_apply_restores_previous_and_redacts_core_errors(self):
        await self.proxy.import_subscription(content=self.source)
        self.ambiguous_apply = True
        with self.assertRaises(ProxyError) as error:
            await self.proxy.import_subscription(content=self.source.replace("alpha", "beta"))
        self.assertNotIn("secret", str(error.exception))
        self.assertEqual(self.selected, "AUTO")

    async def test_reject_builtin_and_unresolved_dialer(self):
        for content in (self.source.replace("type: http", "type: direct"),
                        self.source.replace("port: 8080", "port: 8080, dialer-proxy: MISSING"),
                        "mixed-port: 22\n", "x" * (2 * 1024 * 1024 + 1)):
            with self.assertRaises(ProxyError):
                await self.proxy.import_subscription(content=content)
        with self.assertRaises(ProxyError):
            await self.proxy.ensure_ready()
        self.assertFalse(self.store.state["proxy"])

    async def test_subscription_refresh_and_selection_rollback(self):
        await self.proxy.import_subscription(url=self.base + "/subscription?private=secret")
        self.source = self.source + "- {name: beta, type: http, server: 127.0.0.1, port: 8081}\n"
        result = await self.proxy.refresh_subscription()
        self.assertNotIn("subscription", result)
        self.assertEqual(result["nodes"], ["alpha", "beta"])
        self.fail_probe = True
        with self.assertRaises(ProxyError):
            await self.proxy.select_node("beta")
        self.assertEqual(self.store.state["proxy"]["node"], "AUTO")
        self.assertEqual(self.selected, "AUTO")
        self.fail_probe = False
        self.assertEqual((await self.proxy.select_node("beta"))["node"], "beta")
        before = self.health_checks
        await self.proxy.select_node("AUTO")
        self.assertEqual(self.health_checks, before + 1)


if __name__ == "__main__":
    unittest.main()
