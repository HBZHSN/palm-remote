import unittest
import asyncio
from unittest.mock import patch
from backend.screen import Desktop
from backend.input import Controller
from aiohttp.test_utils import TestClient, TestServer
from backend.server import create_app
from aiohttp import WSMsgType


class FakeDesktop(Desktop):
    def monitors(self):
        return [dict(id='left', left=-1920, top=0, width=1920, height=1080),
                dict(id='right', left=0, top=0, width=2560, height=1440)]

    def cursor(self, identifier):
        return {'x': .5, 'y': .5, 'visible': True}


class Coordinates(unittest.TestCase):
    def test_negative_origin_and_virtual_edges(self):
        desktop, control = FakeDesktop(), Controller(True)
        desktop.position(control, 'left', 0, 0)
        self.assertEqual((control.events[-1].mi.dx, control.events[-1].mi.dy), (0, 0))
        desktop.position(control, 'right', 1, 1)
        event = control.events[-1].mi
        self.assertEqual((event.dx, event.dy, event.dwFlags), (65535, 65535, 0xC001))

    def test_invalid_coordinates_and_disconnected_screen(self):
        for x in (True, float('nan'), float('inf'), -0.1, 1.1, '0'):
            with self.assertRaises(ValueError):
                FakeDesktop().position(Controller(True), 'left', x, 0)
        with self.assertRaises(ValueError):
            FakeDesktop().position(Controller(True), 'missing', 0, 0)


class FakeVideo:
    def __init__(self, desktop):
        self.desktop = desktop
        self.calls = []
        self.closed = False

    def capture(self, identifier, quality, generation, fps=60):
        self.desktop.monitor(identifier)
        self.calls.append((identifier, quality, generation))
        return b'h264-packet', {'codec': 'avc1.42c02a', 'key': True, 'width': 1920,
                                'height': 1080, 'encoder': 'NVENC', 'timestamp': len(self.calls) * 16667}

    def close(self):
        self.closed = True


class ScreenProtocol(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.desktop, self.control = FakeDesktop(), Controller(True)
        self.video = FakeVideo(self.desktop)
        self.patch = patch('backend.server.VideoCapture', return_value=self.video)
        self.patch.start()
        self.client = TestClient(TestServer(create_app(self.control, self.desktop)))
        await self.client.start_server()
        self.ws = await self.client.ws_connect('/ws', headers={'Origin': str(self.client.make_url('/')).rstrip('/')})
        await self.ws.receive_json()

    async def asyncTearDown(self):
        await self.ws.close()
        await self.client.close()
        self.patch.stop()
        self.assertTrue(self.video.closed)

    async def start_stream(self, **changes):
        await self.ws.send_json({'type': 'stream', 'screen': 'left', 'quality': 'original',
                                 'codec': 'h264', 'fps': 60, 'generation': 1, **changes})

    async def receive_frame(self):
        meta = await self.ws.receive_json(timeout=2)
        self.assertEqual(meta['type'], 'cursor')
        frame = await self.ws.receive(timeout=2)
        self.assertEqual((frame.type, frame.data), (WSMsgType.BINARY, b'h264-packet'))
        return meta

    async def test_continuous_stream_backpressure_switch_stop_and_input(self):
        await self.start_stream()
        first, second, third = await self.receive_frame(), await self.receive_frame(), await self.receive_frame()
        await asyncio.sleep(.08)
        self.assertEqual(len(self.video.calls), 3, 'Startup window is bounded')
        await self.ws.send_json({'type': 'frame_ack', 'frame_id': -1})
        await self.ws.send_json({'type': 'position', 'screen': 'right', 'x': 1, 'y': 1, 'seq': 2})
        await self.ws.send_json({'type': 'ping', 'at': 123})
        self.assertEqual(await self.ws.receive_json(), {'type': 'pong', 'at': 123})
        self.assertEqual(self.control.events[-1].mi.dx, 65535)
        self.assertEqual(len(self.video.calls), 3)
        await self.start_stream(quality='smooth', generation=2)
        await self.ws.send_json({'type': 'frame_ack', 'frame_id': first['frame_id']})
        await self.ws.send_json({'type': 'frame_ack', 'frame_id': second['frame_id']})
        changed = await self.receive_frame()
        self.assertEqual(changed['generation'], 2)
        self.assertEqual(self.video.calls[-1], ('left', 'smooth', 2))
        await self.ws.send_json({'type': 'stream_stop'})
        for meta in (third, changed):
            await self.ws.send_json({'type': 'frame_ack', 'frame_id': meta['frame_id']})
        await self.ws.send_json({'type': 'ping'})
        self.assertEqual((await self.ws.receive_json())['type'], 'pong')
        await asyncio.sleep(.08)
        self.assertEqual(len(self.video.calls), 4)

    async def test_validation_and_capture_failure(self):
        for changes in ({'codec': 'jpeg'}, {'fps': True}, {'fps': 120}, {'quality': []}, {'generation': []}):
            await self.start_stream(**changes)
            self.assertEqual((await self.ws.receive_json())['type'], 'error')
        await self.ws.send_json({'type': 'screens'})
        self.assertEqual(len((await self.ws.receive_json())['screens']), 2)
        with self.assertLogs(level='ERROR'):
            await self.start_stream(screen='missing')
            self.assertEqual((await self.ws.receive_json())['type'], 'screen_error')
        await self.ws.send_json({'type': 'ping'})
        self.assertEqual((await self.ws.receive_json())['type'], 'pong')
        await self.start_stream(generation=2)
        await self.receive_frame()

    async def test_metrics_reject_invalid_values_and_ignore_old_generation(self):
        await self.start_stream()
        for _ in range(3):
            await self.receive_frame()
        report = {'type': 'stream_stats', 'generation': 1, 'fps': 58, 'decoded_fps': 59,
                  'mbps': 5, 'decode_ms': 6, 'rtt_ms': 10, 'width': 1920, 'height': 1080}
        await self.ws.send_json(report)
        await self.ws.send_json({'type': 'ping'})
        await self.ws.receive_json()
        result = await (await self.client.get('/api/status')).json()
        self.assertEqual(result['stream']['fps'], 58)
        self.assertEqual(result['stream']['target_fps'], 60)
        await self.ws.send_json({**report, 'fps': 'wrong'})
        self.assertEqual((await self.ws.receive_json())['type'], 'error')
        await self.ws.send_json({**report, 'generation': 0, 'fps': 1})
        await self.ws.send_json({'type': 'ping'})
        await self.ws.receive_json()
        result = await (await self.client.get('/api/status')).json()
        self.assertEqual(result['stream']['fps'], 58)
