import argparse
import asyncio
import logging
import os
from pathlib import Path
import signal

import aiohttp
from aiohttp import web

from . import __version__
from .bot import JMBot
from .config import Settings
from .napcat import NapCat
from .proxy import ManagedProxy
from .store import StateStore
from .web import Runtime, create_app


async def serve() -> None:
    password = os.environ.get("ADMIN_PASSWORD", "")
    if len(password) < 12 or password.startswith("REPLACE_") or "\n" in password or "\r" in password:
        raise ValueError("请在 Compose 中填写自己的 ADMIN_PASSWORD（至少 12 位），然后重新创建容器")
    store = StateStore(Path(os.environ.get("DATA_DIR", "/data")))
    config_dir = Path(os.environ.get("NAPCAT_CONFIG_DIR", "/napcat-config"))
    store.prepare_napcat(config_dir)
    acl = store.get_permissions()
    settings = Settings(ws_url=os.environ.get("ONEBOT_WS_URL", "ws://napcat:3001/"),
                        token=store.state["onebot_token"], allowed_users=frozenset(acl["allowed_users"]),
                        allowed_groups=frozenset(acl["allowed_groups"]), group_allow_all=acl["group_allow_all"],
                        download_dir=Path(os.environ.get("DOWNLOAD_DIR", "/downloads")).resolve())
    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for signum in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(signum, stop.set)
        except NotImplementedError:
            pass  # Windows uses asyncio.run's KeyboardInterrupt handling.
    async with aiohttp.ClientSession(trust_env=False) as session:
        proxy = ManagedProxy(store, Path(os.environ.get("PROXY_DIR", "/proxy-data")), session)
        proxy.prepare()
        napcat = NapCat(session, os.environ.get("NAPCAT_URL", "http://napcat:6099"),
                        store.state["napcat_token"], store.state["onebot_token"], config_dir)
        bot = JMBot(settings, download_options=store.get_download_settings, proxy=proxy)
        app = create_app(Runtime(store, bot, napcat, proxy=proxy), password,
                         Path(__file__).resolve().parent.parent / "samples" / "test.pdf")
        runner = web.AppRunner(app, access_log=None)
        await runner.setup()
        try:
            await web.TCPSite(runner, os.environ.get("WEB_HOST", "0.0.0.0"),
                              int(os.environ.get("WEB_PORT", "8080"))).start()
            logging.info("JM-Bot %s 管理网页已启动；打开 http://宿主机地址:16099（端口以 Compose 为准）", __version__)
            await stop.wait()
        finally:
            await runner.cleanup()


def main() -> None:
    parser = argparse.ArgumentParser(description="JM-Bot：一个管理密码，在网页扫码并配置权限。")
    parser.add_argument("--version", action="version", version=__version__)
    parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    # Native retry logs can dump request headers and proxy credentials; use our safe task logs.
    logging.getLogger("jmcomic").disabled = True
    try:
        asyncio.run(serve())
    except (ValueError, OSError) as exc:
        parser.error(str(exc))
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
