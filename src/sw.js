// Dinky Coop service worker: offline shell + web push.
const CACHE = 'coop-shell-v2';

const SHELL = [
  '/manifest.json',
  '/static/offline.html',
  '/static/app/app.css',
  '/static/app/app.js',
  '/static/js/socket.io.js',
  '/static/js/chart.umd.js',
  '/static/fonts/figtree.woff2',
  '/static/fonts/bricolage-grotesque.woff2',
  '/static/favicon.svg',
  '/static/icons/icon_144x144.png',
  '/static/icons/icon_192x192.png',
];

self.addEventListener('install', (event) => {
  event.waitUntil(
    caches.open(CACHE)
      .then((cache) => cache.addAll(SHELL))
      .catch((err) => console.warn('[sw] precache failed', err))
  );
  self.skipWaiting();
});

self.addEventListener('activate', (event) => {
  event.waitUntil(
    caches.keys()
      .then((names) => Promise.all(names.filter((n) => n !== CACHE).map((n) => caches.delete(n))))
      .then(() => self.clients.claim())
  );
});

self.addEventListener('message', (event) => {
  if (event.data === 'skipWaiting') self.skipWaiting();
});

self.addEventListener('fetch', (event) => {
  const req = event.request;
  if (req.method !== 'GET') return;
  const url = new URL(req.url);
  if (url.origin !== self.location.origin) return;

  // Live data never comes from the cache.
  if (url.pathname.startsWith('/api/') || url.pathname.startsWith('/socket.io') ||
      url.pathname.startsWith('/video_feed') || url.pathname.startsWith('/camera')) {
    return;
  }

  // Pages: network first, cached copy (or the offline page) when unreachable.
  if (req.mode === 'navigate') {
    event.respondWith(
      fetch(req)
        .then((res) => {
          if (res.ok) {
            const copy = res.clone();
            caches.open(CACHE).then((c) => c.put('/', copy));
          }
          return res;
        })
        .catch(() => caches.match('/').then((hit) => hit || caches.match('/static/offline.html')))
    );
    return;
  }

  // Static assets: stale-while-revalidate.
  if (url.pathname.startsWith('/static/') || url.pathname === '/manifest.json') {
    event.respondWith(
      caches.open(CACHE).then((cache) =>
        cache.match(req).then((hit) => {
          const network = fetch(req)
            .then((res) => {
              if (res.ok) cache.put(req, res.clone());
              return res;
            })
            .catch(() => hit);
          return hit || network;
        })
      )
    );
  }
});

self.addEventListener('push', (event) => {
  let data = {};
  try { data = event.data ? event.data.json() : {}; } catch (e) { data = { body: event.data && event.data.text() }; }
  event.waitUntil(
    self.registration.showNotification(data.title || 'Dinky Coop', {
      body: data.body || '',
      icon: '/static/icons/icon_192x192.png',
      badge: '/static/icons/icon_144x144.png',
      data: data.url || '/',
    })
  );
});

self.addEventListener('notificationclick', (event) => {
  event.notification.close();
  const target = event.notification.data || '/';
  event.waitUntil(
    clients.matchAll({ type: 'window', includeUncontrolled: true }).then((list) => {
      for (const c of list) {
        if ('focus' in c) { c.navigate(target); return c.focus(); }
      }
      return clients.openWindow(target);
    })
  );
});
