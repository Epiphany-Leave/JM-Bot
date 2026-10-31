"""Linux/Docker smoke check; uses only a local mock OneBot server."""

import argparse
import asyncio
import json
import os
from pathlib import Path
import secrets
import sys
import tempfile
import uuid

import websockets


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
        # Avoid echoing command arguments, which include a short-lived token.
        raise RuntimeError(f"docker {args[0]} failed: {stderr.decode(errors='replace').strip()}")
    return stdout.decode().strip()


async def smoke(image: str) -> None:
    if sys.platform != "linux":
        raise RuntimeError("This smoke check requires Linux and a local Docker daemon (--network host)")
    token = secrets.token_urlsafe(32)
    exchanges: asyncio.Queue = asyncio.Queue()

    async def onebot(socket):
        if socket.request.headers.get("Authorization") != f"Bearer {token}":
            await exchanges.put(RuntimeError("Bot sent an incorrect Bearer authorization header"))
            await socket.close(code=1008)
            return
        await socket.send(json.dumps({
            "post_type": "message", "message_type": "private", "sub_type": "friend",
            "self_id": 99999, "user_id": 10001, "message_id": 1,
            "message": [{"type": "text", "data": {"text": "/jm"}}],
            "raw_message": "/jm", "sender": {"user_id": 10001, "nickname": "smoke"},
        }))
        async for raw in socket:
            request = json.loads(raw)
            action = request.get("action")
            if action == "get_login_info":
                data = {"user_id": 99999, "nickname": "mock"}
            elif action == "send_private_msg" and str(request.get("params", {}).get("user_id")) == "10001":
                if not request["params"].get("message"):
                    await exchanges.put(RuntimeError("Bot sent an empty unauthorized-user response"))
                    return
                data = {"message_id": 2}
            else:
                await exchanges.put(RuntimeError("Bot called an unexpected API for an unauthorized user"))
                await socket.close(code=1008)
                return
            await socket.send(json.dumps({
                "echo": request["echo"], "status": "ok", "retcode": 0,
                "data": data,
            }))
            if action == "send_private_msg":
                await exchanges.put(None)

    async with websockets.serve(onebot, "127.0.0.1", 0) as server:
        port = server.sockets[0].getsockname()[1]
        with tempfile.TemporaryDirectory(prefix="jm-bot-smoke-") as directory:
            os.chmod(directory, 0o755)
            name = f"jm-bot-smoke-{uuid.uuid4().hex[:12]}"
            mount = f"type=bind,src={Path(directory).resolve()},dst=/downloads"
            started = False
            try:
                async def start_bot():
                    nonlocal started
                    await docker(
                        "run", "--detach", "--name", name, "--network", "host",
                        "--mount", mount,
                        "--env", f"ONEBOT_WS_URL=ws://127.0.0.1:{port}/",
                        "--env", f"ONEBOT_TOKEN={token}",
                        "--env", "ALLOWED_USERS=", "--env", "ALLOWED_GROUPS=",
                        "--env", "DOWNLOAD_DIR=/downloads", image,
                    )
                    started = True
                    failure = await asyncio.wait_for(exchanges.get(), 30)
                    if failure is not None:
                        raise failure
                    running = await docker("inspect", "--format", "{{.State.Running}}", name)
                    if running != "true":
                        raise RuntimeError("Bot container exited after the mocked message exchange")

                await start_bot()
                print("PASS: container startup, Bearer authorization, and unauthorized-message response", flush=True)
                await docker(
                    "exec", "--user", "0:0", name, "python", "-c",
                    "from pathlib import Path; import os; "
                    "p=Path('/downloads/permission-check'); p.mkdir(mode=0o755); "
                    "p.chmod(0o755); f=p/'sample.pdf'; f.write_bytes(b'smoke sample'); "
                    "f.chmod(0o644); assert f.stat().st_uid == 0; "
                    "assert p.stat().st_mode & 0o777 == 0o755; "
                    "assert f.stat().st_mode & 0o777 == 0o644",
                )
                read_check = (
                    "import errno, os; from pathlib import Path\n"
                    "assert os.getuid() == 1000 and os.getgid() == 1000\n"
                    "assert os.statvfs('/downloads').f_flag & os.ST_RDONLY\n"
                    "p = Path('/downloads/permission-check/sample.pdf')\n"
                    "assert p.read_bytes() == b'smoke sample'\n"
                    "try:\n"
                    "    p.write_bytes(b'must not write')\n"
                    "except OSError as exc:\n"
                    "    assert exc.errno in (errno.EROFS, errno.EACCES), exc\n"
                    "else:\n"
                    "    raise AssertionError('Read-only mount accepted a write')\n"
                )
                await docker(
                    "run", "--rm", "--name", name + "-reader", "--network", "none", "--user", "1000:1000",
                    "--mount", mount + ",readonly", "--entrypoint", "python",
                    image, "-c", read_check,
                )
                print("PASS: root-created 0755/0644 output is readable by UID/GID 1000 on a read-only mount", flush=True)
                await asyncio.sleep(2)
                size = await docker("image", "inspect", "--format", "{{.Size}}", image)
                memory = await docker("stats", "--no-stream", "--format", "{{.MemUsage}}", name)
                rss = await docker(
                    "exec", name, "python", "-c",
                    "from pathlib import Path; "
                    "print(next(line for line in Path('/proc/1/status').read_text().splitlines() if line.startswith('VmRSS:')))",
                )
                print(f"Image size: {size} bytes; idle memory (docker stats): {memory}; PID 1 {rss}", flush=True)
                await docker("rm", "--force", name)
                started = False
                await start_bot()
                await docker(
                    "exec", name, "python", "-c",
                    "from pathlib import Path; "
                    "assert Path('/downloads/permission-check/sample.pdf').read_bytes() == b'smoke sample'",
                )
                print("PASS: recreating the container preserves the mounted output", flush=True)
            finally:
                # The name is unique to this invocation; never remove user containers or paths.
                try:
                    await docker("rm", "--force", name + "-reader")
                except (RuntimeError, FileNotFoundError):
                    pass
                try:
                    await docker("rm", "--force", name)
                except (RuntimeError, FileNotFoundError):
                    if started:
                        raise
                # CI's host user cannot delete root-owned files inside a 0755 directory.
                if (Path(directory) / "permission-check").exists():
                    try:
                        await docker(
                            "run", "--rm", "--name", name + "-cleanup", "--network", "none",
                            "--user", "0:0", "--mount", mount, "--entrypoint", "python", image,
                            "-c", "import shutil; shutil.rmtree('/downloads/permission-check')",
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
