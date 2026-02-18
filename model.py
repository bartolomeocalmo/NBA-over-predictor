import pandas as pd
import numpy as np
from io import StringIO
from sklearn.preprocessing import StandardScaler
from sklearn.linear_model import LogisticRegression
from sklearn.ensemble import RandomForestClassifier, VotingClassifier
from sklearn.calibration import CalibratedClassifierCV
import warnings
warnings.filterwarnings('ignore')

try:
    from xgboost import XGBClassifier
    XGBOOST_AVAILABLE = True
except ImportError:
    XGBOOST_AVAILABLE = False


# =========================
# CACHE GLOBALE PER MONOTONICITÀ
# =========================
_probability_cache = {}  # {player_hash: {threshold: probability}}


def get_player_hash(df: pd.DataFrame) -> str:
    """Crea un hash univoco per il giocatore basato sui dati recenti"""
    recent_pts = df.tail(35)['PTS'].values
    return hash(tuple(recent_pts))


def enforce_monotonicity(threshold: float, raw_probability: float, df: pd.DataFrame) -> dict:
    """
    Post-processing che garantisce monotonicità
    
    Regola: se prob(soglia_alta) > prob(soglia_bassa), correggi
    
    Returns:
        dict con probability corretta e metadata
    """
    player_hash = get_player_hash(df)
    
    # Inizializza cache per questo giocatore
    if player_hash not in _probability_cache:
        _probability_cache[player_hash] = {}
    
    cache = _probability_cache[player_hash]
    
    # Salva probabilità raw
    cache[threshold] = raw_probability
    
    # Ottieni soglie ordinate
    sorted_thresholds = sorted(cache.keys())
    
    # Se è la prima soglia, ritorna raw
    if len(sorted_thresholds) == 1:
        return {
            "probability": raw_probability,
            "adjusted": False,
            "method": "raw_ml"
        }
    
    # Trova posizione della soglia corrente
    current_idx = sorted_thresholds.index(threshold)
    
    # Controlla soglia precedente (più bassa)
    if current_idx > 0:
        prev_threshold = sorted_thresholds[current_idx - 1]
        prev_prob = cache[prev_threshold]
        
        # REGOLA: prob corrente NON può essere > prob precedente
        if raw_probability > prev_prob:
            # Correggi: decremento proporzionale alla differenza, non fisso
            decrement = min(0.5, (raw_probability - prev_prob) * 0.5)
            adjusted_prob = prev_prob - decrement
            adjusted_prob = max(0, adjusted_prob)  # Non negativo
            
            cache[threshold] = adjusted_prob
            
            return {
                "probability": adjusted_prob,
                "adjusted": True,
                "method": "adjusted_down_from_lower",
                "original_prob": raw_probability,
                "reference_threshold": prev_threshold,
                "reference_prob": prev_prob
            }
    
    # Controlla soglia successiva (più alta)
    if current_idx < len(sorted_thresholds) - 1:
        next_threshold = sorted_thresholds[current_idx + 1]
        next_prob = cache[next_threshold]
        
        # REGOLA: prob corrente NON può essere < prob successiva
        if raw_probability < next_prob:
            # Correggi: incremento proporzionale alla differenza, non fisso
            increment = min(0.5, (next_prob - raw_probability) * 0.5)
            adjusted_prob = next_prob + increment
            adjusted_prob = min(100, adjusted_prob)  # Max 100%
            
            cache[threshold] = adjusted_prob
            
            return {
                "probability": adjusted_prob,
                "adjusted": True,
                "method": "adjusted_up_from_higher",
                "original_prob": raw_probability,
                "reference_threshold": next_threshold,
                "reference_prob": next_prob
            }
    
    # Nessun aggiustamento necessario
    return {
        "probability": raw_probability,
        "adjusted": False,
        "method": "raw_ml"
    }


def clear_cache():
    """Pulisce la cache (utile per test)"""
    global _probability_cache
    _probability_cache = {}


# =========================
# 1️⃣ PARSE CSV
# =========================
def parse_player_csv(csv_text: str) -> pd.DataFrame:
    """Parse CSV con gestione robusta degli errori"""
    df = pd.read_csv(StringIO(csv_text))
    
    # Rimuovi eventuali righe di totali
    df = df[df['Rk'].notna() & (df['Rk'] != '')]
    df = df[df['Date'].notna()]
    
    df["Date"] = pd.to_datetime(df["Date"])
    df = df.sort_values("Date").reset_index(drop=True)
    
    # Parsing minuti giocati
    def mp_to_minutes(mp):
        if pd.isna(mp):
            return np.nan
        if isinstance(mp, str) and ":" in mp:
            parts = mp.split(":")
            return int(parts[0]) + int(parts[1]) / 60
        try:
            return float(mp)
        except:
            return np.nan
    
    df["MP_min"] = df["MP"].apply(mp_to_minutes)
    
    # Colonne numeriche
    numeric_cols = [
        "PTS", "FG", "FGA", "FG%",
        "3P", "3PA", "3P%",
        "2P", "2PA", "2P%",
        "FT", "FTA", "FT%",
        "ORB", "DRB", "TRB",
        "AST", "STL", "BLK",
        "TOV", "PF", "GmSc", "+/-",
        "eFG%"
    ]
    
    for col in numeric_cols:
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors="coerce")
    
    return df


# =========================
# 2️⃣ FEATURE ENGINEERING
# =========================
def build_advanced_features(df: pd.DataFrame, point_line: float) -> pd.DataFrame:
    """Costruisce feature avanzate"""
    df = df.copy()
    n = len(df)
    
    # Finestre rolling adattive
    w_short = min(5, max(3, n // 4))
    w_medium = min(10, max(5, n // 3))
    w_long = min(15, max(8, n // 2))
    
    # PUNTI
    df["avg_pts_last3"] = df["PTS"].rolling(3, min_periods=1).mean()
    df["avg_pts_last5"] = df["PTS"].rolling(w_short, min_periods=2).mean()
    df["avg_pts_last10"] = df["PTS"].rolling(w_medium, min_periods=3).mean()
    df["avg_pts_season"] = df["PTS"].expanding(min_periods=1).mean()
    
    # Volatilità
    df["std_pts_last10"] = df["PTS"].rolling(w_medium, min_periods=3).std()
    df["cv_pts"] = df["std_pts_last10"] / (df["avg_pts_last10"] + 1e-6)
    
    # Trend
    df["trend_pts"] = df["avg_pts_last5"] - df["avg_pts_last10"]
    df["margin_vs_line"] = df["avg_pts_last10"] - point_line
    
    # EFFICIENZA
    df["TS%"] = df["PTS"] / (2 * (df["FGA"] + 0.44 * df["FTA"]) + 1e-6)
    df["avg_TS"] = df["TS%"].rolling(w_short, min_periods=2).mean()
    df["avg_eFG"] = df["eFG%"].rolling(w_short, min_periods=2).mean()
    df["pts_per_min"] = df["PTS"] / (df["MP_min"] + 1e-6)
    df["avg_pts_per_min"] = df["pts_per_min"].rolling(w_short, min_periods=2).mean()
    
    # UTILIZZO
    df["usage_proxy"] = df["FGA"] + 0.44 * df["FTA"] + df["TOV"]
    df["avg_usage"] = df["usage_proxy"].rolling(w_short, min_periods=2).mean()
    df["avg_fga"] = df["FGA"].rolling(w_short, min_periods=2).mean()
    df["avg_fta"] = df["FTA"].rolling(w_short, min_periods=2).mean()
    
    # IMPATTO
    df["avg_gmsc"] = df["GmSc"].rolling(w_short, min_periods=2).mean()
    df["avg_plusminus"] = df["+/-"].rolling(w_short, min_periods=2).mean()
    df["avg_ast"] = df["AST"].rolling(w_short, min_periods=2).mean()
    
    # PERCENTUALI
    df["avg_fg_pct"] = df["FG%"].rolling(w_short, min_periods=2).mean()
    df["avg_3p_pct"] = df["3P%"].rolling(w_short, min_periods=2).mean()
    df["avg_ft_pct"] = df["FT%"].rolling(w_short, min_periods=2).mean()
    
    # MINUTI
    df["avg_minutes"] = df["MP_min"].rolling(w_short, min_periods=2).mean()
    
    # CONSISTENZA
    def rolling_over_pct(series, window, threshold):
        return series.rolling(window, min_periods=max(1, window-2)).apply(
            lambda x: (x > threshold).mean()
        )
    
    df["pct_over_last5"] = rolling_over_pct(df["PTS"], w_short, point_line)
    df["pct_over_last10"] = rolling_over_pct(df["PTS"], w_medium, point_line)
    
    # STREAK
    def get_streak(series, threshold):
        streaks = []
        current_streak = 0
        for val in series:
            if pd.isna(val):
                streaks.append(0)
            elif val > threshold:
                current_streak = current_streak + 1 if current_streak >= 0 else 1
                streaks.append(current_streak)
            else:
                current_streak = current_streak - 1 if current_streak <= 0 else -1
                streaks.append(current_streak)
        return streaks
    
    df["streak"] = get_streak(df["PTS"].values, point_line)
    
    return df


# =========================
# 3️⃣ SELEZIONE FEATURES
# =========================
def get_feature_columns() -> list:
    """Ritorna la lista delle feature da usare nel modello"""
    return [
        "avg_pts_last3", "avg_pts_last5", "avg_pts_last10",
        "std_pts_last10", "cv_pts", "trend_pts", "margin_vs_line",
        "avg_TS", "avg_eFG", "avg_pts_per_min",
        "avg_usage", "avg_fga", "avg_fta",
        "avg_gmsc", "avg_plusminus", "avg_ast",
        "avg_fg_pct", "avg_3p_pct", "avg_ft_pct",
        "avg_minutes", "pct_over_last5", "pct_over_last10", "streak"
    ]


# =========================
# 4️⃣ ENSEMBLE MODEL
# =========================
def train_ensemble_model(df: pd.DataFrame, point_line: float):
    """Addestra ensemble di modelli"""
    df = df.copy()
    df["over"] = (df["PTS"] > point_line).astype(int)
    
    FEATURES = get_feature_columns()
    available_features = [f for f in FEATURES if f in df.columns and df[f].notna().sum() > len(df) * 0.5]
    
    if len(available_features) < 5:
        raise ValueError("Non abbastanza feature valide")
    
    X = df[available_features].fillna(df[available_features].median())
    y = df["over"]
    
    if y.nunique() < 2:
        raise ValueError("Soglia troppo estrema: nessuna variabilità OVER/UNDER")
    
    # Pesi temporali
    n = len(df)
    time_weights = np.linspace(0.5, 1.0, n)
    
    # Standardizzazione
    scaler = StandardScaler()
    X_scaled = scaler.fit_transform(X)
    
    # Modelli
    lr = LogisticRegression(penalty="l2", C=0.5, class_weight="balanced", max_iter=2000, random_state=42)
    rf = RandomForestClassifier(n_estimators=100, max_depth=5, min_samples_split=10, 
                                min_samples_leaf=5, class_weight="balanced", random_state=42)
    
    estimators = [('lr', lr), ('rf', rf)]
    
    if XGBOOST_AVAILABLE:
        xgb = XGBClassifier(n_estimators=100, max_depth=4, learning_rate=0.1, 
                           subsample=0.8, colsample_bytree=0.8, random_state=42, eval_metric='logloss')
        estimators.append(('xgb', xgb))
    
    ensemble = VotingClassifier(estimators=estimators, voting='soft')
    ensemble.fit(X_scaled, y, sample_weight=time_weights)
    
    try:
        calibrated_model = CalibratedClassifierCV(ensemble, method='isotonic', cv='prefit')
        calibrated_model.fit(X_scaled, y, sample_weight=time_weights)
        return calibrated_model, scaler, available_features
    except:
        return ensemble, scaler, available_features


# =========================
# 5️⃣ PREDIZIONE FINALE (CON POST-PROCESSING)
# =========================
def final_over_probability(
    df: pd.DataFrame,
    point_line: float = 22.5,
    recent_games: int = 35,
    monte_carlo_simulations: int = 1000,
    enforce_mono: bool = True  # ← NUOVO PARAMETRO
) -> dict:
    """
    Calcola probabilità OVER con modello ML + post-processing monotonicità
    
    Args:
        enforce_mono: Se True, applica correzione monotonicità
    """
    
    df_recent = df.tail(recent_games).reset_index(drop=True)
    n_games = len(df_recent)
    
    # Caso 1: Pochissimi dati
    if n_games < 5:
        prob = df_recent["PTS"].gt(point_line).mean() * 100
        return {
            "probability": round(prob, 2),
            "confidence": "very_low",
            "method_used": "empirical_fallback",
            "sample_size": n_games,
            "adjusted": False
        }
    
    # Caso 2: Soglia irrealistica
    max_realistic = df_recent["PTS"].quantile(0.95)
    min_realistic = df_recent["PTS"].quantile(0.05)
    
    if point_line > max_realistic:
        prob = df_recent["PTS"].gt(point_line).mean() * 100
        return {
            "probability": round(prob, 2),
            "confidence": "low",
            "method_used": "extreme_threshold_high",
            "sample_size": n_games,
            "adjusted": False
        }
    
    if point_line < min_realistic:
        prob = df_recent["PTS"].gt(point_line).mean() * 100
        return {
            "probability": round(prob, 2),
            "confidence": "low",
            "method_used": "extreme_threshold_low",
            "sample_size": n_games,
            "adjusted": False
        }
    
    # Caso 3: ML
    try:
        df_feat = build_advanced_features(df_recent, point_line)
        df_feat = df_feat.dropna(subset=["avg_pts_last5", "avg_pts_last10"], how='any')
        
        if len(df_feat) < 8:
            weights = np.linspace(0.5, 1.0, n_games)
            prob = np.average(df_recent["PTS"].gt(point_line), weights=weights) * 100
            return {
                "probability": round(prob, 2),
                "confidence": "low",
                "method_used": "weighted_empirical",
                "sample_size": n_games,
                "adjusted": False
            }
        
        model, scaler, features = train_ensemble_model(df_feat, point_line)
        
        last_game = df_feat.iloc[-1][features].values.reshape(1, -1)
        last_game = np.nan_to_num(last_game, nan=0)
        last_scaled = scaler.transform(last_game)
        
        prob_over = model.predict_proba(last_scaled)[0][1]
        
        # Media ultime 3 predizioni
        if len(df_feat) >= 3:
            last_3_games = df_feat.tail(3)[features].values
            last_3_games = np.nan_to_num(last_3_games, nan=0)
            last_3_scaled = scaler.transform(last_3_games)
            probs_3 = model.predict_proba(last_3_scaled)[:, 1]
            weights = np.array([0.2, 0.3, 0.5])
            prob_over = np.average(probs_3, weights=weights)
        
        raw_probability = prob_over * 100
        
        # ===== POST-PROCESSING MONOTONICITÀ =====
        if enforce_mono:
            mono_result = enforce_monotonicity(point_line, raw_probability, df_recent)
            final_prob = mono_result["probability"]
            adjusted = mono_result["adjusted"]
            method = f"ensemble_ml ({mono_result['method']})"
        else:
            final_prob = raw_probability
            adjusted = False
            method = "ensemble_ml (no_adjustment)"
        
        confidence = "high" if n_games >= 25 else "medium" if n_games >= 15 else "low"
        
        result = {
            "probability": round(final_prob, 2),
            "confidence": confidence,
            "method_used": method,
            "sample_size": len(df_feat),
            "features_used": len(features),
            "adjusted": adjusted
        }
        
        # Se aggiustato, aggiungi metadata
        if adjusted and enforce_mono:
            result["original_probability"] = round(raw_probability, 2)
            if "reference_threshold" in mono_result:
                result["adjustment_info"] = {
                    "reference_threshold": mono_result["reference_threshold"],
                    "reference_prob": round(mono_result["reference_prob"], 2)
                }
        
        return result
        
    except Exception as e:
        weights = np.linspace(0.5, 1.0, n_games)
        prob = np.average(df_recent["PTS"].gt(point_line), weights=weights) * 100
        
        return {
            "probability": round(prob, 2),
            "confidence": "low",
            "method_used": f"fallback_error ({str(e)[:50]})",
            "sample_size": n_games,
            "adjusted": False
        }