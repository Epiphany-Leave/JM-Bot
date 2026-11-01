import io
import os
import tempfile
import threading
import time
import unittest
import jmcomic
import pikepdf
from concurrent.futures import ThreadPoolExecutor
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from unittest.mock import patch

from PIL import Image
from jmcomic import JmModuleConfig

from jm_bot import downloader as d
from jm_bot.download_settings import download_settings


class NetworkTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.job = Path(self.tmp.name)
        self.calls = []
        buffer = io.BytesIO()
        Image.new("RGB", (32, 64), "red").save(buffer, format="PNG")
        self.image = buffer.getvalue()

    def server(self, body=None, delay=0, truncated=False, status=200):
        calls = self.calls
        body = self.image if body is None else body

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self):
                calls.append((self.headers.get("Host"), self.path))
                time.sleep(delay(self.path) if callable(delay) else delay)
                self.send_response(status)
                self.send_header("Content-Length", str(len(body) + (20 if truncated else 0)))
                self.end_headers()
                try:
                    self.wfile.write(body)
                except OSError:
                    pass
                self.close_connection = True

            def log_message(self, *args):
                pass

        server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        server.daemon_threads = True
        threading.Thread(target=server.serve_forever, kwargs={"poll_interval": 0.01}, daemon=True).start()
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        return f"127.0.0.1:{server.server_port}"

    def client(self, settings=None, proxy=None):
        session = d.DownloadSession(settings, proxy)
        with patch.object(d._TaskClient, "after_init"):
            client = d._client(d._option(self.job, session), session)
        return client, session

    def test_corrupt_200_and_truncated_try_different_hosts_preserve_query_and_decode(self):
        first = self.server(b"HTML error")
        second = self.server(self.image[:20], truncated=True)
        third = self.server()
        client, session = self.client()
        path = self.job / "page.webp"
        with patch.object(JmModuleConfig, "DOMAIN_IMAGE_LIST", [first, second, third]):
            client.download_image(f"http://{first}/media/photos/123/00001.png?v=123", str(path), 999999)
        self.assertEqual([host for host, _ in self.calls], [first, second, third])
        self.assertEqual({path for _, path in self.calls}, {"/media/photos/123/00001.png?v=123"})
        with Image.open(path) as image:
            self.assertEqual(image.size, (32, 64))
        self.assertFalse(list(self.job.glob("*.part*")))
        self.assertEqual(session.snapshot()["completed_pages"], 1)
        self.assertEqual(client.last_good, third)

    def test_attempt_cap_cancel_other_workers_and_no_partial_image(self):
        hosts = [self.server(b"corrupt") for _ in range(4)]
        client, session = self.client({"max_sources": 2})
        with patch.object(JmModuleConfig, "DOMAIN_IMAGE_LIST", hosts):
            with self.assertRaises(d.DownloadFailure) as failure:
                client.download_image(f"http://{hosts[0]}/a.png", str(self.job / "one.webp"))
            self.assertEqual(failure.exception.code, "image_sources_exhausted")
            self.assertTrue(session.control.is_cancelled)
            with self.assertRaises(d.DownloadFailure):
                client.download_image(f"http://{hosts[3]}/b.png", str(self.job / "two.webp"))
        self.assertEqual(len(self.calls), 2)
        self.assertFalse(list(self.job.iterdir()))

    def test_environment_proxies_disabled_and_explicit_proxy_only_task(self):
        direct = self.server()
        proxy = self.server()
        environment = {name: "http://127.0.0.1:1" for name in
                       ("HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "http_proxy", "https_proxy", "all_proxy")}
        environment.update(NO_PROXY="", no_proxy="")
        with patch.dict(os.environ, environment):
            client, _ = self.client()
            client.download_image(f"http://{direct}/direct.png", str(self.job / "direct.webp"))
            proxied, _ = self.client({"mode": "mihomo"}, f"http://{proxy}")
            proxied.download_image("http://unresolvable.invalid/proxy.png", str(self.job / "proxy.webp"))
            client.download_image(f"http://{direct}/again.png", str(self.job / "again.webp"))
        self.assertEqual(self.calls[0], (direct, "/direct.png"))
        self.assertEqual(self.calls[1], ("unresolvable.invalid", "http://unresolvable.invalid/proxy.png"))
        self.assertEqual(self.calls[2], (direct, "/again.png"))

    def test_cancelled_request_worker_joins_before_returning(self):
        host = self.server(delay=0.15)
        client, session = self.client()
        with ThreadPoolExecutor(max_workers=1) as pool:
            future = pool.submit(client.download_image, f"http://{host}/a.png", str(self.job / "a.webp"))
            deadline = time.monotonic() + 2
            while not self.calls and time.monotonic() < deadline:
                time.sleep(0.005)
            session.cancel()
            self.assertFalse(future.done())
            with self.assertRaises(d.DownloadFailure) as failure:
                future.result(timeout=2)
            self.assertEqual(failure.exception.code, "cancelled")
        self.assertFalse(list(self.job.iterdir()))

    def test_api_request_at_most_two_distinct_hosts(self):
        hosts = [self.server(b"not JSON") for _ in range(3)]
        client, _ = self.client()
        client.domain_list = [hosts[0], hosts[0], hosts[1], hosts[2]]
        client.of_api_url = lambda path, host: f"http://{host}{path}"
        with self.assertRaises(d.DownloadFailure) as caught:
            client.get("/album")
        self.assertEqual(caught.exception.code, "api_timeout")
        self.assertEqual([host for host, _ in self.calls], hosts[:2])

    def test_initial_metadata_budget_timeout_and_cancellation_codes(self):
        _, session = self.client()
        with patch.object(d.time, "monotonic", return_value=session._start + 61):
            with self.assertRaises(d.DownloadFailure) as error:
                session.timeout()
        self.assertEqual(error.exception.code, "api_timeout")
        for code, settings, advance, images in [
            ("deadline", {"job_timeout": 1}, 2, False),
            ("stalled", {"stall_timeout": 1}, 2, True),
        ]:
            current = d.DownloadSession(settings)
            if images:
                current.update(stage="images")
            with patch.object(d.time, "monotonic", return_value=current._start + advance):
                with self.assertRaises(d.DownloadFailure) as failure:
                    current.check()
            self.assertEqual(failure.exception.code, code)

    def test_timeout_is_total_not_connect_plus_total_and_invalid_settings(self):
        session = d.DownloadSession()
        self.assertEqual(session.timeout(True), (8, 37))
        for value in ({"mode": "bad"}, {"extra": 1}, {"concurrency": True},
                      {"max_sources": 7}, {"connect_timeout": 8, "image_timeout": 2}):
            with self.assertRaises(ValueError):
                download_settings(value)
        with self.assertRaises(d.DownloadFailure):
            d.DownloadSession({"mode": "mihomo"})

    def album_client(self, client, host, count=3):
        album = jmcomic.JmAlbumDetail("123", "999999", "local fixture", [], count,
                                       "", "", "0", "0", 0, [], [], [], [])
        client.get_album_detail = lambda *args, **kwargs: album

        def photo(*args, **kwargs):
            detail = jmcomic.JmPhotoDetail("123", "local fixture", "0", 1, scramble_id="999999",
                                           page_arr=[f"{i:05}.png" for i in range(count)],
                                           data_original_domain=host, from_album=album)
            detail.get_img_data_original = lambda name: f"http://{host}/media/photos/123/{name}"
            return detail

        client.get_photo_detail = photo
        return client

    def test_real_native_downloader_manifest_pdf_and_detection(self):
        host = self.server()
        client, session = self.client()
        self.album_client(client, host)
        registry = dict(JmModuleConfig.REGISTRY_CLIENT)
        proxies = dict(JmModuleConfig.DEFAULT_PROXIES)
        with patch.object(d, "_client", return_value=client):
            pdf = d.download_pdf("123", self.job, session=session)
        with pikepdf.open(pdf) as document:
            self.assertEqual(len(document.pages), 3)
        self.assertEqual(session.snapshot()["completed_pages"], 3)
        self.assertEqual(session.snapshot()["total_pages"], 3)
        self.assertEqual(registry, JmModuleConfig.REGISTRY_CLIENT)
        self.assertEqual(proxies, JmModuleConfig.DEFAULT_PROXIES)
        probe, probe_session = self.client()
        self.album_client(probe, host)
        with patch.object(d, "_client", return_value=probe):
            report = d.detect_download("123", self.job / "probe", session=probe_session)
        self.assertTrue(report["api_ok"])
        self.assertTrue(report["image_ok"])
        self.assertEqual(probe_session.snapshot()["completed_pages"], 1)
        self.assertFalse(list((self.job / "probe").glob("*.pdf")))

    def test_real_native_workers_stop_early_and_never_build_pdf(self):
        host = self.server(b"broken")
        client, session = self.client({"max_sources": 1, "concurrency": 1})
        self.album_client(client, host, count=20)
        with patch.object(d, "_client", return_value=client):
            with self.assertRaises(d.DownloadFailure) as failure:
                d.download_pdf("123", self.job, session=session)
        self.assertEqual(failure.exception.code, "image_sources_exhausted")
        self.assertEqual(len(self.calls), 1)
        self.assertFalse(list(self.job.glob("*.pdf")))
        self.assertFalse(list(self.job.rglob("*.part*")))

    def test_image_total_timeout_fails_over_with_real_transport(self):
        slow = self.server(delay=1.4)
        fast = self.server()
        client, session = self.client({"connect_timeout": 1, "image_timeout": 1, "max_sources": 2})
        with patch.object(JmModuleConfig, "DOMAIN_IMAGE_LIST", [slow, fast]):
            client.download_image(f"http://{slow}/page.png", str(self.job / "page.png"))
        self.assertEqual([host for host, _ in self.calls], [slow, fast])
        self.assertEqual(session.snapshot()["completed_pages"], 1)

    def test_native_concurrent_workers_join_after_first_image_failure(self):
        host = self.server(b"broken", delay=lambda path: 0.1 if "00000" in path else 0.3)
        client, session = self.client({"max_sources": 1, "concurrency": 3})
        self.album_client(client, host, count=20)
        start = time.monotonic()
        with patch.object(d, "_client", return_value=client):
            with self.assertRaises(d.DownloadFailure):
                d.download_pdf("123", self.job, session=session)
        self.assertGreaterEqual(time.monotonic() - start, 0.28)
        self.assertEqual(len(self.calls), 3)
        self.assertFalse(list(self.job.glob("*.pdf")))

    def test_scramble_decoding_retained_across_source_replacement(self):
        raw = Image.new("RGB", (20, 100))
        for y in range(raw.height):
            for x in range(raw.width):
                raw.putpixel((x, y), (y * 2, y, 0))
        buffer = io.BytesIO()
        raw.save(buffer, format="PNG")
        first, second = self.server(b"invalid"), self.server(buffer.getvalue())
        client, _ = self.client({"max_sources": 2})
        original = f"http://{first}/media/photos/123/00001.png?v=12"
        expected = self.job / "expected.png"
        jmcomic.JmImageTool.decode_and_save(10, raw, str(expected))
        with patch.object(JmModuleConfig, "DOMAIN_IMAGE_LIST", [first, second]):
            client.download_image(original, str(self.job / "actual.png"), scramble_id=0)
        with Image.open(expected) as expected_image, Image.open(self.job / "actual.png") as actual:
            self.assertEqual(actual.tobytes(), expected_image.tobytes())
            self.assertNotEqual(actual.tobytes(), raw.tobytes())

    def test_known_missing_detail_is_invalid_id_for_pdf_and_detection(self):
        for detect in (False, True):
            client, session = self.client()
            missing = d.MissingAlbumPhotoException("untrusted native detail", {})
            with patch.object(d, "_client", return_value=client), patch.object(
                jmcomic.JmApiClient, "fetch_detail_entity", side_effect=missing
            ):
                if detect:
                    report = d.detect_download("999999", self.job, session=session)
                    self.assertFalse(report["api_ok"])
                    self.assertIn("编号", report["message"])
                    self.assertNotIn("untrusted", report["message"])
                else:
                    with self.assertRaises(d.DownloadFailure) as error:
                        d.download_pdf("999999", self.job, session=session)
                    self.assertEqual(error.exception.code, "invalid_id")
                self.assertEqual(session._failure.code, "invalid_id")
        self.assertFalse(list(self.job.rglob("*.pdf")))

    def test_cdn_404_is_source_failure_and_can_recover(self):
        missing, working = self.server(b"missing", status=404), self.server()
        client, session = self.client({"max_sources": 2})
        with patch.object(JmModuleConfig, "DOMAIN_IMAGE_LIST", [missing, working]):
            client.download_image(f"http://{missing}/page.png", str(self.job / "page.png"))
        self.assertEqual([host for host, _ in self.calls], [missing, working])
        self.assertEqual(session.snapshot()["completed_pages"], 1)
        self.assertIsNone(session._failure)


if __name__ == "__main__":
    unittest.main()
