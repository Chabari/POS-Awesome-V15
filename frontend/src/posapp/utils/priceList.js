/**
 * Resolve the effective selling price list for a customer.
 *
 * Order (mirrors erpnext.accounts.party.get_default_price_list):
 *   1. Customer.default_price_list       -> customer_info.customer_price_list
 *   2. Customer Group.default_price_list -> customer_info.customer_group_price_list
 *   3. POS Profile.selling_price_list
 *
 * Only the immediate customer group is consulted; ancestor groups are NOT
 * walked, matching ERPNext. Kept O(1) and allocation-free because it is called
 * from inside pricing loops.
 *
 * @param {object} customerInfo - a get_customer_info() payload, or an offline
 *   cached customer row, which carries the same two price list keys.
 * @param {object} posProfile
 * @returns {string|null}
 */
export function resolveEffectivePriceList(customerInfo, posProfile) {
	const info = customerInfo && typeof customerInfo === "object" ? customerInfo : {};
	return (
		info.customer_price_list || info.customer_group_price_list || posProfile?.selling_price_list || null
	);
}
