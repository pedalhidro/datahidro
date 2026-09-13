// datahidro — service worker
//
// Receita do ecossistema (ver levabici/sw.js, amora/web/sw.js):
//   STATIC_CACHE  — casca do app (HTML/CSS/JS/ícones/fontes): stale-while-
//                   revalidate — serve do cache na hora e atualiza por trás.
//                   Exceção: data/*.json é network-first (dado velho não é
//                   só casca velha: candidatura pode ter saído da lista).
//   RUNTIME_CACHE — photos/: cache-first (só mudam quando o ingest roda de
//                   novo, e todo deploy sobe a VERSION, que limpa o cache).
// /api/ (respostas, placar) e /health NUNCA passam pelo cache.
//
// DISCIPLINA: qualquer mudança em arquivo servido (inclusive rodar o ingest
// de novo) exige subir a VERSION.
const VERSION = 'datahidro-v3';
const STATIC_CACHE = `${VERSION}-static`;
const RUNTIME_CACHE = `${VERSION}-runtime`;

const STATIC_ASSETS = [
  './',
  'index.html',
  'style.css',
  'app.js',
  'manifest.json',
  'favicon.ico',
  'icon.svg',
  'icon-192.png',
  'icon-512.png',
  'icon-512-maskable.png',
  'apple-touch-icon.png',
  'lib/fonts/ibm-plex-mono-400.woff2',
  'lib/fonts/ibm-plex-mono-600.woff2',
  'lib/fonts/ibm-plex-mono-700.woff2',
  'data/candidates.json',
];

self.addEventListener('install', (event) => {
  event.waitUntil(
    caches.open(STATIC_CACHE).then((cache) => cache.addAll(STATIC_ASSETS)).then(() => self.skipWaiting())
  );
});

self.addEventListener('activate', (event) => {
  event.waitUntil(
    caches
      .keys()
      .then((keys) => Promise.all(keys.filter((k) => !k.startsWith(VERSION)).map((k) => caches.delete(k))))
      .then(() => self.clients.claim())
  );
});

function staleWhileRevalidate(event, cacheName) {
  return caches.open(cacheName).then((cache) =>
    cache.match(event.request).then((cached) => {
      const network = fetch(event.request)
        .then((res) => {
          if (res && res.ok) cache.put(event.request, res.clone());
          return res;
        })
        .catch(() => cached);
      return cached || network;
    })
  );
}

function networkFirst(event, cacheName) {
  return caches.open(cacheName).then((cache) =>
    fetch(event.request)
      .then((res) => {
        if (res && res.ok) cache.put(event.request, res.clone());
        return res;
      })
      .catch(() => cache.match(event.request))
  );
}

function cacheFirst(event, cacheName) {
  return caches.open(cacheName).then((cache) =>
    cache.match(event.request).then(
      (cached) =>
        cached ||
        fetch(event.request).then((res) => {
          if (res && res.ok) cache.put(event.request, res.clone());
          return res;
        })
    )
  );
}

self.addEventListener('fetch', (event) => {
  const url = new URL(event.request.url);
  if (event.request.method !== 'GET' || url.origin !== location.origin) return;
  // `includes` cobre o app servido em sub-caminho
  if (url.pathname.includes('/api/') || url.pathname.endsWith('/health')) return;

  if (url.pathname.includes('/photos/')) {
    event.respondWith(cacheFirst(event, RUNTIME_CACHE));
  } else if (url.pathname.endsWith('.json') && !url.pathname.endsWith('manifest.json')) {
    event.respondWith(networkFirst(event, STATIC_CACHE));
  } else {
    event.respondWith(staleWhileRevalidate(event, STATIC_CACHE));
  }
});
