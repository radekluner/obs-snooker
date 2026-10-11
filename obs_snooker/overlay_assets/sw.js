// Minimal service worker: Chrome only offers "Install app" for pages that have one.
// It deliberately does not cache anything - the scoreboard must always show live data.
self.addEventListener("install", () => self.skipWaiting());
self.addEventListener("activate", (event) => event.waitUntil(self.clients.claim()));
self.addEventListener("fetch", () => {});
