// ShareNow Service Worker
const CACHE_NAME = 'sharenow-v1';

self.addEventListener('install', (e) => {
  self.skipWaiting();
});

self.addEventListener('activate', (e) => {
  e.waitUntil(clients.claim());
});

self.addEventListener('fetch', (e) => {
  // Let network handle all API and live uploads/downloads
  e.respondWith(fetch(e.request).catch(() => caches.match(e.request)));
});
