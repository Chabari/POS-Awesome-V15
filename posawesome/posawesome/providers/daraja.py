"""M-Pesa Daraja provider for the POS Awesome ``Mobile Payment`` register.

Plugs into :mod:`posawesome.posawesome.api.mobile_payments` through the
``posawesome_payment_providers`` hook.

Credentials live in the payments app's ``Mpesa Settings`` (secret and passkey are
encrypted Password fields). A Mode of Payment is linked to it by setting
``payment_provider = "M-Pesa Daraja"`` and ``account_number`` = the number
customers pay to (the Paybill / Till, i.e. ``Mpesa Settings.till_number``).

* Paybill: ``business_shortcode == till_number`` -> ``CustomerPayBillOnline``.
* Buy Goods: ``business_shortcode`` is the store number, ``till_number`` the till
  -> ``CustomerBuyGoodsOnline``.

Site config (``bench --site <site> set-config``):

* ``daraja_callback_url``   public https base, e.g. ``https://pay.example.com``
* ``daraja_callback_token`` random secret appended to the STK callback URL

Callback paths deliberately avoid words Safaricom's URL filter rejects
("mpesa", "safaricom", "exe", "cmd", "sql", "query" ...).
"""

from __future__ import annotations

import base64
import hmac
import ipaddress
import json
import re

import frappe
import requests
from frappe import _
from frappe.utils import cint, cstr, flt, get_datetime, now_datetime
from werkzeug.wrappers import Response

PROVIDER = "M-Pesa Daraja"
SETTINGS_DOCTYPE = "Mpesa Settings"
LIVE_URL = "https://api.safaricom.co.ke"
SANDBOX_URL = "https://sandbox.safaricom.co.ke"
TIMEOUT = 30
MODULE_PATH = "posawesome.posawesome.providers.daraja"

# Safaricom's published Daraja callback source addresses (196.201.212.x - 196.201.214.x).
SAFARICOM_NETWORKS = tuple(ipaddress.ip_network(n) for n in ("196.201.212.0/23", "196.201.214.0/24"))


# ---------------------------------------------------------------------------
# Settings / auth
# ---------------------------------------------------------------------------


def _callback_base() -> str:
    return cstr(frappe.conf.get("daraja_callback_url")).strip().rstrip("/")


def _settings_for(account_number: str | None = None):
    names = frappe.get_all(
        SETTINGS_DOCTYPE,
        or_filters={"till_number": account_number, "business_shortcode": account_number} if account_number else None,
        pluck="name",
    )
    if not names:
        frappe.throw(_("No Mpesa Settings found for {0}.").format(account_number or _("this site")))
    return frappe.get_doc(SETTINGS_DOCTYPE, names[0])


def _base_url(settings) -> str:
    return SANDBOX_URL if cint(settings.sandbox) else LIVE_URL


def _shortcodes(settings) -> tuple[str, str, bool]:
    """(business_shortcode, party_b, is_paybill)."""
    party_b = cstr(settings.till_number).strip()
    business = party_b if cint(settings.sandbox) else (cstr(settings.business_shortcode).strip() or party_b)
    return business, party_b, business == party_b


def _token(settings) -> str:
    key = f"daraja_token:{settings.name}:{cstr(settings.consumer_key)[-6:]}"
    token = frappe.cache.get_value(key)
    if token:
        return token
    r = requests.get(
        f"{_base_url(settings)}/oauth/v1/generate?grant_type=client_credentials",
        auth=(settings.consumer_key, settings.get_password("consumer_secret")),
        timeout=TIMEOUT,
    )
    if r.status_code != 200:
        frappe.throw(_("M-Pesa authentication failed ({0}).").format(r.status_code))
    data = r.json()
    token = data["access_token"]
    frappe.cache.set_value(key, token, expires_in_sec=max(cint(data.get("expires_in")) - 300, 60))
    return token


def _post(settings, path: str, payload: dict) -> dict:
    r = requests.post(
        f"{_base_url(settings)}{path}",
        json=payload,
        headers={"Authorization": f"Bearer {_token(settings)}"},
        timeout=TIMEOUT,
    )
    try:
        return r.json()
    except ValueError:
        return {"errorCode": str(r.status_code), "errorMessage": r.text[:200]}


def _log(service: str, data: dict, output=None, error=None, remote=False, status=None):
    try:
        doc = frappe.get_doc(
            {
                "doctype": "Integration Request",
                "integration_request_service": f"{PROVIDER} {service}",
                "is_remote_request": 1 if remote else 0,
                "status": status or ("Failed" if error else "Completed"),
                "data": json.dumps(data, default=str),
                "output": json.dumps(output, default=str) if output is not None else None,
                "error": json.dumps(error, default=str) if error is not None else None,
            }
        )
        doc.insert(ignore_permissions=True)
    except Exception:
        frappe.log_error(title=f"{PROVIDER}: could not write integration log")


def normalise_phone(phone: str) -> str:
    digits = re.sub(r"\D", "", cstr(phone))
    if digits.startswith("0") and len(digits) == 10:
        digits = "254" + digits[1:]
    elif len(digits) == 9 and digits[0] in "17":
        digits = "254" + digits
    if not re.fullmatch(r"254[17]\d{8}", digits):
        frappe.throw(_("Enter a valid Safaricom number, e.g. 0712345678."))
    return digits


# ---------------------------------------------------------------------------
# Provider interface (used by mobile_payments)
# ---------------------------------------------------------------------------


def is_configured() -> bool:
    if not _callback_base().startswith("https://") or not frappe.conf.get("daraja_callback_token"):
        return False
    return bool(frappe.db.exists(SETTINGS_DOCTYPE, {"consumer_key": ["is", "set"]}))


def send_push(account_number, phone, amount, reference, description=None, **kwargs) -> dict:
    """Send an STK (M-Pesa Express) prompt. Returns ``{request_id, message}``."""
    if not is_configured():
        frappe.throw(_("M-Pesa callbacks are not configured on this site."))
    settings = _settings_for(account_number)
    business, party_b, is_paybill = _shortcodes(settings)
    phone = normalise_phone(phone)
    # Daraja limits: AccountReference 12 chars, TransactionDesc 13 chars.
    reference = cstr(reference)
    if len(reference) > 12:
        reference = reference[:2] + reference[-10:]
    timestamp = now_datetime().strftime("%Y%m%d%H%M%S")
    password = base64.b64encode(f"{business}{settings.get_password('online_passkey')}{timestamp}".encode()).decode()
    payload = {
        "BusinessShortCode": business,
        "Password": password,
        "Timestamp": timestamp,
        "TransactionType": "CustomerPayBillOnline" if is_paybill else "CustomerBuyGoodsOnline",
        "Amount": int(round(flt(amount))),
        "PartyA": phone,
        "PartyB": party_b,
        "PhoneNumber": phone,
        "CallBackURL": f"{_callback_base()}/api/method/{MODULE_PATH}.stk_result?t={frappe.conf.get('daraja_callback_token')}",
        "AccountReference": reference,
        "TransactionDesc": cstr(description or "Payment")[:13],
    }
    res = _post(settings, "/mpesa/stkpush/v1/processrequest", payload)
    safe = {k: v for k, v in payload.items() if k not in ("Password", "CallBackURL")}
    if cstr(res.get("ResponseCode")) != "0":
        _log("STK Push", safe, error=res)
        frappe.throw(cstr(res.get("errorMessage") or res.get("ResponseDescription") or res))
    _log("STK Push", safe, output=res, status="Queued")
    return {"request_id": res.get("CheckoutRequestID"), "message": res.get("CustomerMessage")}


# ---------------------------------------------------------------------------
# Callbacks (guest)
# ---------------------------------------------------------------------------


def _reply(code=0, desc="Accepted") -> Response:
    return Response(json.dumps({"ResultCode": code, "ResultDesc": desc}), mimetype="application/json")


def _from_safaricom() -> bool:
    try:
        ip = ipaddress.ip_address(cstr(getattr(frappe.local, "request_ip", "")))
    except ValueError:
        return False
    return any(ip in net for net in SAFARICOM_NETWORKS)


def _payload(kwargs) -> dict:
    return {k: v for k, v in kwargs.items() if k not in ("cmd", "t")}


@frappe.whitelist(allow_guest=True, methods=["POST"])
def stk_result(**kwargs):
    data = _payload(kwargs)
    token = cstr(frappe.conf.get("daraja_callback_token"))
    # JSON requests: Frappe's form_dict holds only the body, so read the query string directly.
    given = cstr(frappe.request.args.get("t") if getattr(frappe, "request", None) else None) or cstr(kwargs.get("t"))
    if not token or not hmac.compare_digest(given, token):
        _log("STK Result", {"ip": frappe.local.request_ip, "data": data}, error="bad token", remote=True)
        return _reply(1, "Rejected")
    try:
        cb = frappe._dict((data.get("Body") or {}).get("stkCallback") or {})
        meta = {i.get("Name"): i.get("Value") for i in ((cb.get("CallbackMetadata") or {}).get("Item") or [])}
        success = cint(cb.ResultCode) == 0 and cb.ResultCode is not None
        from posawesome.posawesome.api.mobile_payments import record_push_result

        name = record_push_result(
            PROVIDER,
            cstr(cb.CheckoutRequestID),
            success,
            receipt=meta.get("MpesaReceiptNumber"),
            amount=meta.get("Amount"),
            phone=cstr(meta.get("PhoneNumber")) or None,
            result=cstr(cb.ResultDesc),
        )
        _log("STK Result", {"ip": frappe.local.request_ip, "mobile_payment": name, "data": data}, remote=True)
        frappe.db.commit()
    except Exception:
        frappe.db.rollback()
        frappe.log_error(title=f"{PROVIDER}: STK result failed", message=frappe.get_traceback() + "\n" + json.dumps(data, default=str))
    return _reply()


@frappe.whitelist(allow_guest=True, methods=["POST"])
def c2b_validate(**kwargs):
    return Response(json.dumps({"ResultCode": "0", "ResultDesc": "Accepted"}), mimetype="application/json")


@frappe.whitelist(allow_guest=True, methods=["POST"])
def c2b_confirm(**kwargs):
    data = frappe._dict(_payload(kwargs))
    trusted = _from_safaricom()
    try:
        from posawesome.posawesome.api.mobile_payments import record_payment

        shortcode = cstr(data.BusinessShortCode).strip()
        billref = cstr(data.BillRefNumber).strip()
        msisdn = cstr(data.MSISDN)
        try:
            received = get_datetime(cstr(data.TransTime)) if data.TransTime else now_datetime()
        except Exception:
            received = now_datetime()
        name, created = record_payment(
            {
                "provider": PROVIDER,
                "receipt": cstr(data.TransID).strip(),
                "account_number": shortcode,
                "reference": f"{shortcode}-{billref}" if billref else shortcode,
                "amount": flt(data.TransAmount),
                "currency": "KES",
                # v2 sends a hashed MSISDN; keep it only if it is a real number.
                "phone": msisdn if re.fullmatch(r"\d{9,15}", msisdn) else None,
                "payer_name": " ".join(cstr(data.get(k)).strip() for k in ("FirstName", "MiddleName", "LastName") if data.get(k)),
                "received_at": received,
                "verified": 1 if trusted else 0,
            }
        )
        if created and not trusted:
            # Not from a Safaricom address: keep the record but hide it until reviewed.
            frappe.db.set_value(
                "Mobile Payment", name,
                {"is_hidden": 1, "remarks": _("Hidden: confirmation came from {0}, not a Safaricom address.").format(frappe.local.request_ip)},
            )
            frappe.log_error(title=f"{PROVIDER}: C2B from untrusted IP {frappe.local.request_ip}", message=json.dumps(data, default=str))
        _log("C2B", {"ip": frappe.local.request_ip, "mobile_payment": name, "data": data}, remote=True)
        frappe.db.commit()
    except Exception:
        frappe.db.rollback()
        frappe.log_error(title=f"{PROVIDER}: C2B confirmation failed", message=frappe.get_traceback() + "\n" + json.dumps(data, default=str))
    return _reply()


# ---------------------------------------------------------------------------
# Admin
# ---------------------------------------------------------------------------


@frappe.whitelist()
def register_c2b_urls(settings_name: str, dry_run: int = 1) -> dict:
    """Register the C2B confirmation/validation URLs for a shortcode (System Manager)."""
    frappe.only_for("System Manager")
    base = _callback_base()
    if not base.startswith("https://"):
        frappe.throw(_("Set daraja_callback_url to the public https address first."))
    settings = frappe.get_doc(SETTINGS_DOCTYPE, settings_name)
    business, party_b, _paybill = _shortcodes(settings)
    payload = {
        "ShortCode": party_b if _paybill else business,
        "ResponseType": "Completed",
        "ConfirmationURL": f"{base}/api/method/{MODULE_PATH}.c2b_confirm",
        "ValidationURL": f"{base}/api/method/{MODULE_PATH}.c2b_validate",
    }
    banned = re.compile(r"m-?_?pesa|safaricom|exe|cmd|sql|query", re.I)
    if banned.search(payload["ConfirmationURL"] + payload["ValidationURL"]):
        frappe.throw(_("Callback URL contains a word Safaricom rejects."))
    if cint(dry_run):
        return {"dry_run": True, "payload": payload}
    res = _post(settings, "/mpesa/c2b/v2/registerurl", payload)
    ok = cstr(res.get("ResponseCode")).strip("0") == "" and "ResponseCode" in res
    _log("C2B Register", payload, output=res, error=None if ok else res)
    return {"payload": payload, "response": res}
