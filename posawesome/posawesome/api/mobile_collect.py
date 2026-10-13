"""Collect a mobile payment against submitted Sales Invoices from the desk.

"Collect Payment" on a submitted invoice with an outstanding balance: send a
phone prompt, or pick a payment the customer already made (any amount). One
collection can be split across several of the customer's outstanding invoices;
anything left over stays on the Payment Entry as an unallocated advance.

The Payment Entry posts to the Mode of Payment's own account (never the company
default bank - MPESA doc I10) and carries the receipts in ``reference_no``.
"""

from __future__ import annotations

import json

import frappe
from erpnext.accounts.doctype.payment_entry.payment_entry import get_payment_entry
from erpnext.accounts.doctype.sales_invoice.sales_invoice import get_bank_cash_account
from erpnext.accounts.utils import get_account_currency
from frappe import _
from frappe.utils import add_to_date, cint, cstr, flt, get_datetime, getdate, now_datetime

from posawesome.posawesome.api.mobile_payments import (
    AMOUNT_TOLERANCE,
    DOCTYPE,
    _integrated_mode,
    _is_manager,
    _mask_phone,
    _can_push,
    _ready,
    get_integrated_modes,
)
from posawesome.utils import has_field

DEFAULT_DESK_WINDOW_DAYS = 30
ORPHAN_GRACE_MINUTES = 3


def _desk_window_days() -> int:
    return cint(frappe.conf.get("mobile_payment_desk_window_days")) or DEFAULT_DESK_WINDOW_DAYS


def _collectable_invoice(invoice: str):
    doc = frappe.get_doc("Sales Invoice", invoice)
    doc.check_permission("read")
    if doc.docstatus != 1:
        frappe.throw(_("Submit the invoice before collecting payment."))
    if doc.is_return:
        frappe.throw(_("Payments cannot be collected on a return."))
    if flt(doc.outstanding_amount) <= 0:
        frappe.throw(_("Invoice {0} has nothing outstanding.").format(invoice))
    return doc


def _check_collect_permission():
    if not frappe.has_permission("Payment Entry", "create"):
        frappe.throw(_("You are not allowed to collect payments."), frappe.PermissionError)


@frappe.whitelist()
def get_collect_context(invoice: str) -> dict:
    if not _ready():
        return {"modes": []}
    doc = frappe.get_doc("Sales Invoice", invoice)
    doc.check_permission("read")
    modes = get_integrated_modes(doc.company)
    if not modes or doc.docstatus != 1 or doc.is_return or flt(doc.outstanding_amount) <= 0:
        return {"modes": []}

    default_mode = None
    if doc.pos_profile:
        profile_modes = frappe.get_all(
            "POS Payment Method", filters={"parent": doc.pos_profile}, pluck="mode_of_payment"
        )
        default_mode = next((m for m in profile_modes if m in modes), None)

    others = frappe.get_all(
        "Sales Invoice",
        filters={
            "customer": doc.customer,
            "company": doc.company,
            "docstatus": 1,
            "is_return": 0,
            "outstanding_amount": [">", 0],
            "name": ["!=", doc.name],
        },
        fields=["name", "posting_date", "outstanding_amount", "grand_total"],
        order_by="posting_date asc, name asc",
        limit=50,
    )
    return {
        "modes": [
            {"mode_of_payment": m, "account_number": cfg.account_number, "provider": cfg.provider,
             "can_push": _can_push(cfg.provider)}
            for m, cfg in modes.items()
        ],
        "default_mode": default_mode or next(iter(modes)),
        "outstanding": flt(doc.outstanding_amount),
        "customer": doc.customer,
        "customer_name": doc.customer_name,
        "phone": frappe.db.get_value("Customer", doc.customer, "mobile_no") or "",
        "other_invoices": others,
        "is_manager": _is_manager(),
        "can_collect": bool(frappe.has_permission("Payment Entry", "create")),
        "window_days": _desk_window_days(),
    }


def _desk_candidates(invoice: str, account_number: str, names=None) -> list:
    """Unused payments on an account that the desk may collect for ``invoice``."""
    now = now_datetime()
    params = {
        "invoice": invoice,
        "account": account_number,
        "since": add_to_date(now, days=-_desk_window_days()),
        "today": get_datetime(getdate(now)),
        "abandoned": add_to_date(now, minutes=-15),
        "manager": 1 if _is_manager() else 0,
    }
    printed = "ifnull(si.posa_is_printed, 0) = 0" if has_field("Sales Invoice", "posa_is_printed") else "1=1"
    names_clause = ""
    if names:
        params["names"] = tuple(names)
        names_clause = "and mp.name in %(names)s"
    return frappe.db.sql(
        f"""
        select mp.name, mp.receipt, mp.amount, mp.payer_name, mp.phone, mp.received_at,
               mp.payment_type, mp.provider, mp.released_from,
               coalesce(mp.sales_invoice, mp.push_invoice) as holder
        from `tabMobile Payment` mp
        left join `tabSales Invoice` si on si.name = coalesce(mp.sales_invoice, mp.push_invoice)
        where mp.account_number = %(account)s
          and mp.status = 'Success' and mp.consumed = 0 and mp.is_hidden = 0
          and ifnull(mp.payment_entry, '') = ''
          and (mp.received_at >= %(since)s or coalesce(mp.sales_invoice, mp.push_invoice) = %(invoice)s)
          and (
                mp.released_from is null
             or (mp.released_from_pos = 1 and mp.received_at >= %(today)s)
             or (mp.released_from_pos = 0 and %(manager)s = 1)
          )
          and (
                coalesce(mp.sales_invoice, mp.push_invoice) is null
             or coalesce(mp.sales_invoice, mp.push_invoice) = %(invoice)s
             or si.name is null
             or si.docstatus = 2
             or (si.docstatus = 0 and {printed} and si.modified < %(abandoned)s)
          )
          {names_clause}
        order by (coalesce(mp.sales_invoice, mp.push_invoice) = %(invoice)s) desc, mp.received_at desc
        limit 100
        """,
        params,
        as_dict=True,
    )


@frappe.whitelist()
def list_collectable(invoice: str, mode_of_payment: str) -> list:
    doc = _collectable_invoice(invoice)
    mode = _integrated_mode(mode_of_payment, doc.company)
    return [
        {
            "name": r.name,
            "receipt": r.receipt,
            "amount": flt(r.amount),
            "payer_name": r.payer_name,
            "phone": _mask_phone(r.phone),
            "received_at": cstr(r.received_at),
            "payment_type": r.payment_type,
            "released": bool(r.released_from),
            "for_this_invoice": r.holder == invoice,
        }
        for r in _desk_candidates(invoice, mode.account_number)
    ]


@frappe.whitelist()
def start_push(invoice: str, mode_of_payment: str, phone: str, amount) -> dict:
    _check_collect_permission()
    from posawesome.posawesome.api.mobile_payments import initiate_push

    return initiate_push(invoice, mode_of_payment, phone, amount, context="Desk")


def _normalise_allocations(doc, total: float, allocations) -> list[dict]:
    allocations = json.loads(allocations) if isinstance(allocations, str) else (allocations or [])
    result = []
    seen = set()
    for row in allocations:
        name = cstr(row.get("invoice")).strip()
        amount = flt(row.get("amount"))
        if not name or amount <= 0:
            continue
        if name in seen:
            frappe.throw(_("Invoice {0} is listed twice.").format(name))
        seen.add(name)
        inv = frappe.db.get_value(
            "Sales Invoice", name,
            ["customer", "company", "docstatus", "is_return", "outstanding_amount"], as_dict=True,
        )
        if not inv or inv.docstatus != 1 or inv.is_return:
            frappe.throw(_("{0} is not a submitted sales invoice.").format(name))
        if inv.customer != doc.customer or inv.company != doc.company:
            frappe.throw(_("{0} belongs to another customer or company.").format(name))
        if amount > flt(inv.outstanding_amount) + AMOUNT_TOLERANCE:
            frappe.throw(_("{0}: {1} is more than its outstanding {2}.").format(name, amount, inv.outstanding_amount))
        result.append({"invoice": name, "amount": amount})

    if not result:
        result = [{"invoice": doc.name, "amount": min(total, flt(doc.outstanding_amount))}]
    if sum(r["amount"] for r in result) > total + AMOUNT_TOLERANCE:
        frappe.throw(_("You allocated more than the payments total ({0}).").format(total))
    return result


def _settle(doc, mode_of_payment: str, names: list[str], allocations) -> str:
    mode = _integrated_mode(mode_of_payment, doc.company)
    names = sorted({cstr(n) for n in names if n})
    if not names:
        frappe.throw(_("Select a payment."))

    frappe.db.sql(f"select name from `tab{DOCTYPE}` where name in %s for update", (tuple(names),))
    rows = _desk_candidates(doc.name, mode.account_number, names=names)
    missing = set(names) - {r.name for r in rows}
    if missing:
        frappe.throw(_("Payment {0} is no longer available. Refresh and try again.").format(", ".join(sorted(missing))))

    total = flt(sum(flt(r.amount) for r in rows), 2)
    allocations = _normalise_allocations(doc, total, allocations)
    receipts = ", ".join(cstr(r.receipt or r.name) for r in rows)
    paid_to = get_bank_cash_account(mode_of_payment, doc.company)["account"]

    pe = get_payment_entry("Sales Invoice", doc.name, party_amount=total, ignore_permissions=True)
    pe.mode_of_payment = mode_of_payment
    pe.paid_to = paid_to
    pe.paid_to_account_currency = get_account_currency(paid_to)
    pe.paid_amount = total
    pe.received_amount = total
    pe.reference_no = receipts[:140]
    pe.reference_date = getdate(max(get_datetime(r.received_at) for r in rows))
    pe.set("references", [])
    for row in allocations:
        pe.append(
            "references",
            {"reference_doctype": "Sales Invoice", "reference_name": row["invoice"], "allocated_amount": row["amount"]},
        )
    pe.set_missing_ref_details(force=True)
    pe.set_amounts()
    pe.remarks = _("Mobile payment {0} via {1}").format(receipts, mode_of_payment)
    pe.flags.ignore_permissions = True
    pe.insert()
    pe.submit()

    now = now_datetime()
    for row in rows:
        frappe.db.set_value(
            DOCTYPE,
            row.name,
            {
                "consumed": 1,
                "consumed_at": now,
                "payment_entry": pe.name,
                "sales_invoice": doc.name,
                "mode_of_payment": mode_of_payment,
                "company": doc.company,
                "attached_at": now,
                "attached_by": frappe.session.user,
            },
        )
    doc.add_comment(
        "Info",
        _("Collected {0} ({1}) through {2}: Payment Entry {3}").format(
            frappe.format_value(total, {"fieldtype": "Currency"}), receipts, mode_of_payment, pe.name
        ),
    )
    return pe.name


@frappe.whitelist()
def settle(invoice: str, mode_of_payment: str, payments, allocations=None) -> dict:
    """Post a Payment Entry for ``payments`` (Mobile Payment names) against the allocations."""
    _check_collect_permission()
    doc = _collectable_invoice(invoice)
    names = json.loads(payments) if isinstance(payments, str) else list(payments or [])
    pe = _settle(doc, mode_of_payment, names, allocations)
    return {"payment_entry": pe}


@frappe.whitelist()
def settle_push(invoice: str, payment: str) -> dict:
    """The desk prompt succeeded: post it against its invoice (idempotent)."""
    _check_collect_permission()
    row = frappe.db.get_value(
        DOCTYPE, payment, ["status", "consumed", "payment_entry", "push_invoice", "mode_of_payment", "amount"], as_dict=True
    )
    if not row or row.push_invoice != invoice:
        frappe.throw(_("That payment was not requested for {0}.").format(invoice))
    if row.payment_entry:
        return {"payment_entry": row.payment_entry}
    if row.status != "Success":
        frappe.throw(_("The payment is not confirmed yet."))
    doc = _collectable_invoice(invoice)
    pe = _settle(doc, row.mode_of_payment, [payment], [{"invoice": invoice, "amount": min(flt(row.amount), flt(doc.outstanding_amount))}])
    return {"payment_entry": pe}


def settle_orphaned_desk_pushes():
    """Scheduler: a desk prompt the customer paid after the browser went away."""
    if not _ready():
        return
    cutoff = add_to_date(now_datetime(), minutes=-ORPHAN_GRACE_MINUTES)
    rows = frappe.get_all(
        DOCTYPE,
        filters={
            "payment_type": "STK",
            "push_context": "Desk",
            "status": "Success",
            "consumed": 0,
            "is_hidden": 0,
            "payment_entry": ["is", "not set"],
            "push_result_at": ["<", cutoff],
        },
        fields=["name", "push_invoice", "mode_of_payment", "amount"],
        limit=50,
    )
    for row in rows:
        try:
            doc = frappe.get_doc("Sales Invoice", row.push_invoice)
            if doc.docstatus != 1 or flt(doc.outstanding_amount) <= 0:
                continue
            _settle(doc, row.mode_of_payment, [row.name],
                    [{"invoice": doc.name, "amount": min(flt(row.amount), flt(doc.outstanding_amount))}])
            frappe.db.commit()
        except Exception:
            frappe.db.rollback()
            frappe.log_error(title=f"Mobile payment: could not settle desk prompt {row.name}")
