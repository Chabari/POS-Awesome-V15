from collections import defaultdict

import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import cint, flt, nowdate, nowtime


MANAGER_ROLES = {"Administrator", "System Manager", "Sales Manager"}


def _lock_opening_shift(opening_shift):
    if not opening_shift:
        frappe.throw(_("POS Opening Shift is required."))

    locked = frappe.db.sql(
        "select name from `tabPOS Opening Shift` where name = %s for update",
        opening_shift,
        as_dict=True,
    )
    if not locked:
        frappe.throw(_("POS Opening Shift {0} was not found.").format(frappe.bold(opening_shift)))

    return frappe.get_doc("POS Opening Shift", opening_shift)


def _ensure_shift_access(opening_shift_doc):
    user = frappe.session.user
    if user == opening_shift_doc.user or MANAGER_ROLES.intersection(frappe.get_roles(user)):
        return

    frappe.throw(_("You can only record cash draws for your own POS shift."), frappe.PermissionError)


def _ensure_open_shift(opening_shift_doc):
    if opening_shift_doc.docstatus != 1 or opening_shift_doc.status != "Open" or opening_shift_doc.pos_closing_shift:
        frappe.throw(_("Cash draws can only be recorded against an open POS shift."))


def _get_account_details(account, company, label):
    details = frappe.db.get_value(
        "Account",
        account,
        ["company", "is_group", "account_currency", "root_type"],
        as_dict=True,
    )
    if not details:
        frappe.throw(_("{0} {1} was not found.").format(label, frappe.bold(account)))
    if details.company != company:
        frappe.throw(_("{0} must belong to company {1}.").format(label, frappe.bold(company)))
    if cint(details.is_group):
        frappe.throw(_("{0} cannot be a group account.").format(label))
    return details


def _get_profile_payment_modes(pos_profile_doc):
    return {
        row.mode_of_payment
        for row in pos_profile_doc.get("payments") or []
        if row.get("mode_of_payment")
    }


def get_unposted_cash_draws(pos_opening_shift):
    return frappe.get_all(
        "POS Cash Draw",
        filters={
            "pos_opening_shift": pos_opening_shift,
            "docstatus": 1,
            "pos_closing_shift": ["is", "not set"],
        },
        fields=[
            "name",
            "posting_date",
            "posting_time",
            "recorded_by",
            "mode_of_payment",
            "amount",
            "narration",
            "expense_account",
            "payment_account",
            "creation",
        ],
        order_by="creation asc",
    )


def get_cash_draw_totals(cash_draws):
    totals = defaultdict(float)
    for row in cash_draws or []:
        totals[row.get("mode_of_payment")] += flt(row.get("amount"))
    return totals


def _serialize_cash_draw(row):
    return {
        "name": row.get("name"),
        "posting_date": row.get("posting_date"),
        "posting_time": row.get("posting_time"),
        "recorded_by": row.get("recorded_by"),
        "mode_of_payment": row.get("mode_of_payment"),
        "amount": flt(row.get("amount")),
        "narration": row.get("narration"),
        "creation": row.get("creation"),
    }


def _build_cash_draw_context(opening_shift_doc):
    pos_profile_doc = frappe.get_cached_doc("POS Profile", opening_shift_doc.pos_profile)
    enabled = cint(pos_profile_doc.get("posa_enable_cash_draw") or 0) == 1
    company_currency = frappe.get_cached_value("Company", opening_shift_doc.company, "default_currency")
    modes = []

    if enabled:
        if not pos_profile_doc.get("expense_account"):
            frappe.throw(_("Set an Expense Account on POS Profile {0} before using Cash Draw.").format(
                frappe.bold(pos_profile_doc.name)
            ))
        for mode in sorted(_get_profile_payment_modes(pos_profile_doc)):
            payment_account = frappe.db.get_value(
                "Mode of Payment Account",
                {"parent": mode, "company": opening_shift_doc.company},
                "default_account",
            )
            if not payment_account:
                continue
            account_currency = frappe.get_cached_value("Account", payment_account, "account_currency")
            if account_currency == company_currency:
                modes.append({"mode_of_payment": mode, "payment_account": payment_account})
        if not modes:
            frappe.throw(_("No POS Profile payment mode has a default account in company currency {0}.").format(
                frappe.bold(company_currency)
            ))

    cash_draws = get_unposted_cash_draws(opening_shift_doc.name)
    totals = get_cash_draw_totals(cash_draws)
    return {
        "enabled": enabled,
        "pos_opening_shift": opening_shift_doc.name,
        "pos_profile": opening_shift_doc.pos_profile,
        "company": opening_shift_doc.company,
        "company_currency": company_currency,
        "expense_account": pos_profile_doc.get("expense_account"),
        "modes": modes,
        "cash_draws": [_serialize_cash_draw(row) for row in cash_draws],
        "totals": [
            {"mode_of_payment": mode, "amount": flt(amount)}
            for mode, amount in sorted(totals.items())
        ],
        "grand_total": sum(totals.values()),
    }


class POSCashDraw(Document):
    def before_insert(self):
        self.posting_date = nowdate()
        self.posting_time = nowtime()
        self.recorded_by = frappe.session.user

    def validate(self):
        opening_shift_doc = _lock_opening_shift(self.pos_opening_shift)
        _ensure_shift_access(opening_shift_doc)
        _ensure_open_shift(opening_shift_doc)

        pos_profile_doc = frappe.get_cached_doc("POS Profile", opening_shift_doc.pos_profile)
        if cint(pos_profile_doc.get("posa_enable_cash_draw") or 0) != 1:
            frappe.throw(_("Cash Draw is not enabled for POS Profile {0}.").format(
                frappe.bold(opening_shift_doc.pos_profile)
            ))

        self.pos_profile = opening_shift_doc.pos_profile
        self.company = opening_shift_doc.company
        self.company_currency = frappe.get_cached_value("Company", self.company, "default_currency")
        self.amount = flt(self.amount)
        self.narration = (self.narration or "").strip()

        if self.amount <= 0:
            frappe.throw(_("Cash draw amount must be greater than zero."))
        if not self.narration:
            frappe.throw(_("Narration is required for a cash draw."))
        if self.mode_of_payment not in _get_profile_payment_modes(pos_profile_doc):
            frappe.throw(_("Mode of Payment {0} is not configured on POS Profile {1}.").format(
                frappe.bold(self.mode_of_payment), frappe.bold(self.pos_profile)
            ))

        self.expense_account = pos_profile_doc.get("expense_account")
        if not self.expense_account:
            frappe.throw(_("Expense Account is required on POS Profile {0}.").format(
                frappe.bold(self.pos_profile)
            ))

        self.payment_account = frappe.db.get_value(
            "Mode of Payment Account",
            {"parent": self.mode_of_payment, "company": self.company},
            "default_account",
        )
        if not self.payment_account:
            frappe.throw(_("Mode of Payment {0} has no default account for company {1}.").format(
                frappe.bold(self.mode_of_payment), frappe.bold(self.company)
            ))

        expense_details = _get_account_details(self.expense_account, self.company, _("Expense Account"))
        payment_details = _get_account_details(self.payment_account, self.company, _("Payment Account"))
        if expense_details.root_type != "Expense":
            frappe.throw(_("The POS Profile Expense Account must be an expense account."))
        if expense_details.account_currency != self.company_currency:
            frappe.throw(_("Cash Draw currently requires the Expense Account currency to be {0}.").format(
                frappe.bold(self.company_currency)
            ))
        if payment_details.account_currency != self.company_currency:
            frappe.throw(_("Cash Draw currently requires the Mode of Payment account currency to be {0}.").format(
                frappe.bold(self.company_currency)
            ))

    def before_cancel(self):
        opening_shift_doc = frappe.get_doc("POS Opening Shift", self.pos_opening_shift)
        _ensure_shift_access(opening_shift_doc)
        if self.pos_closing_shift:
            closing_status = frappe.db.get_value("POS Closing Shift", self.pos_closing_shift, "docstatus")
            if closing_status == 1:
                frappe.throw(_("Cancel the submitted POS Closing Shift before cancelling this cash draw."))


@frappe.whitelist()
def get_cash_draw_context(pos_opening_shift):
    opening_shift_doc = _lock_opening_shift(pos_opening_shift)
    _ensure_shift_access(opening_shift_doc)
    _ensure_open_shift(opening_shift_doc)
    return _build_cash_draw_context(opening_shift_doc)


@frappe.whitelist()
def create_cash_draw(pos_opening_shift, mode_of_payment, amount, narration, client_request_id=None):
    if client_request_id:
        existing = frappe.db.get_value(
            "POS Cash Draw",
            {"client_request_id": client_request_id, "pos_opening_shift": pos_opening_shift},
            "name",
        )
        if existing:
            return _serialize_cash_draw(frappe.get_doc("POS Cash Draw", existing))

    cash_draw = frappe.get_doc(
        {
            "doctype": "POS Cash Draw",
            "pos_opening_shift": pos_opening_shift,
            "mode_of_payment": mode_of_payment,
            "amount": amount,
            "narration": narration,
            "client_request_id": client_request_id,
        }
    )
    cash_draw.flags.ignore_permissions = True
    cash_draw.insert()
    cash_draw.submit()
    return _serialize_cash_draw(cash_draw)


@frappe.whitelist()
def cancel_cash_draw(name):
    cash_draw = frappe.get_doc("POS Cash Draw", name)
    opening_shift_doc = _lock_opening_shift(cash_draw.pos_opening_shift)
    _ensure_shift_access(opening_shift_doc)
    cash_draw.flags.ignore_permissions = True
    cash_draw.cancel()
    return cash_draw.name
