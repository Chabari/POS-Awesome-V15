/* global __, frappe */

export function parseBooleanSetting(value) {
	if (value === undefined || value === null) {
		return false;
	}

	if (typeof value === "string") {
		const normalized = value.trim().toLowerCase();
		return ["1", "true", "yes", "on"].includes(normalized);
	}

	if (typeof value === "number") {
		return value === 1;
	}

	return Boolean(value);
}

// When a POS Profile has fuel customizations enabled, sales invoices are saved
// with update_stock = 0 (handled server-side in _apply_fuel_item_warehouses), so
// stock is never decremented from the POS warehouse. Skip client-side stock
// availability checks for these profiles to avoid blocking legitimate sales.
export function isFuelStockCheckDisabled(posProfile) {
	return parseBooleanSetting(posProfile?.custom_enable_fuel_customization);
}

export function formatStockShortageError(itemName, availableQty, requestedQty) {
	const label = itemName || __("this item");
	const available = availableQty ?? 0;
	const requested = requestedQty ?? 0;

	return __("{0} has only {1} in stock. You requested {2}. Adjust quantity or restock.", [
		label,
		available,
		requested,
	]);
}

export function formatNegativeStockWarning(itemName, availableQty, requestedQty) {
	const label = itemName || __("this item");
	const available = availableQty ?? 0;
	const requested = requestedQty ?? 0;

	return __("Stock update: {0} has {1} available. Adding {2} will bring the stock below zero.", [
		label,
		available,
		requested,
	]);
}

// ---------------------------------------------------------------------------
// Live stock confirmation
//
// The till's saved quantities can lag behind the server (a Stock Entry does not
// change Item.modified, so the delta sync never sees it). Before refusing a sale
// because the saved number looks short, ask the server once for the live
// warehouse quantity. Callers fall back to the saved number when this returns null.
// ---------------------------------------------------------------------------

const toFiniteNumber = (value) => {
	if (value === undefined || value === null || value === "") {
		return null;
	}
	const num = Number(value);
	return Number.isFinite(num) ? num : null;
};

/**
 * True when the saved quantity on `item` would make a stock gate refuse
 * `requestedQty`, so the number is worth confirming with the server.
 * Returns false when there is no saved number (the gates don't block then) and
 * for batch items, whose availability is per batch and handled elsewhere.
 */
export function needsLiveStockCheck(item, requestedQty) {
	if (!item || !item.item_code) {
		return false;
	}
	if (!parseBooleanSetting(item.is_stock_item ?? 1) || parseBooleanSetting(item.has_batch_no)) {
		return false;
	}
	const saved = toFiniteNumber(item.available_qty) ?? toFiniteNumber(item.actual_qty);
	if (saved === null) {
		return false;
	}
	const requested = Math.abs(toFiniteNumber(requestedQty) ?? 1) || 1;
	const factor = toFiniteNumber(item.conversion_factor) || 1;
	// Gates compare both in raw and in stock units; cover whichever is stricter.
	return saved < Math.max(requested, requested * factor);
}

/**
 * Live quantity (stock UOM) of `itemCode` in `warehouse` from the server, or
 * null when offline, on error, or if the server takes longer than `timeoutMs`.
 */
export async function fetchLiveStockQty(itemCode, warehouse, { timeoutMs = 4000, call } = {}) {
	if (!itemCode || !warehouse) {
		return null;
	}
	const frappeCall = call || (typeof frappe !== "undefined" ? frappe.call : null);
	if (!frappeCall) {
		return null;
	}

	let timer = null;
	const timeout = new Promise((resolve) => {
		timer = setTimeout(() => resolve(null), timeoutMs);
	});

	const request = Promise.resolve(
		frappeCall({
			method: "posawesome.posawesome.api.items.get_available_qty",
			args: { items: JSON.stringify([{ item_code: itemCode, warehouse }]) },
		}),
	)
		.then((response) => {
			const row = Array.isArray(response?.message) ? response.message[0] : null;
			return row ? toFiniteNumber(row.available_qty) : null;
		})
		.catch((error) => {
			console.warn("Live stock check failed", itemCode, error);
			return null;
		});

	try {
		return await Promise.race([request, timeout]);
	} finally {
		clearTimeout(timer);
	}
}
