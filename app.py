from flask import Flask, request, jsonify, send_from_directory
from flask_cors import CORS
from model import parse_player_csv, final_over_probability
import requests
from bs4 import BeautifulSoup
import time
import os
import re

app = Flask(__name__)
CORS(app)

# ============================================
# CONFIGURAZIONE FRONTEND FIREBASE
# ============================================

FRONTEND_PATH = os.path.join(os.path.dirname(__file__), '..', 'frontend', 'firebase')

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
    return send_from_directory(FRONTEND_PATH, 'firebase-config.js')


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


@app.route("/styles.css")
def serve_styles():
    """Serve external CSS file"""
    return send_from_directory(FRONTEND_PATH, 'style.css')


# ============================================
# API ENDPOINTS
# ============================================

@app.route("/search_players", methods=["POST"])
def search_players():
    """
    Autocomplete giocatori NBA
    Input:  {"query": "lebr"}
    Output: {"results": [{"name": "LeBron James", "slug": "jamesle01"}, ...]}
    """
    data = request.get_json()
    query = data.get("query", "").lower().strip()

    if not query or len(query) < 2:
        return jsonify({"results": []}), 200

    results = []
    for name, slug in NBA_PLAYERS.items():
        if query in name.lower():
            results.append({"name": name, "slug": slug})

    # Priorità: nomi che iniziano con la query
    results.sort(key=lambda x: (not x["name"].lower().startswith(query), x["name"]))

    return jsonify({"results": results[:10]}), 200


@app.route("/fetch_player_csv", methods=["POST"])
def fetch_player_csv():
    """
    Scarica il game log da Basketball Reference e restituisce un CSV pulito.
    Input:  {"slug": "jamesle01", "season": "2026"}
    Output: {"csv": "...", "player_name": "LeBron James", "games": 49}
    """
    data = request.get_json()
    slug   = data.get("slug", "").strip()
    season = data.get("season", "2026")

    if not slug:
        return jsonify({"error": "Slug mancante"}), 400

    known_slugs = set(NBA_PLAYERS.values())
    if slug not in known_slugs:
        return jsonify({"error": "Giocatore non riconosciuto"}), 400

    target_url = f"https://www.basketball-reference.com/players/{slug[0]}/{slug}/gamelog/{season}"

    # Headers che imitano Chrome reale
    browser_headers = {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36',
        'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8',
        'Accept-Language': 'en-US,en;q=0.9',
        'Accept-Encoding': 'gzip, deflate, br',
        'Connection': 'keep-alive',
        'Upgrade-Insecure-Requests': '1',
        'Sec-Fetch-Dest': 'document',
        'Sec-Fetch-Mode': 'navigate',
        'Sec-Fetch-Site': 'none',
        'Sec-Fetch-User': '?1',
        'Cache-Control': 'max-age=0',
        'sec-ch-ua': '"Google Chrome";v="131", "Chromium";v="131", "Not_A Brand";v="24"',
        'sec-ch-ua-mobile': '?0',
        'sec-ch-ua-platform': '"Windows"',
    }

    try:
        # Usa una Session: prima visita la homepage per ottenere i cookie,
        # poi scarica la pagina del giocatore — come farebbe un browser vero
        session = requests.Session()
        session.headers.update(browser_headers)

        # Step 1: warm-up homepage per ottenere cookie
        try:
            session.get('https://www.basketball-reference.com/', timeout=10)
            time.sleep(1.5)
        except Exception:
            pass  # se fallisce, proviamo comunque

        # Step 2: richiesta pagina giocatore con Referer corretto
        session.headers.update({
            'Referer': 'https://www.basketball-reference.com/',
            'Sec-Fetch-Site': 'same-origin',
        })

        response = None
        for attempt in range(3):
            try:
                if attempt > 0:
                    time.sleep(3 * attempt)
                response = session.get(target_url, timeout=25)

                if response.status_code == 429:
                    # Rate limit: aspetta di più
                    time.sleep(10)
                    continue

                response.raise_for_status()
                break
            except requests.exceptions.HTTPError as e:
                if response and response.status_code == 403:
                    return jsonify({
                        "error": "Basketball Reference ha bloccato la richiesta (403). "
                                 "Prova tra qualche secondo, oppure usa il CSV manuale."
                    }), 403
                if attempt == 2:
                    raise
            except Exception:
                if attempt == 2:
                    raise

        if not response or not response.ok:
            return jsonify({"error": "Impossibile scaricare i dati"}), 500

        soup = BeautifulSoup(response.text, 'html.parser')

        # Nome giocatore
        player_name = "Unknown"
        h1 = soup.find('h1', {'itemprop': 'name'})
        if h1:
            player_name = h1.get_text(strip=True)

        # Tabella game log
        table = soup.find('table', {'id': 'pgl_basic'})
        if not table:
            return jsonify({"error": f"Game log non trovato per {player_name}. "
                                      "Potrebbe non aver ancora giocato questa stagione."}), 404

        # ── Colonne ───────────────────────────────────────────────────────
        thead = table.find('thead')
        if not thead:
            return jsonify({"error": "Struttura tabella non valida"}), 500

        col_names = [th.get_text(strip=True) for th in thead.find('tr').find_all('th')]
        try:
            idx_rk  = col_names.index('Rk')
            idx_pts = col_names.index('PTS')
        except ValueError:
            return jsonify({"error": "Colonne Rk/PTS non trovate nella tabella"}), 500

        # ── Righe ─────────────────────────────────────────────────────────
        tbody = table.find('tbody')
        if not tbody:
            return jsonify({"error": "Nessuna partita trovata"}), 404

        csv_rows    = [','.join(col_names)]
        games_count = 0

        for tr in tbody.find_all('tr'):
            # 1. Salta header ripetuti
            if 'thead' in tr.get('class', []):
                continue

            # 2. Salta DNP / Inactive
            reason_cell = tr.find('td', {'data-stat': 'reason'})
            if reason_cell and reason_cell.get_text(strip=True):
                continue

            raw_cells = [td.get_text(strip=True) for td in tr.find_all(['th', 'td'])]

            # 3. Abbastanza colonne
            if len(raw_cells) < len(col_names):
                continue

            # 4. Rk deve essere intero
            rk_val = raw_cells[idx_rk] if idx_rk < len(raw_cells) else ''
            if not rk_val.isdigit():
                continue

            # 5. PTS deve essere numerico
            pts_val = raw_cells[idx_pts] if idx_pts < len(raw_cells) else ''
            try:
                float(pts_val)
            except ValueError:
                continue

            # 6. Escape virgole
            escaped = [f'"{c}"' if ',' in c else c for c in raw_cells]
            csv_rows.append(','.join(escaped))
            games_count += 1

        if games_count == 0:
            return jsonify({"error": f"Nessuna partita valida trovata per {player_name} "
                                      f"nella stagione {season}"}), 404

        return jsonify({
            "success":     True,
            "csv":         '\n'.join(csv_rows),
            "player_name": player_name,
            "games":       games_count,
            "season":      season
        }), 200

    except Exception as e:
        return jsonify({"error": f"Errore scaricamento: {str(e)}"}), 500


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
    print(" 🏀 NBA OVER PREDICTOR - SERVER STARTED")
    print("=" * 70)
    print(f"  🌐 Server URL:     http://127.0.0.1:5000")
    print(f"  🔥 Firebase:       ENABLED")
    print(f"  📂 Frontend Path:  {os.path.abspath(FRONTEND_PATH)}")
    print("=" * 70)
    print("  ✅ APRI IL BROWSER SU: http://127.0.0.1:5000/")
    print("=" * 70)
    print("\n")

    app.run(debug=True, host="0.0.0.0", port=5000)