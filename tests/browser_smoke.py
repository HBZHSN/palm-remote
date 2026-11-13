"""Optional real-browser check: pip install playwright; uses installed Edge, no real input."""
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from aiohttp import web
from playwright.async_api import async_playwright
from backend.server import create_app
from backend.input import Controller


async def main():
    control = Controller(True)
    runner = web.AppRunner(create_app(control))
    await runner.setup()
    site = web.TCPSite(runner, '127.0.0.1', 0)
    await site.start()
    port = site._server.sockets[0].getsockname()[1]
    try:
        async with async_playwright() as p:
            browser = await p.chromium.launch(channel='msedge', headless=True)
            try:
                page = await browser.new_page(viewport={'width': 390, 'height': 844}, has_touch=True)
                errors = []
                page.on('pageerror', lambda error: errors.append(str(error)))
                await page.goto(f'http://127.0.0.1:{port}')
                await page.locator('#view-toggle').click()
                await page.locator('#virtual-cursor').wait_for()
                options = await page.locator('#screen-options button').count()
                assert options > 0
                canvas = page.locator('#screen-canvas')
                bounds = await canvas.bounding_box()
                assert bounds['width'] <= 390 and bounds['height'] <= 844
                assert abs(bounds['width']/bounds['height'] - await canvas.evaluate('(c) => c.width/c.height')) < .01
                toolbar = await page.locator('.view-bar').bounding_box()
                pad_bounds = await page.locator('#touchpad').bounding_box()
                assert toolbar['y'] >= pad_bounds['y']+pad_bounds['height']
                virtual_cursor = page.locator('#virtual-cursor')
                assert await virtual_cursor.is_visible()
                assert (await virtual_cursor.bounding_box())['width'] == 18
                # Hold outgoing input to prove the pointer responds locally before the network.
                await page.evaluate('''() => {
                    window.originalSend = WebSocket.prototype.send;
                    window.queuedInput = [];
                    WebSocket.prototype.send = function(value) {
                        if (JSON.parse(value).type === 'position') { queuedInput.push([this, value]); return; }
                        return originalSend.call(this, value);
                    };
                }''')
                await page.mouse.move(bounds['x']+bounds['width']*.5, bounds['y']+bounds['height']*.5)
                await page.mouse.down()
                before = await virtual_cursor.get_attribute('style')
                await page.mouse.move(bounds['x']+bounds['width']*.6, bounds['y']+bounds['height']*.5)
                assert await virtual_cursor.get_attribute('style') != before
                await page.evaluate('''() => {
                    WebSocket.prototype.send = originalSend;
                    queuedInput.forEach(([ws, value]) => originalSend.call(ws, value));
                }''')
                await page.mouse.up()
                await page.locator('#input-mode').click()
                assert await page.locator('#input-mode').get_attribute('aria-pressed') == 'true'
                control.events.clear()
                await page.touchscreen.tap(bounds['x']+bounds['width']*.5, bounds['y']+bounds['height']*.5)
                await page.wait_for_timeout(100)
                assert [e.mi.dwFlags for e in control.events][-3:] == [0xC001, 2, 4]
                if options > 1:
                    await page.locator('#screen-select').click()
                    assert await page.locator('#screen-number').inner_text() == '2'
                    await page.wait_for_timeout(500)
                    assert not control.held
                await page.set_viewport_size({'width': 844, 'height': 390})
                await page.wait_for_timeout(100)
                pad_bounds = await page.locator('#touchpad').bounding_box()
                dock_bounds = await page.locator('.dock').bounding_box()
                assert dock_bounds['x'] + dock_bounds['width'] <= pad_bounds['x'] + 1
                assert await page.locator('.app').get_attribute('data-dock') == 'left'
                assert pad_bounds['height'] >= 380
                assert pad_bounds['width'] >= 600
                for panel in ['keyboard', 'media', 'navigation', 'shortcuts', 'settings']:
                    await page.locator(f'[data-panel="{panel}"]').click()
                    assert await page.locator(f'#panel-{panel}').is_visible()
                    assert await page.evaluate('document.querySelector(".toolbar").scrollWidth <= document.querySelector(".toolbar").clientWidth')
                await page.locator('[data-panel="touch"]').click()
                await page.locator('#input-mode').click()
                assert await page.locator('#input-mode').get_attribute('aria-pressed') == 'false'
                await page.set_viewport_size({'width': 1024, 'height': 768})
                await page.wait_for_timeout(100)
                bounds = await canvas.bounding_box()
                assert abs(bounds['width']/bounds['height'] - await canvas.evaluate('(c) => c.width/c.height')) < .01
                await page.locator('#drag-lock').click()
                await page.wait_for_timeout(50)
                assert control.held == {'left'}
                await page.locator('#view-toggle').click()
                await page.wait_for_timeout(50)
                assert not control.held
                assert await canvas.is_hidden()
                assert await virtual_cursor.is_hidden()
                assert not errors, errors
                await page.locator('#view-toggle').click()
                await page.locator('#display-settings').click()
                await page.locator('#stream-quality').select_option('smooth')
                await page.locator('#stream-fps').select_option('15')
                for _ in range(100):
                    if await canvas.evaluate('(c) => c.width <= 1280'):
                        break
                    await page.wait_for_timeout(50)
                assert await canvas.evaluate('(c) => c.width <= 1280')
                await page.evaluate('''() => {
                    window.frameRequests = 0;
                    const send = WebSocket.prototype.send;
                    WebSocket.prototype.send = function(value) {
                        if (JSON.parse(value).type === 'frame_ack') frameRequests++;
                        return send.call(this, value);
                    };
                }''')
                await page.wait_for_timeout(1100)
                requests = await page.evaluate('frameRequests')
                assert 2 <= requests <= 18, requests
                await page.locator('#stream-quality').select_option('original')
                await page.locator('#stream-fps').select_option('30')
                await page.reload()
                assert await page.locator('#stream-quality').input_value() == 'original'
                assert await page.locator('#stream-fps').input_value() == '30'
                assert not errors, errors
                print(f'Stream settings passed: 720p switch, 15 FPS cap ({requests} displayed frames / 1.1s), saved quality and FPS')
                print(f'Browser passed: adaptive bottom/left toolbar, instant 18px cursor, screen buttons and overlay panels, {options} screens, direct tap, drag release')
            finally:
                await browser.close()
    finally:
        await runner.cleanup()


if __name__ == '__main__':
    asyncio.run(main())
