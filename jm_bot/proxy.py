"""Own the Mihomo configuration; subscription files never own listeners or rules."""

import asyncio
import os
from pathlib import Path
import tempfile
from urllib.parse import quote, urlsplit
import uuid

import aiohttp
import yaml


class ProxyError(RuntimeError):
    """A safe, administrator-facing proxy error without subscription secrets."""


LIMIT = 2 * 1024 * 1024
GROUP = "JM_PROXY"
AUTO = "AUTO"
PROVIDER = "subscription"
BUILTINS = {"direct", "reject", "reject-drop", "compatible", "pass"}


def private_write(path: Path, content: str) -> None:
    descriptor, temporary = tempfile.mkstemp(prefix=".proxy-", dir=path.parent)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        os.chmod(temporary, 0o600)
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


class ManagedProxy:
    def __init__(self, store, directory: Path, session: aiohttp.ClientSession,
                 api_url="http://mihomo:9090", server_url="http://mihomo:7890",
                 test_url="https://www.gstatic.com/generate_204"):
        self.store = store
        self.directory = directory.resolve()
        self.session = session
        self.api_url = api_url.rstrip("/")
        self.server_url = server_url
        self.test_url = test_url
        self._healthy = False
        self._prepare_error = ""

    @property
    def proxy_url(self) -> str:
        parsed = urlsplit(self.server_url)
        return f"{parsed.scheme}://jm:{quote(self.store.state['proxy_password'], safe='')}@{parsed.netloc}"

    def _config(self, value: dict) -> str:
        config = {
            "mixed-port": 7890, "allow-lan": True, "bind-address": "*",
            "external-controller": "0.0.0.0:9090",
            "secret": self.store.state["proxy_control_token"],
            "authentication": [f"jm:{self.store.state['proxy_password']}"],
            "mode": "rule", "log-level": "silent", "ipv6": False,
            "tun": {"enable": False}, "dns": {"enable": False},
            "profile": {"store-selected": False},
            "proxy-groups": [{"name": GROUP, "type": "select", "proxies": ["REJECT"]}],
            "rules": [f"MATCH,{GROUP}"],
        }
        if value.get("provider"):
            config["proxy-providers"] = {PROVIDER: {
                "type": "file", "path": str(self.directory / value["provider"]),
                "health-check": {"enable": True, "url": self.test_url,
                                 "interval": 300, "timeout": 5000, "lazy": True,
                                 "expected-status": "204"},
            }}
            config["proxy-groups"][0]["proxies"] = [AUTO, "REJECT"]
            config["proxy-groups"][0]["use"] = [PROVIDER]
            config["proxy-groups"].append({"name": AUTO, "type": "fallback", "use": [PROVIDER],
                                           "url": self.test_url, "interval": 300,
                                           "timeout": 5000, "lazy": True, "expected-status": "204"})
        return yaml.safe_dump(config, allow_unicode=True, sort_keys=False)

    def prepare(self) -> None:
        self.directory.mkdir(parents=True, exist_ok=True)
        self.directory.chmod(0o700)
        value = self.store.state.get("proxy", {})
        provider = value.get("provider")
        if provider and (Path(provider).name != provider or not provider.startswith("provider-")
                         or not (self.directory / provider).is_file()):
            self._prepare_error = "代理配置文件缺失；请恢复备份或重新导入订阅"
            private_write(self.directory / "config.yaml", self._config({}))
            return
        private_write(self.directory / "config.yaml", self._config(value))

    async def _api(self, method: str, path: str, body=None):
        try:
            async with self.session.request(
                method, self.api_url + path, json=body,
                headers={"Authorization": f"Bearer {self.store.state['proxy_control_token']}"},
                timeout=aiohttp.ClientTimeout(total=15), proxy=None,
            ) as response:
                if response.status >= 400:
                    raise ProxyError("Mihomo 拒绝代理配置或节点操作")
                if response.status == 204:
                    return None
                return await response.json()
        except (aiohttp.ClientError, asyncio.TimeoutError, ValueError):
            raise ProxyError("无法连接 Mihomo；请检查代理容器是否运行") from None

    async def _apply(self, value: dict) -> None:
        await self._api("PUT", "/configs?force=true", {"payload": self._config(value)})

    async def _nodes(self) -> list[str]:
        response = await self._api("GET", f"/providers/proxies/{PROVIDER}")
        nodes = response.get("proxies", []) if isinstance(response, dict) else []
        if not isinstance(nodes, list) or not nodes or len(nodes) > 2000:
            raise ProxyError("订阅没有可用的代理节点")
        names = []
        for node in nodes:
            if (not isinstance(node, dict) or not isinstance(node.get("name"), str)
                    or not node["name"] or len(node["name"]) > 256
                    or node["name"].lower() in BUILTINS | {GROUP.lower(), AUTO.lower()}
                    or str(node.get("type", "")).lower() in BUILTINS):
                raise ProxyError("订阅含有直连、内置或无效节点，请仅导入实际代理节点")
            names.append(node["name"])
        if len(set(names)) != len(names):
            raise ProxyError("订阅节点名称重复")
        return names

    async def _select(self, node: str) -> None:
        await self._api("PUT", f"/proxies/{GROUP}", {"name": node})

    async def _activate(self, node: str) -> None:
        if node == AUTO:
            # This endpoint waits for Mihomo's concurrent checks; our API deadline bounds the wait.
            await self._api("GET", f"/providers/proxies/{PROVIDER}/healthcheck")
        await self._select(node)
        await self._probe()

    async def _probe(self) -> None:
        try:
            async with self.session.get(self.test_url, proxy=self.proxy_url,
                                        timeout=aiohttp.ClientTimeout(total=12),
                                        allow_redirects=False) as response:
                if response.status != 204:
                    raise ProxyError("代理连通性验证失败；节点必须能够访问验证地址")
        except (aiohttp.ClientError, asyncio.TimeoutError, ValueError):
            raise ProxyError("代理连通性验证失败，请更换节点后重试") from None

    async def status(self) -> dict:
        value = self.store.state.get("proxy", {})
        result = {"available": False, "configured": bool(value.get("provider")),
                  "healthy": False, "node": value.get("node", ""), "nodes": [], "error": self._prepare_error}
        try:
            await self._api("GET", "/version")
            result["available"] = True
            if result["configured"]:
                result["nodes"] = await self._nodes()
                selected = await self._api("GET", f"/proxies/{GROUP}")
                result["healthy"] = self._healthy and selected.get("now") == value.get("node")
        except ProxyError as exc:
            self._healthy = False
            result["error"] = str(exc)
        return result

    async def ensure_ready(self) -> None:
        value = self.store.state.get("proxy", {})
        self._healthy = False
        if self._prepare_error:
            raise ProxyError(self._prepare_error)
        if not value.get("provider") or not value.get("node"):
            raise ProxyError("请先导入订阅并选择可用代理节点")
        # Reapply our persisted config after a restart or an ambiguous API timeout.
        await self._apply(value)
        nodes = await self._nodes()
        if value["node"] != AUTO and value["node"] not in nodes:
            raise ProxyError("已选代理节点不可用，请重新导入订阅或选择节点")
        await self._activate(value["node"])
        self._healthy = True

    async def _source(self, url: str | None, content: str | None) -> str:
        if (url is None) == (content is None):
            raise ProxyError("请提供订阅链接或上传内容，两者选一")
        if url is not None:
            if not isinstance(url, str) or len(url) > 8192:
                raise ProxyError("订阅链接无效")
            try:
                parsed = urlsplit(url)
                if parsed.scheme not in ("http", "https") or not parsed.hostname or parsed.username or parsed.password:
                    raise ValueError
                # A dedicated session ignores host HTTP_PROXY/HTTPS_PROXY completely.
                async with aiohttp.ClientSession(trust_env=False) as direct:
                    async with direct.get(url, timeout=aiohttp.ClientTimeout(total=20)) as response:
                        if response.status != 200:
                            raise ProxyError("订阅下载失败，请上传订阅内容重试")
                        chunks, size = [], 0
                        async for chunk in response.content.iter_chunked(65536):
                            size += len(chunk)
                            if size > LIMIT:
                                raise ProxyError("订阅内容不能超过 2 MiB")
                            chunks.append(chunk)
                        content = b"".join(chunks).decode("utf-8-sig")
            except (aiohttp.ClientError, asyncio.TimeoutError, UnicodeError, ValueError):
                raise ProxyError("订阅下载失败，请上传订阅内容重试") from None
        if not isinstance(content, str) or not content.strip() or len(content.encode()) > LIMIT:
            raise ProxyError("订阅为空或超过 2 MiB")
        try:
            parsed = yaml.safe_load(content)
        except (yaml.YAMLError, RecursionError):
            raise ProxyError("订阅格式无效；支持 Clash YAML、节点 URI 或 Base64") from None
        if isinstance(parsed, dict):
            nodes = parsed.get("proxies")
            if not isinstance(nodes, list) or not nodes or len(nodes) > 2000:
                raise ProxyError("Clash 订阅必须包含 proxies 节点列表")
            names = [node.get("name") for node in nodes if isinstance(node, dict)]
            if len(names) != len(nodes) or any(not isinstance(name, str) for name in names):
                raise ProxyError("订阅节点格式无效")
            for node in nodes:
                if (str(node.get("type", "")).lower() in BUILTINS
                        or node["name"].lower() in BUILTINS | {GROUP.lower(), AUTO.lower()}
                        or (node.get("dialer-proxy") and node["dialer-proxy"] not in names)):
                    raise ProxyError("订阅含有直连节点或无法解析的前置代理")
            # Only nodes survive: uploaded controllers, ports, rules and scripts are discarded.
            try:
                return yaml.safe_dump({"proxies": nodes}, allow_unicode=True, sort_keys=False)
            except (yaml.YAMLError, RecursionError):
                raise ProxyError("订阅节点格式无效") from None
        if not isinstance(parsed, str):
            raise ProxyError("订阅格式无效；支持 Clash YAML、节点 URI 或 Base64")
        return content

    async def _restore(self, previous: dict) -> None:
        self._healthy = False
        try:
            await self._apply(previous)
            if previous.get("node"):
                await self._select(previous["node"])
        except ProxyError:
            # Disk remains the last known good configuration even if the core is down.
            pass

    async def import_subscription(self, url: str | None = None, content: str | None = None) -> dict:
        source = await self._source(url, content)
        previous = dict(self.store.state.get("proxy", {}))
        filename = f"provider-{uuid.uuid4().hex}.yaml"
        private_write(self.directory / filename, source)
        candidate = {"provider": filename, "subscription_url": url or "", "node": previous.get("node", AUTO)}
        try:
            await self._apply(candidate)
            nodes = await self._nodes()
            candidate["node"] = candidate["node"] if candidate["node"] == AUTO or candidate["node"] in nodes else AUTO
            await self._activate(candidate["node"])
            private_write(self.directory / "config.yaml", self._config(candidate))
            try:
                self.store.save_proxy(candidate)
            except Exception:
                private_write(self.directory / "config.yaml", self._config(previous))
                raise
        except BaseException:
            await self._restore(previous)
            raise
        self._healthy = True
        self._prepare_error = ""
        return await self.status()

    async def refresh_subscription(self) -> dict:
        url = self.store.state.get("proxy", {}).get("subscription_url")
        if not url:
            raise ProxyError("上传的订阅没有更新链接，请重新上传文件")
        return await self.import_subscription(url=url)

    async def select_node(self, name: str) -> dict:
        previous = dict(self.store.state.get("proxy", {}))
        if not previous.get("provider"):
            raise ProxyError("请先导入订阅")
        nodes = await self._nodes()
        if not isinstance(name, str) or (name != AUTO and name not in nodes):
            raise ProxyError("请选择订阅中的有效代理节点")
        try:
            await self._activate(name)
            self.store.save_proxy({**previous, "node": name})
        except BaseException:
            await self._restore(previous)
            raise
        self._healthy = True
        return await self.status()
