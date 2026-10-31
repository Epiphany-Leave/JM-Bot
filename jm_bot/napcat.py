"""Adapter for the pinned NapCat v4.18.42 management API (never exposed directly)."""
import asyncio
import copy
import hashlib
import json
from pathlib import Path
import shutil

import aiohttp


class NapCatError(Exception):
    pass


class NapCat:
    def __init__(self, session: aiohttp.ClientSession, url: str, token: str,
                 onebot_token: str, config_dir: Path):
        self.session = session
        self.url = url.rstrip("/")
        self.token = token
        self.onebot_token = onebot_token
        self.config_dir = config_dir
        self.credential = None
        self._auth_lock = asyncio.Lock()

    async def _post(self, path, body, credential=None):
        headers = {"Authorization": f"Bearer {credential}"} if credential else {}
        try:
            async with self.session.post(self.url + "/api/" + path, json=body,
                                         headers=headers, allow_redirects=False,
                                         timeout=aiohttp.ClientTimeout(total=10)) as response:
                if response.status != 200:
                    raise NapCatError("QQ 登录服务暂时无法访问")
                payload = await response.json()
                if not isinstance(payload, dict) or type(payload.get("code")) is not int:
                    raise NapCatError("QQ 登录服务返回了不兼容的数据")
                return payload
        except (aiohttp.ClientError, TimeoutError, ValueError):
            raise NapCatError("QQ 登录服务暂时无法访问") from None

    async def _login(self):
        digest = hashlib.sha256((self.token + ".napcat").encode()).hexdigest()
        payload = await self._post("auth/login", {"hash": digest})
        data = payload.get("data")
        if isinstance(data, dict) and data.get("require2FA"):
            raise NapCatError("本项目 NapCat 开启了二次验证，请先在原管理页关闭后重启本项目")
        if payload.get("code") != 0 or not isinstance(data, dict) or not isinstance(data.get("Credential"), str) or not data["Credential"]:
            raise NapCatError("内部登录未成功，请确认已用新版 Compose 重建整个 jm-bot 项目")
        self.credential = data["Credential"]

    async def call(self, path: str, body=None):
        async with self._auth_lock:
            if not self.credential:
                await self._login()
            payload = await self._post(path, {} if body is None else body, self.credential)
            if payload.get("code") != 0 and payload.get("message") in (
                    "Unauthorized", "Authorization Failed", "Token has been revoked"):
                # Only replay requests explicitly rejected before their handler ran.
                self.credential = None
                await self._login()
                payload = await self._post(path, {} if body is None else body, self.credential)
        if payload.get("code") != 0:
            if payload.get("message") in ("Unauthorized", "Authorization Failed", "Token has been revoked"):
                self.credential = None
            raise NapCatError("QQ 登录服务尚未就绪，请稍后重试")
        return payload.get("data")

    async def login_status(self):
        data = await self.call("QQLogin/CheckLoginStatus")
        if not isinstance(data, dict):
            raise NapCatError("QQ 登录状态格式不兼容")
        return data

    async def login_info(self):
        data = await self.call("QQLogin/GetQQLoginInfo")
        return data if isinstance(data, dict) else {}

    async def qrcode(self):
        data = await self.call("QQLogin/GetQQLoginQrcode")
        if not isinstance(data, dict) or not isinstance(data.get("qrcode"), str) or not data["qrcode"]:
            raise NapCatError("二维码尚未准备好，请稍后刷新")
        return data["qrcode"]

    async def refresh_qrcode(self):
        return await self.call("QQLogin/RefreshQRcode")

    async def ensure_onebot(self):
        config = await self.call("OB11Config/GetConfig")
        if not isinstance(config, dict) or not isinstance(config.get("network"), dict):
            raise NapCatError("QQ 连接配置格式不兼容")
        updated = copy.deepcopy(config)
        network = updated["network"]
        servers = network.get("websocketServers", [])
        if not isinstance(servers, list) or not all(isinstance(x, dict) for x in servers):
            raise NapCatError("QQ 连接配置格式不兼容")
        managed = {"enable": True, "name": "jm-bot", "host": "0.0.0.0", "port": 3001,
                   "reportSelfMessage": False, "enableForcePushEvent": True,
                   "messagePostFormat": "array", "token": self.onebot_token,
                   "debug": False, "heartInterval": 30000}
        candidates = [x for x in servers if x.get("name") == "jm-bot" or str(x.get("port")) == "3001"]
        if len(candidates) > 1 or (candidates and candidates[0].get("name") not in ("jm-bot", "ws", "")):
            raise NapCatError("旧连接设置存在冲突；请检查本项目是否配置了多个 3001 服务")
        existing = candidates[0] if candidates else {}
        managed = {**existing, **managed}
        network["websocketServers"] = [x for x in servers if x.get("name") != "jm-bot" and str(x.get("port")) != "3001"] + [managed]
        if updated == config:
            return
        # Keep original account-specific configuration before the first managed write.
        for path in self.config_dir.glob("onebot11*.json"):
            backup = path.with_name(path.name + ".before-jm-bot")
            if not backup.exists():
                await asyncio.to_thread(shutil.copy2, path, backup)
                backup.chmod(0o600)
        await self.call("OB11Config/SetConfig", {"config": json.dumps(updated, ensure_ascii=False)})
        saved = await self.call("OB11Config/GetConfig")
        if not isinstance(saved, dict) or saved.get("network", {}).get("websocketServers") != network["websocketServers"]:
            raise NapCatError("连接设置尚未保存成功，请稍后重试")
