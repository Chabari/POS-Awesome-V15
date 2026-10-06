// Copyright (c) 2026, Youssef Restom and contributors
// For license information, please see license.txt

// "Collect Payment" on submitted Sales Invoices: phone prompt, or a payment the
// customer already made (split across their outstanding invoices if needed).
// Server side: posawesome/posawesome/api/mobile_collect.py

frappe.ui.form.on("Sales Invoice", {
	refresh(frm) {
		posa_mobile_collect.setup(frm);
	},
});

const posa_mobile_collect = {
	API: "posawesome.posawesome.api.mobile_collect",
	POLL_MS: 4000,

	setup(frm) {
		const doc = frm.doc;
		if (doc.docstatus !== 1 || doc.is_return || flt(doc.outstanding_amount) <= 0) {
			return;
		}
		frappe.call({
			method: `${this.API}.get_collect_context`,
			args: { invoice: doc.name },
			callback: (r) => {
				const ctx = r.message || {};
				if (!(ctx.modes || []).length || !ctx.can_collect) {
					return;
				}
				frm.add_custom_button(__("Collect Payment"), () => this.choose(frm, ctx)).addClass(
					"btn-primary",
				);
			},
		});
	},

	choose(frm, ctx) {
		const modes = ctx.modes.map((m) => m.mode_of_payment);
		const canPush = (mode) => (ctx.modes.find((m) => m.mode_of_payment === mode) || {}).can_push;
		const d = new frappe.ui.Dialog({
			title: __("Collect Payment - {0}", [frm.doc.name]),
			fields: [
				{
					fieldtype: "HTML",
					options: `<p>${__("Outstanding")}: <b>${format_currency(ctx.outstanding, frm.doc.currency)}</b>
						&nbsp;·&nbsp;${frappe.utils.escape_html(ctx.customer_name || ctx.customer)}</p>`,
				},
				{
					fieldname: "mode_of_payment",
					fieldtype: "Select",
					label: __("Mode of Payment"),
					options: modes.join("\n"),
					default: ctx.default_mode,
					reqd: 1,
				},
				{
					fieldname: "method",
					fieldtype: "Select",
					label: __("How is the customer paying?"),
					options: [__("Customer already paid"), __("Send payment prompt")].join("\n"),
					default: __("Customer already paid"),
					reqd: 1,
				},
				{
					fieldname: "phone",
					fieldtype: "Data",
					label: __("Customer phone"),
					default: ctx.phone,
					depends_on: `eval:doc.method==='${__("Send payment prompt")}'`,
				},
				{
					fieldname: "amount",
					fieldtype: "Currency",
					label: __("Amount"),
					default: Math.floor(ctx.outstanding),
					description: __("Whole shillings, at most the outstanding amount."),
					depends_on: `eval:doc.method==='${__("Send payment prompt")}'`,
				},
			],
			primary_action_label: __("Continue"),
			primary_action: (values) => {
				if (values.method === __("Send payment prompt")) {
					if (!canPush(values.mode_of_payment)) {
						frappe.msgprint(__("{0} cannot send payment prompts.", [values.mode_of_payment]));
						return;
					}
					if (!values.phone || !(values.amount > 0)) {
						frappe.msgprint(__("Enter the phone number and amount."));
						return;
					}
					d.hide();
					this.push(frm, values);
				} else {
					d.hide();
					this.pick(frm, ctx, values.mode_of_payment);
				}
			},
		});
		d.show();
	},

	// ---- Phone prompt ------------------------------------------------------
	push(frm, values) {
		frappe.call({
			method: `${this.API}.start_push`,
			args: {
				invoice: frm.doc.name,
				mode_of_payment: values.mode_of_payment,
				phone: values.phone,
				amount: values.amount,
			},
			freeze: true,
			freeze_message: __("Sending prompt..."),
			callback: (r) => {
				if (r.message && r.message.name) {
					this.wait(frm, r.message.name, values);
				}
			},
		});
	},

	wait(frm, name, values) {
		let done = false;
		let timer = null;
		const d = new frappe.ui.Dialog({
			title: __("Waiting for the customer"),
			fields: [{ fieldname: "status", fieldtype: "HTML" }],
			primary_action_label: __("Check status"),
			primary_action: () => check(),
			secondary_action_label: __("Not now - leave on credit"),
			secondary_action: () => close(),
		});
		const setStatus = (html) => d.fields_dict.status.$wrapper.html(html);
		setStatus(
			`<p>${__("Ask the customer to enter their M-Pesa PIN for {0} on {1}.", [
				format_currency(values.amount, frm.doc.currency),
				frappe.utils.escape_html(values.phone),
			])}</p><p class="text-muted">${__("This usually takes under a minute.")}</p>`,
		);
		const close = () => {
			clearInterval(timer);
			if (!done) {
				frappe.call({ method: "posawesome.posawesome.api.mobile_payments.cancel_push", args: { name } });
				frappe.show_alert({
					message: __("If the customer still pays, the payment is posted to this invoice automatically."),
					indicator: "orange",
				});
			}
			d.hide();
		};
		const apply = (row) => {
			if (!row || row.name !== name || done) return;
			if (row.status === "Success") {
				done = true;
				clearInterval(timer);
				setStatus(`<p class="text-success">${__("Payment {0} received. Posting...", [row.receipt || ""])}</p>`);
				frappe.call({
					method: `${this.API}.settle_push`,
					args: { invoice: frm.doc.name, payment: name },
					callback: (r) => {
						d.hide();
						this.posted(frm, r.message && r.message.payment_entry);
					},
				});
			} else if (row.status === "Failed") {
				done = true;
				clearInterval(timer);
				setStatus(
					`<p class="text-danger">${frappe.utils.escape_html(
						row.push_result || __("The customer did not complete the payment."),
					)}</p>`,
				);
			}
		};
		const check = () =>
			frappe.call({
				method: "posawesome.posawesome.api.mobile_payments.get_push_status",
				args: { name },
				callback: (r) => apply(r.message),
			});
		const onRealtime = (row) => apply(row);
		frappe.realtime.on("mobile_push_result", onRealtime);
		d.onhide = () => {
			clearInterval(timer);
			frappe.realtime.off("mobile_push_result", onRealtime);
		};
		timer = setInterval(check, this.POLL_MS);
		d.show();
	},

	// ---- Customer already paid -------------------------------------------
	pick(frm, ctx, mode) {
		frappe.call({
			method: `${this.API}.list_collectable`,
			args: { invoice: frm.doc.name, mode_of_payment: mode },
			callback: (r) => this.allocate(frm, ctx, mode, r.message || []),
		});
	},

	allocate(frm, ctx, mode, payments) {
		if (!payments.length) {
			frappe.msgprint(
				__("No unused payments on {0} in the last {1} days.", [mode, ctx.window_days]),
				__("Nothing to collect"),
			);
			return;
		}
		const invoices = [{ name: frm.doc.name, outstanding_amount: ctx.outstanding }].concat(
			ctx.other_invoices || [],
		);
		const cur = frm.doc.currency;
		const esc = frappe.utils.escape_html;
		const rows = payments
			.map(
				(p, i) => `<tr>
				<td><input type="checkbox" class="posa-mp" data-i="${i}"></td>
				<td><b>${format_currency(p.amount, cur)}</b></td>
				<td>${esc(p.receipt || p.name)}${p.released ? ` <span class="indicator-pill orange">${__("Released")}</span>` : ""}${
					p.for_this_invoice ? ` <span class="indicator-pill green">${__("For this invoice")}</span>` : ""
				}</td>
				<td>${esc(p.payer_name || "")} <span class="text-muted">${esc(p.phone || "")}</span></td>
				<td class="text-muted">${esc((p.received_at || "").slice(0, 16))}</td></tr>`,
			)
			.join("");
		const d = new frappe.ui.Dialog({
			title: __("Select the customer's payment - {0}", [mode]),
			size: "large",
			fields: [
				{
					fieldname: "payments_html",
					fieldtype: "HTML",
					options: `<div style="max-height:260px;overflow:auto"><table class="table table-sm">
						<thead><tr><th></th><th>${__("Amount")}</th><th>${__("Receipt")}</th><th>${__(
							"Payer",
						)}</th><th>${__("Received")}</th></tr></thead><tbody>${rows}</tbody></table></div>`,
				},
				{ fieldname: "summary", fieldtype: "HTML" },
				{
					fieldname: "allocations",
					fieldtype: "Table",
					label: __("Allocate to invoices"),
					cannot_add_rows: true,
					in_place_edit: true,
					data: [],
					fields: [
						{ fieldname: "invoice", fieldtype: "Link", options: "Sales Invoice", label: __("Invoice"), in_list_view: 1, read_only: 1, columns: 4 },
						{ fieldname: "outstanding", fieldtype: "Currency", label: __("Outstanding"), in_list_view: 1, read_only: 1, columns: 3 },
						{ fieldname: "amount", fieldtype: "Currency", label: __("Allocate"), in_list_view: 1, columns: 3 },
					],
				},
			],
			primary_action_label: __("Post Payment"),
			primary_action: () => {
				const selected = selection();
				if (!selected.length) {
					frappe.msgprint(__("Select a payment."));
					return;
				}
				const allocations = (d.fields_dict.allocations.grid.get_data() || [])
					.filter((r) => flt(r.amount) > 0)
					.map((r) => ({ invoice: r.invoice, amount: flt(r.amount) }));
				const total = selected.reduce((s, p) => s + flt(p.amount), 0);
				const allocated = allocations.reduce((s, r) => s + r.amount, 0);
				if (allocated - total > 0.05) {
					frappe.msgprint(__("You allocated more than the selected payments."));
					return;
				}
				const go = () =>
					frappe.call({
						method: `${this.API}.settle`,
						args: {
							invoice: frm.doc.name,
							mode_of_payment: mode,
							payments: selected.map((p) => p.name),
							allocations,
						},
						freeze: true,
						freeze_message: __("Posting payment..."),
						callback: (r) => {
							d.hide();
							this.posted(frm, r.message && r.message.payment_entry);
						},
					});
				const left = total - allocated;
				if (left > 0.05) {
					frappe.confirm(
						__("{0} is not allocated to any invoice and will stay on the customer as an advance. Continue?", [
							format_currency(left, cur),
						]),
						go,
					);
				} else {
					go();
				}
			},
		});
		const selection = () =>
			d.$wrapper
				.find("input.posa-mp:checked")
				.toArray()
				.map((el) => payments[cint(el.dataset.i)]);
		const refresh = () => {
			const total = selection().reduce((s, p) => s + flt(p.amount), 0);
			let left = total;
			const data = invoices.map((inv) => {
				const amount = Math.min(left, flt(inv.outstanding_amount));
				left = flt(left - amount, 2);
				return { invoice: inv.name, outstanding: flt(inv.outstanding_amount), amount };
			});
			d.fields_dict.allocations.df.data = data;
			d.fields_dict.allocations.grid.refresh();
			d.fields_dict.summary.$wrapper.html(
				total
					? `<p>${__("Selected")}: <b>${format_currency(total, cur)}</b>${
							left > 0.05 ? ` · ${__("Advance")}: <b>${format_currency(left, cur)}</b>` : ""
						}</p>`
					: "",
			);
		};
		d.$wrapper.on("change", "input.posa-mp", refresh);
		d.show();
		const own = payments.findIndex((p) => p.for_this_invoice);
		if (own >= 0) {
			d.$wrapper.find(`input.posa-mp[data-i="${own}"]`).prop("checked", true);
		}
		refresh();
	},

	posted(frm, payment_entry) {
		frappe.show_alert({
			message: __("Payment Entry {0} posted", [payment_entry || ""]),
			indicator: "green",
		});
		frm.reload_doc();
	},
};
