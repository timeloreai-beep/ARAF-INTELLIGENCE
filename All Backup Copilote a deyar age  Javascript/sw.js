/* ==========================================================================
   ARAF INTELLIGENCE — HARDENED PWA SERVICE WORKER (sw.js)
   ========================================================================== */

const CACHE_NAME = 'araf-intelligence-app-static-v10';
const STATIC_ASSETS = [
    './',
    './index.html',
    './style.css',
    './engines.js',
    './app.js',
    './manifest.json'
];

self.addEventListener('install', (event) => {
    event.waitUntil(
        caches.open(CACHE_NAME).then((cache) => cache.addAll(STATIC_ASSETS))
    );
    self.skipWaiting();
});

self.addEventListener('activate', (event) => {
    event.waitUntil(
        caches.keys()
            .then((keys) => Promise.all(keys.filter((k) => k !== CACHE_NAME).map((k) => caches.delete(k))))
            .then(() => clients.claim())
    );
});

self.addEventListener('fetch', (event) => {
    if (event.request.method !== 'GET') return;
    const reqUrl = new URL(event.request.url);

    if (reqUrl.origin !== self.location.origin) return;
    if (event.request.headers.get('Authorization') || reqUrl.searchParams.has('target')) return;

    event.respondWith(
        fetch(event.request)
            .then((networkRes) => {
                if (networkRes && networkRes.status === 200) {
                    const copy = networkRes.clone();
                    caches.open(CACHE_NAME).then((cache) => cache.put(event.request, copy));
                }
                return networkRes;
            })
            .catch(async () => {
                const cached = await caches.match(event.request);
                if (cached) return cached;
                if (event.request.mode === 'navigate') {
                    return caches.match('./index.html');
                }
                return new Response('Offline', { status: 503, statusText: 'Offline' });
            })
    );
});