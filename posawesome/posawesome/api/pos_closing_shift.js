// Fuel customization: render nozzle reading & sales charts on POS Closing Shift.
// Only activates when the linked POS Profile has fuel customization enabled.
frappe.ui.form.on("POS Closing Shift", {
	refresh(frm) {
		render_fuel_nozzle_charts(frm);
	},
});

function render_fuel_nozzle_charts(frm) {
	const rows = (frm.doc.custom_nozzle_readings || []).filter(
		(r) => r.nozzle || r.fuel_item,
	);
	if (!rows.length || !frm.doc.pos_profile) {
		return;
	}

	frappe.db
		.get_value("POS Profile", frm.doc.pos_profile, "custom_enable_fuel_customization")
		.then((r) => {
			const enabled = r && r.message && r.message.custom_enable_fuel_customization;
			if (!cint(enabled)) {
				return;
			}
			draw_charts(frm, rows);
		});
}

function draw_charts(frm, rows) {
	const labels = rows.map((r) => r.nozzle || r.fuel_item || "");
	const opening = rows.map((r) => flt(r.opening_reading));
	const closing = rows.map((r) => flt(r.closing_reading));
	const sold = rows.map((r) => Math.max(flt(r.closing_reading) - flt(r.opening_reading), 0));

	frm.dashboard.add_section(
		`<div id="fuel-nozzle-readings-chart" style="margin-bottom:15px;"></div>
		 <div id="fuel-nozzle-sales-chart"></div>`,
		__("Fuel Nozzle Reconciliation"),
	);

	new frappe.Chart("#fuel-nozzle-readings-chart", {
		title: __("Nozzle Closing Readings"),
		data: {
			labels,
			datasets: [
				{ name: __("Opening"), values: opening },
				{ name: __("Closing"), values: closing },
			],
		},
		type: "line",
		height: 240,
		colors: ["#7cd6fd", "#2e7d32"],
	});

	new frappe.Chart("#fuel-nozzle-sales-chart", {
		title: __("Sales per Nozzle (Qty)"),
		data: { labels, datasets: [{ name: __("Sold Qty"), values: sold }] },
		type: "bar",
		height: 240,
		colors: ["#1976d2"],
	});
}
