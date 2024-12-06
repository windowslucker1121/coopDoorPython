const CACHE_NAME = 'static-cache-v1';
const DYNAMIC_CACHE_NAME = 'dynamic-cache-v1';

// List of URLs to cache during the install event
const STATIC_ASSETS = [
  '/',
  '/manifest.json',
  '/static/offline.html', 
  '/static/waiting.png',
  '/static/icons/icon_144x144.png',
  '/static/icons/icon_192x192.png',
  '/static/icons/icon_512x512.png',
];


// Install event
self.addEventListener('install', (event) => {
  console.log('[Service Worker] Installing...');
  event.waitUntil(
    caches.open(CACHE_NAME).then((cache) => {
      console.log('[Service Worker] Caching static assets');
      return Promise.all(
        STATIC_ASSETS.map((asset) => {
          return cache.add(asset).catch((error) => {
            console.error(`[Service Worker] Failed to cache ${asset}:`, error);
          });
        })
      );
    })
  );
});

// Activate event
self.addEventListener('activate', (event) => {
  console.log('[Service Worker] Activating...');
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

// Fetch event
self.addEventListener('fetch', (event) => {
  // console.log('[Service Worker] Fetching:', event.request.url);

  event.respondWith(
    caches.match(event.request).then((cachedResponse) => {
      if (cachedResponse) {
        console.log('[Service Worker] Found in cache:', event.request.url);
        return cachedResponse;
      }

      return fetch(event.request)
        .then((response) => {
          if (event.request.url === '/') {
            caches.open(CACHE_NAME).then((cache) => {
              cache.put(event.request, response.clone());
              console.log('[Service Worker] Caching index.html dynamically');
            });
          }
          return response;
        })
        .catch((error) => {
          console.error('[Service Worker] Fetch failed:', error);
          // TODO
          // Serve the offline fallback page but currently it isnt displayed
          if (event.request.mode === 'navigate') {
            return caches.match('/static/offline.html');
          }
        });
    })
  );
});
