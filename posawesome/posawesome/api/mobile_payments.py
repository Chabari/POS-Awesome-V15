"""Mobile-money payments (KCB Buni today, M-Pesa Daraja later) for POS Awesome.

Every receipt is one ``Mobile Payment`` row. Providers live in their own apps and
plug in through the ``posawesome_payment_providers`` hook::

    posawesome_payment_providers = {"KCB Buni": "kcbbuni.pos_provider"}

A provider module exposes ``is_configured()`` and ``send_push(**kwargs)`` and
calls :func:`record_payment` / :func:`record_push_result` from its callbacks.

A Mode of Payment is *integrated* only when it has both ``account_number`` and
``payment_provider``. With no integrated mode every hook here returns at once,
so sites without an integration (e.g. Victoria) are unaffected.

Rules carried over from the M-Pesa design (MPESA_JOURNEY_AND_KCB_BUNI_PORT.md §13):

* one receipt, one row - UNIQUE ``receipt`` is the dedupe gate (I1, I2);
* a confirmed push belongs to its sale until used: ``push_invoice`` never changes (I3);
* payments are consumed in the invoice's own submit transaction (I4);
* releases are single guarded UPDATEs (I5);
* one shared check for the till, ``before_submit`` and background submits (I9);
* time cutoffs are computed in Python, never with SQL ``NOW()`` (I11);
* closing a push dialog hides the push, it never fails it (I13);
* a failed push is detached at once and ignored everywhere (I15).
"""

from __future__ import annotations

import json
import random
import re
import string

import frappe
from frappe import _
from frappe.utils import add_to_date, cint, cstr, flt, get_datetime, getdate, now_datetime

from posawesome.utils import has_field

DOCTYPE = "Mobile Payment"
PROVIDER_HOOK = "posawesome_payment_providers"
AMOUNT_TOLERANCE = 0.05
ABANDONED_DRAFT_MINUTES = 15
PUSH_EXPIRY_HOURS = 2
DEFAULT_TILL_WINDOW_HOURS = 3
MANAGER_ROLES = ("Sales Manager", "Accounts Manager", "System Manager")

EVENT_RECEIVED = "mobile_payment_received"
EVENT_PUSH_RESULT = "mobile_push_result"


# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------


def _ready() -> bool:
    """The register exists on this site (it may not before a migrate)."""
    return bool(frappe.db.table_exists(DOCTYPE))


def get_integrated_modes(company: str | None = None) -> dict:
    """``{mode_of_payment: {provider, account_number}}`` for integrated, enabled modes.

    With ``company`` only modes that have an account for that company count.
    Cached per request: it is read by every invoice validation.
    """
    if not (has_field("Mode of Payment", "payment_provider") and has_field("Mode of Payment", "account_number")):
        return {}

    cache = getattr(frappe.local, "posa_integrated_modes", None)
    if cache is None:
        cache = frappe.local.posa_integrated_modes = {}
    key = company or ""
    if key in cache:
        return cache[key]

    rows = frappe.db.sql(
        """
        select mop.name, mop.payment_provider, mop.account_number
        from `tabMode of Payment` mop
        where mop.enabled = 1
          and ifnull(mop.payment_provider, '') != ''
          and ifnull(mop.account_number, '') != ''
          {company_clause}
        """.format(
            company_clause=(
                "and exists (select 1 from `tabMode of Payment Account` a"
                " where a.parent = mop.name and a.company = %(company)s)"
                if company
                else ""
            )
        ),
        {"company": company},
        as_dict=True,
    )
    result = {
        r.name: frappe._dict(provider=r.payment_provider, account_number=cstr(r.account_number).strip())
        for r in rows
    }
    cache[key] = result
    return result


def _till_window_hours() -> float:
    return flt(frappe.conf.get("mobile_payment_till_window_hours")) or DEFAULT_TILL_WINDOW_HOURS


def _provider_module(provider: str):
    paths = (frappe.get_hooks(PROVIDER_HOOK) or {}).get(provider) or []
    if not paths:
        return None
    path = paths[-1] if isinstance(paths, list) else paths
    try:
        return frappe.get_module(path)
    except Exception:
        frappe.log_error(title=f"Mobile payment provider {provider} failed to load")
        return None


def _can_push(provider: str) -> bool:
    module = _provider_module(provider)
    if not module or not hasattr(module, "send_push"):
        return False
    try:
        return bool(module.is_configured()) if hasattr(module, "is_configured") else True
    except Exception:
        return False


def _integrated_mode(mode_of_payment: str, company: str | None) -> frappe._dict:
    mode = get_integrated_modes(company).get(mode_of_payment)
    if not mode:
        frappe.throw(_("{0} is not set up for automatic payments.").format(mode_of_payment))
    return mode


def _mode_for_account(provider: str, account_number: str | None) -> tuple[str | None, str | None]:
    """(mode_of_payment, company) for an account number, if exactly one mode claims it."""
    if not account_number:
        return None, None
    matches = [m for m, v in get_integrated_modes().items() if v.provider == provider and v.account_number == account_number]
    if len(matches) != 1:
        return None, None
    companies = frappe.get_all(
        "Mode of Payment Account", filters={"parent": matches[0]}, pluck="company"
    )
    return matches[0], (companies[0] if len(companies) == 1 else None)


def _is_manager() -> bool:
    return bool(set(MANAGER_ROLES) & set(frappe.get_roles()))


def _mask_phone(phone: str | None) -> str:
    digits = re.sub(r"\D", "", cstr(phone))
    return f"•••••{digits[-3:]}" if len(digits) >= 3 else ""


def _publish(event: str, message: dict, user: str | None = None) -> None:
    try:
        frappe.publish_realtime(event, message, user=user, after_commit=True)
    except Exception:
        # Realtime is a convenience; the dialogs also poll.
        pass


# ---------------------------------------------------------------------------
# Inbound: called by provider apps
# ---------------------------------------------------------------------------


def record_payment(data: dict, commit: bool = False) -> tuple[str | None, bool]:
    """Record an unprompted payment (C2B / IPN). Returns ``(name, created)``.

    ``data`` keys: provider, receipt, bank_reference, account_number, reference,
    amount, currency, phone, payer_name, received_at, verified, source_doctype,
    source_name.
    """
    if not _ready():
        return None, False

    data = frappe._dict(data)
    receipt = cstr(data.receipt).strip()
    if not receipt:
        frappe.throw(_("A mobile payment needs a receipt number."))

    existing = frappe.db.get_value(
        DOCTYPE, {"receipt": receipt}, ["name", "payment_type", "status", "bank_reference"], as_dict=True
    )
    if existing:
        # Our own push, mirrored back by the provider as a notification (I2).
        if existing.payment_type == "STK":
            if data.bank_reference and not existing.bank_reference:
                frappe.db.set_value(DOCTYPE, existing.name, "bank_reference", data.bank_reference, update_modified=False)
            if existing.status != "Success":
                _apply_push_success(existing.name, receipt=receipt, amount=data.amount, phone=data.phone,
                                    payer_name=data.payer_name, result=_("Confirmed by payment notification"))
        if commit:
            frappe.db.commit()
        return existing.name, False

    mirror_of = _find_push_for_mirror(data)
    if mirror_of:
        _apply_push_success(mirror_of, receipt=receipt, amount=data.amount, phone=data.phone,
                            payer_name=data.payer_name, result=_("Confirmed by payment notification"),
                            bank_reference=data.bank_reference)
        if commit:
            frappe.db.commit()
        return mirror_of, False

    mode, company = _mode_for_account(data.provider, data.account_number)
    doc = frappe.get_doc(
        {
            "doctype": DOCTYPE,
            "provider": data.provider,
            "payment_type": "C2B",
            "status": "Success",
            "receipt": receipt,
            "bank_reference": data.bank_reference,
            "account_number": data.account_number,
            "reference": cstr(data.reference)[:140],
            "amount": flt(data.amount),
            "currency": data.currency or "KES",
            "phone": data.phone,
            "payer_name": cstr(data.payer_name)[:140],
            "received_at": data.received_at or now_datetime(),
            "company": company,
            "mode_of_payment": mode,
            "verified": cint(data.verified),
            "source_doctype": data.source_doctype,
            "source_name": data.source_name,
        }
    )
    frappe.db.savepoint("mobile_payment_insert")
    try:
        doc.insert(ignore_permissions=True)
    except (frappe.UniqueValidationError, frappe.DuplicateEntryError):
        # A concurrent retry of the same notification won (I1).
        frappe.db.rollback(save_point="mobile_payment_insert")
        return frappe.db.get_value(DOCTYPE, {"receipt": receipt}, "name"), False

    if data.account_number:
        _publish(EVENT_RECEIVED, {"name": doc.name, "account_number": data.account_number, "amount": doc.amount})
    if commit:
        frappe.db.commit()
    return doc.name, True


def record_push_result(
    provider: str,
    request_id: str,
    success: bool,
    receipt: str | None = None,
    amount: float | None = None,
    phone: str | None = None,
    payer_name: str | None = None,
    result: str | None = None,
    commit: bool = False,
) -> str | None:
    """Apply a push (STK) result. Returns the Mobile Payment name, or None if unknown."""
    if not (_ready() and request_id):
        return None

    name = frappe.db.get_value(DOCTYPE, {"provider": provider, "push_request_id": request_id}, "name")
    if not name:
        return None

    if success:
        _apply_push_success(name, receipt=receipt, amount=amount, phone=phone, payer_name=payer_name, result=result)
    else:
        _apply_push_failure(name, result)

    if commit:
        frappe.db.commit()
    return name


def _find_push_for_mirror(data: frappe._dict) -> str | None:
    """A notification for one of our pushes whose callback has not been applied yet."""
    reference = cstr(data.reference).strip()
    if not reference or not data.account_number or reference == data.account_number:
        return None
    rows = frappe.db.sql(
        """
        select name from `tabMobile Payment`
        where payment_type = 'STK' and provider = %(provider)s and reference = %(reference)s
          and status in ('Pending', 'Failed') and receipt is null and consumed = 0
          and abs(amount - %(amount)s) <= %(tol)s and received_at >= %(since)s
        order by received_at desc limit 1
        """,
        {
            "provider": data.provider,
            "reference": reference,
            "amount": flt(data.amount),
            "tol": AMOUNT_TOLERANCE,
            "since": add_to_date(now_datetime(), days=-2),
        },
    )
    return rows[0][0] if rows else None


def _apply_push_success(name, receipt=None, amount=None, phone=None, payer_name=None, result=None, bank_reference=None):
    row = frappe.db.sql(
        f"select * from `tab{DOCTYPE}` where name = %s for update", name, as_dict=True
    )[0]
    receipt = cstr(receipt).strip() or None
    updates = {
        "status": "Success",
        "is_hidden": 0,
        "push_result": cstr(result)[:140] or _("Paid"),
        "push_result_at": now_datetime(),
    }
    if amount:
        updates["amount"] = flt(amount)
    if phone:
        updates["phone"] = phone
    if payer_name:
        updates["payer_name"] = cstr(payer_name)[:140]
    if bank_reference and not row.bank_reference:
        updates["bank_reference"] = bank_reference

    if receipt and receipt != row.receipt:
        holder = frappe.db.get_value(
            DOCTYPE, {"receipt": receipt, "name": ["!=", name]},
            ["name", "payment_type", "consumed", "sales_invoice", "payment_entry"], as_dict=True,
        )
        if holder and holder.payment_type == "C2B" and not (holder.consumed or holder.sales_invoice or holder.payment_entry):
            # The mirror notification beat the callback; the push row wins (I2).
            frappe.delete_doc(DOCTYPE, holder.name, ignore_permissions=True, force=True)
            holder = None
        if holder:
            # The same money is already recorded and in use elsewhere: never let
            # it become usable twice.
            updates.update(is_hidden=1, remarks=_("Receipt {0} is already recorded as {1}.").format(receipt, holder.name))
            frappe.log_error(
                title="Mobile payment: push receipt already in use",
                message=f"{name} paid with receipt {receipt}, already held by {holder.name}.",
            )
        else:
            updates["receipt"] = receipt

    # A till push pays for the sale it was sent for.
    if (
        row.push_context == "Till"
        and not row.sales_invoice
        and not row.consumed
        and not updates.get("is_hidden")
        and row.push_invoice
        and frappe.db.get_value("Sales Invoice", row.push_invoice, "docstatus") == 0
    ):
        updates.update(
            sales_invoice=row.push_invoice, attached_at=now_datetime(), attached_by=row.push_initiated_by
        )

    frappe.db.set_value(DOCTYPE, name, updates)
    _publish_push(name)


def _apply_push_failure(name, result=None):
    row = frappe.db.sql(
        f"select name, status, consumed from `tab{DOCTYPE}` where name = %s for update", name, as_dict=True
    )[0]
    if row.consumed or row.status == "Success":
        # A failure after a success would erase real money: keep the success.
        frappe.log_error(
            title="Mobile payment: failure reported for a settled push",
            message=f"{name} is {row.status} (consumed={row.consumed}); ignored failure: {result}",
        )
        return
    frappe.db.set_value(
        DOCTYPE,
        name,
        {
            "status": "Failed",
            "is_hidden": 1,
            "sales_invoice": None,
            "attached_at": None,
            "attached_by": None,
            "push_result": cstr(result)[:140] or _("Failed"),
            "push_result_at": now_datetime(),
        },
    )
    _publish_push(name)


def _publish_push(name):
    row = frappe.db.get_value(
        DOCTYPE, name,
        ["name", "status", "receipt", "amount", "push_invoice", "sales_invoice", "push_initiated_by",
         "push_result", "mode_of_payment", "payer_name"],
        as_dict=True,
    )
    if row and row.push_initiated_by:
        _publish(EVENT_PUSH_RESULT, dict(row), user=row.push_initiated_by)


# ---------------------------------------------------------------------------
# Eligibility
# ---------------------------------------------------------------------------


def _till_candidates(invoice: str, account_number: str, amount=None, exact=True, names=None) -> list:
    """Payments the till may attach to ``invoice`` (draft) on ``account_number``."""
    now = now_datetime()
    params = {
        "invoice": invoice,
        "account": account_number,
        "cutoff": add_to_date(now, hours=-_till_window_hours()),
        "today": get_datetime(getdate(now)),
        "abandoned": add_to_date(now, minutes=-ABANDONED_DRAFT_MINUTES),
        "amount": flt(amount),
        "tol": AMOUNT_TOLERANCE,
    }
    printed = "ifnull(si.posa_is_printed, 0) = 0" if has_field("Sales Invoice", "posa_is_printed") else "1=1"
    conditions = []
    if amount is not None:
        conditions.append(
            "abs(mp.amount - %(amount)s) <= %(tol)s" if exact else "mp.amount <= %(amount)s + %(tol)s"
        )
    if names:
        params["names"] = tuple(names)
        conditions.append("mp.name in %(names)s")

    return frappe.db.sql(
        f"""
        select mp.name, mp.receipt, mp.amount, mp.payer_name, mp.phone, mp.received_at,
               mp.payment_type, mp.provider, mp.account_number,
               coalesce(mp.sales_invoice, mp.push_invoice) as holder
        from `tabMobile Payment` mp
        left join `tabSales Invoice` si on si.name = coalesce(mp.sales_invoice, mp.push_invoice)
        where mp.account_number = %(account)s
          and mp.status = 'Success' and mp.consumed = 0 and mp.is_hidden = 0
          and (
                (mp.released_from is null and mp.received_at >= %(cutoff)s)
             or (mp.released_from_pos = 1 and mp.received_at >= %(today)s)
             or coalesce(mp.sales_invoice, mp.push_invoice) = %(invoice)s
          )
          and (
                coalesce(mp.sales_invoice, mp.push_invoice) is null
             or coalesce(mp.sales_invoice, mp.push_invoice) = %(invoice)s
             or si.name is null
             or si.docstatus = 2
             or (si.docstatus = 0 and {printed} and si.modified < %(abandoned)s)
          )
          {"and " + " and ".join(conditions) if conditions else ""}
        order by mp.received_at desc
        limit 50
        """,
        params,
        as_dict=True,
    )


def _serialize_payment(row, invoice=None) -> dict:
    return {
        "name": row.name,
        "receipt": row.receipt,
        "amount": flt(row.amount),
        "payer_name": row.payer_name,
        "phone": _mask_phone(row.phone),
        "received_at": cstr(row.received_at),
        "payment_type": row.payment_type,
        "provider": row.provider,
        "attached_here": bool(invoice and row.get("holder") == invoice),
    }


def _till_invoice(invoice: str):
    if not invoice or not frappe.db.exists("Sales Invoice", invoice):
        frappe.throw(_("Save the sale before taking a mobile payment."))
    doc = frappe.get_doc("Sales Invoice", invoice)
    doc.check_permission("write")
    if doc.docstatus != 0:
        frappe.throw(_("Invoice {0} is already submitted.").format(invoice))
    return doc


# ---------------------------------------------------------------------------
# Till API
# ---------------------------------------------------------------------------


@frappe.whitelist()
def get_mobile_modes(pos_profile: str | None = None, company: str | None = None) -> list:
    if not _ready():
        return []
    if not company and pos_profile:
        company = frappe.db.get_value("POS Profile", pos_profile, "company")
    return [
        {
            "mode_of_payment": mode,
            "provider": cfg.provider,
            "account_number": cfg.account_number,
            "can_push": _can_push(cfg.provider),
        }
        for mode, cfg in get_integrated_modes(company).items()
    ]


@frappe.whitelist()
def find_payments(invoice: str, mode_of_payment: str, amount=None, exact=1) -> dict:
    doc = _till_invoice(invoice)
    mode = _integrated_mode(mode_of_payment, doc.company)
    rows = _till_candidates(invoice, mode.account_number, amount=amount, exact=cint(exact))
    return {
        "account_number": mode.account_number,
        "window_hours": _till_window_hours(),
        "payments": [_serialize_payment(r, invoice) for r in rows],
    }


@frappe.whitelist()
def attach_payments(invoice: str, mode_of_payment: str, payments, amount) -> dict:
    """Attach exactly these payments to ``mode_of_payment`` on the draft ``invoice``.

    Replaces what was attached on that mode. Their total must equal ``amount``.
    """
    doc = _till_invoice(invoice)
    mode = _integrated_mode(mode_of_payment, doc.company)
    names = json.loads(payments) if isinstance(payments, str) else list(payments or [])
    names = sorted({cstr(n) for n in names if n})
    if not names:
        frappe.throw(_("Select a payment."))

    frappe.db.sql(f"select name from `tab{DOCTYPE}` where name in %s for update", (tuple(names),))
    eligible = _till_candidates(invoice, mode.account_number, names=names)
    missing = set(names) - {r.name for r in eligible}
    if missing:
        frappe.throw(
            _("Payment {0} is no longer available. It may have been used on another sale. Search again.").format(
                ", ".join(sorted(missing))
            )
        )

    total = sum(flt(r.amount) for r in eligible)
    if abs(total - flt(amount)) > AMOUNT_TOLERANCE:
        frappe.throw(
            _("The selected payments total {0}, but {1} needs exactly {2}.").format(
                frappe.format_value(total, {"fieldtype": "Currency"}),
                mode_of_payment,
                frappe.format_value(flt(amount), {"fieldtype": "Currency"}),
            )
        )

    # Replace: release what this mode held on this invoice and is no longer wanted.
    frappe.db.sql(
        f"""update `tab{DOCTYPE}` set sales_invoice = null, attached_at = null, attached_by = null
            where sales_invoice = %s and mode_of_payment = %s and consumed = 0 and name not in %s""",
        (invoice, mode_of_payment, tuple(names)),
    )
    now = now_datetime()
    for name in names:
        frappe.db.set_value(
            DOCTYPE,
            name,
            {
                "sales_invoice": invoice,
                "mode_of_payment": mode_of_payment,
                "company": doc.company,
                "attached_at": now,
                "attached_by": frappe.session.user,
            },
        )
    return get_attached(invoice)


@frappe.whitelist()
def detach_payment(invoice: str, payment: str) -> dict:
    _till_invoice(invoice)
    # One guarded statement (I5): only an unconsumed row held by this draft.
    frappe.db.sql(
        f"""update `tab{DOCTYPE}` set sales_invoice = null, attached_at = null, attached_by = null
            where name = %s and sales_invoice = %s and consumed = 0""",
        (payment, invoice),
    )
    return get_attached(invoice)


@frappe.whitelist()
def get_attached(invoice: str) -> dict:
    """Payments attached to ``invoice`` per mode, plus confirmed pushes for it not yet attached."""
    if not (_ready() and invoice):
        return {"attached": {}, "unclaimed": []}
    frappe.has_permission("Sales Invoice", "read", invoice, throw=True)

    attached = {}
    for row in frappe.get_all(
        DOCTYPE,
        filters={"sales_invoice": invoice, "status": "Success", "is_hidden": 0},
        fields=["name", "receipt", "amount", "payer_name", "phone", "received_at", "payment_type",
                "provider", "mode_of_payment", "account_number"],
        order_by="attached_at asc",
    ):
        attached.setdefault(row.mode_of_payment, []).append(_serialize_payment(row))

    unclaimed = [
        dict(_serialize_payment(row), mode_of_payment=row.mode_of_payment)
        for row in frappe.get_all(
            DOCTYPE,
            filters={"push_invoice": invoice, "status": "Success", "consumed": 0, "is_hidden": 0,
                     "sales_invoice": ["is", "not set"]},
            fields=["name", "receipt", "amount", "payer_name", "phone", "received_at", "payment_type",
                    "provider", "mode_of_payment"],
        )
    ]
    return {"attached": attached, "unclaimed": unclaimed}


@frappe.whitelist()
def get_push_context(invoice: str) -> dict:
    customer = frappe.db.get_value("Sales Invoice", invoice, "customer")
    phone = ""
    if customer:
        phone = frappe.db.get_value("Customer", customer, "mobile_no") or ""
    return {"phone": phone}


def _push_reference(invoice: str) -> str:
    """Short, unique per push: ``SI<digits><3 chars>``; KCB prefixes the account."""
    digits = re.sub(r"\D", "", invoice)[-10:]
    suffix = "".join(random.choices(string.ascii_uppercase + string.digits, k=3))
    return f"SI{digits}{suffix}"


@frappe.whitelist()
def initiate_push(invoice: str, mode_of_payment: str, phone: str, amount, context: str = "Till") -> dict:
    """Send a payment prompt to the customer's phone for ``invoice``."""
    if context not in ("Till", "Desk"):
        frappe.throw(_("Invalid push context."))
    doc = frappe.get_doc("Sales Invoice", invoice)
    if context == "Till":
        doc = _till_invoice(invoice)
    else:
        doc.check_permission("read")
        if not frappe.has_permission("Payment Entry", "create"):
            frappe.throw(_("You are not allowed to collect payments."), frappe.PermissionError)
        if doc.docstatus != 1 or flt(amount) > flt(doc.outstanding_amount) + AMOUNT_TOLERANCE:
            frappe.throw(_("The amount cannot be more than the outstanding {0}.").format(doc.outstanding_amount))

    mode = _integrated_mode(mode_of_payment, doc.company)
    amount = flt(amount)
    if amount <= 0 or abs(amount - round(amount)) > 0.001:
        frappe.throw(_("Mobile payments are in whole shillings. Adjust the amount to {0}.").format(round(amount)))
    module = _provider_module(mode.provider)
    if not module or not _can_push(mode.provider):
        frappe.throw(_("{0} cannot send payment prompts on this site.").format(mode.provider))

    row = frappe.get_doc(
        {
            "doctype": DOCTYPE,
            "provider": mode.provider,
            "payment_type": "STK",
            "status": "Pending",
            "account_number": mode.account_number,
            "reference": f"{mode.account_number}-{_push_reference(invoice)}",
            "amount": amount,
            "phone": phone,
            "company": doc.company,
            "mode_of_payment": mode_of_payment,
            "push_invoice": invoice,
            "push_context": context,
            "push_initiated_by": frappe.session.user,
            "received_at": now_datetime(),
        }
    ).insert(ignore_permissions=True)
    # The row exists before any money can move (I3).
    frappe.db.commit()

    try:
        result = module.send_push(
            account_number=mode.account_number,
            phone=phone,
            amount=amount,
            reference=row.reference.split("-", 1)[1],
            description="INV" + re.sub(r"\D", "", invoice)[-9:],
        )
    except Exception as exc:
        frappe.db.rollback()
        frappe.db.set_value(
            DOCTYPE, row.name,
            {"status": "Failed", "is_hidden": 1, "push_result": cstr(exc)[:140], "push_result_at": now_datetime()},
        )
        frappe.db.commit()
        frappe.throw(_("The payment prompt could not be sent: {0}").format(cstr(exc)))

    frappe.db.set_value(DOCTYPE, row.name, "push_request_id", result.get("request_id"))
    frappe.db.commit()
    return {"name": row.name, "status": "Pending", "message": result.get("message") or ""}


@frappe.whitelist()
def get_push_status(name: str) -> dict:
    row = frappe.db.get_value(
        DOCTYPE, name,
        ["name", "status", "receipt", "amount", "push_result", "sales_invoice", "push_invoice",
         "push_initiated_by", "payer_name", "mode_of_payment", "consumed", "payment_entry"],
        as_dict=True,
    )
    if not row:
        frappe.throw(_("Unknown payment {0}").format(name))
    if row.push_initiated_by != frappe.session.user:
        frappe.has_permission("Sales Invoice", "read", row.push_invoice, throw=True)
    return row


@frappe.whitelist()
def cancel_push(name: str) -> None:
    """The cashier closed the dialog. Hide the push; never mark it Failed (I13)."""
    frappe.db.sql(
        f"update `tab{DOCTYPE}` set is_hidden = 1 where name = %s and status = 'Pending'", name
    )


# ---------------------------------------------------------------------------
# The gate (I9): one check for the till, before_submit and background submits
# ---------------------------------------------------------------------------


def get_attachment_errors(doc) -> list[str]:
    modes = get_integrated_modes(doc.company)
    if not modes or not _ready():
        return []

    charged = {}
    errors = []
    for payment in doc.get("payments") or []:
        if payment.mode_of_payment not in modes or not flt(payment.amount):
            continue
        if doc.doctype != "Sales Invoice":
            errors.append(_("{0} can only be used on Sales Invoices.").format(payment.mode_of_payment))
            continue
        if doc.get("is_return") or flt(payment.amount) < 0:
            errors.append(_("Refunds cannot be paid out through {0}. Use cash.").format(payment.mode_of_payment))
            continue
        charged[payment.mode_of_payment] = charged.get(payment.mode_of_payment, 0) + flt(payment.amount)

    if doc.doctype != "Sales Invoice" or doc.is_new():
        if charged:
            errors.append(_("Save the sale before taking a mobile payment."))
        return errors

    attached = {}
    for row in frappe.get_all(
        DOCTYPE,
        filters={"sales_invoice": doc.name, "status": "Success", "is_hidden": 0},
        fields=["name", "receipt", "amount", "mode_of_payment"],
    ):
        attached.setdefault(row.mode_of_payment, []).append(row)

    for mode, amount in charged.items():
        rows = attached.get(mode) or []
        total = sum(flt(r.amount) for r in rows)
        if not rows:
            errors.append(
                _("{0}: no payment is attached. Use the {0} button to find the customer's payment or send a prompt.").format(mode)
            )
        elif abs(total - amount) > AMOUNT_TOLERANCE:
            errors.append(
                _("{0}: the attached payments total {1}, but the sale charges {2}.").format(
                    mode, frappe.format_value(total, {"fieldtype": "Currency"}),
                    frappe.format_value(amount, {"fieldtype": "Currency"}),
                )
            )

    for mode, rows in attached.items():
        if mode not in charged:
            errors.append(
                _("Payment {0} is attached to this sale but not charged on {1}. Charge it or remove it.").format(
                    ", ".join(cstr(r.receipt or r.name) for r in rows), mode or _("a mobile payment mode")
                )
            )

    # The customer already paid this sale by prompt (I8).
    for row in frappe.get_all(
        DOCTYPE,
        filters={"push_invoice": doc.name, "status": "Success", "consumed": 0, "is_hidden": 0,
                 "sales_invoice": ["is", "not set"]},
        fields=["receipt", "amount", "mode_of_payment"],
    ):
        errors.append(
            _("The customer already paid {0} by phone prompt (receipt {1}) for this sale. Use it from the {2} button.").format(
                frappe.format_value(row.amount, {"fieldtype": "Currency"}), row.receipt, row.mode_of_payment
            )
        )
    return errors


def stamp_references(doc) -> None:
    """Write the receipts into ``reference_no`` of each integrated payment row."""
    modes = get_integrated_modes(doc.company)
    if not modes or doc.doctype != "Sales Invoice" or doc.is_new() or not _ready():
        return
    receipts = {}
    for row in frappe.get_all(
        DOCTYPE,
        filters={"sales_invoice": doc.name, "status": "Success", "is_hidden": 0},
        fields=["receipt", "name", "mode_of_payment"],
        order_by="attached_at asc",
    ):
        receipts.setdefault(row.mode_of_payment, []).append(row.receipt or row.name)
    for payment in doc.get("payments") or []:
        if payment.mode_of_payment in modes and flt(payment.amount) > 0:
            payment.reference_no = ", ".join(receipts.get(payment.mode_of_payment) or [])[:140]


def validate_mobile_payments(doc) -> None:
    errors = get_attachment_errors(doc)
    if errors:
        frappe.throw("<br>".join(errors), title=_("Mobile payment not complete"))
    stamp_references(doc)


# ---------------------------------------------------------------------------
# doc_events
# ---------------------------------------------------------------------------


def before_submit(doc, method=None):
    validate_mobile_payments(doc)


def on_submit(doc, method=None):
    """Consume in the invoice's own transaction: if the submit fails, so does this (I4)."""
    if doc.doctype != "Sales Invoice" or not _ready():
        return
    frappe.db.sql(
        f"""update `tab{DOCTYPE}` set consumed = 1, consumed_at = %s
            where sales_invoice = %s and consumed = 0 and status = 'Success'""",
        (now_datetime(), doc.name),
    )


def on_cancel(doc, method=None):
    """A cancelled sale gives its payments back (released_from_pos limits till reuse to that day)."""
    if doc.doctype != "Sales Invoice" or not _ready():
        return
    frappe.db.sql(
        f"""update `tab{DOCTYPE}`
            set consumed = 0, consumed_at = null, sales_invoice = null, attached_at = null, attached_by = null,
                released_from = %s, released_from_pos = %s, released_at = %s
            where sales_invoice = %s and ifnull(payment_entry, '') = ''""",
        (doc.name, cint(doc.is_pos), now_datetime(), doc.name),
    )


def on_payment_entry_cancel(doc, method=None):
    if not _ready():
        return
    frappe.db.sql(
        f"""update `tab{DOCTYPE}`
            set consumed = 0, consumed_at = null, sales_invoice = null, payment_entry = null,
                attached_at = null, attached_by = null,
                released_from = %s, released_from_pos = 0, released_at = %s
            where payment_entry = %s""",
        (doc.name, now_datetime(), doc.name),
    )


# ---------------------------------------------------------------------------
# Scheduler
# ---------------------------------------------------------------------------


def expire_stale_pushes():
    """Pending pushes with no answer: Failed after PUSH_EXPIRY_HOURS (a late mirror still revives them)."""
    if not _ready():
        return
    now = now_datetime()
    frappe.db.sql(
        f"""update `tab{DOCTYPE}` set status = 'Failed', is_hidden = 1, sales_invoice = null,
                push_result = 'No answer from provider', push_result_at = %(now)s
            where payment_type = 'STK' and status = 'Pending' and consumed = 0
              and (received_at < %(expiry)s or (ifnull(push_request_id, '') = '' and received_at < %(unsent)s))""",
        {"now": now, "expiry": add_to_date(now, hours=-PUSH_EXPIRY_HOURS), "unsent": add_to_date(now, minutes=-10)},
    )
    frappe.db.commit()
