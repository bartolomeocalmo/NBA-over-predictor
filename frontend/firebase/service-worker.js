// ============================================
// NBA Over Predictor — Service Worker PWA
// ============================================

const CACHE_NAME = "nba-predictor-v3";

// File da cachare per funzionamento offline
const STATIC_ASSETS = [
  "/",
  "/index.html",
  "/style.css",
  "/firebase-config.js",
  "/manifest.json",
  "https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700;800&display=swap",
  "https://cdn.plot.ly/plotly-2.34.0.min.js",
  "https://www.gstatic.com/firebasejs/9.22.0/firebase-app-compat.js",
  "https://www.gstatic.com/firebasejs/9.22.0/firebase-auth-compat.js",
  "https://www.gstatic.com/firebasejs/9.22.0/firebase-firestore-compat.js",
];

// ── INSTALL: scarica e salva tutti gli asset statici ──────────────────────
self.addEventListener("install", (event) => {
  console.log("[SW] Installazione in corso...");
  event.waitUntil(
    caches
      .open(CACHE_NAME)
      .then((cache) => {
        console.log("[SW] Cache aperta, scarico asset...");
        // Carica i file locali garantiti, ignora eventuali fallimenti CDN
        return cache.addAll(STATIC_ASSETS).catch((err) => {
          console.warn(
            "[SW] Alcuni asset non cachati (probabilmente CDN):",
            err,
          );
        });
      })
      .then(() => {
        console.log("[SW] Installazione completata ✅");
        return self.skipWaiting(); // Attiva subito senza aspettare il refresh
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
        return self.clients.claim(); // Prende controllo di tutte le tab aperte
      }),
  );
});

// ── FETCH: strategia cache-first per static, network-first per API ────────
self.addEventListener("fetch", (event) => {
  const url = new URL(event.request.url);

  // Ignora URL non HTTP (chrome-extension://, ecc.)
  if (!event.request.url.startsWith("http")) return;

  // Chiamate API → sempre dalla rete (mai cachare risposte dinamiche)
  const isApiCall = [
    "/predict",
    "/predict_multiple",
    "/calculate_bet",
    "/fetch_player_csv",
    "/search_players",
    "/health",
    "/stripe/create-checkout",
    "/paypal/create-order",
    "/paypal/capture-order",
    "/config/paypal-client-id",
    "/webhook/stripe",
  ].some((path) => url.pathname.startsWith(path));

  if (isApiCall) {
    // Lascia passare le chiamate API direttamente senza intercettarle
    // Evita falsi "offline" causati da timeout del backend (es. NBA API lenta)
    return;
  }

  // Asset statici → prima dalla cache, poi dalla rete
  event.respondWith(cacheFirst(event.request));
});

// ── Strategia: Cache First (per HTML, CSS, JS, fonts) ────────────────────
async function cacheFirst(request) {
  const cached = await caches.match(request);
  if (cached) {
    return cached;
  }
  try {
    const networkResponse = await fetch(request);
    // Salva nella cache solo risposte valide
    if (networkResponse.ok) {
      const cache = await caches.open(CACHE_NAME);
      cache.put(request, networkResponse.clone());
    }
    return networkResponse;
  } catch {
    // Se offline e non in cache, mostra pagina offline
    return offlineFallback();
  }
}

// ── Strategia: Network First (per le API) ─────────────────────────────────
async function networkFirst(request) {
  try {
    const networkResponse = await fetch(request);
    return networkResponse;
  } catch {
    // Se offline, rispondi con errore JSON leggibile dall'app
    return new Response(
      JSON.stringify({
        error: "Sei offline. Connettiti per usare il predittore.",
      }),
      { status: 503, headers: { "Content-Type": "application/json" } },
    );
  }
}

// ── Fallback offline minimale ─────────────────────────────────────────────
function offlineFallback() {
  return new Response(
    `
    <!DOCTYPE html>
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
                 border: none; border-radius: 8px; font-size: 16px;
                 cursor: pointer; }
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
    </html>
  `,
    { headers: { "Content-Type": "text/html" } },
  );
}
