"""Single-password local management UI and NapCat lifecycle coordination."""
import asyncio
from collections import defaultdict, deque
from contextlib import suppress
import hashlib
import hmac
import logging
from pathlib import Path
import secrets
import time

from aiohttp import web
import qrcode
import qrcode.image.svg

from .config import positive_id
from .napcat import NapCatError
from .store import permissions


log = logging.getLogger(__name__)
RUNTIME = web.AppKey("runtime", object)
STATIC = Path(__file__).with_name("static")


class Runtime:
    def __init__(self, store, bot, napcat):
        self.store, self.bot, self.napcat = store, bot, napcat
        self.qq = {}
        self.napcat_reachable = False
        self.config_synced = False
        self.verified_generation = 0
        self.error = "正在启动 QQ 登录服务"
        self.next_step = "首次启动可能需要稍等，准备好后请前往 QQ 登录页面。"
        self._tasks = []

    async def start(self):
        self._tasks = [asyncio.create_task(self.bot.client.run()), asyncio.create_task(self._monitor())]

    async def close(self):
        if self._tasks:
            self._tasks[1].cancel()
            with suppress(asyncio.CancelledError):
                await self._tasks[1]
        await self.bot.close()
        if self._tasks:
            await asyncio.gather(self._tasks[0], return_exceptions=True)

    async def _monitor(self):
        while True:
            await self.poll()
            await asyncio.sleep(3)

    async def poll(self):
        reachable = False
        try:
            status = await self.napcat.login_status()
            reachable = True
            self.napcat_reachable = True
            self.qq = {key: status.get(key) for key in ("isLogin", "isOffline", "loginPhase", "loginError")}
            if status.get("isLogin") is not True:
                self.config_synced = False
                self.verified_generation = 0
                self.error = "QQ 已离线" if status.get("isOffline") else "QQ 尚未登录"
                self.next_step = "请前往 QQ 登录页面扫码，并在手机上确认。"
                return
            await self.napcat.ensure_onebot()
            self.config_synced = True
            if not self.bot.client.connected:
                self.error = "QQ 已登录，正在连接下载程序"
                self.next_step = "请稍等，程序会自动连接；无需填写内部口令。"
                return
            generation = self.bot.client.generation
            async with asyncio.timeout(5):
                info = await self.bot.client.call("get_login_info")
                health = await self.bot.client.call("get_status")
            if not isinstance(info, dict) or not isinstance(health, dict) or health.get("online") is not True:
                raise NapCatError("QQ 暂未在线，请查看 QQ 登录页面")
            if generation != self.bot.client.generation or not self.bot.client.connected:
                raise NapCatError("连接正在恢复，请稍等")
            self.qq.update(user_id=positive_id(info.get("user_id")), nickname=str(info.get("nickname", "")))
            self.verified_generation = generation
            self.error, self.next_step = "", ""
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            self.verified_generation = 0
            self.error = str(exc) if isinstance(exc, NapCatError) else "QQ 连接尚未就绪，正在自动恢复"
            self.next_step = "稍后会自动重试；若持续失败，请检查两个容器都已启动。"
            self.napcat_reachable = reachable
            if not reachable:
                self.qq = {}
                self.config_synced = False

    def status(self):
        allowed = self.store.get_permissions()
        connected = self.bot.client.connected
        ready = bool(self.napcat_reachable and self.qq.get("isLogin") and self.config_synced
                     and connected and self.verified_generation == self.bot.client.generation)
        error, next_step = self.error, self.next_step
        if ready and not (allowed["allowed_users"] or (allowed["allowed_groups"] and allowed["group_allow_all"])):
            ready = False
            error, next_step = "尚未设置使用权限", "请在使用权限页面勾选允许使用的群，或添加私聊用户。"
        value = {"ready": ready, "napcat_reachable": self.napcat_reachable,
                 "qq": self.qq, "onebot_connected": connected, "config_synced": self.config_synced,
                 "busy": self.bot.busy, "current_task": self.bot.current_task,
                 "last_result": self.bot.last_result, "error": error, "next_step": next_step}
        def redact(item):
            if isinstance(item, str):
                for token in (self.store.state["onebot_token"], self.store.state["napcat_token"],
                              self.napcat.credential or ""):
                    if token:
                        item = item.replace(token, "[已隐藏]")
                return item[:2000]
            if isinstance(item, dict):
                return {key: redact(val) for key, val in item.items()}
            return item
        return redact(value)


def create_app(runtime: Runtime, password: str, test_pdf: Path, *, manage_runtime=True):
    if len(password) < 12 or password.startswith("REPLACE_") or "\n" in password or "\r" in password:
        raise ValueError("请在 Compose 中填写自己的 ADMIN_PASSWORD（至少 12 位），然后重新创建容器")
    sessions = {}
    attempts = defaultdict(deque)
    password_hash = hashlib.sha256(password.encode()).digest()

    @web.middleware
    async def protection(request, handler):
        if request.method not in ("GET", "HEAD", "OPTIONS"):
            origin = request.headers.get("Origin")
            allowed_origins = {f"{request.scheme}://{request.host}"}
            if request.scheme == "http":
                # A NAS gateway terminates TLS while preserving the external Host.
                allowed_origins.add(f"https://{request.host}")
            if (origin and origin not in allowed_origins) or request.headers.get("Sec-Fetch-Site") == "cross-site":
                return web.json_response({"error": "请求来源不匹配，请直接打开管理网页重试"}, status=403)
            if request.content_type != "application/json":
                return web.json_response({"error": "请使用管理网页提交设置"}, status=415)
        if request.path.startswith("/api/") and request.path not in ("/api/auth/login", "/api/auth/session"):
            key = request.cookies.get("jm_session", "")
            if sessions.get(key, 0) <= time.monotonic():
                sessions.pop(key, None)
                return web.json_response({"error": "请先登录管理网页"}, status=401)
        try:
            response = await handler(request)
        except ValueError as exc:
            response = web.json_response({"error": str(exc) or "设置格式无效，请检查必填项目"}, status=400)
        except (TypeError, KeyError):
            response = web.json_response({"error": "设置格式无效，请检查 QQ 号、群号和必填项目"}, status=400)
        except NapCatError as exc:
            response = web.json_response({"error": str(exc), "next_step": "请查看概览和 QQ 登录页面的状态。"}, status=503)
        except OSError:
            response = web.json_response({"error": "文件读写失败，请检查 NAS 项目目录权限及剩余空间"}, status=500)
        except web.HTTPException:
            raise
        except Exception as exc:
            log.error("Management request failed (%s)", type(exc).__name__)
            response = web.json_response({"error": "操作暂未完成，请检查连接状态后重试"}, status=503)
        response.headers["Cache-Control"] = "no-store"
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Content-Security-Policy"] = "default-src 'self'; img-src 'self' data:; object-src 'none'; frame-ancestors 'none'; base-uri 'none'"
        return response

    app = web.Application(middlewares=[protection], client_max_size=64 * 1024)
    app[RUNTIME] = runtime

    async def login(request):
        now = time.monotonic()
        for key in list(sessions):
            if sessions[key] <= now:
                del sessions[key]
        for key in list(attempts):
            while attempts[key] and attempts[key][0] < now - 60:
                attempts[key].popleft()
            if not attempts[key]:
                del attempts[key]
        key = request.remote or "local"
        if len(attempts) >= 10000 or len(attempts[key]) >= 5:
            return web.json_response({"error": "登录尝试过多，请一分钟后重试"}, status=429)
        body = await request.json()
        candidate = body.get("password") if isinstance(body, dict) else None
        if not isinstance(candidate, str) or not hmac.compare_digest(hashlib.sha256(candidate.encode()).digest(), password_hash):
            attempts[key].append(now)
            return web.json_response({"error": "管理密码不正确，请使用 Compose 中的 ADMIN_PASSWORD"}, status=401)
        attempts.pop(key, None)
        while len(sessions) >= 64:
            sessions.pop(next(iter(sessions)))
        session = secrets.token_urlsafe(32)
        sessions[session] = now + 12 * 3600
        response = web.json_response({"ok": True})
        response.set_cookie("jm_session", session, httponly=True, samesite="Strict",
                            secure=request.secure or request.headers.get("Origin") == f"https://{request.host}",
                            max_age=12 * 3600)
        return response

    async def logout(request):
        sessions.pop(request.cookies.get("jm_session", ""), None)
        response = web.json_response({"ok": True})
        response.del_cookie("jm_session")
        return response

    async def session_status(request):
        authenticated = sessions.get(request.cookies.get("jm_session", ""), 0) > time.monotonic()
        return web.json_response({"authenticated": authenticated})

    async def health(request):
        return web.json_response({"ok": True})

    async def favicon(request):
        return web.Response(status=204)

    async def status(request):
        return web.json_response(runtime.status())

    async def get_permissions(request):
        return web.json_response(runtime.store.get_permissions())

    async def put_permissions(request):
        value = permissions(await request.json())
        value = runtime.store.save_permissions(value)
        runtime.bot.update_permissions(value)
        return web.json_response(value)

    async def qr(request):
        value = await runtime.napcat.qrcode()
        image = qrcode.make(value, image_factory=qrcode.image.svg.SvgPathFillImage, border=4)
        return web.Response(body=image.to_string(), content_type="image/svg+xml")

    async def refresh_qr(request):
        await runtime.napcat.refresh_qrcode()
        return web.json_response({"ok": True, "message": "正在准备新的二维码，请稍等"})

    async def groups(request):
        async with asyncio.timeout(10):
            data = await runtime.bot.client.call("get_group_list")
        if not isinstance(data, list):
            raise NapCatError("群列表尚未准备好，请稍后刷新")
        result = [{"group_id": positive_id(item["group_id"]), "group_name": str(item.get("group_name", ""))}
                  for item in data if isinstance(item, dict)]
        return web.json_response({"groups": result})

    async def test_file(request):
        body = await request.json()
        if not isinstance(body, dict):
            raise ValueError
        result = await runtime.bot.send_test_file(body["kind"], body["target_id"], test_pdf)
        return web.json_response(result)

    async def index(request):
        return web.FileResponse(STATIC / "index.html")

    async def legacy_login(request):
        raise web.HTTPFound("/")

    app.router.add_get("/healthz", health)
    app.router.add_get("/favicon.ico", favicon)
    app.router.add_get("/", index)
    for path in ("/webui", "/webui/", "/webui/web_login"):
        app.router.add_get(path, legacy_login)
    app.router.add_static("/static/", STATIC, show_index=False)
    app.router.add_post("/api/auth/login", login)
    app.router.add_get("/api/auth/session", session_status)
    app.router.add_post("/api/auth/logout", logout)
    app.router.add_get("/api/status", status)
    app.router.add_get("/api/permissions", get_permissions)
    app.router.add_put("/api/permissions", put_permissions)
    app.router.add_get("/api/qq/qrcode", qr)
    app.router.add_post("/api/qq/refresh", refresh_qr)
    app.router.add_get("/api/groups", groups)
    app.router.add_post("/api/test-file", test_file)
    if manage_runtime:
        async def lifecycle(app):
            await runtime.start()
            yield
            await runtime.close()
        app.cleanup_ctx.append(lifecycle)
    return app
