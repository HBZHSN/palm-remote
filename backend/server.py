import argparse
import asyncio
from concurrent.futures import ThreadPoolExecutor
import json
import logging
import math
import re
from logging.handlers import RotatingFileHandler
from pathlib import Path
import ssl
import time

from aiohttp import web, WSMsgType
from .input import Controller
from .screen import Desktop
from .video import VideoCapture
from .flow import VideoFlow

ROOT = Path(__file__).resolve().parents[1]
STATE = web.AppKey('state', dict)


@web.middleware
async def security(request, handler):
    if request.path.startswith('/api/') or request.path == '/ws':
        origin = request.headers.get('Origin')
        required = request.method == 'POST' or request.path == '/ws'
        if (required or origin) and origin != f'{request.scheme}://{request.host}':
            raise web.HTTPForbidden(text='Origin rejected')
        if request.headers.get('Sec-Fetch-Site') == 'cross-site':
            raise web.HTTPForbidden(text='Cross-site request rejected')
    response = await handler(request)
    response.headers['X-Content-Type-Options'] = 'nosniff'
    response.headers['Referrer-Policy'] = 'no-referrer'
    # Some mobile browsers do not match WebSocket URLs against CSP 'self'.
    # Restrict the explicit source to this host and port; reject CSP delimiters.
    if not re.fullmatch(r'[A-Za-z0-9.\-:\[\]]+', request.host):
        raise web.HTTPBadRequest(text='Invalid host')
    websocket_origin = f"{'wss' if request.secure else 'ws'}://{request.host}"
    response.headers['Content-Security-Policy'] = f"default-src 'self'; connect-src 'self' {websocket_origin}; style-src 'self'; script-src 'self'; img-src 'self'; frame-ancestors 'none'; base-uri 'self'; form-action 'self'"
    if request.path.startswith('/api/'):
        response.headers['Cache-Control'] = 'no-store'
    return response


async def websocket(request):
    state = request.app[STATE]
    # One active controller avoids another phone releasing a drag in progress.
    if state['active'] is not None:
        raise web.HTTPConflict(text='另一台设备正在控制')
    # H.264 is already compressed; deflate adds CPU time and buffering per frame.
    ws = web.WebSocketResponse(heartbeat=10, max_msg_size=16384, compress=False)
    state['active'] = ws
    controller = state['controller']
    last_ping = time.monotonic()

    async def watchdog():
        while not ws.closed:
            await asyncio.sleep(1)
            if time.monotonic() - last_ping > 5:
                controller.release()
                await ws.close(code=1001, message=b'Connection timed out')

    task = None
    video = VideoCapture(state['desktop'])
    stream_settings = None
    stream_task = None
    stream_wake = asyncio.Event()
    flow = VideoFlow()
    frame_id = 0

    async def frame(identifier, seq, generation, quality, fps, number):
        try:
            started = time.perf_counter()
            payload, metadata = await asyncio.get_running_loop().run_in_executor(
                state['capture_pool'], video.capture, identifier, quality, generation, fps)
            capture_ms = (time.perf_counter() - started) * 1000
            if not ws.closed:
                cursor = state['desktop'].cursor(identifier)
                await ws.send_json({'type': 'cursor', 'screen': identifier, 'seq': seq,
                                    'generation': generation, 'video': metadata,
                                    'capture_ms': round(capture_ms, 1), 'frame_id': number, **cursor})
                state['stream_metrics']['capture_ms'] = round(capture_ms, 1)
                state['stream_metrics']['stages'] = metadata.get('stages')
                state['stream_metrics']['source_width'] = metadata.get('source_width')
                state['stream_metrics']['source_height'] = metadata.get('source_height')
                flow.sent(number, len(payload))
                await ws.send_bytes(payload)
            return True
        except Exception:
            logging.exception('Screen capture failed')
            if not ws.closed:
                await ws.send_json({'type': 'screen_error', 'message': '硬件推流失败，请检查显卡驱动或重新选择显示器'})
            return False

    async def stream():
        nonlocal frame_id, stream_settings
        while not ws.closed:
            stream_wake.clear()
            if stream_settings is None or not flow.can_send():
                await stream_wake.wait()
                continue
            settings = stream_settings
            started = time.monotonic()
            frame_id += 1
            ok = await frame(settings['screen'], settings.get('seq'), settings['generation'],
                             settings['quality'], settings['fps'], frame_id)
            if not ok:
                flow.pending.pop(frame_id, None)
                if stream_settings is settings:
                    stream_settings = None
            # Bound decoded-frame acknowledgments separately from browser paint.
            delay = 1 / settings['fps'] - (time.monotonic() - started)
            if delay > 0:
                # Windows asyncio timers can round a short wait up by ~15 ms.
                # Python 3.11+ time.sleep uses a high-resolution waitable timer;
                # keep that wait off the event loop so input remains responsive.
                await asyncio.get_running_loop().run_in_executor(state['capture_pool'], time.sleep, delay)
            else:
                await asyncio.sleep(0)

    try:
        await ws.prepare(request)
        await ws.send_json({'type': 'ready'})
        task = asyncio.create_task(watchdog())
        window, count = time.monotonic(), 0
        async for msg in ws:
            if msg.type != WSMsgType.TEXT:
                continue
            now = time.monotonic()
            if now - window >= 1:
                window, count = now, 0
            count += 1
            if count > 240:
                await ws.close(code=1008, message=b'Rate limit')
                break
            try:
                data = json.loads(msg.data)
                if isinstance(data, dict) and data.get('type') == 'ping':
                    last_ping = now
                    await ws.send_json({'type': 'pong', **({'at': data['at']} if 'at' in data else {})})
                elif isinstance(data, dict) and data.get('type') == 'stream':
                    if data.get('quality') not in ('original', 'high', 'balanced', 'smooth'):
                        raise ValueError('Invalid screen quality')
                    if data.get('codec') != 'h264':
                        raise ValueError('Invalid screen codec')
                    if type(data.get('fps')) is not int or data['fps'] not in (15, 30, 60):
                        raise ValueError('Invalid frame rate')
                    if not isinstance(data.get('screen'), str) or type(data.get('generation')) is not int:
                        raise ValueError('Invalid stream selection')
                    stream_settings = data
                    state['stream_metrics']['client'] = request.headers.get('User-Agent', '')[:256]
                    stream_wake.set()
                    if stream_task is None:
                        stream_task = asyncio.create_task(stream())
                elif isinstance(data, dict) and data.get('type') == 'frame_ack':
                    number = data.get('frame_id')
                    if type(number) is int:
                        flow.acknowledge(number, stream_settings['fps'] if stream_settings else 60)
                        stream_wake.set()
                elif isinstance(data, dict) and data.get('type') == 'stream_stop':
                    stream_settings = None
                    state['stream_metrics'] = {}
                elif isinstance(data, dict) and data.get('type') == 'stream_stats':
                    if stream_settings and data.get('generation') == stream_settings['generation']:
                        metrics = {}
                        for field in ('fps', 'decoded_fps', 'mbps', 'decode_ms', 'rtt_ms', 'width', 'height'):
                            value = data.get(field)
                            if type(value) not in (int, float) or not math.isfinite(value) or not 0 <= value <= 100000:
                                raise ValueError('Invalid stream metrics')
                            metrics[field] = round(value, 2)
                        state['stream_metrics'].update(metrics, window=flow.window, in_flight=len(flow.pending),
                                                       ack_ms=round(flow.last_ack_ms, 1), quality=stream_settings['quality'],
                                                       target_fps=stream_settings['fps'], updated_at=time.time())
                elif isinstance(data, dict) and data.get('type') == 'screens':
                    try:
                        monitors = await asyncio.to_thread(state['desktop'].monitors)
                        await ws.send_json({'type': 'screens', 'screens': monitors})
                    except Exception:
                        await ws.send_json({'type': 'screen_error', 'message': '无法读取桌面显示器'})
                elif isinstance(data, dict) and data.get('type') == 'position':
                    state['desktop'].position(controller, data.get('screen'), data.get('x'), data.get('y'))
                    if stream_settings is not None:
                        stream_settings = {**stream_settings, 'seq': data.get('seq')}
                else:
                    controller.dispatch(data)
                    if data.get('type') == 'text':
                        await ws.send_json({'type': 'text_sent'})
            except (ValueError, TypeError, KeyError, OSError) as exc:
                controller.release()
                await ws.send_json({'type': 'error', 'message': str(exc)})
    finally:
        # Release before awaiting cancellation: the HTTP handler itself may be
        # cancelled as soon as a phone drops its TCP connection.
        controller.release()
        state['active'] = None
        state['stream_metrics'] = {}
        if stream_task:
            stream_task.cancel()
        if task:
            task.cancel()
        # Queue resource cleanup before any await: aiohttp may cancel this
        # handler during disconnect. It runs after the last capture on the same
        # executor, and app cleanup also waits for it before shutting the pool.
        closing = asyncio.get_running_loop().run_in_executor(state['capture_pool'], video.close)
        state['video_cleanup'].add(closing)
        closing.add_done_callback(state['video_cleanup'].discard)
        await asyncio.shield(closing)
        await asyncio.gather(*(t for t in (stream_task, task) if t), return_exceptions=True)
    return ws


async def status(request):
    state = request.app[STATE]
    return web.json_response({'busy': state['active'] is not None, 'stream': state['stream_metrics']})


async def asset(request):
    name = request.match_info.get('name') or 'index.html'
    allowed = {'index.html', 'app.js', 'decoder.js', 'gestures.js', 'style.css', 'sw.js', 'manifest.webmanifest', 'icon-192.png', 'icon-512.png'}
    if name not in allowed:
        raise web.HTTPNotFound()
    response = web.FileResponse(ROOT / 'web' / name)
    response.headers['Cache-Control'] = 'no-cache'
    return response


def create_app(controller=None, desktop=None):
    app = web.Application(middlewares=[security], client_max_size=16384)
    app[STATE] = {'controller': controller or Controller(), 'active': None, 'desktop': desktop or Desktop(),
                  'capture_pool': ThreadPoolExecutor(max_workers=1, thread_name_prefix='screen'), 'video_cleanup': set(),
                  'stream_metrics': {}}
    app.router.add_get('/api/status', status)
    app.router.add_get('/ws', websocket)
    app.router.add_get('/', asset)
    app.router.add_get('/{name}', asset)

    async def shutdown(app):
        if app[STATE]['active']:
            await app[STATE]['active'].close(code=1001)
        app[STATE]['controller'].release()
    app.on_shutdown.append(shutdown)
    async def cleanup(app):
        state = app[STATE]
        await asyncio.gather(*state['video_cleanup'], return_exceptions=True)
        state['capture_pool'].shutdown(wait=True)
    app.on_cleanup.append(cleanup)
    return app


def main():
    parser = argparse.ArgumentParser(description='Palm Remote · 局域网触摸板')
    parser.add_argument('--host', default='0.0.0.0')
    parser.add_argument('--port', type=int, default=8765)
    parser.add_argument('--dry-run', action='store_true')
    parser.add_argument('--http', action='store_true', help='Development only; LAN PWA installation requires HTTPS')
    args = parser.parse_args()
    (ROOT / 'data').mkdir(exist_ok=True)
    handler = RotatingFileHandler(ROOT / 'data/server.log', maxBytes=1000000, backupCount=2, encoding='utf-8')
    logging.basicConfig(level=logging.INFO, handlers=[handler])
    context = None
    if not args.http:
        cert, key = ROOT / 'data/server.pem', ROOT / 'data/server-key.pem'
        if not cert.exists() or not key.exists():
            parser.error('请先运行 ./remote.ps1 -Action Certificate 生成 HTTPS 证书，或使用 --http 开发模式。')
        context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        context.minimum_version = ssl.TLSVersion.TLSv1_2
        context.load_cert_chain(cert, key)
    print(f'Listening on port {args.port}')
    web.run_app(create_app(Controller(args.dry_run)), host=args.host,
                port=args.port, ssl_context=context, access_log=None)


if __name__ == '__main__':
    main()
