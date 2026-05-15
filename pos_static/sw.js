// Lé Sang POS — Service Worker
// Versión del cache — incrementar para forzar actualización
var CACHE_VERSION = 'lesang-pos-v1';
var STATIC_CACHE  = CACHE_VERSION + '-static';
var PRODUCTS_CACHE = CACHE_VERSION + '-products';

// Archivos que se cachean siempre (app shell)
var STATIC_FILES = [
  '/pos',
  '/pos/',
  '/pos/static/index.html',
  '/pos/config',
];

// ── Install: cachear app shell ────────────────────────────────────────────────
self.addEventListener('install', function(event) {
  event.waitUntil(
    caches.open(STATIC_CACHE).then(function(cache) {
      return cache.addAll(STATIC_FILES).catch(function(err) {
        console.log('[SW] Some static files failed to cache:', err);
      });
    }).then(function() {
      return self.skipWaiting();
    })
  );
});

// ── Activate: limpiar caches viejos ──────────────────────────────────────────
self.addEventListener('activate', function(event) {
  event.waitUntil(
    caches.keys().then(function(keys) {
      return Promise.all(
        keys.filter(function(key) {
          return key.startsWith('lesang-pos-') && key !== STATIC_CACHE && key !== PRODUCTS_CACHE;
        }).map(function(key) {
          console.log('[SW] Deleting old cache:', key);
          return caches.delete(key);
        })
      );
    }).then(function() {
      return self.clients.claim();
    })
  );
});

// ── Fetch: estrategia por tipo de request ────────────────────────────────────
self.addEventListener('fetch', function(event) {
  var url = new URL(event.request.url);
  var path = url.pathname;

  // 1. Productos de Shopify — Cache First con revalidación en background
  if (path === '/pos/products') {
    event.respondWith(cacheFirstWithUpdate(event.request, PRODUCTS_CACHE));
    return;
  }

  // 2. Ventas, historial, config — Network First (datos críticos siempre frescos)
  if (path.indexOf('/pos/venta') === 0 ||
      path.indexOf('/pos/historial') === 0 ||
      path.indexOf('/pos/sheet') === 0 ||
      path.indexOf('/pos/foto') === 0) {
    event.respondWith(networkFirst(event.request));
    return;
  }

  // 3. App shell (HTML, assets estáticos) — Cache First
  if (path === '/pos' || path === '/pos/' || path.indexOf('/pos/static/') === 0) {
    event.respondWith(cacheFirst(event.request, STATIC_CACHE));
    return;
  }

  // 4. Resto — Network normal
  event.respondWith(fetch(event.request));
});

// ── Estrategias de cache ──────────────────────────────────────────────────────

// Cache First: sirve desde cache, actualiza en background
function cacheFirstWithUpdate(request, cacheName) {
  return caches.open(cacheName).then(function(cache) {
    return cache.match(request).then(function(cached) {
      // Actualizar en background siempre
      var fetchPromise = fetch(request).then(function(response) {
        if (response && response.status === 200) {
          cache.put(request, response.clone());
        }
        return response;
      }).catch(function() { return null; });

      // Servir cache inmediatamente si existe, sino esperar red
      return cached || fetchPromise;
    });
  });
}

// Cache First simple: sirve cache, si no hay va a red
function cacheFirst(request, cacheName) {
  return caches.open(cacheName).then(function(cache) {
    return cache.match(request).then(function(cached) {
      if (cached) return cached;
      return fetch(request).then(function(response) {
        if (response && response.status === 200) {
          cache.put(request, response.clone());
        }
        return response;
      });
    });
  });
}

// Network First: intenta red, si falla usa cache
function networkFirst(request) {
  return fetch(request).then(function(response) {
    return response;
  }).catch(function() {
    return caches.match(request);
  });
}

// ── Mensaje desde la app para limpiar cache ───────────────────────────────────
self.addEventListener('message', function(event) {
  if (event.data && event.data.type === 'CLEAR_PRODUCTS_CACHE') {
    caches.open(PRODUCTS_CACHE).then(function(cache) {
      cache.delete('/pos/products');
      console.log('[SW] Products cache cleared');
    });
  }
  if (event.data && event.data.type === 'SKIP_WAITING') {
    self.skipWaiting();
  }
});