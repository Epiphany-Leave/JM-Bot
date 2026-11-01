import asyncio
import base64
import os
import socket
import unittest
from unittest.mock import AsyncMock, patch

import aiohttp
from aiohttp import web

import test_managed_proxy as managed
from jm_bot.proxy import LIMIT, ProxyError


class SubscriptionTests(unittest.IsolatedAsyncioTestCase):
    asyncTearDown = managed.ManagedProxyTests.asyncTearDown

    async def serve(self, request):
        if request.path == '/providers/proxies/subscription' and self.ready_nodes_failure:
            if self.ready_nodes_failure == 'timeout':
                await asyncio.sleep(0.1)
            else:
                return web.Response(status=503)
        if request.path.startswith('/sub/'):
            self.requests.append((request.path, request.headers.get('User-Agent'),
                                  request.headers.get('Proxy-Authorization')))
            self.assertEqual(request.headers.get('User-Agent'), 'mihomo/1.19.33')
            self.assertNotIn('Authorization', request.headers)
            kind = request.match_info['tail'].split('/')[-1]
            if kind == 'redirect':
                raise web.HTTPFound('/sub/yaml')
            if kind == 'slow':
                await asyncio.sleep(0.1)
            if kind.isdigit():
                return web.Response(status=int(kind), text='private-body-secret')
            if kind == 'fallback':
                if not request.headers.get('Proxy-Authorization') or self.fail_fetch_proxy:
                    return web.Response(status=503, text='private-body-secret')
            if kind == 'html':
                return web.Response(text='<!DOCTYPE HTML><html>private-body-secret</html>')
            if kind == 'encoding':
                return web.Response(body=b'\xff\xfeprivate-body-secret')
            if kind == 'large':
                return web.Response(body=b'x' * (LIMIT + 1))
            if kind == 'format':
                return web.Response(text='proxies: [broken')
            uri = 'ss://YWVzLTEyOC1nY206c2VjcmV0@127.0.0.1:8080#alpha'
            text = {'uri': uri, 'base64': base64.b64encode(uri.encode()).decode()}.get(kind, self.source)
            # MIME alone must not reject valid provider content.
            return web.Response(text=text, content_type='text/html')
        return await managed.ManagedProxyTests.serve(self, request)

    async def asyncSetUp(self):
        await managed.ManagedProxyTests.asyncSetUp(self)
        self.requests = []
        self.fail_fetch_proxy = False
        self.ready_nodes_failure = None

    async def test_fixed_ua_environment_independence_and_native_formats(self):
        with patch.dict(os.environ, {'HTTP_PROXY': 'http://127.0.0.1:1',
                                     'HTTPS_PROXY': 'http://127.0.0.1:1', 'NO_PROXY': ''}):
            for kind in ('yaml', 'uri', 'base64', 'redirect'):
                result = await self.proxy._source(self.base + '/sub/' + kind, None)
                self.assertTrue(result)
        self.assertEqual(len(self.requests), 5)
        self.assertTrue(all(auth is None for _, _, auth in self.requests))

    async def test_http_content_errors_and_no_bootstrap_are_safe(self):
        cases = [('401', 'http', 401), ('403', 'http', 403), ('404', 'http', 404),
                 ('429', 'http', 429), ('500', 'http', 500), ('503', 'http', 503),
                 ('html', 'html', 200), ('encoding', 'encoding', None),
                 ('large', 'too_large', None), ('format', 'format', None)]
        for path, code, status in cases:
            with self.subTest(path=path), self.assertRaises(ProxyError) as caught:
                await self.proxy.import_subscription(url=self.base + '/sub/' + path + '?token=url-secret')
            error = caught.exception
            self.assertEqual(error.code, 'subscription_' + code)
            self.assertEqual(error.http_status, status)
            for secret in ('url-secret', 'private-body-secret', self.base, 'controller-secret', 'proxy-secret'):
                self.assertNotIn(secret, str(error))
                self.assertNotIn(secret, error.next_step)
            self.assertFalse(self.store.state['proxy'])
        self.assertEqual(len(self.requests), len(cases))

    async def test_existing_verified_proxy_fallback_and_refresh(self):
        await self.proxy.import_subscription(content=self.source)
        before = self.probe_calls
        result = await self.proxy.import_subscription(url=self.base + '/sub/fallback?token=url-secret')
        self.assertTrue(result['healthy'])
        self.assertEqual(self.probe_calls, before + 2)  # old route and candidate both probe 204
        self.assertEqual([bool(auth) for _, _, auth in self.requests], [False, True])
        self.assertEqual(self.requests[-1][2], 'Basic am06cHJveHktc2VjcmV0')
        self.requests.clear()
        await self.proxy.refresh_subscription()
        self.assertEqual([bool(auth) for _, _, auth in self.requests], [False, True])

    async def test_both_routes_fail_without_changing_saved_state_or_provider(self):
        await self.proxy.import_subscription(content=self.source)
        previous = dict(self.store.state['proxy'])
        config = (self.root / 'config.yaml').read_bytes()
        providers = set(self.root.glob('provider-*'))
        self.fail_fetch_proxy = True
        with self.assertRaises(ProxyError) as caught:
            await self.proxy.import_subscription(url=self.base + '/sub/fallback?token=url-secret')
        self.assertEqual(caught.exception.code, 'subscription_http')
        self.assertEqual(caught.exception.stage, 'subscription_fetch_proxy')
        self.assertEqual(caught.exception.http_status, 503)
        self.assertIn('直连与已有代理', str(caught.exception))
        self.assertEqual(len(self.requests), 2)
        self.assertEqual(self.store.state['proxy'], previous)
        self.assertEqual((self.root / 'config.yaml').read_bytes(), config)
        self.assertEqual(set(self.root.glob('provider-*')), providers)
        self.assertEqual(self.selected, previous['node'])

    async def test_unhealthy_old_proxy_never_fetches_or_installs_new_nodes(self):
        await self.proxy.import_subscription(content=self.source)
        previous = dict(self.store.state['proxy'])
        self.fail_probe = True
        with self.assertRaises(ProxyError) as caught:
            await self.proxy.import_subscription(url=self.base + '/sub/fallback')
        self.assertEqual(caught.exception.stage, 'subscription_proxy_ready')
        self.assertEqual(len(self.requests), 1)
        self.assertEqual(self.store.state['proxy'], previous)

    async def test_failed_old_proxy_readiness_restores_manual_selection(self):
        self.source += '- {name: beta, type: http, server: 127.0.0.1, port: 8081}\n'
        await self.proxy.import_subscription(content=self.source)
        await self.proxy.select_node('beta')
        previous = dict(self.store.state['proxy'])
        config = (self.root / 'config.yaml').read_bytes()
        providers = set(self.root.glob('provider-*'))
        timeout = asyncio.timeout
        for failure in ('http', 'timeout'):
            self.ready_nodes_failure = failure
            with self.subTest(failure=failure):
                with patch('jm_bot.proxy.asyncio.timeout', side_effect=lambda seconds: timeout(0.01 if seconds == 20 else seconds)):
                    with self.assertRaises(ProxyError) as caught:
                        await self.proxy.import_subscription(url=self.base + '/sub/fallback')
                self.assertEqual(caught.exception.code, 'subscription_proxy_unavailable')
                self.assertEqual(caught.exception.stage, 'subscription_proxy_ready')
                self.assertEqual(self.selected, 'beta')
                self.assertEqual(self.store.state['proxy'], previous)
                self.assertEqual((self.root / 'config.yaml').read_bytes(), config)
                self.assertEqual(set(self.root.glob('provider-*')), providers)
        self.assertEqual(len(self.requests), 2)  # only one direct fetch per attempt

    async def test_real_connection_failure_tls_and_shortened_request_timeout(self):
        sock = socket.socket()
        sock.bind(('127.0.0.1', 0))
        unused_port = sock.getsockname()[1]
        sock.close()
        for url, code in ((f'http://127.0.0.1:{unused_port}/?token=url-secret', 'connection'),
                          (self.base.replace('http:', 'https:') + '/sub/yaml', 'tls')):
            with self.subTest(code=code), self.assertRaises(ProxyError) as caught:
                await self.proxy._source(url, None)
            self.assertEqual(caught.exception.code, 'subscription_' + code)
            self.assertNotIn('url-secret', str(caught.exception))
        timeout = aiohttp.ClientTimeout
        with patch('jm_bot.proxy.aiohttp.ClientTimeout', side_effect=lambda **kw: timeout(total=0.01)):
            with self.assertRaises(ProxyError) as caught:
                await self.proxy._source(self.base + '/sub/slow', None)
        self.assertEqual(caught.exception.code, 'subscription_timeout')

    async def test_dns_errors_are_classified_and_budget_bounds_verification(self):
        error = socket.gaierror(-2, 'private-dns-secret')
        with patch('aiohttp.connector.TCPConnector._resolve_host', new=AsyncMock(side_effect=error)):
            with self.assertRaises(ProxyError) as caught:
                await self.proxy._source('http://dns.invalid/?token=url-secret', None)
        self.assertEqual(caught.exception.code, 'subscription_dns')
        self.assertNotIn('private-dns-secret', str(caught.exception))
        await self.proxy.import_subscription(content=self.source)
        timeout = asyncio.timeout
        async def slow_ready():
            await asyncio.sleep(0.1)
        for limit, expected in ((20, 'subscription_proxy_unavailable'), (60, 'subscription_timeout')):
            with patch.object(self.proxy, 'ensure_ready', side_effect=slow_ready):
                with patch('jm_bot.proxy.asyncio.timeout', side_effect=lambda seconds: timeout(0.01 if seconds == limit else seconds)):
                    with self.assertRaises(ProxyError) as caught:
                        await self.proxy._source(self.base + '/sub/fallback', None)
            self.assertEqual(caught.exception.code, expected)


if __name__ == '__main__':
    unittest.main()
