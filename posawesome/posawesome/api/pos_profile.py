# Copyright (c) 2026, Youssef Restom and contributors
# For license information, please see license.txt

import frappe
from frappe import _


def validate(doc, method):
    validate_additional_price_lists(doc)


def validate_additional_price_lists(doc):
    """Ensure the extra price lists configured for the POS item list are usable.

    Each row must reference a Price List other than the POS Profile's own
    default (``selling_price_list``) and no Price List may be added twice,
    otherwise the POS item list would show a duplicate or redundant column.
    """

    rows = doc.get("posa_additional_price_lists") or []
    if not rows:
        return

    seen = set()
    for row in rows:
        price_list = row.price_list
        if not price_list:
            continue
        if doc.selling_price_list and price_list == doc.selling_price_list:
            frappe.throw(
                _("Row #{0}: {1} is already the default Price List for this POS Profile.").format(
                    row.idx, price_list
                )
            )
        if price_list in seen:
            frappe.throw(
                _("Row #{0}: {1} has already been added to the additional price lists.").format(
                    row.idx, price_list
                )
            )
        seen.add(price_list)
