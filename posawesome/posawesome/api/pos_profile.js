// Copyright (c) 20201 Youssef Restom and contributors
// For license information, please see license.txt

frappe.ui.form.on("POS Profile", {
	setup: function (frm) {
		frm.set_query("posa_cash_mode_of_payment", function (doc) {
			return {
				filters: { type: "Cash" },
			};
		});

		// Prevent selecting the POS Profile's own default price list (or a
		// price list already added) as an additional price list column.
		frm.set_query("price_list", "posa_additional_price_lists", function (doc) {
			const already_selected = (doc.posa_additional_price_lists || [])
				.map((row) => row.price_list)
				.filter(Boolean);
			const excluded = doc.selling_price_list
				? [doc.selling_price_list, ...already_selected]
				: already_selected;
			if (!excluded.length) {
				return {};
			}
			return {
				filters: [["Price List", "name", "not in", excluded]],
			};
		});

		frappe.call({
			method: "posawesome.posawesome.api.utilities.get_language_options",
			callback: function (r) {
				if (!r.exc) {
					frm.fields_dict["posa_language"].df.options = r.message;
					frm.refresh_field("posa_language");
				}
			},
		});
	},
});
