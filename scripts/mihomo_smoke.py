"""Linux/Docker test of the real Mihomo core against a local, controlled HTTP node.

Run from the repository root: python scripts/mihomo_smoke.py
No real subscription, VPN service or Internet connectivity is used by the test.
"""

import argparse
import asyncio
import base64
import json
from pathlib import Path
import secrets
import sys
import tempfile
import uuid

import aiohttp
import yaml

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from jm_bot.proxy import ManagedProxy, ProxyError
from scripts.container_smoke import docker


class SmokeStore:
    def __init__(self, root: Path):
        self.path = root / "state.json"
        self.state = {"proxy_control_token": secrets.token_urlsafe(32),
                      "proxy_password": secrets.token_urlsafe(32), "proxy": {}}

    def save_proxy(self, value):
        self.state["proxy"] = dict(value)
        self.path.write_text(json.dumps(self.state), encoding="utf-8")
        self.path.chmod(0o600)


async def smoke(image: str) -> None:
    if sys.platform != "linux":
        raise RuntimeError("This smoke check requires Linux and a local Docker daemon")
    name = "jm-mihomo-smoke-" + uuid.uuid4().hex[:12]
    fail_users = set()
    seen_users = []
    node_events = []

    class SmokeProxy(ManagedProxy):
        async def _probe(self):
            try:
                await super()._probe()
            except ProxyError:
                # Capture only controlled fixture names/health, before import rolls the core back.
                try:
                    provider = await self._api("GET", "/providers/proxies/subscription")
                    health = [(node["name"], node.get("alive"),
                               [item.get("alive") for item in node.get("extra", {}).values()])
                              for node in provider["proxies"]]
                    selected = await self._api("GET", "/proxies/AUTO")
                    print(f"Probe diagnostic: nodes={health}; AUTO={selected.get('now')}; fixture={node_events[-30:]}", flush=True)
                except ProxyError:
                    print("Probe diagnostic: core unavailable", flush=True)
                raise

    async def read_request(reader):
        raw = await asyncio.wait_for(reader.readuntil(b"\r\n\r\n"), 5)
        lines = raw.decode("latin-1").split("\r\n")
        headers = dict(line.split(":", 1) for line in lines[1:] if ":" in line)
        return lines[0].split(" ", 2)[0], {key.lower(): value.strip() for key, value in headers.items()}

    async def node(reader, writer):
        try:
            method, headers = await read_request(reader)
            auth = headers.get("proxy-authorization", "")
            username = base64.b64decode(auth.removeprefix("Basic ")).decode().split(":", 1)[0]
            if username not in {"alpha", "beta", "uri-alpha"}:
                writer.write(b"HTTP/1.1 407 Proxy Authentication Required\r\nContent-Length: 0\r\n\r\n")
                await writer.drain()
                return
            if method == "CONNECT":
                # Mihomo's HTTP outbound tunnels even a plain HTTP destination.
                writer.write(b"HTTP/1.1 200 Connection Established\r\n\r\n")
                await writer.drain()
                method, headers = await read_request(reader)
            # Ignore Mihomo's concurrent HEAD health checks in selection assertions.
            if "aiohttp" in headers.get("user-agent", ""):
                seen_users.append(username)
            status = b"502 Bad Gateway" if username in fail_users else b"204 No Content"
            node_events.append((username, method, int(status[:3])))
            writer.write(b"HTTP/1.1 " + status + b"\r\nContent-Length: 0\r\nConnection: close\r\n\r\n")
            await writer.drain()
        except (ValueError, UnicodeError, OSError, asyncio.IncompleteReadError, asyncio.TimeoutError):
            pass
        finally:
            writer.close()
            await writer.wait_closed()

    server = await asyncio.start_server(node, "0.0.0.0", 0)
    node_port = server.sockets[0].getsockname()[1]
    try:
        with tempfile.TemporaryDirectory(prefix="jm-mihomo-") as temporary:
            root = Path(temporary).resolve()
            store = SmokeStore(root)
            async with aiohttp.ClientSession(trust_env=False) as session:
                manager = SmokeProxy(store, root, session, test_url="http://smoke.invalid/generate_204")
                manager.prepare()
                try:
                    await docker("run", "--detach", "--name", name,
                                 "--publish", "127.0.0.1::9090", "--publish", "127.0.0.1::7890",
                                 "--mount", f"type=bind,src={root},dst={root}",
                                 image, "-d", str(root), "-f", str(root / "config.yaml"))
                    api_port = (await docker("port", name, "9090/tcp")).rsplit(":", 1)[-1]
                    proxy_port = (await docker("port", name, "7890/tcp")).rsplit(":", 1)[-1]
                    manager.api_url = f"http://127.0.0.1:{api_port}"
                    manager.server_url = f"http://127.0.0.1:{proxy_port}"
                    gateway = await docker("inspect", "--format",
                                           "{{range .NetworkSettings.Networks}}{{.Gateway}}{{end}}", name)
                    if not gateway:
                        raise RuntimeError("Docker bridge gateway was not found")

                    async def wait_core():
                        for attempt in range(30):
                            if (await manager.status())["available"]:
                                return
                            if attempt == 29:
                                raise RuntimeError("Mihomo did not start with the owned offline seed")
                            await asyncio.sleep(0.5)

                    await wait_core()
                    try:
                        await manager.ensure_ready()
                    except ProxyError:
                        pass
                    else:
                        raise AssertionError("Empty bootstrap must fail closed")

                    nodes = [{"name": user, "type": "http", "server": gateway,
                              "port": node_port, "username": user, "password": "smoke-node-password"}
                             for user in ("alpha", "beta")]
                    upload = yaml.safe_dump({"proxies": nodes, "mixed-port": 1,
                                             "external-controller": "evil.invalid:22",
                                             "tun": {"enable": True}, "rules": ["MATCH,DIRECT"]})
                    fail_users.add("alpha")
                    status = await manager.import_subscription(content=upload)
                    assert status["healthy"] and status["node"] == "AUTO", status
                    assert seen_users[-1] == "beta", "Cold AUTO must skip the failed first node"
                    assert (await manager._api("GET", "/proxies/AUTO"))["now"] == "beta"
                    print("PASS: cold AUTO selects the live second node when the first returns HTTP 502", flush=True)
                    fail_users.clear()
                    config = await manager._api("GET", "/configs")
                    assert config["mixed-port"] == 7890
                    assert not config.get("tun", {}).get("enable", False)
                    assert "subscription_url" not in status and "password" not in str(status)
                    assert set(yaml.safe_load((root / store.state["proxy"]["provider"]).read_text())) == {"proxies"}
                    await manager.select_node("beta")
                    assert seen_users[-1] == "beta"
                    good = dict(store.state["proxy"])
                    disk = (root / "config.yaml").read_bytes()

                    for invalid in (upload.replace("type: http", "type: not-a-real-type"),
                                    upload.replace("type: http", "type: direct")):
                        try:
                            await manager.import_subscription(content=invalid)
                        except ProxyError:
                            pass
                        else:
                            raise AssertionError("Invalid provider unexpectedly became active")
                        assert store.state["proxy"] == good
                        assert (root / "config.yaml").read_bytes() == disk
                        await manager.ensure_ready()
                        assert seen_users[-1] == "beta"

                    fail_users.add("alpha")
                    try:
                        await manager.select_node("alpha")
                    except ProxyError:
                        pass
                    else:
                        raise AssertionError("Broken selected node unexpectedly passed the probe")
                    assert store.state["proxy"] == good
                    fail_users.clear()
                    await manager.ensure_ready()
                    assert seen_users[-1] == "beta"
                    print("PASS: real core parse/load, owned listeners, node selection, rollback and HTTP proxy probe", flush=True)

                    # Mihomo's native provider parser is responsible for URI and Base64 formats.
                    uri = f"http://uri-alpha:smoke-node-password@{gateway}:{node_port}#uri-alpha"
                    for content in (uri, base64.b64encode(uri.encode()).decode()):
                        await manager.import_subscription(content=content)
                        assert seen_users[-1] == "uri-alpha"
                    print("PASS: native Mihomo URI and Base64 provider parsing", flush=True)

                    manager.prepare()
                    await docker("restart", name)
                    # Docker may allocate new host ports for an ephemeral publish on restart.
                    api_port = (await docker("port", name, "9090/tcp")).rsplit(":", 1)[-1]
                    proxy_port = (await docker("port", name, "7890/tcp")).rsplit(":", 1)[-1]
                    manager.api_url = f"http://127.0.0.1:{api_port}"
                    manager.server_url = f"http://127.0.0.1:{proxy_port}"
                    await wait_core()
                    restored = SmokeStore(root)
                    restored.state = json.loads(restored.path.read_text())
                    manager = SmokeProxy(restored, root, session, api_url=manager.api_url,
                                           server_url=manager.server_url, test_url=manager.test_url)
                    await manager.ensure_ready()
                    assert seen_users[-1] == "uri-alpha"
                    size = await docker("image", "inspect", "--format", "{{.Size}}", image)
                    memory = await docker("stats", "--no-stream", "--format", "{{.MemUsage}}", name)
                    print(f"Mihomo image size: {size} bytes; idle memory: {memory}", flush=True)
                    await docker("stop", "--time", "1", name)
                    try:
                        await manager.ensure_ready()
                    except ProxyError:
                        pass
                    else:
                        raise AssertionError("Unavailable core must fail closed")
                    assert not (await manager.status())["healthy"]
                    print("PASS: persisted restart recovery and unavailable-core failure", flush=True)
                finally:
                    try:
                        await docker("rm", "--force", name)
                    except (RuntimeError, FileNotFoundError):
                        pass
    finally:
        server.close()
        await server.wait_closed()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--image", default="metacubex/mihomo:v1.19.33")
    asyncio.run(smoke(parser.parse_args().image))
