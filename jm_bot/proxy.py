"""Own the Mihomo configuration; subscription files never own listeners or rules."""

import asyncio
import os
import socket
import ssl
from pathlib import Path
import tempfile
from urllib.parse import quote, urlsplit
import uuid

import aiohttp
import yaml


class ProxyError(RuntimeError):
    """A safe, administrator-facing proxy error without subscription secrets."""

    def __init__(self, message, *, code="proxy_error", stage="proxy",
                 next_step="请在下载设置中检查内置代理。", http_status=None):
        super().__init__(message)
        self.code = code
        self.stage = stage
        self.next_step = next_step
        self.http_status = http_status


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
                    if path == "/configs?force=true" and response.status == 400:
                        raise ProxyError("Mihomo 拒绝订阅格式或节点配置", code="subscription_format",
                                         stage="subscription_apply", http_status=400,
                                         next_step="请检查订阅格式和节点类型，或上传有效的订阅文件。")
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
            # A large pool may still be checking; only the real proxy probe decides readiness.
            try:
                async with asyncio.timeout(5):
                    await self._api("GET", f"/providers/proxies/{PROVIDER}/healthcheck")
            except TimeoutError:
                pass
        await self._select(node)
        await self._probe()

    async def _probe(self) -> None:
        try:
            async with self.session.get(self.test_url, proxy=self.proxy_url,
                                        timeout=aiohttp.ClientTimeout(total=12),
                                        allow_redirects=False) as response:
                if response.status != 204:
                    raise ProxyError(f"代理连通性验证失败（HTTP {response.status}）；节点必须能够访问验证地址")
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
            raise ProxyError("请提供订阅链接或上传内容，两者选一", code="subscription_format", stage="subscription_validate")
        if url is not None:
            if not isinstance(url, str) or len(url) > 8192:
                raise ProxyError("订阅链接无效", code="subscription_url", stage="subscription_validate",
                                 next_step="请填写 HTTP(S) 订阅链接或上传订阅文件。")
            try:
                parsed = urlsplit(url)
                if parsed.scheme not in ("http", "https") or not parsed.hostname or parsed.username or parsed.password:
                    raise ValueError
            except ValueError:
                raise ProxyError("订阅链接无效", code="subscription_url", stage="subscription_validate",
                                 next_step="请填写 HTTP(S) 订阅链接或上传订阅文件。") from None
            content = await self._download_subscription(url)
        if not isinstance(content, str) or not content.strip():
            raise ProxyError("订阅内容为空或格式无效", code="subscription_format", stage="subscription_validate",
                             next_step="请上传非空的 Clash YAML、节点 URI 或 Base64 订阅文件。")
        try:
            size = len(content.encode("utf-8"))
        except UnicodeError:
            raise ProxyError("订阅不是有效的 UTF-8 内容", code="subscription_encoding",
                             stage="subscription_validate", next_step="请上传 UTF-8 编码的订阅文件。") from None
        if size > LIMIT:
            raise ProxyError("订阅内容不能超过 2 MiB", code="subscription_too_large", stage="subscription_validate",
                             next_step="请上传不超过 2 MiB 的订阅文件。")
        if content.lstrip().lower().startswith(("<!doctype html", "<html", "<head", "<body")):
            raise ProxyError("订阅返回了 HTML 页面", code="subscription_html", stage="subscription_validate",
                             next_step="请检查订阅权限或上传实际订阅文件。")
        try:
            parsed = yaml.safe_load(content)
        except (yaml.YAMLError, RecursionError):
            raise ProxyError("订阅格式无效；支持 Clash YAML、节点 URI 或 Base64",
                             code="subscription_format", stage="subscription_validate") from None
        if isinstance(parsed, dict):
            nodes = parsed.get("proxies")
            if not isinstance(nodes, list) or not nodes or len(nodes) > 2000:
                raise ProxyError("Clash 订阅必须包含 proxies 节点列表", code="subscription_format", stage="subscription_validate")
            names = [node.get("name") for node in nodes if isinstance(node, dict)]
            if len(names) != len(nodes) or any(not isinstance(name, str) for name in names):
                raise ProxyError("订阅节点格式无效", code="subscription_format", stage="subscription_validate")
            for node in nodes:
                if (str(node.get("type", "")).lower() in BUILTINS
                        or node["name"].lower() in BUILTINS | {GROUP.lower(), AUTO.lower()}
                        or (node.get("dialer-proxy") and node["dialer-proxy"] not in names)):
                    raise ProxyError("订阅含有直连节点或无法解析的前置代理",
                                     code="subscription_format", stage="subscription_validate")
            # Only nodes survive: uploaded controllers, ports, rules and scripts are discarded.
            try:
                return yaml.safe_dump({"proxies": nodes}, allow_unicode=True, sort_keys=False)
            except (yaml.YAMLError, RecursionError):
                raise ProxyError("订阅节点格式无效", code="subscription_format", stage="subscription_validate") from None
        if not isinstance(parsed, str):
            raise ProxyError("订阅格式无效；支持 Clash YAML、节点 URI 或 Base64",
                             code="subscription_format", stage="subscription_validate")
        return content

    async def _fetch_subscription(self, session, url: str, proxy: str | None, stage: str) -> str:
        next_step = "请检查订阅链接和网络，或上传订阅文件。"
        try:
            async with session.get(url, proxy=proxy, headers={"User-Agent": "mihomo/1.19.33"},
                                   timeout=aiohttp.ClientTimeout(total=20)) as response:
                if response.status != 200:
                    raise ProxyError(f"订阅下载失败（HTTP {response.status}）", code="subscription_http",
                                     stage=stage, next_step=next_step, http_status=response.status)
                chunks, size = [], 0
                async for chunk in response.content.iter_chunked(65536):
                    size += len(chunk)
                    if size > LIMIT:
                        raise ProxyError("订阅内容不能超过 2 MiB", code="subscription_too_large",
                                         stage=stage, next_step=next_step)
                    chunks.append(chunk)
                content = b"".join(chunks).decode("utf-8-sig")
                if content.lstrip().lower().startswith(("<!doctype html", "<html", "<head", "<body")):
                    raise ProxyError("订阅返回了 HTML 页面（HTTP 200）", code="subscription_html",
                                     stage=stage, next_step=next_step, http_status=200)
                return content
        except ProxyError:
            raise
        except (aiohttp.ClientError, TimeoutError, UnicodeError, ValueError, ssl.SSLError) as exc:
            if isinstance(exc, TimeoutError):
                code, message = "timeout", "订阅下载超时"
            elif isinstance(exc, UnicodeError):
                code, message = "encoding", "订阅不是有效的 UTF-8 内容"
            elif isinstance(exc, (aiohttp.ClientSSLError, ssl.SSLError)):
                code, message = "tls", "订阅 TLS 验证或握手失败"
            elif isinstance(exc, aiohttp.ClientConnectorError) and isinstance(exc.os_error, socket.gaierror):
                code, message = "dns", "订阅服务器 DNS 解析失败"
            elif isinstance(exc, (aiohttp.InvalidURL, ValueError)):
                code, message = "url", "订阅链接或重定向无效"
            else:
                code, message = "connection", "订阅服务器连接失败"
            raise ProxyError(message, code="subscription_" + code, stage=stage, next_step=next_step) from None

    async def _download_subscription(self, url: str) -> str:
        stage = "subscription_fetch_direct"
        try:
            async with asyncio.timeout(60):
                # This session never inherits environment proxies or application headers.
                async with aiohttp.ClientSession(trust_env=False) as session:
                    try:
                        return await self._fetch_subscription(session, url, None, stage)
                    except ProxyError as direct_error:
                        previous = self.store.state.get("proxy", {})
                        if not previous.get("provider") or not previous.get("node"):
                            raise ProxyError(str(direct_error) + "；尚无可用旧节点，请先上传订阅文件。",
                                             code=direct_error.code, stage=direct_error.stage,
                                             next_step="请先上传订阅文件并验证节点，再重试链接。",
                                             http_status=direct_error.http_status) from None
                    stage = "subscription_proxy_ready"
                    try:
                        async with asyncio.timeout(20):
                            await self.ensure_ready()
                    except (ProxyError, TimeoutError):
                        # Reapplying the saved config may reset the core's manual selection.
                        try:
                            await self._select(previous["node"])
                        except ProxyError:
                            pass
                        raise ProxyError("订阅直连失败，已有代理验证失败或超时", code="subscription_proxy_unavailable",
                                         stage=stage, next_step="请检查已有节点，或上传订阅文件。") from None
                    stage = "subscription_fetch_proxy"
                    try:
                        return await self._fetch_subscription(session, url, self.proxy_url, stage)
                    except ProxyError as exc:
                        raise ProxyError("订阅直连与已有代理获取均失败；" + str(exc), code=exc.code,
                                         stage=exc.stage, next_step=exc.next_step, http_status=exc.http_status) from None
        except TimeoutError:
            raise ProxyError("订阅获取超时（总限时 60 秒）", code="subscription_timeout", stage=stage,
                             next_step="请检查网络和已有节点，或上传订阅文件。") from None

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
