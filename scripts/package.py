"""Package only public source and deployment examples; never include NAS data."""

import hashlib
from pathlib import Path
from zipfile import ZIP_DEFLATED, ZipFile


ROOT = Path(__file__).resolve().parents[1]
SOURCE_FILES = (
    ".gitignore", ".dockerignore", "LICENSE", "Dockerfile", "requirements.txt", "compose.yaml", "README.md",
    ".github/workflows/ci.yml", "docs/decisions-standalone.md", "docs/verification.md", "samples/test.pdf",
    "jm_bot/__init__.py", "jm_bot/__main__.py", "jm_bot/config.py", "jm_bot/bot.py",
    "jm_bot/downloader.py", "jm_bot/onebot.py", "scripts/package.py", "scripts/container_smoke.py",
    "tests/__init__.py", "tests/test_bot.py", "tests/test_config.py",
    "tests/test_downloader.py", "tests/test_onebot.py", "tests/test_integration.py",
)


def write_zip(destination: Path, files: list[tuple[Path, str]]) -> None:
    with ZipFile(destination, "w", compression=ZIP_DEFLATED) as archive:
        for path, name in files:
            archive.write(path, name)
    with ZipFile(destination) as archive:
        assert archive.testzip() is None, f"Corrupt ZIP: {destination}"
        assert set(archive.namelist()) == {name for _, name in files}


def main() -> None:
    dist = ROOT / "dist"
    dist.mkdir(exist_ok=True)
    source = dist / "jm-bot-source.zip"
    write_zip(source, [(ROOT / name, name) for name in SOURCE_FILES])
    deployment = dist / "jm-bot-deploy.zip"
    write_zip(deployment, [
        (ROOT / "compose.yaml", "compose.yaml"),
        (ROOT / "README.md", "README.md"),
        (ROOT / "samples" / "test.pdf", "samples/test.pdf"),
    ])
    checksums = []
    for path in (source, deployment):
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        checksums.append(f"{digest}  {path.name}")
        print(f"{path.name}: {path.stat().st_size} bytes, sha256={digest}")
    (dist / "SHA256SUMS.txt").write_text("\n".join(checksums) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
