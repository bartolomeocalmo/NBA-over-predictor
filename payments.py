"""
payments.py — Stripe + PayPal Subscriptions per NBA Over Predictor
"""

import os
import json
import hashlib
import hmac
import requests as http_requests
import firebase_admin
from firebase_admin import credentials, firestore as admin_firestore
from flask import Blueprint, request, jsonify

payments_bp = Blueprint("payments", __name__)

# ============================================================
# FIREBASE ADMIN
# ============================================================

def get_admin_db():
    if not firebase_admin._apps:
        private_key = os.environ.get("FIREBASE_PRIVATE_KEY", "")
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
# Variabili Railway: STRIPE_SECRET_KEY, STRIPE_PRICE_ID,
#                   STRIPE_WEBHOOK_SECRET, APP_URL
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
    except Exception:
        return jsonify({"error": "Webhook non valido"}), 400

    event_type = event["type"]
    if event_type == "checkout.session.completed":
        session_obj = event["data"]["object"]
        uid         = session_obj.get("metadata", {}).get("uid")
        customer_id = session_obj.get("customer")
        email       = session_obj.get("customer_email", "")
        if uid:
            set_user_premium(uid)
            # Salva customer_id per il portale di gestione abbonamento
            if customer_id:
                db = get_admin_db()
                db.collection("users").document(uid).set(
                    {"stripe_customer_id": customer_id, "email": email},
                    merge=True
                )
            print(f"[Stripe] ✅ Premium attivato uid={uid} customer={customer_id}")
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


@payments_bp.route("/stripe/customer-portal", methods=["POST"])
def stripe_customer_portal():
    if not STRIPE_ENABLED:
        return jsonify({"error": "Stripe non configurato"}), 500

    data  = request.get_json()
    uid   = data.get("uid")
    if not uid:
        return jsonify({"error": "uid mancante"}), 400

    try:
        # Recupera il customer_id Stripe da Firestore
        db = get_admin_db()
        snap = db.collection("users").document(uid).get()
        user_data = snap.to_dict() or {}
        customer_id = user_data.get("stripe_customer_id")

        # Se non abbiamo il customer_id salvato, cercalo su Stripe per email
        if not customer_id:
            # Cerca per email dell'utente su Stripe
            email = user_data.get("email", "")
            if not email:
                # Prova a recuperare l'email da Firebase Auth
                try:
                    import firebase_admin.auth as fb_auth
                    user_record = fb_auth.get_user(uid)
                    email = user_record.email or ""
                except Exception:
                    pass
            if email:
                customers = stripe.Customer.list(email=email, limit=5)
                if customers.data:
                    customer_id = customers.data[0].id
                    db.collection("users").document(uid).set(
                        {"stripe_customer_id": customer_id, "email": email}, merge=True
                    )
                    print(f"[Stripe portal] customer_id trovato per email={email}: {customer_id}")

        if not customer_id:
            return jsonify({"error": "Nessun abbonamento Stripe trovato. Hai sottoscritto con PayPal?"}), 404

        session = stripe.billing_portal.Session.create(
            customer=customer_id,
            return_url=f"{APP_URL}/?profile=open",
        )
        return jsonify({"url": session.url})

    except Exception as e:
        print(f"[Stripe portal] Errore: {e}")
        return jsonify({"error": str(e)}), 500


@payments_bp.route("/stripe/cancel-subscription", methods=["POST"])
def stripe_cancel_subscription():
    if not STRIPE_ENABLED:
        return jsonify({"error": "Stripe non configurato"}), 500

    data = request.get_json()
    uid  = data.get("uid")
    if not uid:
        return jsonify({"error": "uid mancante"}), 400

    try:
        db          = get_admin_db()
        snap        = db.collection("users").document(uid).get()
        user_data   = snap.to_dict() or {}
        customer_id = user_data.get("stripe_customer_id")

        # Fallback: cerca per email
        if not customer_id:
            email = user_data.get("email", "")
            if not email:
                try:
                    import firebase_admin.auth as fb_auth
                    user_record = fb_auth.get_user(uid)
                    email = user_record.email or ""
                except Exception:
                    pass
            if email:
                customers = stripe.Customer.list(email=email, limit=1)
                if customers.data:
                    customer_id = customers.data[0].id
                    db.collection("users").document(uid).set(
                        {"stripe_customer_id": customer_id, "email": email}, merge=True
                    )

        if not customer_id:
            return jsonify({"error": "Nessun abbonamento Stripe trovato"}), 404

        # Trova la subscription attiva e cancellala a fine periodo
        subscriptions = stripe.Subscription.list(customer=customer_id, status="active", limit=1)
        if not subscriptions.data:
            return jsonify({"error": "Nessuna sottoscrizione attiva trovata"}), 404

        sub = subscriptions.data[0]
        stripe.Subscription.modify(sub.id, cancel_at_period_end=True)
        set_user_free(uid)
        print(f"[Stripe] ⬇️ Cancellazione pianificata uid={uid} sub={sub.id}")
        return jsonify({"success": True})

    except Exception as e:
        print(f"[Stripe cancel] Errore: {e}")
        return jsonify({"error": str(e)}), 500


# ============================================================
# PAYPAL SUBSCRIPTIONS
# Variabili Railway: PAYPAL_CLIENT_ID, PAYPAL_SECRET,
#                   PAYPAL_PLAN_ID, PAYPAL_WEBHOOK_ID,
#                   PAYPAL_ENV (live | sandbox)
# ============================================================

PAYPAL_CLIENT_ID  = os.environ.get("PAYPAL_CLIENT_ID", "")
PAYPAL_SECRET     = os.environ.get("PAYPAL_SECRET", "")
PAYPAL_PLAN_ID    = os.environ.get("PAYPAL_PLAN_ID", "")
PAYPAL_WEBHOOK_ID = os.environ.get("PAYPAL_WEBHOOK_ID", "")
PAYPAL_ENV        = os.environ.get("PAYPAL_ENV", "sandbox")
PAYPAL_BASE_URL   = (
    "https://api-m.paypal.com"
    if PAYPAL_ENV == "live"
    else "https://api-m.sandbox.paypal.com"
)


def paypal_get_access_token():
    r = http_requests.post(
        f"{PAYPAL_BASE_URL}/v1/oauth2/token",
        auth=(PAYPAL_CLIENT_ID, PAYPAL_SECRET),
        data={"grant_type": "client_credentials"},
        timeout=10,
    )
    r.raise_for_status()
    return r.json()["access_token"]


def paypal_headers():
    return {
        "Authorization": f"Bearer {paypal_get_access_token()}",
        "Content-Type": "application/json",
    }


@payments_bp.route("/paypal/create-subscription", methods=["POST"])
def paypal_create_subscription():
    if not PAYPAL_CLIENT_ID or not PAYPAL_PLAN_ID:
        return jsonify({"error": "PayPal non configurato"}), 500
    data = request.get_json()
    uid  = data.get("uid")
    if not uid:
        return jsonify({"error": "uid mancante"}), 400
    try:
        r = http_requests.post(
            f"{PAYPAL_BASE_URL}/v1/billing/subscriptions",
            headers=paypal_headers(),
            json={
                "plan_id": PAYPAL_PLAN_ID,
                "custom_id": uid,
                "application_context": {
                    "brand_name": "NBA Over Predictor",
                    "locale": "it-IT",
                    "shipping_preference": "NO_SHIPPING",
                    "user_action": "SUBSCRIBE_NOW",
                    "return_url": f"{APP_URL}/premium.html?payment=success&provider=paypal",
                    "cancel_url": f"{APP_URL}/premium.html?payment=cancel",
                },
            },
            timeout=15,
        )
        r.raise_for_status()
        sub = r.json()
        approval_url = next(
            (link["href"] for link in sub.get("links", []) if link["rel"] == "approve"),
            None
        )
        if not approval_url:
            return jsonify({"error": "approval_url non trovato"}), 500
        return jsonify({"subscription_id": sub["id"], "approval_url": approval_url})
    except Exception as e:
        print(f"[PayPal create-subscription] {e}")
        return jsonify({"error": str(e)}), 500


@payments_bp.route("/paypal/verify-subscription", methods=["POST"])
def paypal_verify_subscription():
    if not PAYPAL_CLIENT_ID:
        return jsonify({"error": "PayPal non configurato"}), 500
    data   = request.get_json()
    uid    = data.get("uid")
    sub_id = data.get("subscription_id")
    if not uid or not sub_id:
        return jsonify({"error": "uid o subscription_id mancante"}), 400
    try:
        r = http_requests.get(
            f"{PAYPAL_BASE_URL}/v1/billing/subscriptions/{sub_id}",
            headers=paypal_headers(),
            timeout=10,
        )
        r.raise_for_status()
        sub    = r.json()
        status = sub.get("status")
        print(f"[PayPal verify] sub_id={sub_id} status={status} uid={uid}")
        if status == "ACTIVE":
            set_user_premium(uid)
            db = get_admin_db()
            db.collection("users").document(uid).set(
                {"paypal_subscription_id": sub_id}, merge=True
            )
            print(f"[PayPal] ✅ Premium attivato uid={uid}")
            return jsonify({"success": True})
        return jsonify({"success": False, "status": status})
    except Exception as e:
        print(f"[PayPal verify-subscription] {e}")
        return jsonify({"error": str(e)}), 500


@payments_bp.route("/paypal/cancel-subscription", methods=["POST"])
def paypal_cancel_subscription():
    if not PAYPAL_CLIENT_ID:
        return jsonify({"error": "PayPal non configurato"}), 500
    data   = request.get_json()
    uid    = data.get("uid")
    sub_id = data.get("subscription_id")
    if not uid or not sub_id:
        return jsonify({"error": "uid o subscription_id mancante"}), 400
    try:
        r = http_requests.post(
            f"{PAYPAL_BASE_URL}/v1/billing/subscriptions/{sub_id}/cancel",
            headers=paypal_headers(),
            json={"reason": "Cancellato dall'utente tramite NBA Over Predictor"},
            timeout=10,
        )
        if r.status_code in (200, 204):
            set_user_free(uid)
            db = get_admin_db()
            db.collection("users").document(uid).set(
                {"paypal_subscription_id": None}, merge=True
            )
            print(f"[PayPal] ⬇️ Abbonamento cancellato uid={uid}")
            return jsonify({"success": True})
        return jsonify({"error": f"PayPal ha risposto {r.status_code}: {r.text}"}), 500
    except Exception as e:
        print(f"[PayPal cancel-subscription] {e}")
        return jsonify({"error": str(e)}), 500


@payments_bp.route("/webhook/paypal", methods=["POST"])
def paypal_webhook():
    if not PAYPAL_CLIENT_ID:
        return jsonify({"error": "PayPal non configurato"}), 500
    payload    = request.get_json(force=True)
    event_type = payload.get("event_type", "")
    resource   = payload.get("resource", {})
    print(f"[PayPal Webhook] event={event_type}")

    # Verifica firma
    if PAYPAL_WEBHOOK_ID:
        try:
            verify = http_requests.post(
                f"{PAYPAL_BASE_URL}/v1/notifications/verify-webhook-signature",
                headers=paypal_headers(),
                json={
                    "auth_algo":         request.headers.get("PAYPAL-AUTH-ALGO", ""),
                    "cert_url":          request.headers.get("PAYPAL-CERT-URL", ""),
                    "transmission_id":   request.headers.get("PAYPAL-TRANSMISSION-ID", ""),
                    "transmission_sig":  request.headers.get("PAYPAL-TRANSMISSION-SIG", ""),
                    "transmission_time": request.headers.get("PAYPAL-TRANSMISSION-TIME", ""),
                    "webhook_id":        PAYPAL_WEBHOOK_ID,
                    "webhook_event":     payload,
                },
                timeout=10,
            )
            if verify.json().get("verification_status") != "SUCCESS":
                return jsonify({"error": "Firma non valida"}), 400
        except Exception as e:
            print(f"[PayPal Webhook] Errore verifica: {e}")

    if event_type == "BILLING.SUBSCRIPTION.ACTIVATED":
        uid    = resource.get("custom_id")
        sub_id = resource.get("id")
        if uid:
            set_user_premium(uid)
            db = get_admin_db()
            db.collection("users").document(uid).set(
                {"paypal_subscription_id": sub_id}, merge=True
            )
            print(f"[PayPal Webhook] ✅ Premium uid={uid}")

    elif event_type in (
        "BILLING.SUBSCRIPTION.CANCELLED",
        "BILLING.SUBSCRIPTION.SUSPENDED",
        "BILLING.SUBSCRIPTION.EXPIRED",
    ):
        uid    = resource.get("custom_id")
        sub_id = resource.get("id")
        if uid:
            set_user_free(uid)
            print(f"[PayPal Webhook] ⬇️ Free uid={uid} ({event_type})")
        elif sub_id:
            try:
                db = get_admin_db()
                docs = db.collection("users").where("paypal_subscription_id", "==", sub_id).stream()
                for doc in docs:
                    set_user_free(doc.id)
                    print(f"[PayPal Webhook] ⬇️ Free uid={doc.id} (lookup)")
            except Exception as e:
                print(f"[PayPal Webhook] Errore lookup: {e}")

    return jsonify({"received": True}), 200