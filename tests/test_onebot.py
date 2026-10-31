import asyncio
import json
import unittest

import websockets

from jm_bot.onebot import OneBotAPIError, OneBotClient, OneBotDisconnected, OneBotError


async def ignore_event(event, generation):
    pass


class OneBotTests(unittest.IsolatedAsyncioTestCase):
    async def start(self, handler, on_event=ignore_event, timeout=1):
        server = await websockets.serve(handler, "127.0.0.1", 0)
        self.addAsyncCleanup(server.wait_closed)
        self.addCleanup(server.close)
        port = server.sockets[0].getsockname()[1]
        client = OneBotClient(f"ws://127.0.0.1:{port}", "secret", on_event, timeout)
        runner = asyncio.create_task(client.run())
        self.addAsyncCleanup(client.close)
        async with asyncio.timeout(3):
            while not client.connected:
                await asyncio.sleep(0.005)
        return client, runner

    async def test_event_callback_can_call_api(self):
        result = asyncio.get_running_loop().create_future()

        async def handler(ws):
            self.assertEqual(ws.request.headers["Authorization"], "Bearer secret")
            await ws.send(json.dumps({"post_type": "message"}))
            request = json.loads(await ws.recv())
            await ws.send(json.dumps({"echo": request["echo"], "status": "ok", "retcode": 0, "data": 42}))
            await ws.wait_closed()

        async def on_event(event, generation):
            result.set_result(await client.call("get_status"))

        # The event task can run before start() returns.
        async def delayed_event(event, generation):
            await ready.wait()
            await on_event(event, generation)

        ready = asyncio.Event()
        client, _ = await self.start(handler, delayed_event)
        ready.set()
        self.assertEqual(await asyncio.wait_for(result, 2), 42)

    async def test_out_of_order_echo_matching(self):
        async def handler(ws):
            requests = [json.loads(await ws.recv()), json.loads(await ws.recv())]
            for request in reversed(requests):
                await ws.send(json.dumps({"echo": request["echo"], "status": "ok", "retcode": 0, "data": request["params"]["value"]}))
            await ws.wait_closed()

        client, _ = await self.start(handler)
        self.assertEqual(await asyncio.gather(client.call("test", value=1), client.call("test", value=2)), [1, 2])

    async def test_failure_and_empty_data(self):
        async def handler(ws):
            for status, retcode in [("failed", 100), ("ok", 1), ("async", 1), ("ok", False), ("ok", "0"), ("ok", 0)]:
                request = json.loads(await ws.recv())
                await ws.send(json.dumps({"echo": request["echo"], "status": status, "retcode": retcode, "data": None}))
            await ws.wait_closed()

        client, _ = await self.start(handler)
        for _ in range(5):
            with self.assertRaises(OneBotAPIError):
                await client.call("test")
        self.assertIsNone(await client.call("test"))

    async def test_timeout(self):
        async def handler(ws):
            await ws.recv()
            await ws.wait_closed()

        client, _ = await self.start(handler, timeout=0.03)
        with self.assertRaisesRegex(OneBotError, "timed out"):
            await client.call("test")

    async def test_disconnect_fails_pending(self):
        async def handler(ws):
            await ws.recv()
            await ws.close()

        client, _ = await self.start(handler)
        with self.assertRaises(OneBotDisconnected):
            await asyncio.wait_for(client.call("test"), 0.5)

    async def test_close_fails_pending(self):
        received = asyncio.Event()

        async def handler(ws):
            await ws.recv()
            received.set()
            await ws.wait_closed()

        client, runner = await self.start(handler)
        call = asyncio.create_task(client.call("test"))
        await asyncio.wait_for(received.wait(), 1)
        await client.close()
        with self.assertRaises(OneBotDisconnected):
            await call
        self.assertTrue(runner.done())
        self.assertFalse(client.connected)

    async def test_reconnect_normal_and_abnormal_close(self):
        sockets = asyncio.Queue()

        async def handler(ws):
            await sockets.put(ws)
            await ws.wait_closed()

        client, _ = await self.start(handler)
        for code in [1000, 1011]:
            ws = await asyncio.wait_for(sockets.get(), 1)
            generation = client.generation
            await ws.close(code=code)
            async with asyncio.timeout(3):
                while client.generation == generation:
                    await asyncio.sleep(0.01)
            self.assertTrue(client.connected)

    async def test_disconnect_and_close_keep_callback_alive(self):
        started = asyncio.Event()
        release = asyncio.Event()
        finished = asyncio.Event()
        disconnect = asyncio.Event()
        disconnected = asyncio.Event()

        async def on_event(event, generation):
            started.set()
            await release.wait()
            finished.set()

        async def handler(ws):
            await ws.send(json.dumps({"post_type": "message"}))
            await started.wait()
            await disconnect.wait()
            await ws.close()
            disconnected.set()

        client, _ = await self.start(handler, on_event)
        await asyncio.wait_for(started.wait(), 1)
        disconnect.set()
        await asyncio.wait_for(disconnected.wait(), 1)
        await client.close()
        release.set()
        await asyncio.wait_for(finished.wait(), 1)

    async def test_invalid_messages_and_late_echo_are_ignored(self):
        result = asyncio.get_running_loop().create_future()

        async def handler(ws):
            for raw in ["bad json", "[]", '{"echo": {}}', '{"echo": "unknown"}']:
                await ws.send(raw)
            await ws.send('{"post_type": "message"}')
            await ws.wait_closed()

        async def on_event(event, generation):
            result.set_result(event)

        await self.start(handler, on_event)
        self.assertEqual(await asyncio.wait_for(result, 1), {"post_type": "message"})

    async def test_callback_keeps_event_connection_generation(self):
        sockets = asyncio.Queue()
        release = asyncio.Event()
        result = asyncio.get_running_loop().create_future()

        async def handler(ws):
            await sockets.put(ws)
            await ws.send('{"post_type": "message"}')
            await ws.wait_closed()

        async def on_event(event, generation):
            if generation == 1:
                await release.wait()
                result.set_result(generation)

        client, _ = await self.start(handler, on_event)
        first = await sockets.get()
        await first.close()
        await asyncio.wait_for(sockets.get(), 3)
        async with asyncio.timeout(1):
            while client.generation < 2:
                await asyncio.sleep(0.005)
        release.set()
        self.assertEqual(await asyncio.wait_for(result, 1), 1)


if __name__ == "__main__":
    unittest.main()
