// End-to-end-ish check of the till's add paths in ItemsSelector.vue with a stale
// saved quantity: the real component methods, real useCartValidation and real
// stockCoordinator run; only the server (frappe.call) and storage layer are faked.
import { beforeAll, beforeEach, describe, expect, it, vi } from "vitest";

let offline = false;
vi.mock("../src/offline/index.js", async () => {
	const handler = {
		has: (_t, key) => key !== "then",
		get: (_t, key) => (key === "then" ? undefined : key === "isOffline" ? () => offline : vi.fn()),
	};
	return new Proxy({}, handler);
});
vi.mock("../src/posapp/components/pos/CameraScanner.vue", () => ({ default: {} }));
vi.mock("vue-virtual-scroller", () => ({ RecycleScroller: {} }));

let ItemsSelector;
let stockCoordinator;
let useCartValidation;

beforeAll(async () => {
	globalThis.__ = (s, args) =>
		Array.isArray(args) ? s.replace(/\{(\d+)\}/g, (_m, i) => args[i]) : s;
	globalThis.frappe = { call: vi.fn(), _: globalThis.__, utils: { play_sound: vi.fn() } };
	ItemsSelector = (await import("../src/posapp/components/pos/ItemsSelector.vue")).default;
	stockCoordinator = (await import("../src/posapp/utils/stockCoordinator.js")).default;
	({ useCartValidation } = await import("../src/posapp/composables/useCartValidation.js"));
});

const BIASHARA = "BIASHARA STREET SHOP - VKB";

function serverHas(qty) {
	frappe.call.mockImplementation(async ({ method, args }) => {
		if (method !== "posawesome.posawesome.api.items.get_available_qty") {
			throw new Error("unexpected call " + method);
		}
		const [row] = JSON.parse(args.items);
		return { message: [{ ...row, available_qty: qty }] };
	});
}

function makeTill() {
	const m = ItemsSelector.methods;
	const till = {
		pos_profile: { name: "BIASHARA STREET", warehouse: BIASHARA, posa_block_sale_beyond_available_qty: 1 },
		stock_settings: { allow_negative_stock: 0 },
		blockSaleBeyondAvailableQty: true,
		qty: 1,
		hide_qty_decimals: true,
		float_precision: 2,
		eventBus: { emit: vi.fn() },
		cartValidation: useCartValidation(),
		prepareItemForCart: vi.fn(),
		showScanError: vi.fn(),
		playScanTone: vi.fn(),
		clearSearch: vi.fn(),
		focusItemSearch: vi.fn(),
		__: globalThis.__,
	};
	["add_item", "addScannedItemToInvoice", "confirmStockWithServer", "isNegativeStockEnabled"].forEach(
		(name) => (till[name] = m[name].bind(till)),
	);
	return till;
}

// What the Biashara till had in memory: KNEATED SWADDLE saved at 0.
const staleSwaddle = () => ({
	item_code: "VBS-00462",
	item_name: "KNEATED SWADDLE",
	is_stock_item: 1,
	stock_uom: "Nos",
	uom: "Nos",
	conversion_factor: 1,
	actual_qty: 0,
	_base_actual_qty: 0,
	item_uoms: [{ uom: "Nos", conversion_factor: 1 }],
});

const addedPayloads = (till) => till.eventBus.emit.mock.calls.filter(([e]) => e === "add_item").map(([, p]) => p);
const errorMessages = (till) =>
	till.eventBus.emit.mock.calls.filter(([e, p]) => e === "show_message" && p?.color === "error");

beforeEach(() => {
	offline = false;
	frappe.call.mockReset();
	stockCoordinator.clearAll?.();
});

describe("Biashara till with a stale saved quantity", () => {
	it("tap: sells KNEATED SWADDLE when the server has 13", async () => {
		serverHas(13);
		const till = makeTill();
		await till.add_item(staleSwaddle());

		expect(frappe.call).toHaveBeenCalledTimes(1);
		expect(JSON.parse(frappe.call.mock.calls[0][0].args.items)[0].warehouse).toBe(BIASHARA);
		expect(errorMessages(till)).toHaveLength(0);
		const [payload] = addedPayloads(till);
		// The cart's own gate (useItemAddition) reads these fields.
		expect(payload).toMatchObject({ item_code: "VBS-00462", actual_qty: 13, _base_actual_qty: 13 });
		expect(stockCoordinator.getBase("VBS-00462")).toBe(13);
	});

	it("scan: sells KNEATED SWADDLE when the server has 13", async () => {
		serverHas(13);
		const till = makeTill();
		await till.addScannedItemToInvoice(staleSwaddle(), "2100000004317");

		expect(till.showScanError).not.toHaveBeenCalled();
		expect(addedPayloads(till)).toHaveLength(1);
		expect(frappe.call).toHaveBeenCalledTimes(1); // confirmed once, not again in add_item
	});

	it("still blocks when the server agrees there is no stock", async () => {
		serverHas(0);
		const till = makeTill();
		await till.addScannedItemToInvoice(staleSwaddle(), "2100000004317");
		expect(till.showScanError).toHaveBeenCalledTimes(1);
		expect(addedPayloads(till)).toHaveLength(0);

		await till.add_item(staleSwaddle());
		expect(errorMessages(till)).toHaveLength(1);
		expect(addedPayloads(till)).toHaveLength(0);
	});

	it("blocks a request bigger than live stock, counting what's already in the cart", async () => {
		serverHas(13);
		stockCoordinator.updateReservations({ "VBS-00462": 12 }); // 12 already in the cart
		const till = makeTill();
		till.qty = 2;
		await till.add_item(staleSwaddle());
		expect(errorMessages(till)).toHaveLength(1);
		expect(addedPayloads(till)).toHaveLength(0);
	});

	it("offline: keeps the old behaviour and makes no call", async () => {
		offline = true;
		const till = makeTill();
		await till.add_item(staleSwaddle());
		expect(frappe.call).not.toHaveBeenCalled();
		expect(addedPayloads(till)).toHaveLength(0);
	});

	it("no server call when the saved quantity is enough", async () => {
		const till = makeTill();
		await till.add_item({ ...staleSwaddle(), actual_qty: 13, _base_actual_qty: 13 });
		expect(frappe.call).not.toHaveBeenCalled();
		expect(addedPayloads(till)).toHaveLength(1);
	});
});
