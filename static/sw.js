const CACHE_NAME = "araf-v3";
const NETWORK_FIRST_URLS = ["/", "/index.html", "/app.js", "/style.css", "/engines.js"];

self.addEventListener("install", (event) => {
  event.waitUntil(self.skipWaiting());
});

self.addEventListener("activate", (event) => {
  event.waitUntil(
    clients.claim()
  );
  event.waitUntil(
    caches.keys().then((keys) => {
      return Promise.all(
        keys
          .filter((key) => key !== CACHE_NAME)
          .map((key) => caches.delete(key))
      );
    })
  );
});

self.addEventListener("fetch", (event) => {
  if (event.request.method !== "GET" && event.request.method !== "HEAD") return;

  const url = new URL(event.request.url);

  if (url.pathname.startsWith("/_")) {
    event.respondWith(fetch(event.request));
    return;
  }

  if (url.pathname === "/manifest.json" || url.pathname.endsWith(".css") || url.pathname.endsWith(".js")) {
    event.respondWith(
      fetch(event.request).then((response) => {
        if (response.ok) {
          const copied = response.clone();
          caches.open(CACHE_NAME).then((cache) => {
            cache.put(event.request, copied);
          });
        }
        return response;
      }).catch(() => caches.match(event.request))
    );
    return;
  }

  event.respondWith(
    caches.match(event.request).then((cached) => {
      if (cached) return cached;
      return fetch(event.request);
    })
  );
});