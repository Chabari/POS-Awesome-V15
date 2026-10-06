# Copyright (c) 2026, Youssef Restom and contributors
# For license information, please see license.txt

"""One row per mobile-money receipt (KCB Buni IPN / STK, later M-Pesa Daraja C2B / STK).

Rows are created only by the provider callbacks and the push API in
``posawesome.posawesome.api.mobile_payments``; the form is read-only.
"""

import frappe
from frappe.model.document import Document
from frappe.utils import now_datetime


class MobilePayment(Document):
    def before_insert(self):
        if not self.received_at:
            self.received_at = now_datetime()
        # UNIQUE allows many NULLs but only one "": a pending push has no receipt yet.
        self.receipt = (self.receipt or "").strip() or None


def on_doctype_update():
    frappe.db.add_index("Mobile Payment", ["account_number", "consumed", "status"])
    frappe.db.add_index("Mobile Payment", ["payment_type", "status"])
