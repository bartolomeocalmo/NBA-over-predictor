"""
payments.py — Stripe + PayPal integration per NBA Over Predictor
"""

import os
import json
import firebase_admin
from firebase_admin import credentials, firestore as admin_firestore
from flask import Blueprint, request, jsonify

payments_bp = Blueprint("payments", __name__)

# ============================================================
# FIREBASE ADMIN
# Legge le credenziali da variabili d'ambiente Railway:
#   FIREBASE_PROJECT_ID
#   FIREBASE_CLIENT_EMAIL
#   FIREBASE_PRIVATE_KEY
# ============================================================

def get_admin_db():
    if not firebase_admin._apps:
        # Costruisce il dict del service account dalle env var
        private_key = os.environ.get("FIREBASE_PRIVATE_KEY", "")
        # Railway a volte trasforma \n in \\n — lo sistemiamo
        private_key = private_key.replace("\\n", "\n")

        service_account = {
            "type": "service_account",
            "project_id":   os.environ.get("FIREBASE_PROJECT_ID", ""),
            "private_key":  private_key,
            "client_email": os.environ.get("FIREBASE_CLIENT_EMAIL", ""),
            "token_uri":    "https://oauth2.googleapis.com/token",
        }

        cred = credentials.Certificate(service_account)
        firebase_admin.initialize_app(cred)

    return admin_firestore.client()


def set_user_premium(uid: str):
    db = get_admin_db()
    db.collection("users").document(uid).set(
        {"plan": "premium", "plan_updated_at": admin_firestore.SERVER_TIMESTAMP},
        merge=True
    )


def set_user_free(uid: str):
    db = get_admin_db()
    db.collection("users").document(uid).set(
        {"plan": "free", "plan_updated_at": admin_firestore.SERVER_TIMESTAMP},
        merge=True
    )


# ============================================================
# STRIPE
# Variabili Railway da impostare:
#   STRIPE_SECRET_KEY=sk_live_... (o sk_test_... per i test)
#   STRIPE_PRICE_ID=price_...
#   STRIPE_WEBHOOK_SECRET=whsec_...
#   APP_URL=https://nbaoverpredictor.it
# ============================================================

try:
    import stripe
    stripe.api_key = os.environ.get("STRIPE_SECRET_KEY", "")
    STRIPE_ENABLED = bool(stripe.api_key)
except ImportError:
    STRIPE_ENABLED = False
    print("[payments] stripe non installato")

STRIPE_PRICE_ID       = os.environ.get("STRIPE_PRICE_ID", "")
STRIPE_WEBHOOK_SECRET = os.environ.get("STRIPE_WEBHOOK_SECRET", "")
APP_URL               = os.environ.get("APP_URL", "https://nbaoverpredictor.it")


@payments_bp.route("/stripe/create-checkout", methods=["POST"])
def stripe_create_checkout():
    if not STRIPE_ENABLED:
        return jsonify({"error": "Stripe non configurato"}), 500

    data  = request.get_json()
    uid   = data.get("uid")
    email = data.get("email", "")

    if not uid:
        return jsonify({"error": "uid mancante"}), 400

    try:
        session = stripe.checkout.Session.create(
            payment_method_types=["card"],
            mode="subscription",
            customer_email=email,
            line_items=[{"price": STRIPE_PRICE_ID, "quantity": 1}],
            success_url=f"{APP_URL}/premium.html?payment=success&provider=stripe",
            cancel_url=f"{APP_URL}/premium.html?payment=cancel",
            metadata={"uid": uid},
            locale="it",
        )
        return jsonify({"url": session.url})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@payments_bp.route("/webhook/stripe", methods=["POST"])
def stripe_webhook():
    if not STRIPE_ENABLED:
        return jsonify({"error": "Stripe non configurato"}), 500

    payload    = request.get_data()
    sig_header = request.headers.get("Stripe-Signature", "")

    try:
        event = stripe.Webhook.construct_event(payload, sig_header, STRIPE_WEBHOOK_SECRET)
    except Exception as e:
        return jsonify({"error": "Webhook non valido"}), 400

    event_type = event["type"]

    if event_type == "checkout.session.completed":
        uid = event["data"]["object"].get("metadata", {}).get("uid")
        if uid:
            set_user_premium(uid)
            print(f"[Stripe] ✅ Premium attivato uid={uid}")

    elif event_type == "customer.subscription.deleted":
        customer_id = event["data"]["object"].get("customer")
        if customer_id:
            try:
                db = get_admin_db()
                docs = db.collection("users").where("stripe_customer_id", "==", customer_id).stream()
                for doc in docs:
                    set_user_free(doc.id)
                    print(f"[Stripe] ⬇️ Piano Free ripristinato uid={doc.id}")
            except Exception as e:
                print(f"[Stripe] Errore: {e}")

    return jsonify({"received": True}), 200


# ============================================================
# PAYPAL
# Variabili Railway da impostare:
#   PAYPAL_CLIENT_ID=...
#   PAYPAL_SECRET=...
#   PAYPAL_ENV=live  (oppure sandbox per test)
# ============================================================

import requests as http_requests

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
    r = http_requests.post(
        f"{PAYPAL_BASE_URL}/v1/oauth2/token",
        auth=(PAYPAL_CLIENT_ID, PAYPAL_SECRET),
        data={"grant_type": "client_credentials"},
        timeout=10,
    )
    r.raise_for_status()
    return r.json()["access_token"]


@payments_bp.route("/paypal/create-order", methods=["POST"])
def paypal_create_order():
    if not PAYPAL_CLIENT_ID:
        return jsonify({"error": "PayPal non configurato"}), 500

    data = request.get_json()
    uid  = data.get("uid")
    if not uid:
        return jsonify({"error": "uid mancante"}), 400

    try:
        token = paypal_get_access_token()
        r = http_requests.post(
            f"{PAYPAL_BASE_URL}/v2/checkout/orders",
            headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
            json={
                "intent": "CAPTURE",
                "purchase_units": [{
                    "amount": {"currency_code": "EUR", "value": PREMIUM_PRICE_EUR},
                    "description": "NBA Over Predictor Premium — 1 mese",
                    "custom_id": uid,
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
    data     = request.get_json()
    order_id = data.get("order_id")
    uid      = data.get("uid")

    if not order_id or not uid:
        return jsonify({"error": "order_id o uid mancante"}), 400

    try:
        token = paypal_get_access_token()
        r = http_requests.post(
            f"{PAYPAL_BASE_URL}/v2/checkout/orders/{order_id}/capture",
            headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
            timeout=10,
        )
        r.raise_for_status()
        result = r.json()

        print(f"[PayPal] capture result: {result}")
        status = result.get("status")

        if status == "COMPLETED":
            set_user_premium(uid)
            print(f"[PayPal] ✅ Premium attivato uid={uid}, order={order_id}")
            return jsonify({"success": True})
        elif status in ("APPROVED", "CREATED"):
            # A volte sandbox restituisce APPROVED invece di COMPLETED
            # tentiamo un secondo capture
            print(f"[PayPal] status={status}, ritento capture...")
            r2 = http_requests.post(
                f"{PAYPAL_BASE_URL}/v2/checkout/orders/{order_id}/capture",
                headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
                timeout=10,
            )
            result2 = r2.json()
            print(f"[PayPal] secondo capture: {result2}")
            if result2.get("status") == "COMPLETED":
                set_user_premium(uid)
                print(f"[PayPal] ✅ Premium attivato uid={uid} (secondo tentativo)")
                return jsonify({"success": True})
            else:
                return jsonify({"success": False, "status": result2.get("status"), "detail": result2}), 400
        else:
            print(f"[PayPal] ❌ Status inatteso: {status}, body: {result}")
            return jsonify({"success": False, "status": status, "detail": result}), 400

    except Exception as e:
        print(f"[PayPal] ❌ Eccezione: {e}")
        return jsonify({"error": str(e)}), 500