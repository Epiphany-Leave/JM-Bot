"""Local image evidence for browser QA; keep the real bot task lifecycle intact."""
from contextlib import asynccontextmanager
import io
import json
from pathlib import Path
import threading
from urllib.request import ProxyHandler, build_opener
from unittest.mock import patch

from aiohttp import web
from PIL import Image

from jm_bot.downloader import DownloadFailure


SUBSCRIPTION = """mixed-port: 9999
external-controller: 0.0.0.0:9999
tun: {enable: true}
rules: [MATCH,DIRECT]
proxies:
  - {name: alpha, type: http, server: 127.0.0.1, port: 8080}
  - {name: beta, type: http, server: 127.0.0.1, port: 8081}
"""


class LocalDownload:
    def __init__(self, base_url):
        self.base_url = base_url
        self.image_reads = 0
        self.sessions = []
        self.stop_seen = threading.Event()
        self.release_stop = threading.Event()

    def image(self, job, session):
        self.sessions.append(session)
        direct = build_opener(ProxyHandler({}))
        with direct.open(self.base_url + "/metadata", timeout=5) as response:
            metadata = json.load(response)
        session.update(stage="images", total_pages=1, source="local-image-fixture")
        with direct.open(self.base_url + metadata["image"], timeout=5) as response:
            data = response.read()
        with Image.open(io.BytesIO(data)) as image:
            image.load()
            assert image.size == (32, 48)
        destination = Path(job) / "evidence.png"
        destination.write_bytes(data)
        session.completed(str(destination))
        self.image_reads += 1

    def detect(self, album_id, job, *, session):
        self.image(job, session)
        session.check()
        session.update(stage="complete")
        return {"api_ok": True, "image_ok": True, "source": "local-image-fixture",
                "message": "本地测试接口和完整图片读取成功"}

    def download(self, album_id, job, *, session):
        self.image(job, session)
        try:
            while not self.release_stop.wait(0.02):
                session.check()
            session.check()
            raise AssertionError("browser must cancel this fixture download")
        except DownloadFailure:
            self.stop_seen.set()
            # Hold the real worker so the browser can verify that busy survives cancellation.
            self.release_stop.wait(15)
            raise


@asynccontextmanager
async def local_download():
    image = io.BytesIO()
    Image.new("RGB", (32, 48), "#91b8a2").save(image, "PNG")
    app = web.Application()

    async def metadata(request):
        return web.json_response({"image": "/image.png"})

    async def image_response(request):
        return web.Response(body=image.getvalue(), content_type="image/png")

    async def subscription(request):
        return web.Response(text=SUBSCRIPTION, content_type="text/yaml")

    app.router.add_get("/metadata", metadata)
    app.router.add_get("/image.png", image_response)
    app.router.add_get("/subscription.yaml", subscription)
    runner = web.AppRunner(app, access_log=None)
    await runner.setup()
    site = web.TCPSite(runner, "127.0.0.1", 0)
    await site.start()
    port = site._server.sockets[0].getsockname()[1]
    fixture = LocalDownload(f"http://127.0.0.1:{port}")
    try:
        with patch("jm_bot.bot.detect_download", fixture.detect), patch("jm_bot.bot.download_pdf", fixture.download):
            yield fixture
    finally:
        fixture.release_stop.set()
        await runner.cleanup()
