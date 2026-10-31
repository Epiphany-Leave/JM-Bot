from pathlib import Path

import img2pdf
import jmcomic


def download_pdf(album_id: str, job_dir: Path) -> Path:
    """Download a complete album before producing a PDF; run outside the event loop."""
    option = jmcomic.JmOption.construct({
        "dir_rule": {
            "base_dir": str(job_dir / "images"),
            "rule": "Bd_Aid_Pindex",
        },
        "download": {
            "cache": False,
            "threading": {"photo": 1, "image": 5},
        },
        "client": {
            "impl": "api",
            "retry_times": 2,
            # The pinned library defaults to a localhost proxy absent from this Compose.
            "postman": {"meta_data": {"timeout": 30, "proxies": None}},
        },
    })
    result = jmcomic.download_album(album_id, option=option, check_exception=True)
    images = result.manifest.image_filepath_list
    if not result.downloader.all_success or not images:
        raise ValueError("漫画下载不完整或没有图片，拒绝生成 PDF")
    if any(not Path(path).is_file() or Path(path).stat().st_size == 0 for path in images):
        raise ValueError("下载图片缺失或为空，拒绝生成 PDF")

    # The pinned jmcomic manifest orders chapters and pages numerically.
    pdf = job_dir / f"jm_{album_id}.pdf"
    partial = pdf.with_suffix(".pdf.part")
    with partial.open("wb") as stream:
        img2pdf.convert(images, outputstream=stream)
    if partial.stat().st_size == 0:
        raise ValueError("PDF 合成结果为空")
    partial.replace(pdf)
    pdf.chmod(0o644)
    return pdf
