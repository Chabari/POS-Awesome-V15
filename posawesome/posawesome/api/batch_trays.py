"""
API endpoints for Propagation Batch tray picking in POS.
Handles fetching available batches, trays, pricing, and deducting seedlings on sale.
Also provides server-side hooks for tray deduction/reversal on invoice submit/cancel.
"""
import json

import frappe
from frappe import _
from frappe.utils import flt, cint, nowdate
from erpnext import get_default_currency

@frappe.whitelist()
def get_available_batches(item_code=None):
    """Get Propagation Batches that have available seedlings (occupancy > 0).
    Optionally filter by item_code if the item is linked to a seedling variety."""
    filters = {
        "docstatus": 1,
        "status": ["in", ["Ready", "Completed"]],
    }

    # If item_code provided, try to find linked seedling variety
    if item_code:
        filters["seedling_variety"] = item_code

    batches = frappe.get_all(
        "Propagation Batch",
        filters=filters,
        fields=[
            "name", "batch_id", "seedling_variety", "status",
            "available_seedlings", "total_tray_count",
            "start_date", "expected_ready_date",
        ],
        order_by="start_date desc",
    )

    # Only return batches that actually have seedlings in trays
    result = []
    for batch in batches:
        total_occupancy = frappe.db.sql("""
            SELECT COALESCE(SUM(t.current_occupancy), 0)
            FROM `tabTray` t
            WHERE t.propagation_batch = %s AND t.current_occupancy > 0
        """, batch.name)[0][0]

        if cint(total_occupancy) > 0:
            batch["total_available"] = cint(total_occupancy)
            result.append(batch)

    return result


@frappe.whitelist()
def get_batch_trays(propagation_batch):
	"""Get all trays for a propagation batch that have seedlings (occupancy > 0)."""
	if not propagation_batch:
		frappe.throw(_("Propagation Batch is required"))

	trays = frappe.get_all(
		"Tray",
		filters={
			"propagation_batch": propagation_batch,
			"current_occupancy": [">", 0],
		},
		fields=[
			"name", "tray_label", "tray_type", "total_holes",
			"current_occupancy", "status", "seedling_variety",
			"deposit_amount",
		],
		order_by="name asc",
	)

	# Get tray type details for deposit info
	for tray in trays:
		if tray.tray_type:
			tray_type_data = frappe.db.get_value(
				"Try Type", tray.tray_type,
				["name", "default_deposit_amount"],
				as_dict=True,
			)
			if tray_type_data:
				tray["default_deposit"] = flt(tray_type_data.get("default_deposit_amount", 0))

	return trays


@frappe.whitelist()
def get_tray_pricing(item_code):
    """Get pricing for tray UOM and loose pieces (Nos) for an item."""
    result = {"tray_rate": 0, "piece_rate": 0, "tray_uom": "Tray", "piece_uom": "Nos"}

    if not item_code:
        return result

    # Check item prices for different UOMs
    prices = frappe.get_all(
        "Item Price",
        filters={
            "item_code": item_code,
            "selling": 1,
        },
        fields=["uom", "price_list_rate", "price_list"],
        order_by="modified desc",
    )

    for price in prices:
        uom_lower = (price.uom or "").lower()
        if uom_lower in ("tray", "trays"):
            result["tray_rate"] = flt(price.price_list_rate)
            result["tray_uom"] = price.uom
        elif uom_lower in ("nos", "no", "piece", "pieces", "each", "unit"):
            result["piece_rate"] = flt(price.price_list_rate)
            result["piece_uom"] = price.uom

    # Fallback: check standard rate from Item
    if not result["piece_rate"]:		
        valuation_rate = flt(
            frappe.db.get_value("Bin", {"item_code": item_code}, "valuation_rate")
        )
        if not valuation_rate:
            valuation_rate = flt(frappe.db.get_value("Item", item_code, "valuation_rate"))
        if valuation_rate:
            result["piece_rate"] = valuation_rate

    return result


@frappe.whitelist()
def deduct_tray_seedlings(picked_trays, item_code=None, customer=None):
	"""Deduct seedlings from trays after invoice submission (legacy frontend call).

	Args:
		picked_trays: list of dicts with keys: tray, qty, is_whole_tray, propagation_batch
		item_code: the item being sold
		customer: the customer buying
	"""
	if isinstance(picked_trays, str):
		picked_trays = json.loads(picked_trays)

	if not picked_trays:
		return

	results = _deduct_trays(picked_trays, customer)
	frappe.db.commit()
	return results


# ============================================================
# SERVER-SIDE INVOICE HOOKS
# ============================================================

def process_tray_data_on_submit(doc):
	"""Called from before_submit hook on Sales Invoice / POS Invoice.
	Persists tray data from frontend into custom fields and deducts seedlings."""
	has_tray_items = False

	for item in doc.get("items", []):
		picked_trays_raw = item.get("posa_picked_trays")
		if not picked_trays_raw:
			continue

		picked_trays = picked_trays_raw
		if isinstance(picked_trays, str):
			try:
				picked_trays = json.loads(picked_trays)
			except (json.JSONDecodeError, TypeError):
				continue

		if not picked_trays:
			continue

		has_tray_items = True
		tray_summary_raw = item.get("posa_tray_summary")
		tray_summary = tray_summary_raw
		if isinstance(tray_summary, str):
			try:
				tray_summary = json.loads(tray_summary)
			except (json.JSONDecodeError, TypeError):
				tray_summary = {}

		# Set propagation batch from picked trays
		prop_batch = None
		for pt in picked_trays:
			if pt.get("propagation_batch"):
				prop_batch = pt["propagation_batch"]
				break
		if prop_batch:
			item.posa_propagation_batch = prop_batch

		# Populate summary fields
		if tray_summary:
			item.posa_total_whole_trays = cint(tray_summary.get("total_whole_trays", 0))
			item.posa_total_loose_pieces = cint(tray_summary.get("total_loose_pieces", 0))
			item.posa_tray_deposit = flt(tray_summary.get("total_tray_deposit", 0))

		# Serialize for storage
		item.posa_picked_trays = json.dumps(picked_trays) if not isinstance(picked_trays_raw, str) else picked_trays_raw
		if tray_summary and not isinstance(tray_summary_raw, str):
			item.posa_tray_summary = json.dumps(tray_summary)

		# Deduct seedlings from actual trays
		_deduct_trays(picked_trays, doc.customer, sales_invoice=doc.name)

	if has_tray_items:
		# Persist deposit amounts at invoice level
		deposit_received = flt(doc.get("posa_tray_deposit_received", 0))
		deposit_summary_raw = doc.get("posa_tray_deposit_summary")
		deposit_calculated = 0

		if deposit_summary_raw:
			dep_summary = deposit_summary_raw
			if isinstance(dep_summary, str):
				try:
					dep_summary = json.loads(dep_summary)
				except (json.JSONDecodeError, TypeError):
					dep_summary = {}
			deposit_calculated = flt(dep_summary.get("calculatedDeposit", 0))
			doc.posa_tray_deposit_summary = json.dumps(dep_summary) if not isinstance(deposit_summary_raw, str) else deposit_summary_raw

		doc.posa_tray_deposit_received = deposit_received
		doc.posa_tray_deposit_calculated = deposit_calculated

		# Check and complete batches if all seedlings sold
		_check_batch_completion(doc)


def revert_tray_data_on_cancel(doc):
	"""Called from on_cancel hook on Sales Invoice / POS Invoice.
	Reverses seedling deductions and restores batch status."""
	for item in doc.get("items", []):
		picked_trays_raw = item.get("posa_picked_trays")
		if not picked_trays_raw:
			continue

		picked_trays = picked_trays_raw
		if isinstance(picked_trays, str):
			try:
				picked_trays = json.loads(picked_trays)
			except (json.JSONDecodeError, TypeError):
				continue

		if not picked_trays:
			continue

		_reverse_tray_deductions(picked_trays, doc.customer)

	# Cancel the deposit payment entry if one was created
	pe_name = doc.get("posa_tray_deposit_payment_entry")
	if pe_name and frappe.db.exists("Payment Entry", pe_name):
		pe_doc = frappe.get_doc("Payment Entry", pe_name)
		if pe_doc.docstatus == 0:
			pe_doc.delete(ignore_permissions=True)
		elif pe_doc.docstatus == 1:
			pe_doc.cancel()


def create_deposit_payment_entry(doc):
	"""Called after submit. Creates a draft Payment Entry for the tray deposit amount."""
	deposit_received = flt(doc.get("posa_tray_deposit_received", 0))
	if deposit_received <= 0:
		return

	company = doc.company
	customer = doc.customer
	if not customer:
		return

	# Find the default receivable account
	receivable_account = frappe.db.get_value(
		"Company", company, "default_receivable_account"
	)
	if not receivable_account:
		frappe.log_error(
			f"Cannot create tray deposit PE for {doc.name}: no default receivable account for {company}"
		)
		return

	# Find a cash mode of payment from the POS profile
	mode_of_payment = None
	if doc.pos_profile:
		mode_of_payment = frappe.db.get_value(
			"POS Profile", doc.pos_profile, "posa_cash_mode_of_payment"
		)
	if not mode_of_payment:
		mode_of_payment = "Cash"

	# Get the paid-to account for the mode of payment
	paid_to = None
	mop_account = frappe.db.get_value(
		"Mode of Payment Account",
		{"parent": mode_of_payment, "company": company},
		"default_account",
	)
	if mop_account:
		paid_to = mop_account
	else:
		paid_to = frappe.db.get_value("Company", company, "default_cash_account")

	if not paid_to:
		frappe.log_error(
			f"Cannot create tray deposit PE for {doc.name}: no cash account found"
		)
		return

	currency = doc.currency or get_default_currency()

	pe = frappe.new_doc("Payment Entry")
	pe.payment_type = "Receive"
	pe.posting_date = nowdate()
	pe.company = company
	pe.mode_of_payment = mode_of_payment
	pe.party_type = "Customer"
	pe.party = customer
	pe.paid_from = receivable_account
	pe.paid_to = paid_to
	pe.paid_amount = deposit_received
	pe.received_amount = deposit_received
	pe.source_exchange_rate = 1
	pe.target_exchange_rate = 1
	pe.paid_from_account_currency = currency
	pe.paid_to_account_currency = currency
	pe.reference_no = doc.name
	pe.reference_date = nowdate()
	pe.remarks = _("Tray deposit for {0}").format(doc.name)

	pe.flags.ignore_permissions = True
	pe.save()

	# Link the payment entry back to the invoice
	doc.db_set("posa_tray_deposit_payment_entry", pe.name, update_modified=False)

	return pe.name


# ============================================================
# INTERNAL HELPERS
# ============================================================

def _deduct_trays(picked_trays, customer=None, sales_invoice=None):
	"""Deduct seedlings from individual trays and update batch counts."""
	results = []
	batches_affected = set()

	for pick in picked_trays:
		tray_name = pick.get("tray")
		qty = cint(pick.get("qty", 0))
		is_whole_tray = pick.get("is_whole_tray", False)
		prop_batch = pick.get("propagation_batch")

		if not tray_name or qty <= 0:
			continue

		tray = frappe.get_doc("Tray", tray_name)
		if tray.current_occupancy < qty:
			frappe.throw(
				_("Tray {0} only has {1} seedlings, cannot deduct {2}").format(
					tray_name, tray.current_occupancy, qty
				)
			)

		# Remember the batch before we potentially clear it
		if not prop_batch:
			prop_batch = tray.propagation_batch

		new_occupancy = tray.current_occupancy - qty
		tray.current_occupancy = new_occupancy

		if new_occupancy == 0:
			if is_whole_tray and customer:
				tray.customer = customer
				tray.customer_name = frappe.db.get_value("Customer", customer, "customer_name")
				tray.issued_date = nowdate()
				tray.status = "With Customer"
				# Append audit log entry
				tray.append("customer_log", {
					"customer": customer,
					"customer_name": tray.customer_name,
					"sales_invoice": sales_invoice,
					"propagation_batch": prop_batch,
					"issued_date": nowdate(),
					"deposit_amount": pick.get("deposit", 0),
				})
			else:
				tray.status = "Empty"
				tray.propagation_batch = ""
				tray.seedling_variety = ""
		elif new_occupancy < tray.total_holes:
			tray.status = "Partial"

		tray.flags.ignore_permissions = True
		tray.save()

		# Sync Tray Allocation current_seedlings
		_sync_tray_allocation_seedlings(tray_name, new_occupancy, prop_batch)

		if prop_batch:
			batches_affected.add(prop_batch)
			_update_batch_seedlings(prop_batch, qty)

		results.append({
			"tray": tray_name,
			"deducted": qty,
			"remaining": new_occupancy,
			"status": tray.status,
		})

	return results


def _reverse_tray_deductions(picked_trays, customer=None):
	"""Reverse seedling deductions when an invoice is cancelled."""
	batches_affected = set()

	for pick in picked_trays:
		tray_name = pick.get("tray")
		qty = cint(pick.get("qty", 0))
		is_whole_tray = pick.get("is_whole_tray", False)
		prop_batch = pick.get("propagation_batch")

		if not tray_name or qty <= 0:
			continue

		if not frappe.db.exists("Tray", tray_name):
			continue

		tray = frappe.get_doc("Tray", tray_name)

		# Restore occupancy
		new_occupancy = cint(tray.current_occupancy) + qty
		tray.current_occupancy = new_occupancy

		# Restore batch linkage if it was cleared
		if prop_batch and not tray.propagation_batch:
			tray.propagation_batch = prop_batch
			variety = frappe.db.get_value("Propagation Batch", prop_batch, "seedling_variety")
			if variety:
				tray.seedling_variety = variety

		# Restore tray status
		if is_whole_tray and tray.status == "With Customer":
			tray.customer = ""
			tray.customer_name = ""
			tray.issued_date = None
			# Remove the last customer log entry added during submit
			if tray.customer_log:
				tray.customer_log.pop()

		if new_occupancy >= tray.total_holes:
			tray.status = "Full"
		elif new_occupancy > 0:
			tray.status = "Partial"
			if tray.propagation_batch:
				tray.status = "Sown"

		tray.flags.ignore_permissions = True
		tray.save()

		# Sync Tray Allocation current_seedlings
		_sync_tray_allocation_seedlings(tray_name, new_occupancy, prop_batch)

		if prop_batch:
			batches_affected.add(prop_batch)
			_revert_batch_seedlings(prop_batch, qty)

	# Check if any completed batches should revert to Ready
	for batch_name in batches_affected:
		_check_batch_revert_to_ready(batch_name)


def _update_batch_seedlings(batch_name, qty_sold):
	"""Update propagation batch available seedlings after sale."""
	batch = frappe.get_doc("Propagation Batch", batch_name)
	current = cint(batch.available_seedlings)
	new_available = max(0, current - cint(qty_sold))
	batch.db_set("available_seedlings", new_available)

	if hasattr(batch, "quantity_sold"):
		current_sold = cint(batch.quantity_sold)
		batch.db_set("quantity_sold", current_sold + cint(qty_sold))


def _revert_batch_seedlings(batch_name, qty_returned):
	"""Reverse seedling deduction on a propagation batch."""
	batch = frappe.get_doc("Propagation Batch", batch_name)
	current = cint(batch.available_seedlings)
	batch.db_set("available_seedlings", current + cint(qty_returned))

	if hasattr(batch, "quantity_sold"):
		current_sold = cint(batch.quantity_sold)
		batch.db_set("quantity_sold", max(0, current_sold - cint(qty_returned)))


def _sync_tray_allocation_seedlings(tray_name, current_occupancy, batch_name=None):
	"""Update current_seedlings on the Tray Allocation row matching this tray."""
	filters = {"tray": tray_name, "parenttype": "Propagation Batch"}
	if batch_name:
		filters["parent"] = batch_name
	alloc_names = frappe.get_all("Tray Allocation", filters=filters, pluck="name")
	for name in alloc_names:
		frappe.db.set_value("Tray Allocation", name, "current_seedlings", cint(current_occupancy), update_modified=False)


def _check_batch_completion(doc):
	"""After deducting seedlings, check if any propagation batch is fully sold."""
	batches_seen = set()
	for item in doc.get("items", []):
		batch_name = item.get("posa_propagation_batch")
		if batch_name and batch_name not in batches_seen:
			batches_seen.add(batch_name)

	for batch_name in batches_seen:
		if not frappe.db.exists("Propagation Batch", batch_name):
			continue

		available = cint(frappe.db.get_value("Propagation Batch", batch_name, "available_seedlings"))
		status = frappe.db.get_value("Propagation Batch", batch_name, "status")

		# Also check if any trays still have occupancy
		remaining_occupancy = frappe.db.sql("""
			SELECT COALESCE(SUM(current_occupancy), 0)
			FROM `tabTray`
			WHERE propagation_batch = %s
		""", batch_name)[0][0]

		if available <= 0 and cint(remaining_occupancy) <= 0 and status == "Ready":
			frappe.db.set_value(
				"Propagation Batch", batch_name,
				{"status": "Completed", "completion_date": nowdate()},
				update_modified=False,
			)


def _check_batch_revert_to_ready(batch_name):
	"""After reversing a cancellation, check if a Completed batch should revert to Ready."""
	if not frappe.db.exists("Propagation Batch", batch_name):
		return

	status = frappe.db.get_value("Propagation Batch", batch_name, "status")
	if status != "Completed":
		return

	available = cint(frappe.db.get_value("Propagation Batch", batch_name, "available_seedlings"))
	if available > 0:
		frappe.db.set_value(
			"Propagation Batch", batch_name,
			{"status": "Ready", "completion_date": None},
			update_modified=False,
		)
