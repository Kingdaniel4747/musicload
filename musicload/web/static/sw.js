/* Minimal service worker required for Android PWA installation and share targets. */
self.addEventListener("install", () => self.skipWaiting());

self.addEventListener("activate", (event) => {
  event.waitUntil(self.clients.claim());
});

// Keep Musicload network-first: music results and downloads must always stay current.
self.addEventListener("fetch", (event) => {
  const url = new URL(event.request.url);
  if (
    event.request.destination === "audio" ||
    event.request.headers.has("range") ||
    url.pathname.startsWith("/api/preview/")
  ) {
    // Streaming media must go directly to the network. Wrapping it in a
    // service-worker response makes some browsers repeatedly request an empty
    // stream when the upstream connection fails.
    return;
  }
  event.respondWith(fetch(event.request));
});
