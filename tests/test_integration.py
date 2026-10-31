"""Real WebSocket and PDF flow; only the remote comic download is substituted."""
import asyncio
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import pikepdf
import websockets

from jm_bot.bot import JMBot
from jm_bot.config import Settings
from tests.test_bot import event, make_pdf


class IntegrationTests(unittest.IsolatedAsyncioTestCase):
    async def test_command_over_websocket_produces_readable_pdf_and_cleans_after_ack(self):
        with tempfile.TemporaryDirectory() as folder:
            uploaded = asyncio.get_running_loop().create_future()
            completed = asyncio.Event()

            async def peer(socket):
                self.assertEqual(socket.request.headers["Authorization"], "Bearer local-test-token")
                await socket.send(json.dumps(event()))
                async for raw in socket:
                    request = json.loads(raw)
                    if request["action"] == "upload_private_file":
                        try:
                            path = Path(request["params"]["file"])
                            self.assertEqual(request["params"]["user_id"], "10001")
                            with pikepdf.open(path) as document:
                                self.assertEqual(len(document.pages), 1)
                            uploaded.set_result(path)
                        except BaseException as exc:
                            uploaded.set_exception(exc)
                            raise
                    elif request["action"] == "send_private_msg":
                        if request["params"]["message"][0]["data"]["text"] == "PDF 已上传。":
                            completed.set()
                    else:
                        raise AssertionError("Unexpected OneBot action")
                    await socket.send(json.dumps({"status": "ok", "retcode": 0,
                                                  "data": None, "echo": request["echo"]}))

            async with websockets.serve(peer, "127.0.0.1", 0) as server:
                port = server.sockets[0].getsockname()[1]
                bot = JMBot(Settings(ws_url=f"ws://127.0.0.1:{port}/", token="local-test-token",
                                     allowed_users=frozenset({"10001"}), download_dir=Path(folder)))
                with patch("jm_bot.bot.download_pdf", side_effect=make_pdf):
                    runner = asyncio.create_task(bot.client.run())
                    try:
                        path = await asyncio.wait_for(uploaded, 3)
                        await asyncio.wait_for(completed.wait(), 3)
                        async with asyncio.timeout(3):
                            while path.exists():
                                await asyncio.sleep(0.005)
                        self.assertFalse(list(Path(folder).iterdir()))
                    finally:
                        await bot.close()
                        await runner
