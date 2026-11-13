import asyncio
import unittest
from aiohttp import WSMsgType
from aiohttp.test_utils import TestClient, TestServer
from backend.server import create_app, STATE
from backend.input import Controller


class ServerTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.controller = Controller(dry_run=True)
        self.app = create_app(self.controller)
        self.client = TestClient(TestServer(self.app))
        await self.client.start_server()
        self.origin = str(self.client.make_url('/')).rstrip('/')
        self.headers = {'Origin': self.origin}

    async def asyncTearDown(self):
        await self.client.close()

    async def test_direct_access_and_origin(self):
        response = await self.client.get('/api/status')
        self.assertEqual(response.status, 200)
        self.assertNotIn('Set-Cookie', response.headers)
        for headers in ({}, {'Origin': 'https://unrelated.example'}):
            self.assertEqual((await self.client.get('/ws', headers=headers)).status, 403)
        self.assertEqual((await self.client.post('/api/pair', json={}, headers=self.headers)).status, 404)

    async def test_websocket_control_cleanup_and_validation(self):
        self.assertEqual((await self.client.get('/api/status')).status, 200)
        ws = await self.client.ws_connect('/ws', headers=self.headers)
        self.assertEqual((await ws.receive_json())['type'], 'ready')
        await ws.send_json({'type': 'button', 'button': 'left', 'down': True})
        await ws.send_json({'type': 'ping', 'at': 1})
        self.assertEqual((await ws.receive_json())['type'], 'pong')
        self.assertEqual(self.controller.held, {'left'})
        response = await self.client.get('/ws', headers=self.headers)
        self.assertEqual(response.status, 409)
        await ws.send_json({'type': 'move', 'dx': 'bad', 'dy': 0})
        self.assertEqual((await ws.receive_json())['type'], 'error')
        self.assertFalse(self.controller.held)
        await ws.send_json({'type': 'text', 'text': '你好 🌿'})
        self.assertEqual((await ws.receive_json())['type'], 'text_sent')
        await ws.send_json({'type': 'button', 'button': 'left', 'down': True})
        await ws.send_json({'type': 'ping'})
        await ws.receive_json()
        await ws.close()
        await asyncio.sleep(.05)
        self.assertFalse(self.controller.held)
        self.assertIsNone(self.app[STATE]['active'])

    async def test_assets_no_secrets(self):
        for path in ('/', '/app.js', '/sw.js', '/manifest.webmanifest', '/icon-192.png', '/icon-512.png'):
            self.assertEqual((await self.client.get(path)).status, 200, path)
        self.assertEqual((await self.client.get('/config.json')).status, 404)

    async def test_watchdog_releases_drag_without_heartbeat(self):
        ws = await self.client.ws_connect('/ws', headers=self.headers)
        await ws.receive_json()
        await ws.send_json({'type': 'button', 'button': 'left', 'down': True})
        message = await ws.receive(timeout=8)
        self.assertEqual(message.type, WSMsgType.CLOSE)
        self.assertFalse(self.controller.held)


class InputTests(unittest.TestCase):
    def test_unicode_and_shortcuts(self):
        control = Controller(True)
        control.dispatch({'type': 'text', 'text': '中😀'})
        self.assertEqual(len(control.events), 6)
        control.dispatch({'type': 'shortcut', 'key': 'copy'})
        self.assertEqual(len(control.events), 10)
        self.assertEqual(control.events[-1].ki.dwFlags, 2)

    def test_invalid_commands(self):
        for data in ([], {'type': 'text', 'text': ''}, {'type': 'key', 'key': 'bad'},
                     {'type': 'move', 'dx': float('nan'), 'dy': 0},
                     {'type': 'button', 'button': 'left', 'down': 'yes'}):
            with self.assertRaises(ValueError):
                Controller(True).dispatch(data)


if __name__ == '__main__':
    unittest.main()
