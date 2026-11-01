import io
import logging
import re
import threading
import time
from copy import deepcopy
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

import img2pdf
import jmcomic
from common import Postmans
from curl_cffi.requests import Session
from jmcomic.jm_exception import MissingAlbumPhotoException
from PIL import Image

from .download_settings import download_settings

logger = logging.getLogger(__name__)


class DownloadFailure(Exception):
    def __init__(self, code, message):
        self.code, self.message = code, message
        super().__init__(message)


class DownloadSession:
    def __init__(self, settings=None, proxy_url=None):
        self.settings = download_settings(settings)
        if self.settings["mode"] == "mihomo" and not proxy_url:
            raise DownloadFailure("api_timeout", "Mihomo 代理未配置，已停止下载")
        self.proxy_url = proxy_url if self.settings["mode"] == "mihomo" else None
        self.control = jmcomic.DownloadControl()
        self._lock = threading.RLock()
        self._start = self._last_progress = time.monotonic()
        self._state = dict(completed_pages=0, total_pages=0, source="", stage="metadata", stop_reason="")
        self._failure = None
        self._completed = set()
        self._photos = {}

    def snapshot(self):
        with self._lock:
            return dict(self._state)

    def cancel(self, reason="用户停止下载"):
        self.fail("cancelled", reason)

    def fail(self, code, message):
        with self._lock:
            if self._failure is None:
                self._failure = DownloadFailure(code, message)
                self._state["stop_reason"] = message
                self.control.cancel(message)

    def update(self, **values):
        with self._lock:
            if values.get("stage") == "images" and self._state["stage"] != "images":
                self._last_progress = time.monotonic()
            self._state.update(values)

    def check(self):
        with self._lock:
            now = time.monotonic()
            if (self._failure is None and self._state["stage"] in ("metadata", "images")
                    and now - self._start >= self.settings["job_timeout"]):
                self.fail("deadline", "下载已达到任务时限")
            if self._failure is None and self._state["stage"] == "metadata" and now - self._start >= 60:
                self.fail("api_timeout", "漫画信息获取超时")
            if (self._failure is None and self._state["stage"] == "images"
                    and now - self._last_progress >= self.settings["stall_timeout"]):
                self.fail("stalled", "图片下载长时间没有进展，已停止")
            if self._failure is not None:
                raise self._failure

    def timeout(self, image=False):
        self.check()
        now = time.monotonic()
        remaining = self.settings["job_timeout"] - (now - self._start)
        if self.snapshot()["stage"] == "metadata":
            remaining = min(remaining, 60 - (now - self._start))
            if remaining <= 0:
                self.fail("api_timeout", "漫画信息获取超时")
                self.check()
        if self.snapshot()["stage"] == "images":
            remaining = min(remaining, self.settings["stall_timeout"] - (now - self._last_progress))
        total = max(0.001, min(remaining, self.settings["image_timeout"] if image else 20))
        connect = min(self.settings["connect_timeout"], total / 2)
        # curl_cffi adds these values to obtain its total timeout.
        return connect, max(0.001, total - connect)

    def completed(self, path):
        with self._lock:
            if path not in self._completed:
                self._completed.add(path)
                self._state["completed_pages"] = len(self._completed)
                self._last_progress = time.monotonic()


def _verify(path):
    with Image.open(path) as image:
        image.load()
        if image.width <= 0 or image.height <= 0:
            raise ValueError("图片为空")


class _TaskClient(jmcomic.JmApiClient):
    def __init__(self, session, postman):
        self.session = session
        self.last_good = ""
        super().__init__(postman, list(jmcomic.JmModuleConfig.DOMAIN_API_LIST), retry_times=0)

    def fetch_detail_entity(self, jmid, clazz):
        try:
            return super().fetch_detail_entity(jmid, clazz)
        except MissingAlbumPhotoException:
            self.session.fail("invalid_id", "漫画或章节不存在，请检查编号")
            self.session.check()

    def after_init(self):
        # Keep discovery and cookies local; the library caches both globally.
        if jmcomic.JmModuleConfig.FLAG_API_CLIENT_AUTO_UPDATE_DOMAIN:
            for url in jmcomic.JmModuleConfig.API_URL_DOMAIN_SERVER_LIST[:1]:
                self.session.check()
                try:
                    domains = self.req_api_domain_server(url)
                    if domains:
                        self.domain_list = list(domains)
                except DownloadFailure:
                    raise
                except Exception:
                    pass
        if jmcomic.JmModuleConfig.FLAG_API_CLIENT_REQUIRE_COOKIES:
            self["cookies"] = dict(self.setting().resp.cookies)

    def request_with_retry(self, request, url, domain_index=0, retry_count=0,
                           is_image=False, _retry_errors=None, **kwargs):
        hosts = list(dict.fromkeys(self.domain_list))[:2] if url.startswith("/") else [None]
        for domain in hosts:
            self.session.check()
            target = self.of_api_url(url, domain) if domain else url
            call_kwargs = dict(kwargs)
            self.update_request_with_specify_domain(call_kwargs, domain, is_image)
            call_kwargs["timeout"] = self.session.timeout(is_image)
            try:
                response = request(target, **call_kwargs)
                self.session.check()
                return self.raise_if_resp_should_retry(response, is_image)
            except DownloadFailure:
                raise
            except Exception:
                self.session.check()
        if not is_image:
            self.session.fail("api_timeout", "漫画信息请求失败或超时")
            self.session.check()
        raise DownloadFailure("api_timeout", "图片请求失败或超时")

    def sources(self, url):
        original = urlsplit(url)
        pool = list(dict.fromkeys([self.last_good, original.netloc,
                                  *jmcomic.JmModuleConfig.DOMAIN_IMAGE_LIST]))
        selected, deferred, families = [], [], set()
        for host in filter(None, pool):
            family = ".".join(host.split(":")[0].split(".")[-2:])
            if family in families:
                deferred.append(host)
            else:
                selected.append(host)
                families.add(family)
        for host in (selected + deferred)[:self.session.settings["max_sources"]]:
            yield urlunsplit((original.scheme, host, original.path, original.query, original.fragment))

    def download_image(self, img_url, img_save_path, scramble_id=None, decode_image=True):
        self.session.check()
        self.session.update(stage="images")
        path = Path(img_save_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        partial = path.with_name(path.stem + ".part" + path.suffix)
        for target in self.sources(img_url):
            self.session.check()
            self.session.update(source=urlsplit(target).netloc)
            try:
                response = self.get_jm_image(target)
                response.require_success()
                _verify(io.BytesIO(response.content))
                self.session.check()
                self.save_image_resp(decode_image, str(partial), img_url, response, scramble_id)
                _verify(partial)
                self.session.check()
                partial.replace(path)
                with self.session._lock:
                    self.last_good = urlsplit(target).netloc
                self.session.completed(str(path))
                logger.info("image complete source=%s completed_pages=%s", urlsplit(target).netloc,
                            self.session.snapshot()["completed_pages"])
                return
            except DownloadFailure as error:
                logger.warning("image source failed source=%s type=%s", urlsplit(target).netloc, error.code)
                self.session.check()
            except Exception as error:
                logger.warning("image source failed source=%s type=%s", urlsplit(target).netloc, type(error).__name__)
                self.session.check()
            finally:
                partial.unlink(missing_ok=True)
        self.session.fail("image_sources_exhausted", "图片候选源均失败，已停止下载")
        self.session.check()


def _option(job_dir, session):
    proxies = {"http": "", "https": "", "all": ""}
    if session.proxy_url:
        proxies = {"http": session.proxy_url, "https": session.proxy_url}
    values = {
        "dir_rule": {"base_dir": str(job_dir / "images"), "rule": "Bd_Aid_Pindex"},
        "download": {"cache": False, "threading": {"photo": 1, "image": session.settings["concurrency"]}},
        "client": {"impl": "api", "retry_times": 0,
                   "postman": {"meta_data": {"timeout": 20, "proxies": proxies}}},
    }
    # option_default_dict aliases DEFAULT_PROXIES; detach before the recursive merge.
    defaults = deepcopy(jmcomic.JmOption.default_dict())
    merged = jmcomic.JmOption.merge_default_dict(values, defaults)
    return jmcomic.JmOption.construct(merged, cover_default=False)


def _postman(option, session):
    postman = Postmans.create(data=deepcopy(option.client.postman.src_dict))
    transport = Session(trust_env=False, proxies=postman.meta_data["proxies"])
    postman.__get__ = lambda: transport.get
    postman.__post__ = lambda: transport.post
    original = postman.before_request

    def bounded(kwargs):
        values = original(kwargs)
        # Domain discovery bypasses request_with_retry, so bound it here too.
        if "timeout" not in kwargs:
            values["timeout"] = session.timeout()
        return values

    postman.before_request = bounded
    return postman


def _client(option, session):
    return _TaskClient(session, _postman(option, session))


def _downloader(session):
    class TaskDownloader(jmcomic.JmDownloader):
        def create_client(self):
            return _client(self.option, session)

        def before_album(self, album):
            session.update(total_pages=int(album.page_count))
            super().before_album(album)

        def before_photo(self, photo):
            with session._lock:
                session._photos[str(photo.id)] = len(photo)
                session._state["total_pages"] = max(session._state["total_pages"], sum(session._photos.values()))
            super().before_photo(photo)

    return TaskDownloader


def _validate_id(album_id):
    if not isinstance(album_id, str) or not re.fullmatch(r"[1-9][0-9]*", album_id):
        raise DownloadFailure("invalid_id", "漫画编号必须是正整数")


def download_pdf(album_id: str, job_dir: Path, *, session=None) -> Path:
    """Return only after every native network worker has joined."""
    _validate_id(album_id)
    session = session or DownloadSession()
    session.check()
    option = _option(job_dir, session)
    try:
        result = jmcomic.download_album(album_id, option=option, downloader=_downloader(session),
                                       check_exception=True, control=session.control)
        session.check()
    except Exception:
        session.check()
        raise DownloadFailure("api_timeout", "漫画下载失败或超时") from None
    images = result.manifest.image_filepath_list
    if not result.downloader.all_success or not images:
        raise DownloadFailure("image_sources_exhausted", "漫画下载不完整，拒绝生成 PDF")
    session.update(stage="pdf")
    try:
        for image in images:
            session.check()
            _verify(image)
    except DownloadFailure:
        raise
    except Exception:
        raise DownloadFailure("image_sources_exhausted", "图片缺失或损坏，拒绝生成 PDF") from None
    session.update(stage="pdf", completed_pages=len(images), total_pages=len(images))
    pdf = job_dir / f"jm_{album_id}.pdf"
    partial = pdf.with_suffix(".pdf.part")
    try:
        with partial.open("wb") as stream:
            img2pdf.convert(images, outputstream=stream)
        if partial.stat().st_size == 0:
            raise ValueError("empty PDF")
        session.check()
        partial.replace(pdf)
        pdf.chmod(0o644)
    except DownloadFailure:
        raise
    except Exception:
        raise DownloadFailure("pdf_error", "PDF 合成失败") from None
    finally:
        partial.unlink(missing_ok=True)
    session.update(stage="complete")
    return pdf


def detect_download(album_id, job_dir, *, session=None):
    _validate_id(album_id)
    session = session or DownloadSession()
    report = dict(api_ok=False, image_ok=False, source="", message="")
    try:
        client = _client(_option(job_dir, session), session)
        album = client.get_album_detail(album_id)
        photo = album[0]
        client.check_photo(photo)
        report["api_ok"] = True
        client.download_by_image_detail(photo[0], str(job_dir / "detect.webp"))
        session.check()
        report.update(image_ok=True, message="漫画信息和完整图片下载成功")
        session.update(stage="complete", total_pages=1)
    except DownloadFailure as error:
        if error.code in ("cancelled", "deadline", "stalled"):
            raise
        report["message"] = error.message
    except Exception:
        report["message"] = "漫画信息或图片检测失败"
    report["source"] = session.snapshot()["source"]
    return report
