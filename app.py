from flask import Flask, request, jsonify, send_from_directory, Response
from flask_cors import CORS
from model import parse_player_csv, final_over_probability
import requests
import pandas as pd
import numpy as np
import os
import re
import time

# Payments (Stripe + PayPal)
try:
    from payments import payments_bp
    PAYMENTS_ENABLED = True
    print("[payments] ✅ Modulo caricato correttamente")
except Exception as e:
    PAYMENTS_ENABLED = False
    print(f"[payments] ❌ Errore import: {e}")

try:
    from nba_api.stats.endpoints import playergamelog
    from nba_api.stats.static import players as nba_players_static
    NBA_API_AVAILABLE = True
except ImportError:
    NBA_API_AVAILABLE = False

app = Flask(__name__)
CORS(app)

# Registra blueprint pagamenti
if PAYMENTS_ENABLED:
    app.register_blueprint(payments_bp)
    print("[payments] ✅ Routes attive: /stripe/create-checkout, /paypal/create-order, /paypal/capture-order")
else:
    # Fallback routes che restituiscono JSON invece di 404 HTML
    @app.route("/paypal/create-order", methods=["POST"])
    def paypal_fallback():
        return jsonify({"error": "Modulo payments non caricato — controlla i log Railway"}), 503
    @app.route("/stripe/create-checkout", methods=["POST"])
    def stripe_fallback():
        return jsonify({"error": "Modulo payments non caricato — controlla i log Railway"}), 503

# ============================================
# CACHE GIOCATORI NBA (caricata una volta all'avvio)
# ============================================

_ALL_NBA_PLAYERS = []  # cache globale

def get_all_players():
    """Carica e cachea tutti i giocatori NBA da nba_api"""
    global _ALL_NBA_PLAYERS
    if _ALL_NBA_PLAYERS:
        return _ALL_NBA_PLAYERS
    try:
        if NBA_API_AVAILABLE:
            _ALL_NBA_PLAYERS = nba_players_static.get_players()
            print(f"✅ Caricati {len(_ALL_NBA_PLAYERS)} giocatori da nba_api")
    except Exception as e:
        print(f"⚠️  Errore caricamento giocatori: {e}")
    return _ALL_NBA_PLAYERS

# ============================================
# CONFIGURAZIONE FRONTEND FIREBASE
# ============================================

FRONTEND_PATH = os.path.join(os.path.dirname(__file__), 'frontend', 'firebase')

print("=" * 70)
print("🏀 NBA OVER PREDICTOR - FIREBASE VERSION")
print("=" * 70)
print(f"📂 Frontend path: {os.path.abspath(FRONTEND_PATH)}")
print("=" * 70)


# ============================================
# DATABASE GIOCATORI NBA (nome → slug Basketball Reference)
# ============================================

NBA_PLAYERS = {
    "LeBron James": "jamesle01",
    "Stephen Curry": "curryst01",
    "Kevin Durant": "duranke01",
    "Giannis Antetokounmpo": "antetgi01",
    "Luka Doncic": "doncilu01",
    "Nikola Jokic": "jokicni01",
    "Joel Embiid": "embiijo01",
    "Kawhi Leonard": "leonaka01",
    "Damian Lillard": "lillada01",
    "Anthony Davis": "davisan02",
    "James Harden": "hardeja01",
    "Jayson Tatum": "tatumja01",
    "Devin Booker": "bookede01",
    "Donovan Mitchell": "mitchdo01",
    "Trae Young": "youngtr01",
    "Kyrie Irving": "irvinky01",
    "Paul George": "georgpa01",
    "Jimmy Butler": "butleji01",
    "Klay Thompson": "thompkl01",
    "Zion Williamson": "willizi01",
    "Jaylen Brown": "brownja02",
    "Karl-Anthony Towns": "townska01",
    "Ja Morant": "moranja01",
    "Bam Adebayo": "adebaba01",
    "Draymond Green": "greendr01",
    "Bradley Beal": "bealbr01",
    "Pascal Siakam": "siakapa01",
    "Shai Gilgeous-Alexander": "gilgesh01",
    "De'Aaron Fox": "foxde01",
    "Domantas Sabonis": "sabondo01",
    "Anthony Edwards": "edwaran01",
    "DeMar DeRozan": "derozde01",
    "Chris Paul": "paulch01",
    "Rudy Gobert": "goberru01",
    "Fred VanVleet": "vanvlfr01",
    "Julius Randle": "randlju01",
    "Jrue Holiday": "holidjr01",
    "CJ McCollum": "mccolcj01",
    "Khris Middleton": "middlkh01",
    "Brandon Ingram": "ingrabr01",
    "Dejounte Murray": "murrade01",
    "Tyrese Haliburton": "halibty01",
    "LaMelo Ball": "ballla01",
    "Jalen Brunson": "brunsja01",
    "Tyler Herro": "herroty01",
    "Jaren Jackson Jr.": "jacksja02",
    "Derrick White": "whitede01",
    "OG Anunoby": "anunoog01",
    "Mikal Bridges": "bridgmi01",
    "Jarrett Allen": "allenja01",
    "Evan Mobley": "mobleev01",
    "Scottie Barnes": "barnessc01",
    "Paolo Banchero": "banchpa01",
    "Chet Holmgren": "holmgch01",
    "Victor Wembanyama": "wembavi01",
    "Scoot Henderson": "hendesk01",
    "Cade Cunningham": "cunnica01",
    "Jalen Green": "greenja05",
    "Franz Wagner": "wagnefr01",
    "Alperen Sengun": "sengual01",
    "Desmond Bane": "banede01",
}


# ============================================
# FRONTEND ROUTES
# ============================================

@app.route("/")
def serve_index():
    return send_from_directory(FRONTEND_PATH, 'index.html')


@app.route("/firebase-config.js")
def serve_firebase_config():
    """Genera firebase-config.js dinamicamente dalle variabili d'ambiente"""
    config = {
        "apiKey":            os.environ.get("FIREBASE_API_KEY", ""),
        "authDomain":        os.environ.get("FIREBASE_AUTH_DOMAIN", ""),
        "projectId":         os.environ.get("FIREBASE_PROJECT_ID", ""),
        "storageBucket":     os.environ.get("FIREBASE_STORAGE_BUCKET", ""),
        "messagingSenderId": os.environ.get("FIREBASE_MESSAGING_SENDER_ID", ""),
        "appId":             os.environ.get("FIREBASE_APP_ID", ""),
        "measurementId":     os.environ.get("FIREBASE_MEASUREMENT_ID", ""),
    }

    js_content = f"""const firebaseConfig = {{
  apiKey: "{config['apiKey']}",
  authDomain: "{config['authDomain']}",
  projectId: "{config['projectId']}",
  storageBucket: "{config['storageBucket']}",
  messagingSenderId: "{config['messagingSenderId']}",
  appId: "{config['appId']}",
  measurementId: "{config['measurementId']}",
}};

if (typeof firebase !== "undefined" && !firebase.apps.length) {{
  firebase.initializeApp(firebaseConfig);
  console.log("✅ Firebase initialized");
}}
"""
    response = Response(js_content, mimetype='application/javascript')
    response.headers['Cache-Control'] = 'no-store'
    return response


@app.route("/logo.png")
def serve_logo():
    return send_from_directory(FRONTEND_PATH, 'logo.png')


@app.route("/manifest.json")
def serve_manifest():
    return send_from_directory(FRONTEND_PATH, 'manifest.json')



@app.route("/service-worker.js")
def serve_service_worker():
    """Il SW deve essere servito dalla root, con header Cache-Control corretto"""
    response = send_from_directory(FRONTEND_PATH, 'service-worker.js')
    response.headers['Cache-Control'] = 'no-cache'
    response.headers['Service-Worker-Allowed'] = '/'
    return response


@app.route("/style.css")
@app.route("/styles.css")
def serve_styles():
    return send_from_directory(FRONTEND_PATH, 'style.css')


@app.route("/css/<path:filename>")
def serve_css(filename):
    return send_from_directory(os.path.join(FRONTEND_PATH, 'css'), filename)

@app.route("/icons/<path:filename>")
def serve_icons(filename):
    icons_path = os.path.join(FRONTEND_PATH, 'icons')
    return send_from_directory(icons_path, filename)

@app.route("/screenshots/<path:filename>")
def serve_screenshots(filename):
    screenshots_path = os.path.join(FRONTEND_PATH, 'screenshots')
    return send_from_directory(screenshots_path, filename)


# ============================================
# API ENDPOINTS
# ============================================

@app.route("/search_players", methods=["POST"])
def search_players():
    """
    Autocomplete giocatori NBA tramite nba_api (tutti i giocatori NBA di sempre)
    Input:  {"query": "lebr"}
    Output: {"results": [{"name": "LeBron James", "player_id": 2544}, ...]}
    """
    data = request.get_json()
    query = data.get("query", "").lower().strip()

    if not query or len(query) < 2:
        return jsonify({"results": []}), 200

    all_players = get_all_players()

    results = []
    for p in all_players:
        # Solo giocatori attivi
        if not p.get("is_active", False):
            continue
        full_name = p.get("full_name", "")
        if query in full_name.lower():
            results.append({
                "name":      full_name,
                "player_id": p["id"],
                "is_active": True
            })

    # Priorità: nome inizia con query, poi alfabetico
    results.sort(key=lambda x: (
        not x["name"].lower().startswith(query),
        x["name"]
    ))

    return jsonify({"results": results[:10]}), 200


@app.route("/fetch_player_csv", methods=["POST"])
def fetch_player_csv():
    """
    Scarica il game log tramite nba_api (ufficiale, non bloccabile).
    Input:  {"slug": "jamesle01", "season": "2026"}
    Output: {"csv": "...", "player_name": "LeBron James", "games": 49}
    """
    if not NBA_API_AVAILABLE:
        return jsonify({"error": "nba_api non installato sul server"}), 500

    data       = request.get_json()
    player_id  = data.get("player_id")
    player_name = data.get("player_name", "Unknown")
    season_end = int(data.get("season", "2025"))

    if not player_id:
        return jsonify({"error": "player_id mancante"}), 400

    # Formato stagione nba_api: "2024-25"
    season_str = f"{season_end - 1}-{str(season_end)[-2:]}"

    try:
        player_id = int(player_id)

        # Scarica game log con retry automatico (NBA API è instabile)
        df = None
        last_error = None
        for attempt in range(3):
            try:
                if attempt > 0:
                    time.sleep(2 * attempt)  # 2s, 4s tra i retry
                    print(f"[nba_api] retry {attempt}/2 per player_id={player_id}")
                log = playergamelog.PlayerGameLog(
                    player_id=player_id,
                    season=season_str,
                    timeout=15 + (attempt * 10)  # 15s, 25s, 35s
                )
                df = log.get_data_frames()[0]
                break  # successo, esci dal loop
            except Exception as e:
                last_error = e
                print(f"[nba_api] tentativo {attempt+1} fallito: {e}")
                continue

        if df is None:
            return jsonify({"error": f"NBA API non raggiungibile dopo 3 tentativi. Usa il CSV manuale. ({str(last_error)[:80]})"}), 503

        if df.empty:
            return jsonify({"error": f"Nessuna partita trovata per {player_name} nella stagione {season_str}"}), 404

        # ── Rinomina colonne → formato Basketball Reference ────────────────
        df = df.rename(columns={
            "GAME_DATE":   "Date",
            "FGM":         "FG",
            "FG_PCT":      "FG%",
            "FG3M":        "3P",
            "FG3A":        "3PA",
            "FG3_PCT":     "3P%",
            "FTM":         "FT",
            "FT_PCT":      "FT%",
            "OREB":        "ORB",
            "DREB":        "DRB",
            "REB":         "TRB",
            "PLUS_MINUS":  "+/-",
            "MIN":         "MP",
        })

        # ── Colonne numeriche ──────────────────────────────────────────────
        num_cols = ["FG", "FGA", "FG%", "3P", "3PA", "3P%",
                    "FT", "FTA", "FT%", "ORB", "DRB", "TRB",
                    "AST", "STL", "BLK", "TOV", "PF", "PTS", "+/-"]
        for col in num_cols:
            if col in df.columns:
                df[col] = pd.to_numeric(df[col], errors="coerce").fillna(0)

        # ── Calcola eFG% ───────────────────────────────────────────────────
        df["eFG%"] = np.where(
            df["FGA"] > 0,
            (df["FG"] + 0.5 * df["3P"]) / df["FGA"],
            0
        ).round(3)

        # ── Calcola GmSc (Game Score) ──────────────────────────────────────
        df["GmSc"] = (
            df["PTS"]
            + 0.4  * df["FG"]
            - 0.7  * df["FGA"]
            - 0.4  * (df["FTA"] - df["FT"])
            + 0.7  * df["ORB"]
            + 0.3  * df["DRB"]
            + df["STL"]
            + 0.7  * df["AST"]
            + 0.7  * df["BLK"]
            - 0.4  * df["PF"]
            - df["TOV"]
        ).round(1)

        # ── Calcola 2P, 2PA, 2P% ──────────────────────────────────────────
        df["2P"]  = df["FG"]  - df["3P"]
        df["2PA"] = df["FGA"] - df["3PA"]
        df["2P%"] = np.where(df["2PA"] > 0, df["2P"] / df["2PA"], 0).round(3)

        # ── Aggiungi Rk progressivo ────────────────────────────────────────
        df = df.iloc[::-1].reset_index(drop=True)  # ordine cronologico
        df["Rk"] = df.index + 1

        # ── Seleziona e ordina colonne finali ──────────────────────────────
        final_cols = ["Rk", "Date", "MP", "FG", "FGA", "FG%",
                      "3P", "3PA", "3P%", "2P", "2PA", "2P%",
                      "FT", "FTA", "FT%", "ORB", "DRB", "TRB",
                      "AST", "STL", "BLK", "TOV", "PF", "PTS",
                      "GmSc", "+/-", "eFG%"]
        final_cols = [c for c in final_cols if c in df.columns]
        df = df[final_cols]

        # ── Converti in CSV ────────────────────────────────────────────────
        csv_text    = df.to_csv(index=False)
        games_count = len(df)

        return jsonify({
            "success":     True,
            "csv":         csv_text,
            "player_name": player_name,
            "games":       games_count,
            "season":      season_str
        }), 200

    except Exception as e:
        return jsonify({"error": f"Errore nba_api: {str(e)}"}), 500


@app.route("/predict", methods=["POST"])
def predict():
    """
    Predizione singola soglia
    Input:  {"csv": "...", "point_line": 28.5}
    Output: {"probability": 0.65, "confidence": "high", ...}
    """
    data = request.get_json()
    csv_text  = data.get("csv")
    point_line = data.get("point_line")

    if not csv_text or point_line is None:
        return jsonify({"error": "Dati mancanti"}), 400

    try:
        point_line = float(point_line)
        df = parse_player_csv(csv_text)

        if len(df) == 0:
            return jsonify({"error": "CSV vuoto o non valido"}), 400

        result = final_over_probability(df, point_line=point_line, recent_games=35)
        prob_over = result["probability"] / 100

        last_10_points = df.tail(10)["PTS"].tolist()
        last_10_dates  = df.tail(10)["Date"].dt.strftime('%m/%d').tolist()

        return jsonify({
            "success": True,
            "point_line": point_line,
            "probability": round(prob_over, 4),
            "over": round(prob_over, 4),
            "over_pct": round(prob_over * 100, 2),
            "confidence": result.get("confidence", "unknown"),
            "method_used": result.get("method_used", "unknown"),
            "total_games": len(df),
            "adjusted": result.get("adjusted", False),
            "player_stats": {
                "avg_points_last_10": round(df.tail(10)["PTS"].mean(), 2),
                "avg_points_season":  round(df["PTS"].mean(), 2),
                "max_points": int(df["PTS"].max()),
                "min_points": int(df["PTS"].min()),
                "std_points": round(df["PTS"].std(), 2)
            },
            "trend": {
                "points": last_10_points,
                "dates":  last_10_dates
            }
        }), 200

    except Exception as e:
        return jsonify({"error": f"Errore: {str(e)}"}), 500


@app.route("/predict_multiple", methods=["POST"])
def predict_multiple():
    """
    Predizione su più soglie
    Input:  {"csv": "...", "thresholds": [26.5, 28.5, 30.5]}
    """
    data = request.get_json()
    csv_text   = data.get("csv")
    thresholds = data.get("thresholds", [])

    if not csv_text or not thresholds:
        return jsonify({"error": "Dati mancanti"}), 400

    try:
        df = parse_player_csv(csv_text)
        results = []

        for threshold in thresholds:
            try:
                threshold = float(threshold)
                result = final_over_probability(df, point_line=threshold, recent_games=35)
                prob_over = result["probability"] / 100
                results.append({
                    "threshold":  threshold,
                    "probability": round(prob_over * 100, 2),
                    "confidence": result.get("confidence", "unknown"),
                    "adjusted":   result.get("adjusted", False)
                })
            except Exception:
                results.append({"threshold": threshold, "error": "Calcolo fallito"})

        return jsonify({
            "success": True,
            "results": results,
            "total_games": len(df)
        }), 200

    except Exception as e:
        return jsonify({"error": f"Errore: {str(e)}"}), 500


@app.route("/calculate_bet", methods=["POST"])
def calculate_bet():
    """
    Calcola stake ottimale con Kelly Criterion
    Input:  {"bankroll": 100, "target_profit": 80, "total_events": 8,
             "probability": 0.65, "odds": 1.90, "confidence": "high"}
    Output: {"recommended": true, "stake": 12.5, "reason": "..."}
    """
    data = request.get_json()

    try:
        bankroll      = float(data["bankroll"])
        target_profit = float(data["target_profit"])
        total_events  = int(data["total_events"])
        prob_over     = float(data["probability"])
        odds          = float(data["odds"])
        confidence    = data.get("confidence", "medium")

        b = odds - 1
        p = prob_over
        q = 1 - p
        kelly_fraction = (b * p - q) / b

        confidence_multipliers = {
            "very_low": 0.0,
            "low":      0.25,
            "medium":   0.40,
            "high":     0.50
        }
        confidence_mult = confidence_multipliers.get(confidence, 0.40)
        adjusted_kelly  = kelly_fraction * confidence_mult

        base_stake_per_event = bankroll / total_events
        kelly_stake = bankroll * adjusted_kelly
        stake = min(base_stake_per_event, kelly_stake)

        max_stake = bankroll * 0.15
        min_stake = bankroll * 0.01
        stake = max(min_stake, min(stake, max_stake))

        potential_profit      = stake * b
        expected_wins         = total_events * prob_over
        expected_total_profit = expected_wins * potential_profit - (total_events - expected_wins) * stake

        recommended = False
        reason      = ""
        risk_level  = "high"

        if kelly_fraction <= 0:
            reason     = "❌ Kelly negativo: NON GIOCARE"
            risk_level = "extreme"
        elif confidence == "very_low":
            reason     = "❌ Confidence troppo bassa"
            risk_level = "extreme"
        elif prob_over < 0.55:
            reason     = "⚠️ Probabilità <55%: troppo rischiosa"
            risk_level = "high"
        elif expected_total_profit < target_profit * 0.7:
            reason     = f"⚠️ Profitto atteso ({expected_total_profit:.2f}€) sotto target"
            risk_level = "medium"
        else:
            recommended = True
            risk_level  = "low" if (confidence == "high" and prob_over > 0.65) else "medium"
            if prob_over >= 0.70 and confidence == "high":
                reason = f"✅ OTTIMA BET! Probabilità {prob_over*100:.1f}% con alta confidence"
            else:
                reason = f"✅ Buona bet. Probabilità {prob_over*100:.1f}%"

        return jsonify({
            "recommended":          recommended,
            "stake":                round(stake, 2),
            "stake_percentage":     round(stake / bankroll * 100, 2),
            "potential_profit":     round(potential_profit, 2),
            "potential_roi":        round((potential_profit / stake) * 100, 2),
            "kelly_fraction":       round(kelly_fraction, 4),
            "kelly_percentage":     round(adjusted_kelly * 100, 2),
            "risk_level":           risk_level,
            "reason":               reason,
            "expected_total_profit": round(expected_total_profit, 2),
            "target_profit":        target_profit,
            "target_achievable":    expected_total_profit >= target_profit * 0.8,
            "stats": {
                "expected_wins":         round(expected_wins, 2),
                "expected_losses":       round(total_events - expected_wins, 2),
                "win_rate":              round(prob_over * 100, 2),
                "break_even_probability": round(1 / odds, 4)
            }
        }), 200

    except Exception as e:
        return jsonify({"error": f"Errore: {str(e)}"}), 500


@app.route("/guida-csv.html")
def serve_guida_csv():
    return send_from_directory(FRONTEND_PATH, 'guida-csv.html')

@app.route("/terms.html")
def serve_terms():
    return send_from_directory(FRONTEND_PATH, 'terms.html')

@app.route("/privacy.html")
def serve_privacy():
    return send_from_directory(FRONTEND_PATH, 'privacy.html')

@app.route("/cookie.html")
def serve_cookie():
    return send_from_directory(FRONTEND_PATH, 'cookie.html')

@app.route("/config/paypal-client-id")
def paypal_client_id():
    """Espone il PayPal Client ID al frontend in modo sicuro (non va su git)."""
    client_id = os.environ.get("PAYPAL_CLIENT_ID", "")
    return jsonify({"client_id": client_id})

@app.route("/premium.html")
def serve_premium():
    return send_from_directory(FRONTEND_PATH, 'premium.html')

@app.route("/health", methods=["GET"])
def health():
    return jsonify({
        "status":  "healthy",
        "service": "nba-over-predictor",
        "version": "3.0-firebase"
    }), 200


# ============================================
# START SERVER
# ============================================

if __name__ == "__main__":
    print("\n")
    print("=" * 70)
    print("  🏀 NBA OVER PREDICTOR - SERVER STARTED")
    print("=" * 70)
    print(f"  🌐 Server URL:     http://127.0.0.1:5000")
    print(f"  🔥 Firebase:       ENABLED")
    print(f"  📂 Frontend Path:  {os.path.abspath(FRONTEND_PATH)}")
    print("=" * 70)
    print("  ✅ APRI IL BROWSER SU: http://127.0.0.1:5000/")
    print("=" * 70)
    print("\n")