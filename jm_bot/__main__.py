import argparse
import asyncio
import logging
import signal

from . import __version__
from .bot import JMBot
from .config import Settings


async def serve(settings: Settings) -> None:
    bot = JMBot(settings)
    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for signum in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(signum, stop.set)
        except NotImplementedError:
            pass  # Windows uses asyncio.run's KeyboardInterrupt handling.
    connection = asyncio.create_task(bot.client.run())
    stopping = asyncio.create_task(stop.wait())
    logging.info("JM-Bot %s 启动；等待 NapCat 连接，用户白名单 %d 项、群白名单 %d 项",
                 __version__, len(settings.allowed_users), len(settings.allowed_groups))
    try:
        await asyncio.wait((connection, stopping), return_when=asyncio.FIRST_COMPLETED)
        if connection.done():
            await connection
    finally:
        stopping.cancel()
        await bot.close()
        await asyncio.gather(connection, stopping, return_exceptions=True)


def main() -> None:
    parser = argparse.ArgumentParser(description="JM-Bot：仅通过 NapCat 处理 /jm <ID>。配置来自 Compose 环境变量。")
    parser.add_argument("--version", action="version", version=__version__)
    parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    try:
        settings = Settings.from_env()
    except ValueError as exc:
        parser.error(str(exc))
    try:
        asyncio.run(serve(settings))
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
