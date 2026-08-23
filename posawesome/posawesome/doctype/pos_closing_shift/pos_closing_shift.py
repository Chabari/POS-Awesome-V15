# Copyright (c) 2020, Youssef Restom and contributors
# For license information, please see license.txt

import json
from collections import defaultdict

import frappe
from erpnext.accounts.doctype.pos_invoice_merge_log.pos_invoice_merge_log import (
    consolidate_pos_invoices,
)
from frappe import _, DoesNotExistError
from frappe.model.document import Document
from frappe.utils import cint, flt, nowdate

from posawesome.posawesome.doctype.pos_cash_draw.pos_cash_draw import (
    get_cash_draw_totals,
    get_unposted_cash_draws,
)


def get_base_value(doc, fieldname, base_fieldname=None, conversion_rate=None):
    """Return the value for a field in company currency."""

    base_fieldname = base_fieldname or f"base_{fieldname}"
    base_value = doc.get(base_fieldname)

    if base_value not in (None, ""):
        return flt(base_value)

    value = doc.get(fieldname)
    if value in (None, ""):
        return 0

    if conversion_rate is None:
        conversion_rate = (
            doc.get("conversion_rate")
            or doc.get("exchange_rate")
            or doc.get("target_exchange_rate")
            or doc.get("plc_conversion_rate")
            or 1
        )

    return flt(value) * flt(conversion_rate or 1)


def _row_as_dict(row):
    if isinstance(row, dict):
        return row
    if hasattr(row, "as_dict"):
        return row.as_dict()
    return {}


def _row_nozzle_value(row):
    row_dict = _row_as_dict(row)
    return row_dict.get("nozzle") or row_dict.get("nozzle_name") or row_dict.get("custom_nozzle_name") or ""


def _row_opening_reading(row):
    row_dict = _row_as_dict(row)
    return flt(
        row_dict.get("opening_reading")
        or row_dict.get("current_reading")
        or row_dict.get("custom_current_reading")
        or row_dict.get("reading")
        or 0
    )


def _row_closing_reading(row):
    row_dict = _row_as_dict(row)
    return flt(
        row_dict.get("closing_reading")
        or row_dict.get("current_reading")
        or row_dict.get("custom_current_reading")
        or row_dict.get("reading")
        or 0
    )


def _row_key(row):
    row_dict = _row_as_dict(row)
    return (
        (_row_nozzle_value(row) or "").strip(),
        (row_dict.get("fuel_item") or row_dict.get("custom_fuel_item") or "").strip(),
        (row_dict.get("fuel_pump") or row_dict.get("custom_fuel_pump") or "").strip(),
    )


def _row_test_return(row):
    row_dict = _row_as_dict(row)
    return max(flt(row_dict.get("test_return_qty") or 0), 0)


def _row_meter_digits(row):
    row_dict = _row_as_dict(row)
    return cint(row_dict.get("meter_digits") or 0) or 6


def _compute_dispensed(opening_reading, closing_reading, meter_digits=6):
    """Gross litres between two meter readings, correcting a meter rollover.

    Returns (dispensed_qty, rolled_over). Kept local so POS Awesome carries no
    hard dependency on the Fuel App.
    """
    opening_reading = flt(opening_reading)
    closing_reading = flt(closing_reading)

    if closing_reading >= opening_reading:
        return closing_reading - opening_reading, False

    wrap_at = float(10 ** (cint(meter_digits) or 6))
    if opening_reading > wrap_at * 0.9 and closing_reading < wrap_at * 0.1:
        return (wrap_at - opening_reading) + closing_reading, True

    return 0.0, False


def _row_item_warehouse_key(row):
    row_dict = _row_as_dict(row)
    return (
        (row_dict.get("fuel_item") or row_dict.get("custom_fuel_item") or "").strip(),
        (row_dict.get("warehouse") or row_dict.get("custom_warehouse") or "").strip(),
    )


def _aggregate_by_item(by_item_warehouse):
    """Collapse an {(item, warehouse): qty} map into {item: qty}."""
    totals = defaultdict(float)
    for (item_code, _warehouse), qty in (by_item_warehouse or {}).items():
        if item_code:
            totals[item_code] += flt(qty)
    return totals


def _validate_shared_tank_closing_readings(opening_shift_doc, nozzle_closing_readings, sold_by_item_warehouse):
    """Every fuel item that sold must have at least one nozzle meter that moved.

    Grouped per item, not per tank: invoice lines rarely carry the nozzle's
    tank warehouse, so per-tank sold quantities are not reliable. The meter,
    not the invoice total, is what the shift is reconciled against, so a
    closing reading that was never actually read cannot be accepted.
    """
    closing_rows_map = {}
    for row in nozzle_closing_readings or []:
        row_dict = _row_as_dict(row)
        closing_rows_map[_row_key(row_dict)] = _row_closing_reading(row_dict)

    sold_by_item = _aggregate_by_item(sold_by_item_warehouse)

    nozzle_groups = defaultdict(list)
    for row in opening_shift_doc.get("custom_nozzle_readings") or []:
        row_dict = _row_as_dict(row)
        item_code = (row_dict.get("fuel_item") or row_dict.get("custom_fuel_item") or "").strip()
        nozzle_groups[item_code].append(
            {
                "row_key": _row_key(row_dict),
                "nozzle": _row_nozzle_value(row_dict),
                "opening_reading": _row_opening_reading(row_dict),
            }
        )

    invalid_groups = []
    for item_code, group_rows in nozzle_groups.items():
        sold_qty = flt(sold_by_item.get(item_code) or 0)
        if sold_qty <= 0:
            continue

        if any(
            flt(closing_rows_map.get(row.get("row_key"), row.get("opening_reading")))
            > flt(row.get("opening_reading"))
            for row in group_rows
        ):
            continue

        invalid_groups.append(
            _("{0} ({1} nozzles, invoiced sales {2})").format(
                frappe.bold(item_code or _("Unknown Item")),
                len(group_rows),
                sold_qty,
            )
        )

    if invalid_groups:
        frappe.throw(
            _(
                "Actual nozzle meter readings are required before closing. "
                "The following tanks sold fuel but still show their opening readings:<br><ul>{0}</ul>"
            ).format("".join(f"<li>{group}</li>" for group in invalid_groups))
        )


def _update_opening_nozzle_rows_and_get_dispensed(opening_shift_doc, nozzle_closing_readings):
    """Write the captured meters back onto the opening shift and return net litres.

    Net means gross meter throughput minus fuel pumped for pump testing and
    poured back into the tank, which the meter counts but the tank never lost.
    """
    closing_rows_map = {}
    test_return_map = {}
    for row in nozzle_closing_readings or []:
        row_dict = _row_as_dict(row)
        key = _row_key(row_dict)
        closing_rows_map[key] = _row_closing_reading(row_dict)
        test_return_map[key] = _row_test_return(row_dict)

    dispensed_by_item_warehouse = defaultdict(float)
    for row in opening_shift_doc.get("custom_nozzle_readings") or []:
        row_dict = _row_as_dict(row)
        row_key = _row_key(row_dict)
        opening_reading = _row_opening_reading(row_dict)
        closing_reading = closing_rows_map.get(row_key, _row_closing_reading(row_dict))
        meter_digits = _row_meter_digits(row_dict)

        dispensed, rolled_over = _compute_dispensed(opening_reading, closing_reading, meter_digits)
        if not rolled_over and closing_reading < opening_reading:
            closing_reading = opening_reading
            dispensed = 0.0

        test_return_qty = min(flt(test_return_map.get(row_key) or 0), flt(dispensed))
        row_dict["closing_reading"] = closing_reading

        # POS Opening Shift is submitted at this stage; update child rows directly.
        child_row_name = row_dict.get("name")
        child_doctype = row_dict.get("doctype") or getattr(row, "doctype", None)
        if child_row_name and child_doctype:
            child_meta = frappe.get_meta(child_doctype)
            updates = {"closing_reading": closing_reading}
            if child_meta.has_field("dispensed_qty"):
                updates["dispensed_qty"] = flt(dispensed)
            if child_meta.has_field("test_return_qty"):
                updates["test_return_qty"] = flt(test_return_qty)
            if child_meta.has_field("meter_digits"):
                updates["meter_digits"] = meter_digits
            if child_meta.has_field("reading_source"):
                updates["reading_source"] = "Rollover Corrected" if rolled_over else "Manual"
            frappe.db.set_value(child_doctype, child_row_name, updates, update_modified=False)

        fuel_item = row_dict.get("fuel_item") or row_dict.get("custom_fuel_item")
        warehouse = row_dict.get("warehouse") or row_dict.get("custom_warehouse") or ""
        if fuel_item:
            dispensed_by_item_warehouse[(fuel_item, warehouse)] += flt(dispensed) - flt(
                test_return_qty
            )

    return dispensed_by_item_warehouse


def _update_shift_nozzle_rows(doc, nozzle_closing_readings, child_fieldname="custom_nozzle_readings"):
    closing_rows_map = {}
    test_return_map = {}
    for row in nozzle_closing_readings or []:
        row_dict = _row_as_dict(row)
        key = _row_key(row_dict)
        closing_rows_map[key] = _row_closing_reading(row_dict)
        test_return_map[key] = _row_test_return(row_dict)

    if not closing_rows_map:
        return

    updated_rows = []
    for row in doc.get(child_fieldname) or []:
        row_dict = _row_as_dict(row)
        row_key = _row_key(row_dict)
        opening_reading = _row_opening_reading(row_dict)
        closing_reading = closing_rows_map.get(row_key)
        if closing_reading is None:
            updated_rows.append(row_dict)
            continue

        meter_digits = _row_meter_digits(row_dict)
        dispensed, rolled_over = _compute_dispensed(opening_reading, closing_reading, meter_digits)
        if not rolled_over and flt(closing_reading) < flt(opening_reading):
            closing_reading = opening_reading
            dispensed = 0.0

        row_dict["closing_reading"] = flt(closing_reading)
        row_dict["dispensed_qty"] = flt(dispensed)
        row_dict["test_return_qty"] = min(flt(test_return_map.get(row_key) or 0), flt(dispensed))
        row_dict["meter_digits"] = meter_digits
        row_dict["reading_source"] = "Rollover Corrected" if rolled_over else "Manual"
        updated_rows.append(row_dict)

    if updated_rows:
        doc.set(child_fieldname, updated_rows)
        doc.save(ignore_permissions=True)


def _get_shift_sold_by_item(opening_shift_name, pos_profile, exclude_invoices=None):
    """Invoiced quantities per (item, warehouse) for the shift.

    A deficit invoice from an earlier (cancelled) close stays counted as sold
    while it is submitted: it also sits in the shift grand total, so the two
    cancel out. `exclude_invoices` exists for the repair path, which cancels
    the inflated invoice it excludes.
    """
    sold_by_item_warehouse = defaultdict(float)
    use_pos_invoice = frappe.db.get_value(
        "POS Profile",
        pos_profile,
        "create_pos_invoice_instead_of_sales_invoice",
    )
    invoice_doctype = "POS Invoice" if use_pos_invoice else "Sales Invoice"
    invoice_item_doctype = "POS Invoice Item" if use_pos_invoice else "Sales Invoice Item"

    invoice_names = frappe.get_all(
        invoice_doctype,
        filters={
            "docstatus": 1,
            "posa_pos_opening_shift": opening_shift_name,
        },
        pluck="name",
    )
    excluded = set(exclude_invoices or [])
    invoice_names = [name for name in invoice_names if name not in excluded]

    if not invoice_names:
        return sold_by_item_warehouse

    items = frappe.get_all(
        invoice_item_doctype,
        filters={"parent": ["in", invoice_names]},
        fields=["item_code", "qty", "warehouse"],
    )
    for item in items:
        if item.get("item_code"):
            warehouse = item.get("warehouse") or ""
            sold_by_item_warehouse[(item.get("item_code"), warehouse)] += flt(item.get("qty") or 0)

    return sold_by_item_warehouse


def _allocate_item_deficits_to_tanks(dispensed_by_item_warehouse, sold_by_item_warehouse):
    """Unbilled litres netted per fuel item, split back over its tanks pro rata.

    Sales invoices rarely carry the nozzle's tank warehouse and one item is
    often dispensed from several tanks, so a per-tank deficit double counts
    fuel that was already billed. Only the per-item net is real; the per-tank
    split is informational (the deficit invoice posts no stock).
    """
    dispensed_by_item = _aggregate_by_item(dispensed_by_item_warehouse)
    sold_by_item = _aggregate_by_item(sold_by_item_warehouse)

    deficit_by_item_warehouse = {}
    for item_code, dispensed_total in dispensed_by_item.items():
        if flt(dispensed_total) <= 0:
            continue
        deficit_qty = flt(dispensed_total) - max(flt(sold_by_item.get(item_code) or 0), 0)
        if deficit_qty <= 0:
            continue

        tanks = sorted(
            (warehouse, flt(qty))
            for (item, warehouse), qty in dispensed_by_item_warehouse.items()
            if item == item_code and flt(qty) > 0
        )
        remaining = flt(deficit_qty)
        for index, (warehouse, qty) in enumerate(tanks):
            if index == len(tanks) - 1:
                share = remaining
            else:
                share = flt(deficit_qty) * flt(qty) / flt(dispensed_total)
                remaining -= share
            if share > 0:
                deficit_by_item_warehouse[(item_code, warehouse)] = flt(share)

    return deficit_by_item_warehouse


def _update_pos_profile_nozzle_current_readings(pos_profile_doc, nozzle_closing_readings):
    closing_rows_map = {}
    for row in nozzle_closing_readings or []:
        row_dict = _row_as_dict(row)
        closing_rows_map[_row_key(row_dict)] = _row_closing_reading(row_dict)

    if not closing_rows_map:
        return

    updated_rows = []
    for row in pos_profile_doc.get("custom_pump_nozzles") or []:
        row_dict = _row_as_dict(row)
        row_key = _row_key(row_dict)
        closing_reading = closing_rows_map.get(row_key)
        if closing_reading is None:
            updated_rows.append(row_dict)
            continue

        if "current_reading" in row_dict:
            row_dict["current_reading"] = closing_reading
        if "custom_current_reading" in row_dict:
            row_dict["custom_current_reading"] = closing_reading
        if "closing_reading" in row_dict:
            row_dict["closing_reading"] = closing_reading
        updated_rows.append(row_dict)

    pos_profile_doc.set("custom_pump_nozzles", updated_rows)
    pos_profile_doc.save(ignore_permissions=True)


def _get_fuel_item_rate(item_code, price_list=None):
    """Resolve the selling rate for a fuel item.

    Prefers the active selling Item Price (POS Profile price list), falling
    back to the item's standard_rate when no price is configured.
    """

    if price_list and item_code:
        rate = frappe.db.get_value(
            "Item Price",
            {
                "item_code": item_code,
                "price_list": price_list,
                "selling": 1,
            },
            "price_list_rate",
        )
        if flt(rate) > 0:
            return flt(rate)

    return flt(frappe.get_cached_value("Item", item_code, "standard_rate") or 0)


def _create_unreconciled_fuel_invoice(closing_shift_doc, pos_profile_doc, deficit_by_item_warehouse, posting_date=None):
    if not deficit_by_item_warehouse:
        return None

    customer = pos_profile_doc.get("customer")
    if not customer:
        frappe.throw(_("Customer is required in POS Profile to create unreconciled fuel invoice."))

    invoice = frappe.new_doc("Sales Invoice")
    invoice.customer = customer
    invoice.company = closing_shift_doc.company
    invoice.posting_date = posting_date or nowdate()
    invoice.due_date = posting_date or nowdate()
    if posting_date:
        invoice.set_posting_time = 1
    invoice.is_pos = 1
    invoice.update_stock = 0
    invoice.pos_profile = pos_profile_doc.name

    if pos_profile_doc.get("taxes_and_charges"):
        invoice.taxes_and_charges = pos_profile_doc.get("taxes_and_charges")
    if pos_profile_doc.get("selling_price_list"):
        invoice.selling_price_list = pos_profile_doc.get("selling_price_list")

    if frappe.db.has_column("Sales Invoice", "posa_pos_opening_shift"):
        invoice.posa_pos_opening_shift = closing_shift_doc.pos_opening_shift
    # if frappe.db.has_column("Sales Invoice", "pos_closing_entry"):
    #     invoice.pos_closing_entry = closing_shift_doc.name

    invoice.remarks = _("Unreconciled fuel quantity invoice generated during POS shift closing.")

    price_list = pos_profile_doc.get("selling_price_list")
    row_warehouses = {}
    for (item_code, warehouse), qty in sorted(deficit_by_item_warehouse.items()):
        if flt(qty) <= 0:
            continue

        item_row = {
            "item_code": item_code,
            "qty": flt(qty),
            "rate": _get_fuel_item_rate(item_code, price_list),
        }
        if warehouse:
            item_row["warehouse"] = warehouse
        row = invoice.append("items", item_row)
        row_warehouses[row.idx] = warehouse

    if not invoice.get("items"):
        return None

    invoice.flags.ignore_permissions = True
    invoice.set_missing_values()
    # set_pos_fields re-applies the POS Profile warehouse and update_stock over
    # what was set above, so both must be re-asserted after set_missing_values.
    invoice.update_stock = 0
    for row in invoice.get("items"):
        if row_warehouses.get(row.idx):
            row.warehouse = row_warehouses[row.idx]
    invoice.calculate_taxes_and_totals()

    cash_mode = pos_profile_doc.get("posa_cash_mode_of_payment") or "Cash"
    invoice.set("payments", [])
    invoice.append("payments", {"mode_of_payment": cash_mode, "amount": flt(invoice.grand_total)})
    invoice.calculate_taxes_and_totals()

    invoice.insert(ignore_permissions=True)
    invoice.submit()
    return invoice


def _attach_fuel_invoice_to_closing(closing_shift_doc, invoice):
    """Add a generated fuel invoice to Linked Invoices and refresh shift totals."""
    if not invoice:
        return

    # Tagged so the meter versus invoice gap can be measured against what was
    # billed during the shift, not against this corrective invoice.
    if frappe.get_meta("POS Closing Shift").has_field("custom_fuel_unreconciled_invoice"):
        closing_shift_doc.custom_fuel_unreconciled_invoice = invoice.name

    closing_shift_doc.append(
        "pos_transactions",
        {
            "sales_invoice": invoice.name,
            "posting_date": invoice.posting_date,
            "grand_total": flt(invoice.base_grand_total or invoice.grand_total),
            "transaction_currency": invoice.currency,
            "transaction_amount": flt(invoice.grand_total),
            "customer": invoice.customer,
        },
    )
    closing_shift_doc.grand_total = flt(closing_shift_doc.grand_total) + flt(
        invoice.base_grand_total or invoice.grand_total
    )
    closing_shift_doc.net_total = flt(closing_shift_doc.net_total) + flt(
        invoice.base_net_total or invoice.net_total
    )
    closing_shift_doc.total_quantity = flt(closing_shift_doc.total_quantity) + flt(invoice.total_qty)


def _get_shift_credit_total(opening_shift_name, pos_profile):
    """Total outstanding (credit) sales for the shift, in company currency."""
    use_pos_invoice = frappe.db.get_value(
        "POS Profile", pos_profile, "create_pos_invoice_instead_of_sales_invoice"
    )
    doctype = "POS Invoice" if use_pos_invoice else "Sales Invoice"
    rows = frappe.get_all(
        doctype,
        filters={
            "docstatus": 1,
            "posa_pos_opening_shift": opening_shift_name,
            "is_return": 0,
            "outstanding_amount": [">", 0],
        },
        fields=["outstanding_amount", "conversion_rate"],
    )
    return sum(flt(r.outstanding_amount) * flt(r.conversion_rate or 1) for r in rows)


def _apply_fuel_payment_reconciliation(closing_shift_doc, pos_profile_doc, credit_total=0):
    """Balance every tender against the shift grand total (meter derived).

    With the deficit invoice attached, the shift grand total equals the metered
    fuel value (plus any non-fuel sales), so the meters stay the single source
    of truth. Every shilling of (grand total - credit) must sit in some tender:
    still in the drawer (closing) or already taken out (cash_drawn). Non-cash
    modes reconcile to whatever the cashier declares; the cash mode expected is
    the balancing remainder, so the identity

        sum(closing - opening + cash_drawn) + credit = grand total

    always holds. The cashier's declared cash closing is never overwritten -
    a real cash shortage surfaces as a difference instead of being hidden.

    Credit sales are intentionally not pushed into payment_reconciliation: in
    ERPNext credit invoices carry no mode of payment, so they are shown only as
    an informational row in the closing dialog.
    """
    cash_mode = pos_profile_doc.get("posa_cash_mode_of_payment") or "Cash"

    cash_row = None
    other_collected = 0.0
    for row in closing_shift_doc.payment_reconciliation:
        if row.mode_of_payment == cash_mode:
            cash_row = row
            continue
        closing = flt(row.get("closing_amount"))
        # The declaration reconciles to itself; whatever it carries (plus what
        # was drawn out of it) is money that never reaches the cash drawer.
        row.expected_amount = closing
        other_collected += closing - flt(row.get("opening_amount")) + flt(row.get("cash_drawn"))

    if cash_row is None:
        cash_row = closing_shift_doc.append(
            "payment_reconciliation",
            {
                "mode_of_payment": cash_mode,
                "opening_amount": 0,
                "closing_amount": 0,
                "cash_drawn": 0,
                "expected_amount": 0,
            },
        )

    expected_cash = (
        flt(closing_shift_doc.grand_total)
        - flt(credit_total)
        - other_collected
        - flt(cash_row.get("cash_drawn"))
        + flt(cash_row.get("opening_amount"))
    )
    expected_cash = flt(expected_cash, 2)

    if expected_cash < -0.005:
        frappe.throw(
            _(
                "Declared tenders plus cash draws exceed the shift total by {0}. "
                "The nozzle meters are the source of truth; review the closing amounts."
            ).format(frappe.bold(abs(expected_cash)))
        )

    cash_row.expected_amount = expected_cash


def _apply_cash_draw_reconciliation(
    closing_shift_doc,
    cash_draws,
    expected_includes_existing_draw=False,
    validate_available=False,
):
    totals = get_cash_draw_totals(cash_draws)
    rows_by_mode = {
        row.mode_of_payment: row
        for row in closing_shift_doc.get("payment_reconciliation") or []
        if row.get("mode_of_payment")
    }

    for mode_of_payment, amount in totals.items():
        row = rows_by_mode.get(mode_of_payment)
        if not row:
            row = closing_shift_doc.append(
                "payment_reconciliation",
                {
                    "mode_of_payment": mode_of_payment,
                    "opening_amount": 0,
                    "closing_amount": 0,
                    "expected_amount": 0,
                },
            )
            rows_by_mode[mode_of_payment] = row

        expected_before_draw = flt(row.expected_amount)
        if expected_includes_existing_draw:
            expected_before_draw += flt(row.get("cash_drawn"))

        row.cash_drawn = flt(amount)
        row.expected_amount = expected_before_draw - flt(amount)
        if validate_available and row.expected_amount < 0:
            frappe.throw(
                _("Cash drawn through {0} exceeds the amount available in that payment mode by {1}.").format(
                    frappe.bold(mode_of_payment),
                    frappe.bold(abs(row.expected_amount)),
                )
            )

    for mode_of_payment, row in rows_by_mode.items():
        if mode_of_payment in totals:
            continue
        if expected_includes_existing_draw:
            row.expected_amount = flt(row.expected_amount) + flt(row.get("cash_drawn"))
        row.cash_drawn = 0

    closing_shift_doc.total_cash_drawn = sum(flt(amount) for amount in totals.values())
    return totals


def _create_cash_draw_journal_entry(closing_shift_doc):
    cash_draws = get_unposted_cash_draws(closing_shift_doc.pos_opening_shift)
    if not cash_draws:
        return None
    if closing_shift_doc.get("cash_draw_journal_entry"):
        return frappe.get_doc("Journal Entry", closing_shift_doc.cash_draw_journal_entry)

    debit_totals = defaultdict(float)
    credit_totals = defaultdict(float)
    for row in cash_draws:
        debit_totals[row.expense_account] += flt(row.amount)
        credit_totals[row.payment_account] += flt(row.amount)

    pos_profile_doc = frappe.get_cached_doc("POS Profile", closing_shift_doc.pos_profile)
    cost_center = pos_profile_doc.get("cost_center") or frappe.get_cached_value(
        "Company", closing_shift_doc.company, "cost_center"
    )
    journal_entry = frappe.new_doc("Journal Entry")
    journal_entry.voucher_type = "Journal Entry"
    journal_entry.company = closing_shift_doc.company
    journal_entry.posting_date = closing_shift_doc.posting_date or nowdate()
    journal_entry.user_remark = _("POS cash draws for closing shift {0}").format(closing_shift_doc.name)

    for account, amount in sorted(debit_totals.items()):
        journal_entry.append(
            "accounts",
            {
                "account": account,
                "debit_in_account_currency": flt(amount),
                "cost_center": cost_center,
            },
        )
    for account, amount in sorted(credit_totals.items()):
        journal_entry.append(
            "accounts",
            {
                "account": account,
                "credit_in_account_currency": flt(amount),
            },
        )

    journal_entry.flags.ignore_permissions = True
    journal_entry.insert()
    journal_entry.submit()

    frappe.db.set_value(
        "POS Closing Shift",
        closing_shift_doc.name,
        "cash_draw_journal_entry",
        journal_entry.name,
        update_modified=False,
    )
    closing_shift_doc.cash_draw_journal_entry = journal_entry.name
    for row in cash_draws:
        frappe.db.set_value(
            "POS Cash Draw",
            row.name,
            {
                "pos_closing_shift": closing_shift_doc.name,
                "journal_entry": journal_entry.name,
            },
            update_modified=False,
        )

    return journal_entry


def _cancel_cash_draw_journal_entry(closing_shift_doc):
    journal_entry_name = closing_shift_doc.get("cash_draw_journal_entry")
    if journal_entry_name and frappe.db.exists("Journal Entry", journal_entry_name):
        journal_entry = frappe.get_doc("Journal Entry", journal_entry_name)
        if journal_entry.docstatus == 1:
            journal_entry.flags.ignore_links = True
            journal_entry.cancel()

    cash_draw_names = frappe.get_all(
        "POS Cash Draw",
        filters={"pos_closing_shift": closing_shift_doc.name, "docstatus": 1},
        pluck="name",
    )
    for cash_draw_name in cash_draw_names:
        frappe.db.set_value(
            "POS Cash Draw",
            cash_draw_name,
            {"pos_closing_shift": None, "journal_entry": None},
            update_modified=False,
        )


class POSClosingShift(Document):
    def validate(self):
        user = frappe.get_all(
            "POS Closing Shift",
            filters={
                "user": self.user,
                "docstatus": 1,
                "pos_opening_shift": self.pos_opening_shift,
                "name": ["!=", self.name],
            },
        )

        if user:
            frappe.throw(
                _(
                    "POS Closing Shift {} against {} between selected period".format(
                        frappe.bold("already exists"), frappe.bold(self.user)
                    )
                ),
                title=_("Invalid Period"),
            )

        if frappe.db.get_value("POS Opening Shift", self.pos_opening_shift, "status") != "Open":
            frappe.throw(
                _("Selected POS Opening Shift should be open."),
                title=_("Invalid Opening Entry"),
            )
        self.validate_unpaid_invoices()
        self.update_payment_reconciliation()

    def validate_unpaid_invoices(self):
        """Block shift closing when there are unpaid invoices and the
        `custom_disable_shift_closing_unpaid_invoices` POS Profile flag is on.

        If `posa_allow_delete` is also enabled, draft invoices for this shift
        are deleted first so they do not block closing.
        """
        profile = frappe.db.get_value(
            "POS Profile",
            self.pos_profile,
            [
                "custom_disable_shift_closing_unpaid_invoices",
                "posa_allow_delete",
                "create_pos_invoice_instead_of_sales_invoice",
            ],
            as_dict=True,
        ) or {}

        if not profile.get("custom_disable_shift_closing_unpaid_invoices"):
            return

        doctype = (
            "POS Invoice"
            if profile.get("create_pos_invoice_instead_of_sales_invoice")
            else "Sales Invoice"
        )

        if profile.get("posa_allow_delete"):
            drafts = frappe.get_all(
                doctype,
                filters={
                    "posa_pos_opening_shift": self.pos_opening_shift,
                    "docstatus": 0,
                    "posa_is_printed": 0,
                },
                pluck="name",
            )
            for name in drafts:
                frappe.delete_doc(doctype, name, force=1)

        unpaid = frappe.get_all(
            doctype,
            filters={
                "posa_pos_opening_shift": self.pos_opening_shift,
                "docstatus": 1,
                "is_return": 0,
                "outstanding_amount": [">", 0],
            },
            fields=["name", "customer", "outstanding_amount"],
        )

        if unpaid:
            rows = "".join(
                "<li>{name} &mdash; {customer} ({amount})</li>".format(
                    name=frappe.bold(inv.name),
                    customer=frappe.utils.escape_html(inv.customer or ""),
                    amount=frappe.utils.fmt_money(inv.outstanding_amount),
                )
                for inv in unpaid
            )
            frappe.throw(
                _(
                    "Cannot close shift. The following invoices are unpaid. "
                    "Please settle them before closing:<br><ul>{0}</ul>"
                ).format(rows),
                title=_("Unpaid Invoices"),
            )

    def update_payment_reconciliation(self):
        # update the difference values in Payment Reconciliation child table
        # get default precision for site
        precision = frappe.get_cached_value("System Settings", None, "currency_precision") or 3
        for d in self.payment_reconciliation:
            d.difference = +flt(d.closing_amount, precision) - flt(d.expected_amount, precision)

    def on_submit(self):
        opening_entry = frappe.get_doc("POS Opening Shift", self.pos_opening_shift)
        opening_entry.pos_closing_shift = self.name
        opening_entry.set_status()
        self.delete_draft_invoices()
        opening_entry.save()
        # link invoices with this closing shift so ERPNext can block edits
        self._set_closing_entry_invoices()

        if frappe.db.get_value(
            "POS Profile",
            self.pos_profile,
            "create_pos_invoice_instead_of_sales_invoice",
        ):
            pos_invoices = []
            for d in self.pos_transactions:
                invoice_details = frappe._dict(
                    frappe.db.get_value(
                        "POS Invoice",
                        d.pos_invoice,
                        [
                            "name as pos_invoice",
                            "customer",
                            "is_return",
                            "return_against",
                            "currency",
                        ],
                        as_dict=True,
                    )
                )
                if invoice_details:
                    pos_invoices.append(invoice_details)

            if pos_invoices:
                invoices_by_currency = {}
                for invoice in pos_invoices:
                    invoices_by_currency.setdefault(invoice.currency, []).append(invoice)

                for invoices in invoices_by_currency.values():
                    consolidate_pos_invoices(pos_invoices=invoices)

        _create_cash_draw_journal_entry(self)

    def on_cancel(self):
        _cancel_cash_draw_journal_entry(self)
        if frappe.db.exists("POS Opening Shift", self.pos_opening_shift):
            opening_entry = frappe.get_doc("POS Opening Shift", self.pos_opening_shift)
            if opening_entry.pos_closing_shift == self.name:
                opening_entry.pos_closing_shift = ""
                opening_entry.set_status()
                opening_entry.save()
        # remove links from invoices so they can be cancelled
        self._clear_closing_entry_invoices()

    def _set_closing_entry_invoices(self):
        """Set `pos_closing_entry` on linked invoices."""
        for d in self.pos_transactions:
            invoice = d.get("sales_invoice") or d.get("pos_invoice")
            if not invoice:
                continue
            doctype = "Sales Invoice" if d.get("sales_invoice") else "POS Invoice"
            if frappe.db.has_column(doctype, "pos_closing_entry"):
                frappe.db.set_value(doctype, invoice, "pos_closing_entry", self.name)

    def _clear_closing_entry_invoices(self):
        """Clear closing shift links, cancel merge logs and cancel consolidated sales invoices."""
        consolidated_sales_invoices = set()
        for d in self.pos_transactions:
            pos_invoice = d.get("pos_invoice")
            sales_invoice = d.get("sales_invoice")
            if pos_invoice:
                if frappe.db.has_column("POS Invoice", "pos_closing_entry"):
                    frappe.db.set_value("POS Invoice", pos_invoice, "pos_closing_entry", None)

                merge_logs = frappe.get_all(
                    "POS Invoice Merge Log",
                    filters={"pos_invoice": pos_invoice},
                    pluck="name",
                )
                for log in merge_logs:
                    log_doc = frappe.get_doc("POS Invoice Merge Log", log)
                    for field in (
                        "consolidated_invoice",
                        "consolidated_credit_note",
                    ):
                        si = log_doc.get(field)
                        if si:
                            consolidated_sales_invoices.add(si)
                    if log_doc.docstatus == 1:
                        log_doc.cancel()
                    frappe.delete_doc("POS Invoice Merge Log", log_doc.name, force=1)

                if frappe.db.has_column("POS Invoice", "consolidated_invoice"):
                    frappe.db.set_value("POS Invoice", pos_invoice, "consolidated_invoice", None)

                if frappe.db.has_column("POS Invoice", "status"):
                    pos_doc = frappe.get_doc("POS Invoice", pos_invoice)
                    pos_doc.set_status(update=True)

            if sales_invoice:
                if frappe.db.has_column("Sales Invoice", "pos_closing_entry"):
                    frappe.db.set_value("Sales Invoice", sales_invoice, "pos_closing_entry", None)
                if self._is_consolidated_sales_invoice(sales_invoice):
                    consolidated_sales_invoices.add(sales_invoice)

        for si in consolidated_sales_invoices:
            if frappe.db.exists("Sales Invoice", si):
                si_doc = frappe.get_doc("Sales Invoice", si)
                if si_doc.docstatus == 1:
                    si_doc.cancel()

    def _is_consolidated_sales_invoice(self, sales_invoice):
        """Return True if the Sales Invoice was generated by consolidating POS Invoices."""

        if not sales_invoice:
            return False

        if frappe.db.exists("POS Invoice Merge Log", {"consolidated_invoice": sales_invoice}):
            return True

        return bool(frappe.db.exists("POS Invoice Merge Log", {"consolidated_credit_note": sales_invoice}))

    def delete_draft_invoices(self):
        if frappe.get_value("POS Profile", self.pos_profile, "posa_allow_delete"):
            doctype = (
                "POS Invoice"
                if frappe.db.get_value(
                    "POS Profile",
                    self.pos_profile,
                    "create_pos_invoice_instead_of_sales_invoice",
                )
                else "Sales Invoice"
            )
            data = frappe.db.sql(
                f"""
		select
		    name
		from
		    `tab{doctype}`
		where
		    docstatus = 0 and posa_is_printed = 0 and posa_pos_opening_shift = %s
		""",
                (self.pos_opening_shift),
                as_dict=1,
            )

            for invoice in data:
                frappe.delete_doc(doctype, invoice.name, force=1)

    @frappe.whitelist()
    def get_payment_reconciliation_details(self):
        company_currency = frappe.get_cached_value("Company", self.company, "default_currency")

        sales_breakdown = defaultdict(float)
        net_breakdown = defaultdict(float)
        payment_breakdown = {}

        def update_payment_breakdown(mode_of_payment, base_amount=0, currency=None, amount=0):
            if not mode_of_payment:
                return

            row = payment_breakdown.setdefault(
                mode_of_payment,
                {"base": 0.0, "currencies": defaultdict(float)},
            )
            row["base"] += flt(base_amount)
            if currency:
                row["currencies"][currency] += flt(amount)

        cash_mode_of_payment = (
            frappe.db.get_value("POS Profile", self.pos_profile, "posa_cash_mode_of_payment") or "Cash"
        )

        for row in self.get("pos_transactions", []):
            invoice = row.get("sales_invoice") or row.get("pos_invoice")
            if not invoice:
                continue

            doctype = "Sales Invoice" if row.get("sales_invoice") else "POS Invoice"
            if not frappe.db.exists(doctype, invoice):
                continue

            invoice_doc = frappe.get_cached_doc(doctype, invoice)
            currency = invoice_doc.get("currency") or company_currency
            conversion_rate = (
                invoice_doc.get("conversion_rate")
                or invoice_doc.get("exchange_rate")
                or invoice_doc.get("target_exchange_rate")
                or invoice_doc.get("plc_conversion_rate")
                or 1
            )

            sales_breakdown[currency] += flt(invoice_doc.get("grand_total") or 0)
            net_breakdown[currency] += flt(invoice_doc.get("net_total") or 0)

            for payment in invoice_doc.get("payments", []):
                update_payment_breakdown(
                    payment.mode_of_payment,
                    get_base_value(payment, "amount", "base_amount", conversion_rate),
                    currency,
                    payment.amount,
                )

            change_amount = invoice_doc.get("change_amount") or 0
            if change_amount:
                update_payment_breakdown(
                    cash_mode_of_payment,
                    -get_base_value(
                        invoice_doc,
                        "change_amount",
                        "base_change_amount",
                        conversion_rate,
                    ),
                    currency,
                    -change_amount,
                )

        for row in self.get("pos_payments", []):
            payment_entry = row.get("payment_entry")
            if not payment_entry or not frappe.db.exists("Payment Entry", payment_entry):
                continue

            payment_doc = frappe.get_cached_doc("Payment Entry", payment_entry)
            multiplier = -1 if payment_doc.get("payment_type") == "Pay" else 1
            currency = (
                payment_doc.get("paid_from_account_currency")
                or payment_doc.get("paid_to_account_currency")
                or payment_doc.get("party_account_currency")
                or payment_doc.get("currency")
                or company_currency
            )
            base_amount = multiplier * abs(flt(payment_doc.get("base_paid_amount") or 0))
            paid_amount = multiplier * abs(flt(payment_doc.get("paid_amount") or 0))
            mode_of_payment = row.get("mode_of_payment") or payment_doc.get("mode_of_payment")

            update_payment_breakdown(mode_of_payment, base_amount, currency, paid_amount)

        mode_summaries = []
        payment_breakdown_copy = payment_breakdown.copy()
        for detail in self.get("payment_reconciliation", []):
            mop = detail.mode_of_payment
            breakdown = payment_breakdown_copy.pop(mop, None)
            currencies = []
            if breakdown:
                currencies = [
                    frappe._dict({"currency": currency, "amount": amount})
                    for currency, amount in sorted(breakdown["currencies"].items())
                    if amount
                ]

            base_total = flt(detail.expected_amount) - flt(detail.opening_amount)

            mode_summaries.append(
                frappe._dict(
                    {
                        "mode_of_payment": mop,
                        "base_amount": base_total,
                        "opening_amount": flt(detail.opening_amount),
                        "expected_amount": flt(detail.expected_amount),
                        "difference": flt(detail.difference),
                        "currency_breakdown": currencies,
                    }
                )
            )

        for mop, breakdown in payment_breakdown_copy.items():
            mode_summaries.append(
                frappe._dict(
                    {
                        "mode_of_payment": mop,
                        "base_amount": breakdown["base"],
                        "opening_amount": 0,
                        "expected_amount": breakdown["base"],
                        "difference": 0,
                        "currency_breakdown": [
                            frappe._dict({"currency": currency, "amount": amount})
                            for currency, amount in sorted(breakdown["currencies"].items())
                            if amount
                        ],
                    }
                )
            )

        sales_currency_breakdown = [
            frappe._dict({"currency": currency, "amount": amount})
            for currency, amount in sorted(sales_breakdown.items())
            if amount
        ]
        net_currency_breakdown = [
            frappe._dict({"currency": currency, "amount": amount})
            for currency, amount in sorted(net_breakdown.items())
            if amount
        ]

        return frappe.render_template(
            "posawesome/posawesome/doctype/pos_closing_shift/closing_shift_details.html",
            {
                "data": self,
                "currency": company_currency,
                "company_currency": company_currency,
                "mode_summaries": mode_summaries,
                "sales_currency_breakdown": sales_currency_breakdown,
                "net_currency_breakdown": net_currency_breakdown,
            },
        )


@frappe.whitelist()
def get_cashiers(doctype, txt, searchfield, start, page_len, filters):
    cashiers_list = frappe.get_all("POS Profile User", filters=filters, fields=["user"])
    result = []
    for cashier in cashiers_list:
        user_email = frappe.get_value("User", cashier.user, "email")
        if user_email:
            # Return list of tuples in format (value, label) where value is user ID and label shows both ID and email
            result.append([cashier.user, f"{cashier.user} ({user_email})"])
    return result


@frappe.whitelist()
def get_pos_invoices(pos_opening_shift, doctype=None):
    if not doctype:
        pos_profile = frappe.db.get_value("POS Opening Shift", pos_opening_shift, "pos_profile")
        use_pos_invoice = frappe.db.get_value(
            "POS Profile",
            pos_profile,
            "create_pos_invoice_instead_of_sales_invoice",
        )
        doctype = "POS Invoice" if use_pos_invoice else "Sales Invoice"
    submit_printed_invoices(pos_opening_shift, doctype)
    cond = " and ifnull(consolidated_invoice,'') = ''" if doctype == "POS Invoice" else ""
    data = frappe.db.sql(
        f"""
	select
		name
	from
		`tab{doctype}`
	where
		docstatus = 1 and posa_pos_opening_shift = %s{cond}
	""",
        (pos_opening_shift),
        as_dict=1,
    )

    data = [frappe.get_doc(doctype, d.name).as_dict() for d in data]

    return data


@frappe.whitelist()
def get_payments_entries(pos_opening_shift):
    return frappe.get_all(
        "Payment Entry",
        filters={
            "docstatus": 1,
            "reference_no": pos_opening_shift,
            "payment_type": ["in", ["Receive", "Pay"]],
        },
        fields=[
            "name",
            "mode_of_payment",
            "paid_amount",
            "base_paid_amount",
            "paid_from_account_currency",
            "paid_to_account_currency",
            "target_exchange_rate",
            "reference_no",
            "posting_date",
            "party",
            "payment_type",
        ],
    )


@frappe.whitelist()
def get_closing_shift_overview(pos_opening_shift):
    """Return invoice and payment totals for the provided POS Opening Shift."""

    if not pos_opening_shift:
        frappe.throw(_("POS Opening Shift is required to compute the overview."))

    opening_shift_doc = None
    opening_shift_name = None
    payload = pos_opening_shift

    if isinstance(payload, str):
        try:
            parsed = json.loads(payload)
        except ValueError:
            opening_shift_name = payload
        else:
            payload = parsed if isinstance(parsed, dict) else payload

    if isinstance(payload, dict):
        opening_shift_name = payload.get("name") or opening_shift_name
    elif getattr(payload, "doctype", None) == "POS Opening Shift":
        opening_shift_doc = payload
        opening_shift_name = payload.name
    elif opening_shift_name is None:
        opening_shift_name = getattr(payload, "name", None)

    if not opening_shift_doc:
        if not opening_shift_name:
            frappe.throw(_("Invalid POS Opening Shift data provided."))
        opening_shift_doc = frappe.get_doc("POS Opening Shift", opening_shift_name)

    if opening_shift_doc.doctype != "POS Opening Shift":
        frappe.throw(_("Unable to resolve POS Opening Shift."))

    pos_profile = opening_shift_doc.pos_profile
    company = opening_shift_doc.company
    company_currency = frappe.get_cached_value("Company", company, "default_currency")

    use_pos_invoice = frappe.db.get_value(
        "POS Profile",
        pos_profile,
        "create_pos_invoice_instead_of_sales_invoice",
    )
    doctype = "POS Invoice" if use_pos_invoice else "Sales Invoice"
    invoices = get_pos_invoices(opening_shift_doc.name, doctype)

    total_invoices = len(invoices)
    company_currency_total = 0
    multi_currency_totals = {}
    payments_by_mode = {}
    credit_company_currency_total = 0
    credit_invoices_count = 0
    credit_totals_by_currency = {}
    gross_company_currency_total = 0
    sale_invoices_count = 0
    returns_company_currency_total = 0
    returns_count = 0
    returns_totals_by_currency = {}
    change_company_currency_total = 0
    change_totals_by_currency = {}
    overpayment_change_company_currency_total = 0
    overpayment_change_totals_by_currency = {}
    total_change_totals_by_currency = {}

    cash_mode_of_payment = frappe.db.get_value("POS Profile", pos_profile, "posa_cash_mode_of_payment")
    if not cash_mode_of_payment:
        cash_mode_of_payment = "Cash"

    def accumulate_payment(container, mode, currency, amount, base_amount=0, conversion_rate=None):
        if not mode:
            return
        currency = currency or company_currency
        key = (mode, currency)
        if key not in container:
            container[key] = {
                "mode_of_payment": mode,
                "currency": currency,
                "total": 0,
                "company_currency_total": 0,
                "exchange_rates": set(),
            }
        container[key]["total"] += flt(amount)
        container[key]["company_currency_total"] += flt(base_amount)

        if currency != company_currency:
            rate = None
            if flt(amount):
                rate = abs(flt(base_amount)) / abs(flt(amount)) if base_amount else None
            if not rate and conversion_rate:
                rate = flt(conversion_rate)
            if rate:
                container[key]["exchange_rates"].add(rate)

    def resolve_payment_currency(payment_row, invoice_currency):
        for fieldname in (
            "currency",
            "account_currency",
            "payment_currency",
        ):
            value = payment_row.get(fieldname)
            if value:
                return value
        return invoice_currency or company_currency

    shift_invoice_names = {invoice.get("name") for invoice in invoices}
    invoice_shift_link_field_cache = {}
    invoice_membership_cache = {}
    overpayment_invoice_names = set()

    def resolve_shift_link_field(doctype_name):
        if doctype_name in invoice_shift_link_field_cache:
            return invoice_shift_link_field_cache[doctype_name]

        link_field = None
        try:
            meta = frappe.get_meta(doctype_name)
        except DoesNotExistError:
            meta = None

        if meta:
            for df in meta.get("fields", []):
                if df.fieldtype == "Link" and df.options == "POS Opening Shift":
                    link_field = df.fieldname
                    break

        invoice_shift_link_field_cache[doctype_name] = link_field
        return link_field

    def reference_belongs_to_shift(doctype_name, docname):
        key = (doctype_name, docname)
        if key in invoice_membership_cache:
            return invoice_membership_cache[key]

        if doctype_name == doctype and docname in shift_invoice_names:
            invoice_membership_cache[key] = True
            return True

        link_field = resolve_shift_link_field(doctype_name)
        if not link_field:
            invoice_membership_cache[key] = False
            return False

        value = frappe.db.get_value(doctype_name, docname, link_field)
        invoice_membership_cache[key] = bool(value and value == opening_shift_doc.name)
        return invoice_membership_cache[key]

    payment_entries = get_payments_entries(opening_shift_doc.name)

    payment_entry_names = [row.get("name") for row in payment_entries if row.get("name")]
    references_by_entry = defaultdict(list)

    if payment_entry_names:
        reference_meta = frappe.get_meta("Payment Entry Reference")
        reference_fieldnames = {df.fieldname for df in reference_meta.get("fields", [])}
        reference_fields = [
            "parent",
            "reference_doctype",
            "reference_name",
            "allocated_amount",
        ]

        if "exchange_rate" in reference_fieldnames:
            reference_fields.append("exchange_rate")
        if "allocated_amount_in_company_currency" in reference_fieldnames:
            reference_fields.append("allocated_amount_in_company_currency")
        if "base_allocated_amount" in reference_fieldnames:
            reference_fields.append("base_allocated_amount")

        reference_rows = frappe.get_all(
            "Payment Entry Reference",
            filters={"parent": ["in", payment_entry_names]},
            fields=reference_fields,
        )

        for reference in reference_rows:
            references_by_entry[reference.get("parent")].append(reference)

    for entry in payment_entries:
        if entry.get("payment_type") != "Pay":
            continue

        references = references_by_entry.get(entry.get("name")) or []

        for reference in references:
            reference_doctype = reference.get("reference_doctype")
            reference_name = reference.get("reference_name")
            belongs_to_shift = False

            if reference_doctype and reference_name:
                belongs_to_shift = reference_belongs_to_shift(
                    reference_doctype,
                    reference_name,
                )

            if belongs_to_shift and reference_doctype in {"POS Invoice", "Sales Invoice"}:
                overpayment_invoice_names.add(reference_name)

    def reference_base_amount(reference, fallback_rate=None):
        for fieldname in (
            "allocated_amount_in_company_currency",
            "base_allocated_amount",
        ):
            value = reference.get(fieldname)
            if value not in (None, ""):
                return flt(value)

        amount_value = flt(reference.get("allocated_amount") or 0)
        rate_value = reference.get("exchange_rate") or fallback_rate or 1
        return amount_value * flt(rate_value or 1)

    for invoice in invoices:
        conversion_rate = invoice.get("conversion_rate")
        base_grand_total = get_base_value(invoice, "grand_total", "base_grand_total", conversion_rate)
        company_currency_total += base_grand_total
        if base_grand_total >= 0:
            gross_company_currency_total += base_grand_total
            sale_invoices_count += 1
        else:
            returns_company_currency_total += abs(base_grand_total)
            returns_count += 1
        invoice_currency = invoice.get("currency") or company_currency
        invoice_total = invoice.get("rounded_total") or invoice.get("grand_total") or 0
        currency_entry = multi_currency_totals.setdefault(
            invoice_currency,
            {
                "currency": invoice_currency,
                "total": 0,
                "invoice_count": 0,
                "company_currency_total": 0,
                "exchange_rates": set(),
            },
        )
        currency_entry["total"] += flt(invoice_total)
        currency_entry["invoice_count"] += 1
        currency_entry["company_currency_total"] += flt(base_grand_total)

        if invoice_currency != company_currency:
            rate = flt(conversion_rate) if conversion_rate else None
            if not rate and flt(invoice_total):
                rate = abs(flt(base_grand_total)) / abs(flt(invoice_total)) if base_grand_total else None
            if rate:
                currency_entry["exchange_rates"].add(rate)

        change_amount = flt(invoice.get("change_amount") or 0)
        has_overpayment_entry = invoice.get("name") in overpayment_invoice_names

        if change_amount and not has_overpayment_entry:
            change_entry = change_totals_by_currency.setdefault(
                invoice_currency,
                {
                    "currency": invoice_currency,
                    "total": 0,
                    "company_currency_total": 0,
                    "exchange_rates": set(),
                },
            )
            change_entry["total"] += change_amount

            change_base_amount = flt(
                get_base_value(invoice, "change_amount", "base_change_amount", conversion_rate)
            )
            change_company_currency_total += change_base_amount
            change_entry["company_currency_total"] += change_base_amount

            total_change_entry = total_change_totals_by_currency.setdefault(
                invoice_currency,
                {
                    "currency": invoice_currency,
                    "total": 0,
                    "company_currency_total": 0,
                    "exchange_rates": set(),
                },
            )
            total_change_entry["total"] += change_amount
            total_change_entry["company_currency_total"] += change_base_amount

            if invoice_currency != company_currency:
                rate = None
                if change_amount:
                    rate = abs(change_base_amount) / abs(change_amount) if change_base_amount else None
                if not rate and conversion_rate:
                    rate = flt(conversion_rate)
                if rate:
                    change_entry["exchange_rates"].add(rate)
                    total_change_entry["exchange_rates"].add(rate)

        outstanding_company_currency = invoice.get("base_outstanding_amount")
        if outstanding_company_currency in (None, ""):
            outstanding_company_currency = invoice.get("outstanding_amount")
        if outstanding_company_currency in (None, ""):
            outstanding_company_currency = get_base_value(
                invoice,
                "outstanding_amount",
                "base_outstanding_amount",
                conversion_rate,
            )
        outstanding_company_currency = flt(outstanding_company_currency or 0)

        if outstanding_company_currency > 0:
            credit_invoices_count += 1
            credit_company_currency_total += outstanding_company_currency
            outstanding_invoice_currency = invoice.get("outstanding_amount")
            if outstanding_invoice_currency in (None, ""):
                base_divisor = flt(conversion_rate) or 0
                if base_divisor:
                    outstanding_invoice_currency = outstanding_company_currency / base_divisor
                else:
                    outstanding_invoice_currency = outstanding_company_currency
            outstanding_invoice_currency = flt(outstanding_invoice_currency or 0)
            credit_entry = credit_totals_by_currency.setdefault(
                invoice_currency,
                {
                    "currency": invoice_currency,
                    "total": 0,
                    "invoice_count": 0,
                    "company_currency_total": 0,
                    "exchange_rates": set(),
                },
            )
            credit_entry["total"] += flt(outstanding_invoice_currency)
            credit_entry["invoice_count"] += 1
            credit_entry["company_currency_total"] += outstanding_company_currency

            if invoice_currency != company_currency:
                rate = None
                if outstanding_invoice_currency:
                    rate = abs(outstanding_company_currency) / abs(flt(outstanding_invoice_currency))
                if not rate and conversion_rate:
                    rate = flt(conversion_rate)
                if rate:
                    credit_entry["exchange_rates"].add(rate)

        is_return = bool(invoice.get("is_return"))
        if not is_return and flt(invoice_total) < 0:
            is_return = True

        if is_return:
            returns_entry = returns_totals_by_currency.setdefault(
                invoice_currency,
                {
                    "currency": invoice_currency,
                    "total": 0,
                    "invoice_count": 0,
                    "company_currency_total": 0,
                    "exchange_rates": set(),
                },
            )
            returns_entry["total"] += abs(flt(invoice_total))
            returns_entry["invoice_count"] += 1
            returns_entry["company_currency_total"] += abs(flt(base_grand_total))

            if invoice_currency != company_currency:
                rate = flt(conversion_rate) if conversion_rate else None
                if not rate and flt(invoice_total):
                    rate = abs(flt(base_grand_total)) / abs(flt(invoice_total)) if base_grand_total else None
                if rate:
                    returns_entry["exchange_rates"].add(rate)

        for payment in invoice.get("payments", []):
            mode = payment.get("mode_of_payment")
            payment_currency = resolve_payment_currency(payment, invoice_currency)
            amount = flt(payment.get("amount") or 0)
            base_amount = get_base_value(payment, "amount", "base_amount", conversion_rate)
            accumulate_payment(
                payments_by_mode,
                mode,
                payment_currency,
                amount,
                base_amount,
                conversion_rate,
            )

    for entry in payment_entries:
        mode = entry.get("mode_of_payment")
        payment_currency = (
            entry.get("paid_to_account_currency")
            or entry.get("paid_from_account_currency")
            or company_currency
        )
        raw_amount = flt(entry.get("paid_amount") or 0)
        entry_rate = (
            entry.get("target_exchange_rate")
            or entry.get("source_exchange_rate")
            or entry.get("exchange_rate")
        )
        raw_base_amount = get_base_value(
            entry,
            "paid_amount",
            "base_paid_amount",
            entry_rate,
        )

        multiplier = -1 if entry.get("payment_type") == "Pay" else 1
        amount = multiplier * abs(raw_amount)
        base_amount = multiplier * abs(raw_base_amount)

        if entry.get("payment_type") == "Pay":
            change_row = overpayment_change_totals_by_currency.setdefault(
                payment_currency,
                {
                    "currency": payment_currency,
                    "total": 0,
                    "company_currency_total": 0,
                    "exchange_rates": set(),
                },
            )
            refund_amount = abs(raw_amount)
            refund_base_amount = abs(raw_base_amount)
            change_row["total"] += refund_amount
            change_row["company_currency_total"] += refund_base_amount
            overpayment_change_company_currency_total += refund_base_amount

            total_change_entry = total_change_totals_by_currency.setdefault(
                payment_currency,
                {
                    "currency": payment_currency,
                    "total": 0,
                    "company_currency_total": 0,
                    "exchange_rates": set(),
                },
            )
            total_change_entry["total"] += refund_amount
            total_change_entry["company_currency_total"] += refund_base_amount

            if payment_currency != company_currency:
                rate = None
                if refund_amount:
                    rate = abs(refund_base_amount) / abs(refund_amount) if refund_base_amount else None
                if not rate and entry_rate:
                    rate = flt(entry_rate)
                if rate:
                    change_row["exchange_rates"].add(rate)
                    total_change_entry["exchange_rates"].add(rate)

        references = references_by_entry.get(entry.get("name")) or []
        allocated_amount_sum = 0
        allocated_base_sum = 0

        if references:
            for reference in references:
                allocated_amount = multiplier * abs(flt(reference.get("allocated_amount") or 0))
                if not allocated_amount:
                    continue

                allocated_base = multiplier * abs(reference_base_amount(reference, entry_rate))
                allocated_amount_sum += allocated_amount
                allocated_base_sum += allocated_base

                reference_doctype = reference.get("reference_doctype")
                reference_name = reference.get("reference_name")
                belongs_to_shift = False
                if reference_doctype and reference_name:
                    belongs_to_shift = reference_belongs_to_shift(
                        reference_doctype,
                        reference_name,
                    )

                rate = reference.get("exchange_rate") or entry_rate

                accumulate_payment(
                    payments_by_mode,
                    mode,
                    payment_currency,
                    allocated_amount,
                    allocated_base,
                    rate,
                )

        residual_amount = amount - allocated_amount_sum
        residual_base = base_amount - allocated_base_sum

        unallocated_amount = entry.get("unallocated_amount")
        if unallocated_amount not in (None, ""):
            residual_amount = multiplier * abs(flt(unallocated_amount))
            residual_base = multiplier * abs(
                get_base_value(
                    entry,
                    "unallocated_amount",
                    "base_unallocated_amount",
                    entry_rate,
                )
            )

        if abs(residual_amount) > 0.0001 or abs(residual_base) > 0.0001:
            accumulate_payment(
                payments_by_mode,
                mode,
                payment_currency,
                residual_amount,
                residual_base,
                entry_rate,
            )

    if cash_mode_of_payment:
        for row in payments_by_mode.values():
            if row["mode_of_payment"] != cash_mode_of_payment:
                continue

            overpayment_change_row = overpayment_change_totals_by_currency.get(row["currency"])
            if overpayment_change_row:
                row["total"] -= flt(overpayment_change_row.get("total"))

                base_overpayment_change = overpayment_change_row.get("company_currency_total")
                if base_overpayment_change:
                    row["company_currency_total"] -= flt(base_overpayment_change)

    cash_expected_totals = []
    cash_expected_company_currency_total = 0
    if cash_mode_of_payment:
        for row in payments_by_mode.values():
            if row["mode_of_payment"] == cash_mode_of_payment:
                cash_expected_totals.append(
                    {
                        "currency": row["currency"],
                        "total": flt(row["total"]),
                        "company_currency_total": flt(row["company_currency_total"]),
                        "exchange_rates": sorted(
                            {flt(rate) for rate in (row.get("exchange_rates") or []) if flt(rate)}
                        ),
                    },
                )
                cash_expected_company_currency_total += flt(row["company_currency_total"])

    average_invoice_value = 0
    if sale_invoices_count:
        average_invoice_value = gross_company_currency_total / sale_invoices_count

    def prepare_currency_rows(container, include_count=False):
        output = []
        for row in container.values():
            exchange_rates = row.get("exchange_rates") or []
            if isinstance(exchange_rates, set):
                exchange_rates = sorted({flt(rate) for rate in exchange_rates if flt(rate)})
            else:
                exchange_rates = [
                    flt(rate) for rate in exchange_rates if rate not in (None, "") and flt(rate)
                ]
                exchange_rates = sorted(set(exchange_rates))

            record = {
                "currency": row.get("currency"),
                "total": flt(row.get("total")),
                "company_currency_total": flt(row.get("company_currency_total")),
                "exchange_rates": exchange_rates,
            }
            if include_count:
                record["invoice_count"] = row.get("invoice_count", 0)
            output.append(record)
        return sorted(output, key=lambda r: (r.get("currency") or ""))

    def prepare_payment_rows(container):
        output = []
        for row in container.values():
            exchange_rates = row.get("exchange_rates") or []
            if isinstance(exchange_rates, set):
                exchange_rates = sorted({flt(rate) for rate in exchange_rates if flt(rate)})
            else:
                exchange_rates = [
                    flt(rate) for rate in exchange_rates if rate not in (None, "") and flt(rate)
                ]
                exchange_rates = sorted(set(exchange_rates))

            output.append(
                {
                    "mode_of_payment": row.get("mode_of_payment"),
                    "currency": row.get("currency"),
                    "total": flt(row.get("total")),
                    "company_currency_total": flt(row.get("company_currency_total")),
                    "exchange_rates": exchange_rates,
                }
            )

        output.sort(key=lambda r: (r.get("mode_of_payment") or "", r.get("currency") or ""))
        return output

    return {
        "total_invoices": total_invoices,
        "company_currency": company_currency,
        "company_currency_total": flt(company_currency_total),
        "multi_currency_totals": prepare_currency_rows(multi_currency_totals, include_count=True),
        "payments_by_mode": prepare_payment_rows(payments_by_mode),
        "credit_invoices": {
            "count": credit_invoices_count,
            "company_currency_total": flt(credit_company_currency_total),
            "by_currency": prepare_currency_rows(credit_totals_by_currency, include_count=True),
        },
        "sales_summary": {
            "gross_company_currency_total": flt(gross_company_currency_total),
            "net_company_currency_total": flt(company_currency_total),
            "average_invoice_value": flt(average_invoice_value),
            "sale_invoices_count": sale_invoices_count,
        },
        "returns": {
            "count": returns_count,
            "company_currency_total": flt(returns_company_currency_total),
            "by_currency": prepare_currency_rows(returns_totals_by_currency, include_count=True),
        },
        "change_returned": {
            "company_currency_total": flt(
                change_company_currency_total + overpayment_change_company_currency_total
            ),
            "by_currency": prepare_currency_rows(total_change_totals_by_currency),
            "invoice_change": {
                "company_currency_total": flt(change_company_currency_total),
                "by_currency": prepare_currency_rows(change_totals_by_currency),
            },
            "overpayment_change": {
                "company_currency_total": flt(overpayment_change_company_currency_total),
                "by_currency": prepare_currency_rows(overpayment_change_totals_by_currency),
            },
        },
        "cash_expected": {
            "mode_of_payment": cash_mode_of_payment,
            "company_currency_total": flt(cash_expected_company_currency_total),
            "by_currency": sorted(
                cash_expected_totals,
                key=lambda row: (row.get("currency") or ""),
            ),
        },
    }


@frappe.whitelist()
def make_closing_shift_from_opening(opening_shift):
    opening_shift = json.loads(opening_shift)
    opening_shift_doc = frappe.get_doc("POS Opening Shift", opening_shift.get("name"))
    use_pos_invoice = frappe.db.get_value(
        "POS Profile",
        opening_shift.get("pos_profile"),
        "create_pos_invoice_instead_of_sales_invoice",
    )
    doctype = "POS Invoice" if use_pos_invoice else "Sales Invoice"
    submit_printed_invoices(opening_shift.get("name"), doctype)
    closing_shift = frappe.new_doc("POS Closing Shift")
    closing_shift.pos_opening_shift = opening_shift.get("name")
    closing_shift.period_start_date = opening_shift.get("period_start_date")
    closing_shift.period_end_date = frappe.utils.get_datetime()
    closing_shift.pos_profile = opening_shift.get("pos_profile")
    closing_shift.user = opening_shift.get("user")
    closing_shift.company = opening_shift.get("company")
    closing_shift.grand_total = 0
    closing_shift.net_total = 0
    closing_shift.total_quantity = 0

    company_currency = frappe.get_cached_value("Company", closing_shift.company, "default_currency")

    invoices = get_pos_invoices(opening_shift.get("name"), doctype)

    pos_transactions = []
    taxes = []
    payments = []
    pos_payments_table = []
    for detail in opening_shift.get("balance_details"):
        payments.append(
            frappe._dict(
                {
                    "mode_of_payment": detail.get("mode_of_payment"),
                    "opening_amount": detail.get("amount") or 0,
                    "expected_amount": detail.get("amount") or 0,
                }
            )
        )

    invoice_field = "pos_invoice" if doctype == "POS Invoice" else "sales_invoice"

    for d in invoices:
        conversion_rate = d.get("conversion_rate")
        pos_transactions.append(
            frappe._dict(
                {
                    invoice_field: d.name,
                    "posting_date": d.posting_date,
                    "grand_total": get_base_value(d, "grand_total", "base_grand_total", conversion_rate),
                    "transaction_currency": d.get("currency") or company_currency,
                    "transaction_amount": flt(d.get("grand_total")),
                    "customer": d.customer,
                }
            )
        )
        base_grand_total = get_base_value(d, "grand_total", "base_grand_total", conversion_rate)
        base_net_total = get_base_value(d, "net_total", "base_net_total", conversion_rate)
        closing_shift.grand_total += base_grand_total
        closing_shift.net_total += base_net_total
        closing_shift.total_quantity += flt(d.total_qty)

        for t in d.taxes:
            existing_tax = [tx for tx in taxes if tx.account_head == t.account_head and tx.rate == t.rate]
            if existing_tax:
                existing_tax[0].amount += get_base_value(
                    t, "tax_amount", "base_tax_amount", d.get("conversion_rate")
                )
            else:
                taxes.append(
                    frappe._dict(
                        {
                            "account_head": t.account_head,
                            "rate": t.rate,
                            "amount": get_base_value(
                                t, "tax_amount", "base_tax_amount", d.get("conversion_rate")
                            ),
                        }
                    )
                )

        for p in d.payments:
            existing_pay = [pay for pay in payments if pay.mode_of_payment == p.mode_of_payment]
            if existing_pay:
                cash_mode_of_payment = frappe.get_value(
                    "POS Profile",
                    opening_shift.get("pos_profile"),
                    "posa_cash_mode_of_payment",
                )
                if not cash_mode_of_payment:
                    cash_mode_of_payment = "Cash"
                conversion_rate = d.get("conversion_rate")
                if existing_pay[0].mode_of_payment == cash_mode_of_payment:
                    amount = get_base_value(p, "amount", "base_amount", conversion_rate) - get_base_value(
                        d, "change_amount", "base_change_amount", conversion_rate
                    )
                else:
                    amount = get_base_value(p, "amount", "base_amount", conversion_rate)
                existing_pay[0].expected_amount += flt(amount)
            else:
                payments.append(
                    frappe._dict(
                        {
                            "mode_of_payment": p.mode_of_payment,
                            "opening_amount": 0,
                            "expected_amount": get_base_value(
                                p, "amount", "base_amount", d.get("conversion_rate")
                            ),
                        }
                    )
                )

    pos_payments = get_payments_entries(opening_shift.get("name"))

    for py in pos_payments:
        pos_payments_table.append(
            frappe._dict(
                {
                    "payment_entry": py.name,
                    "mode_of_payment": py.mode_of_payment,
                    "paid_amount": py.paid_amount,
                    "posting_date": py.posting_date,
                    "customer": py.party,
                }
            )
        )
        existing_pay = [pay for pay in payments if pay.mode_of_payment == py.mode_of_payment]
        multiplier = -1 if py.payment_type == "Pay" else 1
        signed_amount = multiplier * abs(get_base_value(py, "paid_amount", "base_paid_amount"))
        if existing_pay:
            existing_pay[0].expected_amount += signed_amount
        else:
            payments.append(
                frappe._dict(
                    {
                        "mode_of_payment": py.mode_of_payment,
                        "opening_amount": 0,
                        "expected_amount": signed_amount,
                    }
                )
            )

    closing_shift.set("pos_transactions", pos_transactions)
    closing_shift.set("payment_reconciliation", payments)
    closing_shift.set("taxes", taxes)
    closing_shift.set("pos_payments", pos_payments_table)

    # Snapshot per-mode sales before cash draws mutate expected_amount, so the
    # closing dialog can seed its reconciliation from clean figures.
    mode_shift_sales = {
        p.mode_of_payment: flt(p.expected_amount) - flt(p.opening_amount)
        for p in payments
        if p.get("mode_of_payment")
    }

    cash_draws = get_unposted_cash_draws(closing_shift.pos_opening_shift)
    _apply_cash_draw_reconciliation(closing_shift, cash_draws)

    if (
        cint(frappe.db.get_value("POS Profile", closing_shift.pos_profile, "custom_enable_fuel_customization") or 0)
        == 1
        and frappe.get_meta("POS Opening Shift").has_field("custom_nozzle_readings")
    ):
        sold_by_item_warehouse = _get_shift_sold_by_item(opening_shift_doc.name, closing_shift.pos_profile)
        sold_by_item = _aggregate_by_item(sold_by_item_warehouse)
        nozzle_count_by_item_warehouse = defaultdict(int)
        for row in opening_shift_doc.get("custom_nozzle_readings") or []:
            nozzle_count_by_item_warehouse[_row_item_warehouse_key(row)] += 1

        fuel_price_list = frappe.db.get_value("POS Profile", closing_shift.pos_profile, "selling_price_list")
        nozzle_rows = []
        nozzle_row_meta = []
        for row in opening_shift_doc.get("custom_nozzle_readings") or []:
            row_data = row.as_dict() if hasattr(row, "as_dict") else row
            opening_reading = _row_opening_reading(row_data)
            fuel_item, warehouse = _row_item_warehouse_key(row_data)
            sold_qty = flt(sold_by_item_warehouse.get((fuel_item, warehouse)) or 0)
            nozzle_count = nozzle_count_by_item_warehouse.get((fuel_item, warehouse), 0)
            nozzle_rows.append(
                {
                    "nozzle": row_data.get("nozzle") or row_data.get("nozzle_name") or "",
                    "fuel_item": fuel_item,
                    "fuel_pump": row_data.get("fuel_pump") or "",
                    "warehouse": warehouse,
                    "opening_reading": opening_reading,
                    # Never seeded from invoiced sales: the meter has to be read.
                    "closing_reading": opening_reading,
                    "meter_digits": _row_meter_digits(row_data),
                    "test_return_qty": 0,
                }
            )
            nozzle_row_meta.append(
                {
                    "expected_sold_qty": sold_qty,
                    "shared_tank_nozzle_count": nozzle_count,
                    "require_manual_closing": True,
                    "meter_digits": _row_meter_digits(row_data),
                    "rate": _get_fuel_item_rate(fuel_item, fuel_price_list),
                }
            )

        # Use set() so frappe converts row dicts to child documents.
        closing_shift.set("custom_nozzle_readings", nozzle_rows)

    response = closing_shift.as_dict()
    response["custom_nozzle_reading_meta"] = nozzle_row_meta if 'nozzle_row_meta' in locals() else []
    response["custom_fuel_sold_by_item"] = (
        {item: flt(qty) for item, qty in sold_by_item.items()} if 'sold_by_item' in locals() else {}
    )
    response["custom_fuel_mode_shift_sales"] = {
        mode: flt(amount) for mode, amount in mode_shift_sales.items()
    }
    response["custom_fuel_cash_mode_of_payment"] = (
        frappe.db.get_value("POS Profile", closing_shift.pos_profile, "posa_cash_mode_of_payment") or "Cash"
    )
    response["custom_fuel_credit_total"] = flt(
        _get_shift_credit_total(closing_shift.pos_opening_shift, closing_shift.pos_profile)
    )
    response["cash_draws_enabled"] = bool(
        cint(frappe.db.get_value("POS Profile", closing_shift.pos_profile, "posa_enable_cash_draw") or 0)
        or cash_draws
    )
    response["cash_draws"] = cash_draws
    response["cash_draw_names"] = [row.name for row in cash_draws]

    return response


@frappe.whitelist()
def submit_closing_shift(closing_shift):
    closing_shift = json.loads(closing_shift)
    nozzle_closing_readings = closing_shift.pop("nozzle_closing_readings", [])
    submitted_cash_draw_names = sorted(closing_shift.pop("cash_draw_names", []) or [])
    closing_shift_doc = frappe.get_doc(closing_shift)
    frappe.db.sql(
        "select name from `tabPOS Opening Shift` where name = %s for update",
        closing_shift_doc.pos_opening_shift,
    )
    cash_draws = get_unposted_cash_draws(closing_shift_doc.pos_opening_shift)
    current_cash_draw_names = sorted(row.name for row in cash_draws)
    if submitted_cash_draw_names != current_cash_draw_names:
        frappe.throw(_("Cash draws changed while the closing dialog was open. Reopen it and review the totals."))

    can_apply_fuel_reconciliation = (
        cint(
            frappe.db.get_value(
                "POS Profile",
                closing_shift_doc.pos_profile,
                "custom_enable_fuel_customization",
            )
            or 0
        )
        == 1
        and frappe.get_meta("POS Opening Shift").has_field("custom_nozzle_readings")
        and frappe.get_meta("POS Profile").has_field("custom_pump_nozzles")
    )

    _apply_cash_draw_reconciliation(
        closing_shift_doc,
        cash_draws,
        expected_includes_existing_draw=True,
        # On a fuel profile a mode may legitimately be overdrawn against its
        # recorded sales: unbilled metered fuel covers it. The aggregate is
        # validated against the meters in _apply_fuel_payment_reconciliation.
        validate_available=not can_apply_fuel_reconciliation,
    )
    closing_shift_doc.flags.ignore_permissions = True
    closing_shift_doc.save()

    opening_shift_doc = None
    pos_profile_doc = None
    sold_by_item_warehouse = {}

    if can_apply_fuel_reconciliation and nozzle_closing_readings:
        opening_shift_doc = frappe.get_doc("POS Opening Shift", closing_shift_doc.pos_opening_shift)
        pos_profile_doc = frappe.get_doc("POS Profile", closing_shift_doc.pos_profile)
        sold_by_item_warehouse = _get_shift_sold_by_item(
            closing_shift_doc.pos_opening_shift,
            closing_shift_doc.pos_profile,
        )
        _validate_shared_tank_closing_readings(
            opening_shift_doc,
            nozzle_closing_readings,
            sold_by_item_warehouse,
        )

    if can_apply_fuel_reconciliation and nozzle_closing_readings:
        dispensed_by_item_warehouse = _update_opening_nozzle_rows_and_get_dispensed(
            opening_shift_doc,
            nozzle_closing_readings,
        )

    if nozzle_closing_readings and frappe.get_meta("POS Closing Shift").has_field("custom_nozzle_readings"):
        _update_shift_nozzle_rows(closing_shift_doc, nozzle_closing_readings)

    if can_apply_fuel_reconciliation and nozzle_closing_readings:
        deficit_by_item_warehouse = _allocate_item_deficits_to_tanks(
            dispensed_by_item_warehouse,
            sold_by_item_warehouse,
        )

        fuel_invoice = _create_unreconciled_fuel_invoice(
            closing_shift_doc,
            pos_profile_doc,
            deficit_by_item_warehouse,
        )
        _attach_fuel_invoice_to_closing(closing_shift_doc, fuel_invoice)
        credit_total = _get_shift_credit_total(
            closing_shift_doc.pos_opening_shift, closing_shift_doc.pos_profile
        )
        _apply_fuel_payment_reconciliation(
            closing_shift_doc,
            pos_profile_doc,
            credit_total,
        )
        _update_pos_profile_nozzle_current_readings(pos_profile_doc, nozzle_closing_readings)
        closing_shift_doc.save(ignore_permissions=True)

    closing_shift_doc.submit()
    return closing_shift_doc.name


def submit_printed_invoices(pos_opening_shift, doctype):
    invoices_list = frappe.get_all(
        doctype,
        filters={
            "posa_pos_opening_shift": pos_opening_shift,
            "docstatus": 0,
            "posa_is_printed": 1,
        },
    )
    for invoice in invoices_list:
        invoice_doc = frappe.get_doc(doctype, invoice.name)
        invoice_doc.submit()
