// The V1.1 shell had no update button and precached its injected registerSW.js.
// Activate its successor for the next navigation without refreshing open tabs.
self.addEventListener('install', (event) => {
  if (!self.registration.active) return
  const scope = self.registration.scope
  event.waitUntil((async () => {
    const marker = new URL('registerSW.js', scope)
    for (const key of await caches.keys()) {
      if (!key.startsWith('workbox-precache-') || !key.endsWith(scope)) continue
      const previous = await caches.open(key)
      if (await previous.match(marker, { ignoreSearch: true })) {
        await self.skipWaiting()
        return
      }
    }
  })())
})
