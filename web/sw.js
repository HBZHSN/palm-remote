const CACHE = 'palm-shell-v10';
const ASSETS = ['/', '/style.css?v=10', '/app.js?v=10', '/decoder.js?v=10', '/gestures.js?v=10', '/manifest.webmanifest', '/icon-192.png', '/icon-512.png'];
const PATHS = ASSETS.map(path => new URL(path, self.location.origin).pathname);
self.addEventListener('install', event => { event.waitUntil(caches.open(CACHE).then(cache => cache.addAll(ASSETS)).then(() => self.skipWaiting())); });
self.addEventListener('activate', event => {
  event.waitUntil((async () => {
    const old = (await caches.keys()).filter(key => key.startsWith('palm-shell-') && key !== CACHE);
    await Promise.all(old.map(key => caches.delete(key)));
    await self.clients.claim();
    // Legacy pages have no update listener: replace their already-loaded UI once.
    if (old.length) {
      const clients = await self.clients.matchAll({type: 'window'});
      // Navigation fetches wait for activation; awaiting them here would deadlock.
      for (const client of clients) client.navigate(client.url).catch(() => {});
    }
  })());
});
self.addEventListener('fetch', event => {
  const url = new URL(event.request.url);
  if (url.origin !== self.location.origin || event.request.method !== 'GET' || url.pathname.startsWith('/api/') || url.pathname === '/ws') return;
  if (!PATHS.includes(url.pathname)) return;
  event.respondWith(fetch(event.request, {cache: 'no-cache'}).then(response => {
    if (response.ok) { const copy = response.clone(); event.waitUntil(caches.open(CACHE).then(cache => cache.put(event.request, copy))); }
    return response;
  }).catch(() => caches.match(event.request).then(hit => hit || caches.match(ASSETS[PATHS.indexOf(url.pathname)]))));
});
