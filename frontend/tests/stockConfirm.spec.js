import { describe, it, expect, vi, beforeAll } from "vitest";
import { needsLiveStockCheck, fetchLiveStockQty } from "../src/posapp/utils/stock.js";

beforeAll(() => {
	globalThis.__ = (s) => s;
});

describe("needsLiveStockCheck", () => {
	const swaddle = { item_code: "VBS-00462", is_stock_item: 1, actual_qty: 0 };

	it("asks the server when the saved quantity is 0 (the Biashara case)", () => {
		expect(needsLiveStockCheck(swaddle, 1)).toBe(true);
	});

	it("does not ask when the saved quantity covers the request", () => {
		expect(needsLiveStockCheck({ ...swaddle, actual_qty: 13 }, 1)).toBe(false);
	});

	it("prefers available_qty over actual_qty, like the scan gate", () => {
		expect(needsLiveStockCheck({ ...swaddle, actual_qty: 13, available_qty: 0 }, 1)).toBe(true);
	});

	it("compares in stock units for a pack UOM", () => {
		// 2 packs of 6 = 12 units, only 10 saved
		expect(needsLiveStockCheck({ ...swaddle, actual_qty: 10, conversion_factor: 6 }, 2)).toBe(true);
	});

	it("skips items with no saved number, non-stock items and batch items", () => {
		expect(needsLiveStockCheck({ item_code: "X", is_stock_item: 1 }, 1)).toBe(false);
		expect(needsLiveStockCheck({ ...swaddle, is_stock_item: 0 }, 1)).toBe(false);
		expect(needsLiveStockCheck({ ...swaddle, has_batch_no: 1 }, 1)).toBe(false);
	});
});

describe("fetchLiveStockQty", () => {
	it("returns the server's available_qty", async () => {
		const call = vi.fn().mockResolvedValue({
			message: [{ item_code: "VBS-00462", warehouse: "W", available_qty: 13 }],
		});
		await expect(fetchLiveStockQty("VBS-00462", "W", { call })).resolves.toBe(13);
		expect(call).toHaveBeenCalledWith({
			method: "posawesome.posawesome.api.items.get_available_qty",
			args: { items: JSON.stringify([{ item_code: "VBS-00462", warehouse: "W" }]) },
		});
	});

	it("returns null on error, empty reply or missing warehouse", async () => {
		const failing = vi.fn().mockRejectedValue(new Error("network"));
		await expect(fetchLiveStockQty("A", "W", { call: failing })).resolves.toBeNull();
		const empty = vi.fn().mockResolvedValue({ message: [] });
		await expect(fetchLiveStockQty("A", "W", { call: empty })).resolves.toBeNull();
		await expect(fetchLiveStockQty("A", null, { call: empty })).resolves.toBeNull();
	});

	it("gives up after the timeout so the cashier is not left waiting", async () => {
		const hanging = vi.fn(() => new Promise(() => {}));
		await expect(fetchLiveStockQty("A", "W", { call: hanging, timeoutMs: 20 })).resolves.toBeNull();
	});
});
