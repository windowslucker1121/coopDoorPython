const CACHE_NAME = 'static-cache-v1';
const DYNAMIC_CACHE_NAME = 'dynamic-cache-v1';

// List of URLs to cache during the install event
// index.html is cached by default
const STATIC_ASSETS = [
  '/',
  '/manifest.json',
  '/static/offline.html', 
  '/static/waiting.png',
  '/static/icons/icon_144x144.png',
  '/static/icons/icon_192x192.png',
  '/static/icons/icon_512x512.png',
];


self.addEventListener('install', (event) => {
  console.log('[Service Worker] Installing...');
  event.waitUntil(
    caches.open(CACHE_NAME).then((cache) => {
      console.log('[Service Worker] Caching static assets');
      return cache.addAll(STATIC_ASSETS).catch((error) => {
        console.error(`[Service Worker] Error caching static assets:`, error);
      });
    })
  );
  self.skipWaiting();
});

self.addEventListener('activate', (event) => {
  console.log('[Service Worker] Activating new service worker...');
  event.waitUntil(
    caches.keys().then((cacheNames) => {
      return Promise.all(
        cacheNames.map((cache) => {
          if (cache !== CACHE_NAME && cache !== DYNAMIC_CACHE_NAME) {
            console.log('[Service Worker] Removing old cache:', cache);
            return caches.delete(cache);
          }
        })
      );
    })
  );
  return self.clients.claim();
});

self.addEventListener('message', (event) => {
  if (event.data === 'skipWaiting') {
    console.log('[Service Worker] Skipping waiting...');
    self.skipWaiting();
  }
});

// Fetch event
self.addEventListener('fetch', (event) => {
  if (event.request.method === 'GET') {
    if (event.request.mode === 'navigate') {
      event.respondWith(
        fetch(event.request)
          .then((response) => {
            const clone = response.clone();
            caches.open(CACHE_NAME).then((cache) => {
              cache.put(event.request, clone);
            });
            return response;
          })
          .catch(() => {
            return caches.match(event.request).then((cachedResponse) => {
              return cachedResponse || caches.match('/static/offline.html');
            });
          })
      );
    } else {
      event.respondWith(
        caches.match(event.request).then((cachedResponse) => {
          return (
            cachedResponse ||
            fetch(event.request)
              .then((response) => {
                if (response && response.ok) {
                  const clone = response.clone();
                  caches.open(DYNAMIC_CACHE_NAME).then((cache) => {
                    cache.put(event.request, clone);
                  });
                }
                return response;
              })
              .catch((error) => {
                console.error('[Service Worker] Fetch failed:', error);
                return null;
              })
          );
        })
      );
    }
  } else {
    // Allow non-GET requests to bypass the cache
    event.respondWith(fetch(event.request));
  }
});
