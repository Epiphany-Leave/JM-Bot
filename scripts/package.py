"""Package only public source and deployment examples; never include NAS data."""

import hashlib
from pathlib import Path
from zipfile import ZIP_DEFLATED, ZipFile


ROOT = Path(__file__).resolve().parents[1]
SOURCE_FILES = (
    ".gitignore", ".dockerignore", "LICENSE", "Dockerfile", "requirements.txt", "compose.yaml", "README.md",
    "samples/test.pdf",
)


def source_files() -> list[tuple[Path, str]]:
    names = set(SOURCE_FILES)
    for folder in ("jm_bot", "scripts", "tests"):
        names.update(path.relative_to(ROOT).as_posix() for path in (ROOT / folder).rglob("*.py"))
    for folder, pattern in (("docs", "*.md"), (".github/workflows", "*.yml")):
        names.update(path.relative_to(ROOT).as_posix() for path in (ROOT / folder).rglob(pattern))
    static = ROOT / "jm_bot" / "static"
    names.update(path.relative_to(ROOT).as_posix() for path in static.rglob("*") if path.is_file())
    return [(ROOT / name, name) for name in sorted(names) if "__pycache__" not in Path(name).parts]


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
    write_zip(source, source_files())
    deployment = dist / "jm-bot-deploy.zip"
    write_zip(deployment, [
        (ROOT / "compose.yaml", "compose.yaml"),
        (ROOT / "README.md", "README.md"),
        (ROOT / "docs" / "maintenance.md", "docs/maintenance.md"),
        (ROOT / "docs" / "verification.md", "docs/verification.md"),
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
