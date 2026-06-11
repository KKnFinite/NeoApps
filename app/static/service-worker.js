const CACHE_PREFIX = "neogateway-static-";
const CACHE_NAME = "neogateway-static-v20260611-2";
const STATIC_ASSETS = [
  "/static/css/base.css?v=20260611-2",
  "/static/images/neogateway_logo3_small.png",
  "/static/images/neogateway_logo3_medium.png",
  "/static/images/neogateway_logo3_large.png",
];

self.addEventListener("install", (event) => {
  event.waitUntil(
    caches
      .open(CACHE_NAME)
      .then((cache) => cache.addAll(STATIC_ASSETS))
      .catch(() => undefined)
  );
  self.skipWaiting();
});

self.addEventListener("activate", (event) => {
  event.waitUntil(
    caches
      .keys()
      .then((cacheNames) =>
        Promise.all(
          cacheNames
            .filter((cacheName) => cacheName.startsWith(CACHE_PREFIX) && cacheName !== CACHE_NAME)
            .map((cacheName) => caches.delete(cacheName))
        )
      )
      .then(() => self.clients.claim())
  );
});

self.addEventListener("fetch", (event) => {
  const { request } = event;

  if (request.method !== "GET") {
    return;
  }

  const requestUrl = new URL(request.url);
  if (requestUrl.origin !== self.location.origin) {
    return;
  }

  if (request.mode === "navigate") {
    event.respondWith(fetch(request, { cache: "no-store" }));
    return;
  }

  if (!requestUrl.pathname.startsWith("/static/")) {
    return;
  }

  event.respondWith(
    fetch(request)
      .then((response) => {
        if (response.ok && response.type === "basic") {
          const responseForCache = response.clone();
          caches.open(CACHE_NAME).then((cache) => cache.put(request, responseForCache));
        }
        return response;
      })
      .catch(() => caches.match(request))
  );
});
