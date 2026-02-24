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
        return jsonify({"error": str(e)}), 50# ============================================================
# PAYPAL SUBSCRIPTIONS
# Variabili Railway da impostare:
#   PAYPAL_CLIENT_ID=...
#   PAYPAL_SECRET=...
#   PAYPAL_PLAN_ID=P-...   (crea un Plan su PayPal Developer Dashboard)
#   PAYPAL_ENV=live         (oppure sandbox per test)
#   PAYPAL_WEBHOOK_ID=...   (ID del webhook registrato su PayPal Dashboard)
# ============================================================

import requests as http_requests

PAYPAL_CLIENT_ID = os.environ.get("PAYPAL_CLIENT_ID", "")
PAYPAL_SECRET    = os.environ.get("PAYPAL_SECRET", "")
PAYPAL_PLAN_ID   = os.environ.get("PAYPAL_PLAN_ID", "")
PAYPAL_WEBHOOK_ID = os.environ.get("PAYPAL_WEBHOOK_ID", "")
PAYPAL_ENV       = os.environ.get("PAYPAL_ENV", "sandbox")
PAYPAL_BASE_URL  = (
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


@payments_bp.route("/paypal/create-subscription", methods=["POST"])
def paypal_create_subscription():
    """Crea un abbonamento ricorrente PayPal e restituisce l'approval URL."""
    if not PAYPAL_CLIENT_ID or not PAYPAL_PLAN_ID:
        return jsonify({"error": "PayPal non configurato"}), 500

    data = request.get_json()
    uid  = data.get("uid")
    if not uid:
        return jsonify({"error": "uid mancante"}), 400

    try:
        token = paypal_get_access_token()
        r = http_requests.post(
            f"{PAYPAL_BASE_URL}/v1/billing/subscriptions",
            headers={
                "Authorization": f"Bearer {token}",
                "Content-Type": "application/json",
                "Prefer": "return=representation",
            },
            json={
                "plan_id": PAYPAL_PLAN_ID,
                "custom_id": uid,                    # salviamo l'uid qui per i webhook
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

        # Trova il link di approvazione
        approval_url = next(
            (link["href"] for link in sub.get("links", []) if link["rel"] == "approve"),
            None
        )
        if not approval_url:
            return jsonify({"error": "approval URL non trovato"}), 500

        return jsonify({
            "subscription_id": sub["id"],
            "approval_url": approval_url,
        })

    except Exception as e:
        print(f"[PayPal] create-subscription error: {e}")
        return jsonify({"error": str(e)}), 500


@payments_bp.route("/paypal/activate-subscription", methods=["POST"])
def paypal_activate_subscription():
    """Chiamato dal frontend dopo che l'utente ha approvato su PayPal.
       Verifica lo stato e aggiorna Firestore."""
    data            = request.get_json()
    subscription_id = data.get("subscription_id")
    uid             = data.get("uid")

    if not subscription_id or not uid:
        return jsonify({"error": "dati mancanti"}), 400

    try:
        token = paypal_get_access_token()
        r = http_requests.get(
            f"{PAYPAL_BASE_URL}/v1/billing/subscriptions/{subscription_id}",
            headers={"Authorization": f"Bearer {token}"},
            timeout=10,
        )
        r.raise_for_status()
        sub = r.json()

        status = sub.get("status")
        if status in ("ACTIVE", "APPROVED"):
            # Salva anche l'id abbonamento per gestire la cancellazione futura
            db = get_admin_db()
            db.collection("users").document(uid).set(
                {
                    "plan": "premium",
                    "paypal_subscription_id": subscription_id,
                    "plan_updated_at": admin_firestore.SERVER_TIMESTAMP,
                },
                merge=True,
            )
            print(f"[PayPal] ✅ Premium attivato uid={uid}, sub={subscription_id}")
            return jsonify({"success": True})
        else:
            print(f"[PayPal] ❌ Status inatteso: {status}")
            return jsonify({"success": False, "status": status}), 400

    except Exception as e:
        print(f"[PayPal] activate error: {e}")
        return jsonify({"error": str(e)}), 500


@payments_bp.route("/webhook/paypal", methods=["POST"])
def paypal_webhook():
    """Gestisce eventi PayPal: attivazione, rinnovo fallito, cancellazione."""
    if not PAYPAL_CLIENT_ID:
        return jsonify({"error": "PayPal non configurato"}), 500

    payload = request.get_json(force=True)
    event_type = payload.get("event_type", "")
    resource   = payload.get("resource", {})

    print(f"[PayPal webhook] event: {event_type}")

    # Abbonamento attivato (primo pagamento ok)
    if event_type == "BILLING.SUBSCRIPTION.ACTIVATED":
        uid = resource.get("custom_id")
        sub_id = resource.get("id")
        if uid:
            db = get_admin_db()
            db.collection("users").document(uid).set(
                {
                    "plan": "premium",
                    "paypal_subscription_id": sub_id,
                    "plan_updated_at": admin_firestore.SERVER_TIMESTAMP,
                },
                merge=True,
            )
            print(f"[PayPal] ✅ ACTIVATED uid={uid}")

    # Abbonamento cancellato dall'utente o scaduto
    elif event_type in (
        "BILLING.SUBSCRIPTION.CANCELLED",
        "BILLING.SUBSCRIPTION.EXPIRED",
        "BILLING.SUBSCRIPTION.SUSPENDED",
    ):
        uid    = resource.get("custom_id")
        sub_id = resource.get("id")
        if uid:
            set_user_free(uid)
            print(f"[PayPal] ⬇️ Free ripristinato uid={uid} ({event_type})")
        else:
            # fallback: cerca per subscription_id in Firestore
            if sub_id:
                try:
                    db = get_admin_db()
                    docs = db.collection("users")\
                             .where("paypal_subscription_id", "==", sub_id)\
                             .stream()
                    for doc in docs:
                        set_user_free(doc.id)
                        print(f"[PayPal] ⬇️ Free ripristinato uid={doc.id} (lookup)")
                except Exception as e:
                    print(f"[PayPal] webhook lookup error: {e}")

    # Pagamento rinnovo fallito — non togliamo subito il premium, PayPal riprova
    elif event_type == "BILLING.SUBSCRIPTION.PAYMENT.FAILED":
        uid = resource.get("custom_id")
        print(f"[PayPal] ⚠️ Pagamento fallito uid={uid} — PayPal riproverà automaticamente")

    return jsonify({"received": True}), 200


@payments_bp.route("/paypal/cancel-subscription", methods=["POST"])
def paypal_cancel_subscription():
    """Cancella l'abbonamento PayPal dal lato server (chiamato dall'in-app)."""
    data = request.get_json()
    uid  = data.get("uid")
    if not uid:
        return jsonify({"error": "uid mancante"}), 400

    try:
        db  = get_admin_db()
        doc = db.collection("users").document(uid).get()
        if not doc.exists:
            return jsonify({"error": "utente non trovato"}), 404

        sub_id = doc.to_dict().get("paypal_subscription_id")
        if not sub_id:
            return jsonify({"error": "nessun abbonamento PayPal attivo"}), 400

        token = paypal_get_access_token()
        r = http_requests.post(
            f"{PAYPAL_BASE_URL}/v1/billing/subscriptions/{sub_id}/cancel",
            headers={
                "Authorization": f"Bearer {token}",
                "Content-Type": "application/json",
            },
            json={"reason": "Cancellazione richiesta dall'utente"},
            timeout=10,
        )

        if r.status_code == 204:  # No Content = successo
            set_user_free(uid)
            print(f"[PayPal] ✅ Abbonamento cancellato uid={uid}")
            return jsonify({"success": True})
        else:
            print(f"[PayPal] cancel error {r.status_code}: {r.text}")
            return jsonify({"error": f"PayPal ha risposto con {r.status_code}"}), 500

    except Exception as e:
        print(f"[PayPal] cancel exception: {e}")
        return jsonify({"error": str(e)}), 500# ============================================================
# PAYPAL SUBSCRIPTIONS
# Variabili Railway da impostare:
#   PAYPAL_CLIENT_ID=...
#   PAYPAL_SECRET=...
#   PAYPAL_PLAN_ID=P-... (crea il piano su developer.paypal.com o via setup_paypal_plan.py)
#   PAYPAL_WEBHOOK_ID=... (ID del webhook su PayPal Developer Dashboard)
#   PAYPAL_ENV=live  (oppure sandbox per test)
# ============================================================

import requests as http_requests
import hashlib, hmac, base64

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


def paypal_auth_headers():
    return {
        "Authorization": f"Bearer {paypal_get_access_token()}",
        "Content-Type": "application/json",
    }


# ── Crea abbonamento ricorrente ──────────────────────────
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
            headers=paypal_auth_headers(),
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

        # Trova il link di approvazione
        approval_url = next(
            (link["href"] for link in sub.get("links", []) if link["rel"] == "approve"),
            None,
        )
        if not approval_url:
            return jsonify({"error": "Approvazione URL non trovata"}), 500

        # Salva subscription_id pending su Firestore
        db = get_admin_db()
        db.collection("users").document(uid).set(
            {"paypal_subscription_id": sub["id"], "paypal_sub_status": "pending"},
            merge=True,
        )

        return jsonify({"approval_url": approval_url, "subscription_id": sub["id"]})

    except Exception as e:
        print(f"[PayPal] create-subscription errore: {e}")
        return jsonify({"error": str(e)}), 500


# ── Verifica abbonamento dopo il ritorno da PayPal ───────
@payments_bp.route("/paypal/verify-subscription", methods=["POST"])
def paypal_verify_subscription():
    data            = request.get_json()
    uid             = data.get("uid")
    subscription_id = data.get("subscription_id")

    if not uid or not subscription_id:
        return jsonify({"error": "uid o subscription_id mancante"}), 400

    try:
        r = http_requests.get(
            f"{PAYPAL_BASE_URL}/v1/billing/subscriptions/{subscription_id}",
            headers=paypal_auth_headers(),
            timeout=10,
        )
        r.raise_for_status()
        sub = r.json()

        status    = sub.get("status")       # ACTIVE, APPROVAL_PENDING, CANCELLED...
        custom_id = sub.get("custom_id")    # deve corrispondere al uid

        if custom_id != uid:
            return jsonify({"error": "uid non corrispondente"}), 403

        if status == "ACTIVE":
            set_user_premium(uid)
            db = get_admin_db()
            db.collection("users").document(uid).set(
                {"paypal_subscription_id": subscription_id, "paypal_sub_status": "active"},
                merge=True,
            )
            print(f"[PayPal] ✅ Premium attivato uid={uid}, sub={subscription_id}")
            return jsonify({"success": True, "status": status})
        else:
            return jsonify({"success": False, "status": status}), 400

    except Exception as e:
        print(f"[PayPal] verify-subscription errore: {e}")
        return jsonify({"error": str(e)}), 500


# ── Cancella abbonamento (richiesta dall'utente in-app) ──
@payments_bp.route("/paypal/cancel-subscription", methods=["POST"])
def paypal_cancel_subscription():
    data = request.get_json()
    uid  = data.get("uid")
    if not uid:
        return jsonify({"error": "uid mancante"}), 400

    try:
        db   = get_admin_db()
        doc  = db.collection("users").document(uid).get()
        sub_id = doc.to_dict().get("paypal_subscription_id") if doc.exists else None

        if not sub_id:
            return jsonify({"error": "Nessun abbonamento PayPal trovato"}), 404

        r = http_requests.post(
            f"{PAYPAL_BASE_URL}/v1/billing/subscriptions/{sub_id}/cancel",
            headers=paypal_auth_headers(),
            json={"reason": "Cancellazione richiesta dall'utente"},
            timeout=10,
        )

        if r.status_code == 204:  # No Content = successo
            set_user_free(uid)
            db.collection("users").document(uid).set(
                {"paypal_sub_status": "cancelled"},
                merge=True,
            )
            print(f"[PayPal] ⬇️ Abbonamento cancellato uid={uid}, sub={sub_id}")
            return jsonify({"success": True})
        else:
            return jsonify({"error": f"PayPal status {r.status_code}", "detail": r.text}), 500

    except Exception as e:
        print(f"[PayPal] cancel-subscription errore: {e}")
        return jsonify({"error": str(e)}), 500


# ── Webhook PayPal (eventi ricorrenti) ───────────────────
@payments_bp.route("/webhook/paypal", methods=["POST"])
def paypal_webhook():
    if not PAYPAL_CLIENT_ID:
        return jsonify({"error": "PayPal non configurato"}), 500

    event      = request.get_json(force=True)
    event_type = event.get("event_type", "")
    resource   = event.get("resource", {})

    print(f"[PayPal Webhook] {event_type}")

    if event_type == "BILLING.SUBSCRIPTION.ACTIVATED":
        uid = resource.get("custom_id")
        sub_id = resource.get("id")
        if uid:
            set_user_premium(uid)
            db = get_admin_db()
            db.collection("users").document(uid).set(
                {"paypal_subscription_id": sub_id, "paypal_sub_status": "active"},
                merge=True,
            )
            print(f"[PayPal] ✅ Webhook: Premium attivato uid={uid}")

    elif event_type in ("BILLING.SUBSCRIPTION.CANCELLED", "BILLING.SUBSCRIPTION.SUSPENDED",
                         "BILLING.SUBSCRIPTION.EXPIRED"):
        # Cerca l'utente per subscription_id
        sub_id = resource.get("id")
        if sub_id:
            try:
                db   = get_admin_db()
                docs = db.collection("users").where("paypal_subscription_id", "==", sub_id).stream()
                for doc in docs:
                    set_user_free(doc.id)
                    db.collection("users").document(doc.id).set(
                        {"paypal_sub_status": "cancelled"},
                        merge=True,
                    )
                    print(f"[PayPal] ⬇️ Webhook: Piano Free ripristinato uid={doc.id}")
            except Exception as e:
                print(f"[PayPal] Webhook errore: {e}")

    elif event_type == "PAYMENT.SALE.COMPLETED":
        # Rinnovo mensile riuscito — assicurati che sia ancora premium
        sub_id = resource.get("billing_agreement_id")
        if sub_id:
            try:
                db   = get_admin_db()
                docs = db.collection("users").where("paypal_subscription_id", "==", sub_id).stream()
                for doc in docs:
                    set_user_premium(doc.id)
                    print(f"[PayPal] 🔄 Rinnovo ok uid={doc.id}")
            except Exception as e:
                print(f"[PayPal] Webhook rinnovo errore: {e}")

    return jsonify({"received": True}), 200# ============================================================
# PAYPAL SUBSCRIPTIONS
# Variabili Railway da impostare:
#   PAYPAL_CLIENT_ID=...
#   PAYPAL_SECRET=...
#   PAYPAL_PLAN_ID=P-...   ← crea un Piano su PayPal Developer Dashboard
#   PAYPAL_WEBHOOK_ID=...  ← ID webhook da PayPal Developer Dashboard
#   PAYPAL_ENV=live        (oppure sandbox per test)
# ============================================================

import requests as http_requests
import hashlib
import hmac

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


# ── Crea abbonamento ricorrente ───────────────────────────────────────────
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
                "custom_id": uid,                         # salviamo uid nel campo custom
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

        # Trova il link di approvazione
        approval_url = next(
            (link["href"] for link in sub.get("links", []) if link["rel"] == "approve"),
            None
        )
        if not approval_url:
            return jsonify({"error": "approval_url non trovato"}), 500

        return jsonify({
            "subscription_id": sub["id"],
            "approval_url": approval_url,
        })

    except Exception as e:
        print(f"[PayPal create-subscription] Errore: {e}")
        return jsonify({"error": str(e)}), 500


# ── Verifica abbonamento dopo redirect ────────────────────────────────────
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
        sub = r.json()
        status = sub.get("status")

        print(f"[PayPal verify] sub_id={sub_id} status={status} uid={uid}")

        if status == "ACTIVE":
            set_user_premium(uid)
            # Salva subscription_id in Firestore per poter cancellare in futuro
            db = get_admin_db()
            db.collection("users").document(uid).set(
                {"paypal_subscription_id": sub_id},
                merge=True
            )
            print(f"[PayPal] ✅ Premium attivato uid={uid} sub={sub_id}")
            return jsonify({"success": True})
        else:
            # APPROVAL_PENDING = utente non ha ancora approvato su PayPal
            return jsonify({"success": False, "status": status})

    except Exception as e:
        print(f"[PayPal verify-subscription] Errore: {e}")
        return jsonify({"error": str(e)}), 500


# ── Cancella abbonamento dall'app ─────────────────────────────────────────
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
        # PayPal ritorna 204 No Content in caso di successo
        if r.status_code in (200, 204):
            set_user_free(uid)
            db = get_admin_db()
            db.collection("users").document(uid).set(
                {"paypal_subscription_id": None},
                merge=True
            )
            print(f"[PayPal] ⬇️ Abbonamento cancellato uid={uid} sub={sub_id}")
            return jsonify({"success": True})
        else:
            return jsonify({"error": f"PayPal ha risposto {r.status_code}: {r.text}"}), 500

    except Exception as e:
        print(f"[PayPal cancel-subscription] Errore: {e}")
        return jsonify({"error": str(e)}), 500


# ── Webhook PayPal ────────────────────────────────────────────────────────
@payments_bp.route("/webhook/paypal", methods=["POST"])
def paypal_webhook():
    if not PAYPAL_CLIENT_ID:
        return jsonify({"error": "PayPal non configurato"}), 500

    payload    = request.get_json(force=True)
    event_type = payload.get("event_type", "")
    resource   = payload.get("resource", {})

    print(f"[PayPal Webhook] event={event_type}")

    # Verifica webhook signature tramite PayPal API
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
            verification = verify.json().get("verification_status")
            if verification != "SUCCESS":
                print(f"[PayPal Webhook] ⚠️ Verifica fallita: {verification}")
                return jsonify({"error": "Firma non valida"}), 400
        except Exception as e:
            print(f"[PayPal Webhook] Errore verifica: {e}")

    # Abbonamento attivato (es. dopo primo pagamento)
    if event_type == "BILLING.SUBSCRIPTION.ACTIVATED":
        sub_id = resource.get("id")
        uid    = resource.get("custom_id")
        if uid:
            set_user_premium(uid)
            db = get_admin_db()
            db.collection("users").document(uid).set(
                {"paypal_subscription_id": sub_id},
                merge=True
            )
            print(f"[PayPal Webhook] ✅ Premium attivato uid={uid}")

    # Abbonamento cancellato / sospeso / scaduto
    elif event_type in (
        "BILLING.SUBSCRIPTION.CANCELLED",
        "BILLING.SUBSCRIPTION.SUSPENDED",
        "BILLING.SUBSCRIPTION.EXPIRED",
    ):
        sub_id = resource.get("id")
        uid    = resource.get("custom_id")
        if uid:
            set_user_free(uid)
            print(f"[PayPal Webhook] ⬇️ Piano Free uid={uid} ({event_type})")
        elif sub_id:
            # Fallback: cerca per subscription_id se custom_id non presente
            try:
                db = get_admin_db()
                docs = db.collection("users").where("paypal_subscription_id", "==", sub_id).stream()
                for doc in docs:
                    set_user_free(doc.id)
                    print(f"[PayPal Webhook] ⬇️ Piano Free uid={doc.id} (lookup by sub_id)")
            except Exception as e:
                print(f"[PayPal Webhook] Errore lookup: {e}")

    return jsonify({"received": True}), 200