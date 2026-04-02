// Minimal Service Worker to satisfy Chrome PWA Install requirements

self.addEventListener('install', event => {
    self.skipWaiting();
});

self.addEventListener('activate', event => {
    event.waitUntil(clients.claim());
});

self.addEventListener('fetch', event => {
    // Simply pass through requests. Required by Android to classify as a Native App
    return;
});
