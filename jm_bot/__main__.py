import argparse
import asyncio
import logging
import getpass
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
from .store import StateStore, password_credential, validate_password
from .web import Runtime, create_app


async def serve() -> None:
    password = os.environ.get("ADMIN_PASSWORD", "")
    store = await asyncio.to_thread(StateStore, Path(os.environ.get("DATA_DIR", "/data")))
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
    parser.add_argument("--reset-admin-password", action="store_true", help="停止服务后，交互重设管理密码（保留其他配置）")
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    # Native retry logs can dump request headers and proxy credentials; use our safe task logs.
    logging.getLogger("jmcomic").disabled = True
    try:
        if args.reset_admin_password:
            directory = Path(os.environ.get("DATA_DIR", "/data"))
            if not (directory / "config.json").is_file():
                raise ValueError("未找到已有 data/config.json；首次启动请通过网页设置密码")
            password = validate_password(getpass.getpass("新的管理密码: "))
            if password != getpass.getpass("再次输入管理密码: "):
                raise ValueError("两次输入的密码不一致")
            store = StateStore(directory, env={})
            store.save_admin_credential(password_credential(password))
            print("管理密码已重设，请启动服务后使用新密码登录。")
            return
        asyncio.run(serve())
    except (ValueError, OSError) as exc:
        parser.error(str(exc))
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
