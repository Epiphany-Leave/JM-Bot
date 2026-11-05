"""Real local HTTP + WebSocket peers, replacing only the external QQ service."""
import asyncio
from contextlib import asynccontextmanager
import hashlib
import json
import base64
from pathlib import Path

import aiohttp
from aiohttp import web
import pikepdf
import websockets
import yaml

from jm_bot.bot import JMBot
from jm_bot.config import Settings
from jm_bot.napcat import NapCat
from jm_bot.proxy import ManagedProxy
from jm_bot.store import StateStore
from jm_bot.web import Runtime, create_app


PASSWORD = "LocalTestPassword2026"
ROOT = Path(__file__).resolve().parent.parent


class MockQQ:
    def __init__(self, store):
        self.store = store
        self.logged_in = False
        self.reject_upload = False
        self.drop_upload_ack = False
        self.uploads = []
        self.messages = []
        self.sockets = set()
        self.config_writes = 0
        self.config = {"network": {"websocketServers": [{"name": "ws", "port": 3001, "token": "old"}],
                                   "httpServers": [], "websocketClients": []}, "debug": False}

    async def http(self, request):
        path = request.match_info["path"]
        body = await request.json()
        if path == "auth/login":
            digest = hashlib.sha256((self.store.state["napcat_token"] + ".napcat").encode()).hexdigest()
            if body != {"hash": digest}:
                return web.json_response({"code": -1, "message": "token is invalid"})
            data = {"Credential": "local-mock-credential"}
        elif request.headers.get("Authorization") != "Bearer local-mock-credential":
            return web.json_response({"code": -1, "message": "Unauthorized"})
        elif path == "QQLogin/CheckLoginStatus":
            data = {"isLogin": self.logged_in, "isOffline": False,
                    "loginPhase": "online" if self.logged_in else "qrcode_ready", "loginError": ""}
        elif path == "QQLogin/GetQQLoginQrcode":
            data = {"qrcode": "https://example.invalid/qq-login-test"}
        elif path == "QQLogin/RefreshQRcode":
            data = {"qrcodeurl": "https://example.invalid/qq-login-test-refreshed"}
        elif path == "OB11Config/GetConfig":
            data = self.config
        elif path == "OB11Config/SetConfig":
            self.config = json.loads(body["config"])
            self.config_writes += 1
            data = None
        else:
            return web.json_response({"code": -1, "message": "Unknown"})
        return web.json_response({"code": 0, "message": "success", "data": data})

    async def ws(self, socket):
        if socket.request.headers.get("Authorization") != "Bearer " + self.store.state["onebot_token"]:
            await socket.close(code=1008)
            return
        self.sockets.add(socket)
        try:
            async for raw in socket:
                request = json.loads(raw)
                action, params = request["action"], request["params"]
                code, data = 0, None
                if action == "get_login_info":
                    data = {"user_id": 99999, "nickname": "本地测试机器人"}
                elif action == "get_status":
                    data = {"online": self.logged_in, "good": self.logged_in}
                elif action == "get_group_list":
                    data = [{"group_id": 20001, "group_name": "测试文件群"},
                            {"group_id": 20002, "group_name": "未授权的群"}]
                elif action.startswith("upload_"):
                    path = Path(params["file"])
                    with pikepdf.open(path) as pdf:
                        if len(pdf.pages) != 1:
                            raise AssertionError("test PDF must be readable and one page")
                    self.uploads.append({"action": action, **params})
                    if self.drop_upload_ack:
                        continue
                    if self.reject_upload:
                        code = 500
                elif action.startswith("send_"):
                    self.messages.append(params["message"][0]["data"]["text"])
                else:
                    code = 1404
                await socket.send(json.dumps({"status": "ok" if code == 0 else "failed", "retcode": code,
                                              "data": data, "echo": request["echo"]}))
        finally:
            self.sockets.discard(socket)

    async def emit(self, event):
        for socket in list(self.sockets):
            await socket.send(json.dumps(event))


class MockMihomo:
    def __init__(self, store):
        self.store = store
        self.config = {}
        self.selected = "REJECT"
        self.fail_probe = False

    async def http(self, request):
        if request.path == "/generate_204":
            expected = "Basic " + base64.b64encode(("jm:" + self.store.state["proxy_password"]).encode()).decode()
            if request.headers.get("Proxy-Authorization") != expected:
                return web.Response(status=407)
            return web.Response(status=502 if self.fail_probe else 204)
        if request.headers.get("Authorization") != "Bearer " + self.store.state["proxy_control_token"]:
            return web.Response(status=401)
        if request.path == "/version":
            return web.json_response({"version": "mock-core"})
        if request.path == "/configs":
            self.config = yaml.safe_load((await request.json())["payload"])
            self.selected = self.config["proxy-groups"][0]["proxies"][0]
            return web.Response(status=204)
        if request.path == "/providers/proxies/subscription":
            item = self.config.get("proxy-providers", {}).get("subscription")
            if not item:
                return web.Response(status=404)
            source = yaml.safe_load(Path(item["path"]).read_text(encoding="utf-8"))
            nodes = [{"name": node["name"], "type": node["type"]} for node in source["proxies"]]
            return web.json_response({"proxies": nodes})
        if request.path == "/providers/proxies/subscription/healthcheck":
            return web.Response(status=204)
        if request.path == "/proxies/JM_PROXY":
            if request.method == "PUT":
                self.selected = (await request.json())["name"]
                return web.Response(status=204)
            return web.json_response({"now": self.selected})
        return web.Response(status=404)


@asynccontextmanager
async def running_webui(directory, password=PASSWORD):
    store = StateStore(directory / "data", env={})
    store.prepare_napcat(directory / "napcat-config")
    peer = MockQQ(store)
    core = MockMihomo(store)
    peer.proxy_core = core
    upstream = web.Application()
    upstream.router.add_post("/api/{path:.*}", peer.http)
    upstream.router.add_route("*", "/{path:.*}", core.http)
    upstream_runner = web.AppRunner(upstream, access_log=None)
    await upstream_runner.setup()
    upstream_site = web.TCPSite(upstream_runner, "127.0.0.1", 0)
    await upstream_site.start()
    http_port = upstream_site._server.sockets[0].getsockname()[1]
    try:
        async with websockets.serve(peer.ws, "127.0.0.1", 0) as ws_server:
            ws_port = ws_server.sockets[0].getsockname()[1]
            async with aiohttp.ClientSession() as session:
                settings = Settings(ws_url=f"ws://127.0.0.1:{ws_port}/", token=store.state["onebot_token"],
                                    download_dir=directory / "downloads", group_allow_all=True)
                bot = JMBot(settings)
                napcat = NapCat(session, f"http://127.0.0.1:{http_port}", store.state["napcat_token"],
                                store.state["onebot_token"], directory / "napcat-config")
                proxy = ManagedProxy(store, directory / "proxy-data", session,
                                     api_url=f"http://127.0.0.1:{http_port}",
                                     server_url=f"http://127.0.0.1:{http_port}",
                                     test_url="http://probe.invalid/generate_204")
                proxy.prepare()
                runtime = Runtime(store, bot, napcat, proxy=proxy)
                app = create_app(runtime, password, ROOT / "samples" / "test.pdf")
                runner = web.AppRunner(app, access_log=None)
                await runner.setup()
                try:
                    site = web.TCPSite(runner, "127.0.0.1", 0)
                    await site.start()
                    port = site._server.sockets[0].getsockname()[1]
                    async with asyncio.timeout(5):
                        while not bot.client.connected:
                            await asyncio.sleep(0.01)
                    yield f"http://127.0.0.1:{port}", runtime, peer
                finally:
                    await runner.cleanup()
    finally:
        await upstream_runner.cleanup()
