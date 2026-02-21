// ============================================
// NBA Over Predictor — Service Worker PWA
// ============================================

const CACHE_NAME = "nba-predictor-v4";

// Solo file locali garantiti da cachare
const STATIC_ASSETS = ["/", "/index.html", "/manifest.json", "/logo.png"];

// ── INSTALL ───────────────────────────────────────────────────────────────
self.addEventListener("install", (event) => {
  console.log("[SW] Installazione in corso...");
  event.waitUntil(
    caches
      .open(CACHE_NAME)
      .then((cache) => {
        return cache.addAll(STATIC_ASSETS).catch((err) => {
          console.warn("[SW] Alcuni asset non cachati:", err);
        });
      })
      .then(() => {
        console.log("[SW] Installazione completata ✅");
        return self.skipWaiting();
      }),
  );
});

// ── ACTIVATE: elimina cache vecchie ───────────────────────────────────────
self.addEventListener("activate", (event) => {
  console.log("[SW] Attivazione...");
  event.waitUntil(
    caches
      .keys()
      .then((cacheNames) => {
        return Promise.all(
          cacheNames
            .filter((name) => name !== CACHE_NAME)
            .map((name) => {
              console.log("[SW] Elimino cache vecchia:", name);
              return caches.delete(name);
            }),
        );
      })
      .then(() => {
        console.log("[SW] Attivazione completata ✅");
        return self.clients.claim();
      }),
  );
});

// ── FETCH ─────────────────────────────────────────────────────────────────
self.addEventListener("fetch", (event) => {
  const req = event.request;
  const url = new URL(req.url);

  // 1. Ignora tutto tranne HTTP/HTTPS
  if (!req.url.startsWith("http")) return;

  // 2. Ignora tutte le richieste POST (non cachebili)
  if (req.method !== "GET") return;

  // 3. Ignora domini esterni — Firebase, Google, CDN, etc.
  //    Lascia passare solo richieste allo stesso dominio
  const ownOrigin = self.location.origin; // es. https://nbaoverpredictor.it
  if (url.origin !== ownOrigin) return;

  // 4. Ignora le chiamate API del backend (risposte dinamiche)
  const API_PATHS = [
    "/predict",
    "/predict_multiple",
    "/calculate_bet",
    "/fetch_player_csv",
    "/search_players",
    "/health",
    "/stripe/",
    "/paypal/",
    "/config/",
    "/webhook/",
    "/css/", // CSS dinamici: sempre freschi dalla rete
  ];
  if (API_PATHS.some((p) => url.pathname.startsWith(p))) return;

  // 5. Solo per asset statici same-origin: cache-first
  event.respondWith(cacheFirst(req));
});

// ── Cache First (solo GET same-origin statici) ────────────────────────────
async function cacheFirst(request) {
  const cached = await caches.match(request);
  if (cached) return cached;

  try {
    const networkResponse = await fetch(request);
    // Salva solo risposte GET valide (non opaque, non errori)
    if (networkResponse.ok && networkResponse.type !== "opaque") {
      const cache = await caches.open(CACHE_NAME);
      cache.put(request, networkResponse.clone());
    }
    return networkResponse;
  } catch {
    return offlineFallback();
  }
}

// ── Fallback offline ──────────────────────────────────────────────────────
function offlineFallback() {
  return new Response(
    `<!DOCTYPE html>
    <html lang="it">
    <head>
      <meta charset="UTF-8">
      <meta name="viewport" content="width=device-width, initial-scale=1.0">
      <title>NBA Predictor — Offline</title>
      <style>
        body { background: #0f0f0f; color: #f5f5f5; font-family: sans-serif;
               display: flex; align-items: center; justify-content: center;
               min-height: 100vh; text-align: center; padding: 24px; }
        h1 { font-size: 28px; margin-bottom: 12px; }
        p  { color: #a0a0a0; margin-bottom: 24px; }
        button { padding: 12px 24px; background: #3b82f6; color: white;
                 border: none; border-radius: 8px; font-size: 16px; cursor: pointer; }
      </style>
    </head>
    <body>
      <div>
        <div style="font-size: 64px; margin-bottom: 16px">🏀</div>
        <h1>Sei offline</h1>
        <p>Connettiti a internet per usare NBA Over Predictor.</p>
        <button onclick="location.reload()">Riprova</button>
      </div>
    </body>
    </html>`,
    { headers: { "Content-Type": "text/html" } },
  );
}
