/* Minimal service worker required for Android PWA installation and share targets. */
self.addEventListener("install", () => self.skipWaiting());

self.addEventListener("activate", (event) => {
  event.waitUntil(self.clients.claim());
});

// Keep Musicload network-first: music results and downloads must always stay current.
self.addEventListener("fetch", (event) => {
  event.respondWith(fetch(event.request));
});
