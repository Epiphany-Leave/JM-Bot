import os
import socket
import tempfile
import threading
import types
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from unittest.mock import patch

from common import Postmans
import pikepdf
from PIL import Image
from jmcomic import JmModuleConfig
from jmcomic.jm_downloader import BaseDownloader, DownloadResult

from jm_bot import downloader


class Album:
    def __init__(self, count):
        self.count = count

    def __len__(self):
        return self.count

    def is_album(self):
        return True


class Photo:
    def __init__(self, index, count):
        self.index = index
        self.count = count

    def __len__(self):
        return self.count


class DownloadPdfTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.job = Path(self.temporary.name) / "job"
        self.job.mkdir()

    def result(self, option, *, partial=False, empty=False, corrupt=False, missing=False):
        images = self.job / "images"
        images.mkdir(exist_ok=True)
        album = Album(0 if empty else 2)
        dler = BaseDownloader(option)
        dler.begin_manifest(album)
        if not empty:
            first, second = Photo(1, 2), Photo(2, 1)
            pages = []
            for position, width in enumerate((120, 240, 360), start=1):
                path = images / f"page-{position}.png"
                Image.new("RGB", (width, 180), "white").save(path, dpi=(96, 96))
                pages.append(types.SimpleNamespace(index=position, save_path=str(path)))
            if corrupt:
                Path(pages[1].save_path).write_bytes(b"not an image")
            if missing:
                Path(pages[1].save_path).unlink()
            # The real JM manifest must sort both chapters and pages, not dict insertion order.
            dler.download_success_dict[album] = {
                second: [(pages[2].save_path, pages[2])],
                first: [(pages[1].save_path, pages[1]), (pages[0].save_path, pages[0])],
            }
            if partial:
                dler.download_success_dict[album][first].pop()
        else:
            dler.download_success_dict[album] = {}
        dler.finish_manifest(album)
        return DownloadResult(album, dler)

    def run_download(self, **result_options):
        def fetch(album_id, option, **kwargs):
            self.assertEqual(album_id, "11451")
            return self.result(option, **result_options)

        with patch.object(downloader.jmcomic, "download_album", side_effect=fetch):
            return downloader.download_pdf("11451", self.job)

    def test_real_pdf_preserves_chapter_and_page_order(self):
        pdf = self.run_download()
        self.assertEqual(pdf.name, "jm_11451.pdf")
        with pikepdf.open(pdf) as document:
            self.assertEqual(len(document.pages), 3)
            for page, expected_width in zip(document.pages, (90, 180, 270)):
                self.assertAlmostEqual(float(page.mediabox[2]), expected_width, delta=0.05)
        self.assertFalse(list(self.job.glob("*.part")))

    def test_partial_download_never_creates_final_pdf(self):
        with self.assertRaises(Exception):
            self.run_download(partial=True)
        self.assertFalse(list(self.job.glob("*.pdf")))

    def test_empty_download_never_creates_final_pdf(self):
        with self.assertRaises(Exception):
            self.run_download(empty=True)
        self.assertFalse(list(self.job.glob("*.pdf")))

    def test_corrupt_or_missing_image_never_creates_final_pdf(self):
        for failure in ("corrupt", "missing"):
            with self.subTest(failure=failure):
                with self.assertRaises(Exception):
                    self.run_download(**{failure: True})
                self.assertFalse(list(self.job.glob("*.pdf")))

    def test_network_failure_is_not_reported_as_a_pdf(self):
        with patch.object(downloader.jmcomic, "download_album", side_effect=TimeoutError):
            with self.assertRaises(TimeoutError):
                downloader.download_pdf("11451", self.job)
        self.assertFalse(list(self.job.glob("*.pdf")))

    def test_real_http_transport_ignores_inherited_unreachable_proxy(self):
        class Handler(BaseHTTPRequestHandler):
            def do_GET(self):
                body = b"direct-local-request"
                self.send_response(200)
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, *args):
                pass

        server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        threading.Thread(
            target=server.serve_forever, kwargs={"poll_interval": 0.01}, daemon=True
        ).start()
        # Bind without listening so the bad proxy port cannot be reassigned by another process.
        bad_proxy_socket = socket.socket()
        bad_proxy_socket.bind(("127.0.0.1", 0))
        self.addCleanup(bad_proxy_socket.close)
        bad_proxy = f"http://127.0.0.1:{bad_proxy_socket.getsockname()[1]}"
        inherited_proxy = {"http": bad_proxy, "https": bad_proxy}
        proxy_environment = {
            key: "" for key in (
                "HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "NO_PROXY",
                "http_proxy", "https_proxy", "all_proxy", "no_proxy",
            )
        }

        def fetch(album_id, option, **kwargs):
            transport = Postmans.create(data=option.client.postman.src_dict)
            response = transport.get(f"http://127.0.0.1:{server.server_port}/ping", timeout=2)
            self.assertEqual(response.content, b"direct-local-request")
            return self.result(option)

        with patch.dict(os.environ, proxy_environment), patch.object(
            JmModuleConfig, "DEFAULT_PROXIES", inherited_proxy
        ), patch.object(downloader.jmcomic, "download_album", side_effect=fetch):
            pdf = downloader.download_pdf("11451", self.job)
        self.assertTrue(pdf.is_file())


if __name__ == "__main__":
    unittest.main()
