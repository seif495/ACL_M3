/* Pitch Me service worker: makes the site installable and lets pages you have opened load offline.
 *
 * - Page navigations: network first, so a deploy shows up at once; offline, the last app shell
 *   (index.html) is served and the app renders from the cached build.
 * - /assets/* (Vite's content-hashed build files): cache first; they never change under one name.
 *   The app preloads every route's chunk when idle, so after one visit all pages work offline.
 * - Club crests and icons: served from the cache and refreshed in the background.
 * - Everything else, the API included, goes to the network untouched: data is never cached here,
 *   so nothing stale is shown as current.
 *
 * Caddy serves this file with "Cache-Control: no-cache" (the @html matcher), so updates are found
 * on the next visit. Bump VERSION to drop every cache.
 */
const VERSION = 'v1';
// Hashed files never change under one name, so a stored copy fits any request for it; without
// ignoreVary a Vary header (Origin, Accept-Encoding) made module scripts miss the cache offline.
const MATCH = { ignoreVary: true };
const SHELL_CACHE = `pm-shell-${VERSION}`;
const ASSET_CACHE = `pm-assets-${VERSION}`;
const STATIC_CACHE = `pm-static-${VERSION}`;
const KEEP = [SHELL_CACHE, ASSET_CACHE, STATIC_CACHE];
const MAX_ASSETS = 200; // old builds' chunks are dropped beyond this

const OFFLINE_HTML = `<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>Offline · Pitch Me</title><style>body{margin:0;min-height:100vh;display:grid;place-items:center;background:#1E0123;color:#fff;font:16px/1.5 system-ui,sans-serif;text-align:center;padding:16px}h1{font-size:1.5rem;margin:0 0 .5rem}p{opacity:.75;margin:0 0 1rem}button{font:inherit;padding:.5rem 1rem;border-radius:8px;border:0;background:#00FF85;color:#1E0123;font-weight:600;cursor:pointer}</style></head><body><main><h1>You're offline</h1><p>Pitch Me needs a connection the first time you open it.</p><button onclick="location.reload()">Try again</button></main></body></html>`;

self.addEventListener('install', (event) => {
  event.waitUntil(
    (async () => {
      const cache = await caches.open(SHELL_CACHE);
      try {
        const shell = await fetch('/', { cache: 'no-cache' });
        if (shell.ok) {
          await cache.put('/', shell.clone());
          // The entry scripts and styles the shell names, so the first offline load works
          const html = await shell.text();
          const assets = [...html.matchAll(/\/assets\/[^"'\s)]+/g)].map((m) => m[0]);
          const assetCache = await caches.open(ASSET_CACHE);
          await Promise.all([...new Set(assets)].map((url) => assetCache.add(url).catch(() => undefined)));
        }
      } catch {
        // Offline while installing: the shell is cached on the next navigation instead
      }
      await self.skipWaiting();
    })(),
  );
});

self.addEventListener('activate', (event) => {
  event.waitUntil(
    (async () => {
      const names = await caches.keys();
      await Promise.all(names.filter((n) => n.startsWith('pm-') && !KEEP.includes(n)).map((n) => caches.delete(n)));
      await self.clients.claim();
    })(),
  );
});

async function trim(cacheName, max) {
  const cache = await caches.open(cacheName);
  const keys = await cache.keys();
  await Promise.all(keys.slice(0, Math.max(0, keys.length - max)).map((k) => cache.delete(k)));
}

async function navigate(request) {
  try {
    const response = await fetch(request);
    // Keep the newest shell for offline use (every page route is the same index.html)
    if (response.ok && (response.headers.get('content-type') || '').includes('text/html')) {
      const cache = await caches.open(SHELL_CACHE);
      await cache.put('/', response.clone());
    }
    return response;
  } catch {
    const cached = await caches.match('/', { ...MATCH, cacheName: SHELL_CACHE });
    return cached || new Response(OFFLINE_HTML, { headers: { 'Content-Type': 'text/html; charset=utf-8' } });
  }
}

async function cacheFirst(request) {
  const cached = await caches.match(request, { ...MATCH, cacheName: ASSET_CACHE });
  if (cached) return cached;
  const response = await fetch(request);
  if (response.ok) {
    const cache = await caches.open(ASSET_CACHE);
    await cache.put(request, response.clone());
    trim(ASSET_CACHE, MAX_ASSETS);
  }
  return response;
}

async function staleWhileRevalidate(event) {
  const cache = await caches.open(STATIC_CACHE);
  const cached = await cache.match(event.request, MATCH);
  const refresh = fetch(event.request)
    .then((response) => {
      if (response.ok) cache.put(event.request, response.clone());
      return response;
    })
    .catch(() => undefined);
  if (cached) {
    event.waitUntil(refresh);
    return cached;
  }
  return (await refresh) || Response.error();
}

self.addEventListener('fetch', (event) => {
  const { request } = event;
  if (request.method !== 'GET') return;
  const url = new URL(request.url);
  if (url.origin !== self.location.origin) return;
  if (request.mode === 'navigate') {
    // API documents (e.g. /docs) and feeds stay with the network
    if (url.pathname.startsWith('/api/') || url.pathname.startsWith('/docs')) return;
    event.respondWith(navigate(request));
    return;
  }
  if (url.pathname.startsWith('/assets/')) {
    event.respondWith(cacheFirst(request));
    return;
  }
  if (/^\/(logos\/|favicon\.|apple-touch-icon\.png|icon-[^/]+\.png$|site\.webmanifest$)/.test(url.pathname)) {
    event.respondWith(staleWhileRevalidate(event));
  }
});
