"""Optional Edge test: upgrade an open legacy PWA without manually clearing caches."""
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from aiohttp import web
from playwright.async_api import async_playwright
from backend.server import create_app
from backend.input import Controller


async def main():
    legacy = True

    @web.middleware
    async def previous_version(request, handler):
        if legacy and request.path == '/':
            return web.Response(text='<div id="legacy">Old touchpad</div><script src="/legacy.js"></script>', content_type='text/html')
        if request.path == '/legacy.js':
            return web.Response(text="navigator.serviceWorker.register('/sw.js');", content_type='application/javascript')
        if legacy and request.path == '/sw.js':
            return web.Response(text="""
const CACHE = 'palm-shell-v2';
self.addEventListener('install', e => e.waitUntil(caches.open(CACHE).then(c => c.add('/')).then(() => self.skipWaiting())));
self.addEventListener('activate', e => e.waitUntil(self.clients.claim()));
""", content_type='application/javascript', headers={'Cache-Control': 'no-cache'})
        return await handler(request)

    app = create_app(Controller(True))
    app.middlewares.insert(0, previous_version)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, '127.0.0.1', 0)
    await site.start()
    port = site._server.sockets[0].getsockname()[1]
    try:
        async with async_playwright() as p:
            browser = await p.chromium.launch(channel='msedge', headless=True)
            try:
                context = await browser.new_context()
                page = await context.new_page()
                await page.goto(f'http://127.0.0.1:{port}/')
                await page.evaluate('async () => { await navigator.serviceWorker.ready; }')
                await page.locator('#legacy').wait_for()
                legacy = False
                # Simulates the browser's normal worker-update check on reopening a PWA.
                await page.evaluate('async () => { const r = await navigator.serviceWorker.getRegistration(); await r.update(); }')
                await page.locator('#view-toggle').wait_for()
                assert await page.locator('link[href="/style.css?v=10"]').count() == 1
                keys = await page.evaluate('() => caches.keys()')
                assert 'palm-shell-v10' in keys and 'palm-shell-v2' not in keys, keys
                await context.set_offline(True)
                await page.goto(f'http://127.0.0.1:{port}/?refresh=5')
                await page.locator('#view-toggle').wait_for()
                print('PWA passed: open legacy page auto-upgrades; old cache removed; offline query URL works')
            finally:
                await browser.close()
    finally:
        await runner.cleanup()


if __name__ == '__main__':
    asyncio.run(main())
