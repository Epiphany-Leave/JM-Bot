"""Check the image and Compose's directory creation without a real QQ login.

NapCat and Mihomo are replaced only here by short-lived filesystem checkers;
the separate mihomo_smoke.py runs the real core.
"""

import argparse
import asyncio
from http.cookiejar import CookieJar
import json
from pathlib import Path
import secrets
import sys
import tempfile
from urllib.error import HTTPError, URLError
from urllib.request import HTTPCookieProcessor, Request, build_opener
import uuid


async def docker(*args: str, timeout: float = 60) -> str:
    process = await asyncio.create_subprocess_exec(
        "docker", *args, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
    )
    try:
        stdout, stderr = await asyncio.wait_for(process.communicate(), timeout)
    except BaseException:
        if process.returncode is None:
            process.kill()
        await process.wait()
        raise
    if process.returncode:
        # Command arguments can contain a temporary admin password.
        raise RuntimeError(f"docker {args[0]} failed: {stderr.decode(errors='replace').strip()}")
    return stdout.decode().strip()


def request(opener, base: str, path: str, *, method="GET", data=None, status=200):
    body = None if data is None else json.dumps(data).encode()
    headers = {"Content-Type": "application/json"} if body is not None else {}
    req = Request(base + path, data=body, headers=headers, method=method)
    try:
        response = opener.open(req, timeout=5)
    except HTTPError as exc:
        response = exc
    with response:
        if response.code != status:
            raise RuntimeError(f"{method} {path} returned HTTP {response.code}, expected {status}")
        result = response.read()
        if "application/json" in response.headers.get("Content-Type", ""):
            return json.loads(result)
        return result.decode()


async def smoke(image: str) -> None:
    if sys.platform != "linux":
        raise RuntimeError("This smoke check requires Linux and a local Docker daemon")
    password = secrets.token_hex(16)
    permissions = {"allowed_users": ["10001"], "allowed_groups": ["20001"], "group_allow_all": True}
    name = f"jm-bot-smoke-{uuid.uuid4().hex[:12]}"
    with tempfile.TemporaryDirectory(prefix="jm-bot-smoke-") as directory:
        root = Path(directory).resolve()
        root.chmod(0o755)
        folders = ("data", "napcat-config", "downloads", "proxy-data", "qq-data")
        mounts = []
        for folder in folders:
            mounts.extend(("--volume", f"{root / folder}:/{folder}"))
        canonical = Path(__file__).resolve().parents[1] / "compose.yaml"
        config = json.loads(await docker("compose", "--project-directory", str(root),
                                        "--project-name", name, "-f", str(canonical),
                                        "config", "--format", "json"))
        sources = {Path(mount["source"]).resolve() for service in config["services"].values()
                   for mount in service.get("volumes", []) if mount["type"] == "bind"}
        assert sources == {root / folder for folder in folders}, "Compose mounts escaped the temporary project"
        assert all(network.get("name", "").startswith(name + "_") for network in config["networks"].values())
        for service in config["services"].values():
            service["image"] = image
            service["restart"] = "no"
        main_service = config["services"]["jm-bot"]
        main_service["container_name"] = name
        main_service["environment"]["ADMIN_PASSWORD"] = password
        main_service["ports"] = ["127.0.0.1::8080"]
        main_service["healthcheck"].update(interval="1s", start_period="1s")
        napcat = config["services"]["napcat"]
        napcat["entrypoint"] = ["python", "-c"]
        napcat["command"] = [(
            "from pathlib import Path; "
            "p=Path('/app/.config/QQ/compose-check'); "
            "p.exists() or p.write_text('preserved'); "
            "assert p.read_text() == 'preserved'; "
            "assert Path('/app/napcat/config/webui.json').is_file()"
        )]
        mihomo = config["services"]["mihomo"]
        mihomo["entrypoint"] = ["python", "-c"]
        mihomo["command"] = ["from pathlib import Path; assert Path('/proxy-data/config.yaml').is_file()"]
        compose_file = root / "compose.json"
        compose_file.write_text(json.dumps(config), encoding="utf-8")
        compose_file.chmod(0o600)
        compose_args = ("compose", "--project-name", name, "-f", str(compose_file))
        await docker(*compose_args, "config", "--quiet")
        assert not any((root / folder).exists() for folder in folders)
        state_check = (
            "import hashlib, json; from pathlib import Path\n"
            "state_path = Path('/data/config.json')\n"
            "assert state_path.stat().st_mode & 0o777 == 0o600\n"
            "assert state_path.stat().st_uid == 0\n"
            "state = json.loads(state_path.read_text())\n"
            "tokens = [state[key] for key in ('onebot_token', 'napcat_token', 'proxy_control_token', 'proxy_password')]\n"
            "assert all(isinstance(token, str) and len(token) >= 32 for token in tokens)\n"
            "webui = Path('/napcat-config/webui.json')\n"
            "assert webui.stat().st_mode & 0o777 == 0o600\n"
            "assert (webui.stat().st_uid, webui.stat().st_gid) == (1000, 1000)\n"
            "assert json.loads(webui.read_text())['token'] == state['napcat_token']\n"
            "print(hashlib.sha256(json.dumps(tokens).encode()).hexdigest())\n"
        )

        async def start():
            await docker(*compose_args, "up", "--detach")
            checkers = (await docker(*compose_args, "ps", "--all", "--quiet", "napcat", "mihomo")).splitlines()
            assert len(checkers) == 2
            assert all(code == "0" for code in (await docker("wait", *checkers)).splitlines())
            assert all((root / folder).is_dir() for folder in folders)
            port = (await docker("port", name, "8080/tcp")).rsplit(":", 1)[-1]
            base = f"http://127.0.0.1:{port}"
            opener = build_opener(HTTPCookieProcessor(CookieJar()))
            for attempt in range(30):
                try:
                    health = request(opener, base, "/healthz")
                    assert health.get("service") == "jm-bot" and health.get("version")
                    break
                except (URLError, OSError, RuntimeError):
                    if attempt == 29:
                        raise RuntimeError("WebUI did not become healthy without NapCat") from None
                    await asyncio.sleep(1)
            assert request(opener, base, "/"), "Login HTML was empty"
            for asset in ("/static/app.js", "/static/style.css"):
                assert request(opener, base, asset), f"Missing image asset: {asset}"
            request(opener, base, "/api/status", status=401)
            request(opener, base, "/api/auth/login", method="POST", data={"password": password})
            assert request(opener, base, "/api/status")["version"] == health["version"]
            return opener, base

        try:
            opener, base = await start()
            print("PASS: Compose created all five absent bind directories and shared prepared configuration", flush=True)
            request(opener, base, "/api/permissions", method="PUT", data=permissions)
            saved = request(opener, base, "/api/permissions")
            assert all(saved[key] == value for key, value in permissions.items()), "Permissions were not saved"
            token_fingerprint = await docker("exec", "--user", "0:0", name, "python", "-c", state_check)
            print("PASS: offline health, bundled HTML/JS/CSS, authentication and permission save", flush=True)

            await docker(
                "exec", "--user", "0:0", name, "python", "-c",
                "from pathlib import Path; import shutil; "
                "p=Path('/downloads/permission-check'); p.mkdir(mode=0o755); p.chmod(0o755); "
                "f=p/'sample.pdf'; shutil.copyfile('/app/samples/test.pdf', f); f.chmod(0o644); "
                "assert f.stat().st_uid == 0; assert f.read_bytes().startswith(b'%PDF')",
            )
            readonly = f"type=bind,src={root / 'downloads'},dst=/downloads,readonly"
            readonly_config = f"type=bind,src={root / 'napcat-config'},dst=/napcat-config,readonly"
            await docker(
                "run", "--rm", "--name", name + "-reader", "--network", "none", "--user", "1000:1000",
                "--mount", readonly, "--mount", readonly_config, "--entrypoint", "python", image, "-c",
                "import errno, json, os; from pathlib import Path\n"
                "assert os.getuid() == 1000 and os.getgid() == 1000\n"
                "assert os.statvfs('/downloads').f_flag & os.ST_RDONLY\n"
                "assert os.statvfs('/napcat-config').f_flag & os.ST_RDONLY\n"
                "p = Path('/downloads/permission-check/sample.pdf')\n"
                "assert p.read_bytes().startswith(b'%PDF')\n"
                "config = Path('/napcat-config/webui.json')\n"
                "token = json.loads(config.read_text()).get('token')\n"
                "assert isinstance(token, str) and len(token) >= 32\n"
                "for path in (p, config):\n"
                "    try:\n"
                "        path.write_bytes(b'must not write')\n"
                "    except OSError as exc:\n"
                "        assert exc.errno in (errno.EROFS, errno.EACCES), exc\n"
                "    else:\n"
                "        raise AssertionError('Read-only mount accepted a write')\n",
            )
            print("PASS: private config ownership/mode and UID/GID 1000 read-only access to config and PDF", flush=True)

            size = await docker("image", "inspect", "--format", "{{.Size}}", image)
            memory = await docker("stats", "--no-stream", "--format", "{{.MemUsage}}", name)
            rss = await docker(
                "exec", name, "python", "-c",
                "from pathlib import Path; "
                "print(next(line for line in Path('/proc/1/status').read_text().splitlines() if line.startswith('VmRSS:')))",
            )
            print(f"Image size: {size} bytes; idle memory: {memory}; PID 1 {rss}", flush=True)
            await docker(*compose_args, "down")
            opener, base = await start()
            saved = request(opener, base, "/api/permissions")
            assert all(saved[key] == value for key, value in permissions.items()), "Permissions were lost on recreation"
            restored_fingerprint = await docker("exec", "--user", "0:0", name, "python", "-c", state_check)
            assert restored_fingerprint == token_fingerprint, "Internal tokens changed on recreation"
            await docker(
                "exec", name, "python", "-c",
                "from pathlib import Path; "
                "assert Path('/downloads/permission-check/sample.pdf').read_bytes().startswith(b'%PDF')",
            )
            print("PASS: recreating the container preserves permissions, internal tokens and mounted output", flush=True)
        finally:
            try:
                await docker("rm", "--force", name + "-reader")
            except (RuntimeError, FileNotFoundError):
                pass
            await docker(*compose_args, "down")
            # Only these temporary mounts belong to this invocation.
            try:
                await docker(
                    "run", "--rm", "--name", name + "-cleanup", "--network", "none",
                    "--user", "0:0", *mounts, "--entrypoint", "python", image, "-c",
                    "from pathlib import Path; import shutil\n"
                    "for folder in ('/data', '/napcat-config', '/downloads', '/proxy-data', '/qq-data'):\n"
                    "    for path in Path(folder).iterdir():\n"
                    "        if path.is_dir() and not path.is_symlink(): shutil.rmtree(path)\n"
                    "        else: path.unlink()\n"
                    "    Path(folder).chmod(0o755)\n",
                )
            finally:
                try:
                    await docker("rm", "--force", name + "-cleanup")
                except (RuntimeError, FileNotFoundError):
                    pass


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--image", required=True, help="Locally built bot image, e.g. jm-bot:ci")
    args = parser.parse_args()
    asyncio.run(smoke(args.image))


if __name__ == "__main__":
    main()
