"""
payments.py — Stripe + PayPal integration per NBA Over Predictor
Aggiunge le routes a Flask e aggiorna Firestore quando il pagamento va a buon fine.
"""

import os
import json
import stripe
import requests
import firebase_admin
from firebase_admin import credentials, firestore as admin_firestore
from flask import Blueprint, request, jsonify, redirect

payments_bp = Blueprint("payments", __name__)

# ============================================================
# FIREBASE ADMIN (per aggiornare il piano dell'utente lato server)
# ============================================================
# Inizializza solo se non già fatto
def get_admin_db():
    if not firebase_admin._apps:
        # Legge il service account dal file (da scaricare dalla console Firebase)
        cred_path = os.environ.get("FIREBASE_SERVICE_ACCOUNT", "service-account.json")
        cred = credentials.Certificate(cred_path)
        firebase_admin.initialize_app(cred)
    return admin_firestore.client()

def set_user_premium(uid: str):
    """Imposta plan=premium su Firestore per l'utente con uid dato."""
    db = get_admin_db()
    db.collection("users").document(uid).set(
        {"plan": "premium", "plan_updated_at": admin_firestore.SERVER_TIMESTAMP},
        merge=True
    )

def set_user_free(uid: str):
    """Imposta plan=free (es. dopo cancellazione abbonamento)."""
    db = get_admin_db()
    db.collection("users").document(uid).set(
        {"plan": "free", "plan_updated_at": admin_firestore.SERVER_TIMESTAMP},
        merge=True
    )

# ============================================================
# ██████  STRIPE
# ============================================================
#
# Setup:
# 1. Crea account su https://stripe.com
# 2. Dashboard → Developers → API Keys → copia Secret key
# 3. Dashboard → Products → crea "NBA Predictor Premium" a 4,90€/mese
#    → copia il Price ID (es. price_xxxxxxxx)
# 4. Dashboard → Webhooks → aggiungi endpoint: https://tuodominio.com/webhook/stripe
#    → seleziona eventi: checkout.session.completed, customer.subscription.deleted
#    → copia Webhook Secret
# 5. Imposta le variabili d'ambiente su Railway:
#    STRIPE_SECRET_KEY=sk_live_...
#    STRIPE_PRICE_ID=price_...
#    STRIPE_WEBHOOK_SECRET=whsec_...

stripe.api_key = os.environ.get("STRIPE_SECRET_KEY", "")
STRIPE_PRICE_ID       = os.environ.get("STRIPE_PRICE_ID", "")
STRIPE_WEBHOOK_SECRET = os.environ.get("STRIPE_WEBHOOK_SECRET", "")
APP_URL               = os.environ.get("APP_URL", "https://nbaoverpredictor.it")


@payments_bp.route("/stripe/create-checkout", methods=["POST"])
def stripe_create_checkout():
    """
    Crea una Stripe Checkout Session.
    Body JSON: { "uid": "firebase_uid", "email": "user@example.com" }
    Restituisce: { "url": "https://checkout.stripe.com/..." }
    """
    data = request.get_json()
    uid   = data.get("uid")
    email = data.get("email", "")

    if not uid:
        return jsonify({"error": "uid mancante"}), 400
    if not stripe.api_key:
        return jsonify({"error": "Stripe non configurato"}), 500

    try:
        session = stripe.checkout.Session.create(
            payment_method_types=["card"],
            mode="subscription",
            customer_email=email,
            line_items=[{"price": STRIPE_PRICE_ID, "quantity": 1}],
            success_url=f"{APP_URL}/?payment=success&provider=stripe",
            cancel_url=f"{APP_URL}/?payment=cancel",
            metadata={"uid": uid},  # fondamentale per il webhook
            locale="it",
        )
        return jsonify({"url": session.url})
    except stripe.error.StripeError as e:
        return jsonify({"error": str(e)}), 500


@payments_bp.route("/webhook/stripe", methods=["POST"])
def stripe_webhook():
    """
    Riceve gli eventi da Stripe e aggiorna Firestore.
    IMPORTANTE: questa route deve essere esclusa da CSRF protection.
    """
    payload    = request.get_data()
    sig_header = request.headers.get("Stripe-Signature", "")

    try:
        event = stripe.Webhook.construct_event(payload, sig_header, STRIPE_WEBHOOK_SECRET)
    except (ValueError, stripe.error.SignatureVerificationError) as e:
        return jsonify({"error": "Webhook non valido"}), 400

    event_type = event["type"]

    if event_type == "checkout.session.completed":
        session = event["data"]["object"]
        uid = session.get("metadata", {}).get("uid")
        if uid:
            set_user_premium(uid)
            print(f"[Stripe] ✅ Premium attivato per uid={uid}")

    elif event_type == "customer.subscription.deleted":
        # Abbonamento cancellato o scaduto → torna a free
        # Nota: qui non abbiamo l'uid direttamente, lo salviamo nel customer
        customer_id = event["data"]["object"].get("customer")
        if customer_id:
            # Cerca l'uid associato al customer in Firestore
            try:
                db = get_admin_db()
                docs = db.collection("users").where("stripe_customer_id", "==", customer_id).stream()
                for doc in docs:
                    set_user_free(doc.id)
                    print(f"[Stripe] ⬇️ Premium rimosso per uid={doc.id}")
            except Exception as e:
                print(f"[Stripe] Errore ricerca customer: {e}")

    return jsonify({"received": True}), 200


# ============================================================
# ██████  PAYPAL
# ============================================================
#
# Setup:
# 1. Crea account su https://developer.paypal.com
# 2. Dashboard → Apps & Credentials → crea app
#    → copia Client ID e Secret (usa Sandbox per test, Live per produzione)
# 3. Imposta le variabili d'ambiente su Railway:
#    PAYPAL_CLIENT_ID=AaBbCc...
#    PAYPAL_SECRET=EeFfGg...
#    PAYPAL_ENV=live   (oppure "sandbox" per test)
# 4. Prezzo abbonamento: 4.90 EUR/mese

PAYPAL_CLIENT_ID = os.environ.get("PAYPAL_CLIENT_ID", "")
PAYPAL_SECRET    = os.environ.get("PAYPAL_SECRET", "")
PAYPAL_ENV       = os.environ.get("PAYPAL_ENV", "sandbox")
PAYPAL_BASE_URL  = (
    "https://api-m.paypal.com"
    if PAYPAL_ENV == "live"
    else "https://api-m.sandbox.paypal.com"
)
PREMIUM_PRICE_EUR = "4.90"


def paypal_get_access_token():
    """Ottiene un token OAuth2 da PayPal."""
    r = requests.post(
        f"{PAYPAL_BASE_URL}/v1/oauth2/token",
        auth=(PAYPAL_CLIENT_ID, PAYPAL_SECRET),
        data={"grant_type": "client_credentials"},
        timeout=10,
    )
    r.raise_for_status()
    return r.json()["access_token"]


@payments_bp.route("/paypal/create-order", methods=["POST"])
def paypal_create_order():
    """
    Crea un ordine PayPal (pagamento una-tantum mensile).
    Body JSON: { "uid": "firebase_uid" }
    Restituisce: { "id": "paypal_order_id" }
    """
    data = request.get_json()
    uid  = data.get("uid")

    if not uid:
        return jsonify({"error": "uid mancante"}), 400
    if not PAYPAL_CLIENT_ID:
        return jsonify({"error": "PayPal non configurato"}), 500

    try:
        token = paypal_get_access_token()
        r = requests.post(
            f"{PAYPAL_BASE_URL}/v2/checkout/orders",
            headers={
                "Authorization": f"Bearer {token}",
                "Content-Type": "application/json",
            },
            json={
                "intent": "CAPTURE",
                "purchase_units": [{
                    "amount": {"currency_code": "EUR", "value": PREMIUM_PRICE_EUR},
                    "description": "NBA Over Predictor Premium — 1 mese",
                    "custom_id": uid,  # passiamo l'uid qui per riconoscerlo al capture
                }],
            },
            timeout=10,
        )
        r.raise_for_status()
        return jsonify({"id": r.json()["id"]})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@payments_bp.route("/paypal/capture-order", methods=["POST"])
def paypal_capture_order():
    """
    Cattura il pagamento dopo che l'utente ha approvato su PayPal.
    Body JSON: { "order_id": "...", "uid": "firebase_uid" }
    """
    data     = request.get_json()
    order_id = data.get("order_id")
    uid      = data.get("uid")

    if not order_id or not uid:
        return jsonify({"error": "order_id o uid mancante"}), 400

    try:
        token = paypal_get_access_token()
        r = requests.post(
            f"{PAYPAL_BASE_URL}/v2/checkout/orders/{order_id}/capture",
            headers={
                "Authorization": f"Bearer {token}",
                "Content-Type": "application/json",
            },
            timeout=10,
        )
        r.raise_for_status()
        result = r.json()

        if result.get("status") == "COMPLETED":
            set_user_premium(uid)
            print(f"[PayPal] ✅ Premium attivato per uid={uid}, order={order_id}")
            return jsonify({"success": True, "status": "COMPLETED"})
        else:
            return jsonify({"success": False, "status": result.get("status")}), 400

    except Exception as e:
        return jsonify({"error": str(e)}), 500