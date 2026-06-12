/* Service worker minimal pour Oracle Foot (PWA installable).
   Objectif : permettre l'installation et un lancement plein écran, SANS jamais
   servir de données périmées. On ne met en cache QUE la coquille statique, et on
   joue le réseau d'abord. Les appels /api/ ne sont jamais mis en cache. */
"use strict";

const CACHE = "oracle-foot-shell-v1";
const SHELL = ["/", "/static/style.css", "/static/app.js", "/static/icon.svg", "/manifest.json"];

self.addEventListener("install", (e) => {
  e.waitUntil(caches.open(CACHE).then((c) => c.addAll(SHELL)).then(() => self.skipWaiting()));
});

self.addEventListener("activate", (e) => {
  e.waitUntil(
    caches.keys()
      .then((keys) => Promise.all(keys.filter((k) => k !== CACHE).map((k) => caches.delete(k))))
      .then(() => self.clients.claim())
  );
});

self.addEventListener("fetch", (e) => {
  const req = e.request;
  const url = new URL(req.url);

  // On ne gère que les GET de notre origine ; le reste passe directement au réseau.
  if (req.method !== "GET" || url.origin !== self.location.origin) return;

  // Données : toujours le réseau, jamais de cache (pas de fraîcheur sacrifiée).
  if (url.pathname.startsWith("/api/")) return;

  // Coquille statique : réseau d'abord, repli sur le cache hors-ligne.
  e.respondWith(
    fetch(req)
      .then((res) => {
        if (res && res.ok) {
          const copy = res.clone();
          caches.open(CACHE).then((c) => c.put(req, copy));
        }
        return res;
      })
      .catch(() => caches.match(req).then((hit) => hit || caches.match("/")))
  );
});
