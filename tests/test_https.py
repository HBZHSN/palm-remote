import ctypes
import ssl
import unittest
from pathlib import Path
from aiohttp import web, ClientSession
from backend.server import create_app, ROOT
from backend.input import Controller, Input


@unittest.skipUnless((ROOT / 'data/server.pem').exists(), 'Run ./remote.ps1 -Action Certificate to create local certificates')
class HTTPSTests(unittest.IsolatedAsyncioTestCase):
    async def test_trusted_https(self):
        context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        context.load_cert_chain(ROOT / 'data/server.pem', ROOT / 'data/server-key.pem')
        runner = web.AppRunner(create_app(Controller(True)))
        await runner.setup()
        try:
            site = web.TCPSite(runner, '127.0.0.1', 0, ssl_context=context)
            await site.start()
            port = site._server.sockets[0].getsockname()[1]
            trust = ssl.create_default_context(cafile=str(ROOT / 'data/palm-ca.crt'))
            async with ClientSession() as client:
                async with client.get(f'https://127.0.0.1:{port}/', ssl=trust) as response:
                    self.assertEqual(response.status, 200)
                    self.assertIn('id="touchpad"', await response.text())
        finally:
            await runner.cleanup()

    @unittest.skipUnless(__import__('sys').platform == 'win32', 'Windows ABI')
    def test_windows_input_abi(self):
        self.assertEqual(ctypes.sizeof(Input), 40 if ctypes.sizeof(ctypes.c_void_p) == 8 else 28)
        Controller(False)  # Initialize only; never inject real input in tests.
