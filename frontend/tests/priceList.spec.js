import { describe, it, expect } from "vitest";
import { resolveEffectivePriceList } from "../src/posapp/utils/priceList.js";

const profile = { selling_price_list: "Regular Price" };

describe("resolveEffectivePriceList", () => {
	it("prefers the customer's own price list", () => {
		const info = {
			customer_price_list: "VIP",
			customer_group_price_list: "Wholesale",
		};
		expect(resolveEffectivePriceList(info, profile)).toBe("VIP");
	});

	it("falls back to the customer group's price list", () => {
		const info = {
			customer_price_list: null,
			customer_group_price_list: "Wholesale",
		};
		expect(resolveEffectivePriceList(info, profile)).toBe("Wholesale");
	});

	it("falls back to the POS Profile price list", () => {
		const info = { customer_price_list: null, customer_group_price_list: null };
		expect(resolveEffectivePriceList(info, profile)).toBe("Regular Price");
	});

	it("returns null when nothing is configured", () => {
		expect(resolveEffectivePriceList({}, {})).toBeNull();
	});

	it("tolerates missing customer info and profile", () => {
		expect(resolveEffectivePriceList(undefined, undefined)).toBeNull();
		expect(resolveEffectivePriceList(null, profile)).toBe("Regular Price");
	});

	it("ignores a customer info payload that is not an object", () => {
		expect(resolveEffectivePriceList("PAMOJA AGROVET", profile)).toBe("Regular Price");
	});

	it("treats an offline cached customer row the same as get_customer_info", () => {
		// setCustomerStorage() persists these two keys with the same names the
		// online payload uses, so the offline branch resolves identically.
		const cachedRow = {
			name: "PAMOJA AGROVET",
			customer_group: "WHOLESALE",
			customer_price_list: null,
			customer_group_price_list: "Wholesale",
		};
		expect(resolveEffectivePriceList(cachedRow, profile)).toBe("Wholesale");
	});
});
