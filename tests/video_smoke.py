"""Optional real DXGI/NVENC -> Edge test, including slow-decoder backpressure.

Run on an interactive Windows desktop with an NVENC GPU. No input is injected.
Measurements cover this computer and loopback, not the phone or Wi-Fi.
"""
import asyncio
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from aiohttp import web
from playwright.async_api import async_playwright
from backend.server import create_app
from backend.input import Controller


async def main():
    runner = web.AppRunner(create_app(Controller(True)))
    await runner.setup()
    site = web.TCPSite(runner, '127.0.0.1', 0)
    await site.start()
    url = f'http://127.0.0.1:{site._server.sockets[0].getsockname()[1]}'
    try:
        async with async_playwright() as p:
            browser = await p.chromium.launch(channel='msedge', headless=True)
            try:
                page = await browser.new_page()
                errors = []
                page.on('pageerror', lambda error: errors.append(str(error)))
                await page.add_init_script('''{
                    window.metrics = {frames: 0, painted: 0, bytes: 0, capture: []};
                    const draw = CanvasRenderingContext2D.prototype.drawImage;
                    CanvasRenderingContext2D.prototype.drawImage = function(...args) {
                        if (this.canvas.id === 'screen-canvas') metrics.painted++;
                        return draw.apply(this, args);
                    };
                    const WS = WebSocket;
                    window.WebSocket = class extends WS {
                        constructor(...args) {
                            super(...args);
                            this.addEventListener('message', e => {
                                if (e.data instanceof ArrayBuffer) metrics.bytes += e.data.byteLength;
                                else {
                                    const data = JSON.parse(e.data);
                                    if (data.type === 'cursor') metrics.capture.push(data.capture_ms);
                                }
                            });
                        }
                        send(value) {
                            if (JSON.parse(value).type === 'frame_ack') metrics.frames++;
                            return super.send(value);
                        }
                    };
                }''')
                await page.goto(url)
                await page.locator('#view-toggle').click()
                await page.locator('#virtual-cursor').wait_for()
                options = await page.locator('#screen-options button').count()
                await page.locator('#display-settings').click()
                results = []
                for monitor in range(options):
                    await page.locator('#screen-options button').nth(monitor).click()
                    for quality in ('original', 'balanced'):
                        await page.locator('#stream-quality').select_option(quality)
                        await page.wait_for_timeout(1200)
                        await page.evaluate('() => { metrics = {frames: 0, painted: 0, bytes: 0, capture: []}; }')
                        started = asyncio.get_running_loop().time()
                        await page.wait_for_timeout(3000)
                        elapsed = asyncio.get_running_loop().time() - started
                        result = await page.evaluate('() => ({...metrics, status: document.querySelector("#screen-status").textContent})')
                        assert 'NVENC' in result['status'], result
                        assert result['painted'] / elapsed > 20, result
                        result = {'screen': monitor, 'quality': quality,
                                  'fps': round(result['painted'] / elapsed, 1),
                                  'mbps': round(result['bytes'] * 8 / elapsed / 1e6, 2),
                                  'status': result['status']}
                        results.append(result)
                        print(json.dumps(result, ensure_ascii=True), flush=True)
                # Delayed ACKs emulate a LAN with an 80 ms return path. Fixed
                # two-frame credit would cap this at 25 FPS even with fast decode.
                await page.locator('#stream-quality').select_option('balanced')
                await page.evaluate('''() => {
                    window.originalSend = WebSocket.prototype.send;
                    WebSocket.prototype.send = function(value) {
                        if (JSON.parse(value).type === 'frame_ack') {
                            setTimeout(() => { if (this.readyState === 1) originalSend.call(this, value); }, 80);
                        } else originalSend.call(this, value);
                    };
                }''')
                await page.wait_for_timeout(1800)
                await page.evaluate('() => { metrics = {frames: 0, painted: 0, bytes: 0, capture: []}; }')
                await page.wait_for_timeout(3000)
                delayed = await page.evaluate('metrics')
                assert delayed['painted'] / 3 > 35, delayed
                print(f"80 ms ACK delay: {delayed['painted'] / 3:.1f} rendered FPS", flush=True)
                await page.evaluate('() => { WebSocket.prototype.send = originalSend; }')
                # A slow paint loop must keep only the latest decoded frame.
                await page.evaluate('''() => {
                    window.originalRAF = requestAnimationFrame;
                    window.requestAnimationFrame = callback => originalRAF(() => setTimeout(callback, 150));
                    metrics = {frames: 0, painted: 0, bytes: 0, capture: []};
                }''')
                await page.wait_for_timeout(1500)
                slow = await page.evaluate('metrics')
                assert len(slow['capture']) - slow['frames'] <= 8, slow
                assert slow['painted'] <= 11, slow
                await page.evaluate('() => { window.requestAnimationFrame = originalRAF; }')
                await page.locator('#stream-quality').select_option('smooth')
                await page.wait_for_timeout(1500)
                assert '1280×720' in await page.locator('#screen-status').inner_text()
                assert not errors, errors
                print('Video passed: real H.264 decode, both monitors, quality changes, adaptive ACK window, latest-frame rendering and recovery')
            finally:
                await browser.close()
    finally:
        await runner.cleanup()


if __name__ == '__main__':
    asyncio.run(main())
