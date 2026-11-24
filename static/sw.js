const CACHE_NAME = "ARAF-InteLLIGENCE-v1";
const PRECACHE_URLS = [
  "/",
  "/manifest.json",
  "/style.css",
  "/engines.js",
  "/app.js"
];

self.addEventListener("install", (event) => {
  event.waitUntil(
    caches.open(CACHE_NAME).then((cache) => cache.addAll(PRECACHE_URLS))
  );
});

self.addEventListener("fetch", (event) => {
  event.respondWith(
    caches.match(event.request).then((response) => {
      if (response) {
        return response;
      }
      return fetch(event.request);
    })
  );
});