# 🚀 GUIDA INSTALLAZIONE COMPLETA (Python 3.13)

## 📁 STEP 1: Organizza i File

Crea questa struttura di cartelle:

```
C:\Users\Bartolomeo\Desktop\nba-predictor\
├── model.py
├── app.py
├── test_model.py
└── requirements.txt
```

### Come fare:

1. Apri Esplora File
2. Vai su Desktop
3. Crea cartella: `nba-predictor`
4. Metti dentro i 4 file che ti ho mandato

---

## 💻 STEP 2: Installa le Librerie (Python 3.13)

Apri **PowerShell** o **CMD** e vai nella cartella:

```bash
cd C:\Users\Bartolomeo\Desktop\nba-predictor
```

Poi esegui questi comandi **UNO ALLA VOLTA**:

```bash
# 1. Aggiorna pip
python -m pip install --upgrade pip

# 2. Installa Flask (semplice, nessun problema)
pip install flask

# 3. Installa NumPy (usa versione più recente)
pip install numpy

# 4. Installa Pandas (può richiedere qualche minuto)
pip install pandas

# 5. Installa scikit-learn
pip install scikit-learn

# 6. Installa XGBoost (opzionale, ma consigliato)
pip install xgboost
```

### ⚠️ Se Pandas Dà Errore:

Prova questo comando alternativo:

```bash
pip install --only-binary :all: pandas
```

Se continua a dare errore, **salta pandas per ora** e prova prima il test:

```bash
pip install pandas --no-build-isolation
```

---

## ✅ STEP 3: Testa che Funzioni

```bash
python test_model.py
```

### Cosa dovresti vedere:

```
============================================================
🏀 TEST MODELLO NBA OVER/UNDER PREDICTOR
============================================================

1️⃣ Test parsing CSV...
✅ CSV parsato: 20 partite
   Media punti: 32.85

2️⃣ Test predizione con diverse soglie...

   Soglia: 25.5 punti
   ├─ Probabilità OVER: 100.00%
   ├─ Confidence: medium
   └─ Metodo: ensemble_ml

============================================================
✅ TEST COMPLETATI
============================================================
```

Se vedi questo → **TUTTO OK!** ✅

---

## 🚀 STEP 4: Avvia il Server

```bash
python app.py
```

Dovresti vedere:

```
 * Running on http://127.0.0.1:5000
```

Il server è attivo! 🎉

---

## 🧪 STEP 5: Testa l'API

### Opzione A: Con Postman

1. Apri Postman
2. Crea richiesta POST a `http://localhost:5000/predict`
3. Headers: `Content-Type: application/json`
4. Body (raw JSON):

```json
{
  "csv": "Rk,Gcar,Gtm,Date,Team,,Opp,Result,GS,MP,FG,FGA,FG%,3P,3PA,3P%,2P,2PA,2P%,eFG%,FT,FTA,FT%,ORB,DRB,TRB,AST,STL,BLK,TOV,PF,PTS,GmSc,+/-\n1,463,1,2025-10-21,OKC,,HOU,W 125-124 (2OT),*,47:13,12,26,.462,1,9,.111,11,17,.647,.481,10,14,.714,0,5,5,5,2,2,3,2,35,24.6,3",
  "point_line": 28.5
}
```

### Opzione B: Con Python (crea test_api.py)

```python
import requests
import json

csv_text = """Rk,Gcar,Gtm,Date,Team,,Opp,Result,GS,MP,FG,FGA,FG%,3P,3PA,3P%,2P,2PA,2P%,eFG%,FT,FTA,FT%,ORB,DRB,TRB,AST,STL,BLK,TOV,PF,PTS,GmSc,+/-
1,463,1,2025-10-21,OKC,,HOU,W 125-124 (2OT),*,47:13,12,26,.462,1,9,.111,11,17,.647,.481,10,14,.714,0,5,5,5,2,2,3,2,35,24.6,3"""

response = requests.post(
    "http://localhost:5000/predict",
    json={"csv": csv_text, "point_line": 28.5}
)

print(json.dumps(response.json(), indent=2))
```

---

## 🔧 TROUBLESHOOTING

### Errore: "No module named 'pandas'"

```bash
# Prova installazione forzata
pip install pandas --no-build-isolation

# Oppure usa conda
conda install pandas
```

### Errore: "No module named 'sklearn'"

```bash
pip install scikit-learn
```

### Errore: "No module named 'xgboost'"

```bash
pip install xgboost

# Se fallisce, il modello funziona comunque senza XGBoost
# (userà solo Logistic Regression + Random Forest)
```

### Server non parte

```bash
# Controlla che la porta 5000 sia libera
netstat -ano | findstr :5000

# Se occupata, usa porta diversa
# In app.py cambia l'ultima riga:
app.run(debug=True, host="0.0.0.0", port=8080)
```

---

## 📊 RISPOSTA ATTESA DALL'API

Quando invii una richiesta, riceverai:

```json
{
  "success": true,
  "point_line": 28.5,
  "over": 0.6234,
  "under": 0.3766,
  "over_pct": 62.34,
  "under_pct": 37.66,
  "confidence": "high",
  "method_used": "ensemble_ml",
  "sample_size": 35,
  "player_stats": {
    "avg_points_last_10": 32.4,
    "avg_points_season": 31.8,
    "max_points": 55,
    "min_points": 20
  }
}
```

---

## 🎯 RECAP VELOCE

```bash
# 1. Crea cartella
mkdir nba-predictor
cd nba-predictor

# 2. Metti i 4 file dentro

# 3. Installa
pip install flask pandas numpy scikit-learn xgboost

# 4. Testa
python test_model.py

# 5. Avvia
python app.py
```

---

## ❓ Hai Problemi?

Scrivi qui l'errore completo che vedi e ti aiuto! 🚀
