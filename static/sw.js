// OventechIOT — minimal service worker.
// Amaç: PWA "yükle" (install) kriterlerini sağlamak. Canlı SCADA verisi
// önbelleklenmemeli (tag değerleri her zaman güncel olmalı) — bu yüzden
// bilinçli olarak agresif bir offline-cache stratejisi YOK, sadece
// tarayıcının PWA olarak tanıması için gereken minimum kaydı yapıyoruz.
self.addEventListener('install', (event) => {
  self.skipWaiting();
});

self.addEventListener('activate', (event) => {
  self.clients.claim();
});

// Tüm istekler doğrudan ağdan (network) karşılanır — önbellek yok.
self.addEventListener('fetch', (event) => {
  event.respondWith(fetch(event.request));
});
