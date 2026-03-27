"""
API endpoints for Propagation Batch tray picking in POS.
Handles fetching available batches, trays, pricing, and deducting seedlings on sale.
"""
from warnings import filters

import frappe
from frappe import _
from frappe.utils import flt, cint, nowdate


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
	"""Deduct seedlings from trays after invoice submission.

	Args:
		picked_trays: list of dicts with keys: tray, qty, is_whole_tray
		item_code: the item being sold
		customer: the customer buying
	"""
	import json
	if isinstance(picked_trays, str):
		picked_trays = json.loads(picked_trays)

	if not picked_trays:
		return

	results = []
	for pick in picked_trays:
		tray_name = pick.get("tray")
		qty = cint(pick.get("qty", 0))
		is_whole_tray = pick.get("is_whole_tray", False)

		if not tray_name or qty <= 0:
			continue

		tray = frappe.get_doc("Tray", tray_name)
		if tray.current_occupancy < qty:
			frappe.throw(
				_("Tray {0} only has {1} seedlings, cannot deduct {2}").format(
					tray_name, tray.current_occupancy, qty
				)
			)

		new_occupancy = tray.current_occupancy - qty
		tray.current_occupancy = new_occupancy

		if new_occupancy == 0:
			tray.status = "Empty"
			tray.propagation_batch = ""
			tray.seedling_variety = ""
			if is_whole_tray and customer:
				tray.customer = customer
				tray.customer_name = frappe.db.get_value("Customer", customer, "customer_name")
				tray.issued_date = nowdate()
				tray.status = "With Customer"
		elif new_occupancy < tray.total_holes:
			tray.status = "Partial"

		tray.save(ignore_permissions=True)

		# Update the propagation batch available seedlings
		if pick.get("propagation_batch"):
			_update_batch_seedlings(pick["propagation_batch"], qty)

		results.append({
			"tray": tray_name,
			"deducted": qty,
			"remaining": new_occupancy,
			"status": tray.status,
		})

	frappe.db.commit()
	return results


def _update_batch_seedlings(batch_name, qty_sold):
	"""Update propagation batch available seedlings after sale."""
	batch = frappe.get_doc("Propagation Batch", batch_name)
	current = cint(batch.available_seedlings)
	new_available = max(0, current - cint(qty_sold))
	batch.db_set("available_seedlings", new_available)

	# Also update quantity_sold if the field exists
	if hasattr(batch, "quantity_sold"):
		current_sold = cint(batch.quantity_sold)
		batch.db_set("quantity_sold", current_sold + cint(qty_sold))

	# Update tray allocation child table
	_update_tray_allocation(batch_name, qty_sold)


def _update_tray_allocation(batch_name, qty_sold):
	"""Update tray allocation current_seedlings in batch."""
	# This is handled implicitly since we update Tray.current_occupancy
	# and batch.available_seedlings directly
	pass
