/* global frappe */
/**
 * Guarded POS Profile (branch) switch.
 *
 * Switching branch has to do three things in the right order, or the till ends
 * up selling one branch's stock under another branch's name:
 *
 *   1. Get unsynced sales off the device if we can.
 *   2. Drop every cache that belongs to the old branch — but not the sales
 *      queue.
 *   3. Re-bind the terminal and reload the new branch's data.
 *
 * It deliberately never blocks.  Refusing to switch while offline sales are
 * pending would recreate the very lockout this feature exists to fix: a
 * terminal stranded on the wrong branch with no way back.
 */
import {
	clearProfileScopedCache,
	getPendingOfflineInvoiceCount,
	getPendingOfflinePaymentCount,
	syncOfflineInvoices,
	syncOfflinePayments,
} from "../offline/index.js";
import { setTerminalBinding, getBoundProfileName } from "./terminal.js";
import { pinia, useItemsStore } from "../posapp/stores/index.js";

function isOnline() {
	return typeof navigator === "undefined" ? true : navigator.onLine && window.serverOnline !== false;
}

export function getPendingWorkCount() {
	let invoices = 0;
	let payments = 0;
	try {
		invoices = getPendingOfflineInvoiceCount() || 0;
	} catch (e) {
		console.warn("Failed to read pending invoice count", e);
	}
	try {
		payments = getPendingOfflinePaymentCount() || 0;
	} catch (e) {
		console.warn("Failed to read pending payment count", e);
	}
	return { invoices, payments, total: invoices + payments };
}

/**
 * @param {{name: string, company?: string}} nextProfile
 * @param {{onProgress?: (stage: string, detail?: object) => void}} [options]
 * @returns {Promise<{switched: boolean, from: string|null, to: string,
 *                    pendingCarriedOver: number, synced: boolean}>}
 */
export async function switchPosProfile(nextProfile, { onProgress } = {}) {
	const target = typeof nextProfile === "string" ? { name: nextProfile } : nextProfile || {};
	if (!target.name) {
		throw new Error("switchPosProfile requires a POS Profile name");
	}

	const from = getBoundProfileName();
	const report = (stage, detail) => {
		try {
			onProgress?.(stage, detail);
		} catch (e) {
			console.warn("profile switch progress handler failed", e);
		}
	};

	let pending = getPendingWorkCount();
	let synced = false;

	// 1. Try to drain the queue first so the previous branch's sales land before
	//    its caches disappear.  Each queued invoice already carries its own
	//    pos_profile, so they post against the right branch either way — this is
	//    about getting them off a device that is changing hands.
	if (pending.total > 0 && isOnline()) {
		report("syncing", pending);
		try {
			if (pending.invoices > 0) await syncOfflineInvoices();
			if (pending.payments > 0) await syncOfflinePayments();
			synced = true;
		} catch (e) {
			console.error("Failed to sync pending work before profile switch", e);
		}
		pending = getPendingWorkCount();
	}

	// 2. Purge the old branch's data.  Never touches the offline queues.
	report("clearing", { from, to: target.name });
	await clearProfileScopedCache(target.name);

	// itemsStore holds its own LRU Maps and posa_items_* session entries on top
	// of the persistent cache. Pass the pinia instance explicitly — this runs
	// outside a component setup().
	try {
		useItemsStore(pinia).clearAllCaches();
	} catch (e) {
		console.warn("Failed to clear items store caches", e);
	}

	// 3. Bind the terminal to its new branch.
	setTerminalBinding({ pos_profile: target.name, company: target.company });

	// The cached boot profile is the other route by which a stale branch leaks
	// back in (utils/pos_profile.js reads frappe.boot.pos_profile).
	try {
		if (typeof frappe !== "undefined" && frappe.boot) {
			frappe.boot.pos_profile = null;
		}
	} catch (e) {
		console.warn("Failed to reset boot profile", e);
	}

	report("done", { from, to: target.name, pendingCarriedOver: pending.total });

	return {
		switched: true,
		from,
		to: target.name,
		pendingCarriedOver: pending.total,
		synced,
	};
}
