/* global frappe */
import { ref, getCurrentInstance } from "vue";
import {
	initPromise,
	checkDbHealth,
	getOpeningStorage,
	setOpeningStorage,
	clearOpeningStorage,
	setTaxTemplate,
	ensureCacheProfile,
} from "../../offline/index.js";
import { refreshCsrfToken as syncCsrfToken } from "../../utils/session.js";
import { getBoundProfileName } from "../../utils/terminal.js";

/**
 * Decide whether a cached opening shift may be used.
 *
 * The cached shift is the previous user's, and on an offline or failed
 * check_opening_shift it used to be re-registered unconditionally — which is
 * how a till ended up selling under another branch's profile. Only honour it
 * when it belongs to the branch this terminal is actually bound to.
 */
function cachedOpeningForThisTerminal() {
	const data = getOpeningStorage();
	if (!data?.pos_profile?.name) return null;

	const bound = getBoundProfileName();
	if (bound && data.pos_profile.name !== bound) {
		console.warn(
			"Ignoring cached opening shift for",
			data.pos_profile.name,
			"- this terminal is bound to",
			bound,
		);
		return null;
	}
	return data;
}

export function usePosShift(openDialog) {
	const { proxy } = getCurrentInstance();
	const eventBus = proxy?.eventBus;

	const pos_profile = ref(null);
	const pos_opening_shift = ref(null);

	async function check_opening_entry() {
		await initPromise;
		await checkDbHealth();
		return frappe
			.call("posawesome.posawesome.api.shifts.check_opening_shift", {
				user: frappe.session.user,
			})
			.then(async (r) => {
				if (r.message) {
					pos_profile.value = r.message.pos_profile;
					pos_opening_shift.value = r.message.pos_opening_shift;

					// Backstop: almost no cache in this app is keyed by profile, so
					// if the server hands us a different branch than the cached data
					// belongs to, drop that data before anything reads it.
					try {
						await ensureCacheProfile(r.message.pos_profile?.name);
					} catch (e) {
						console.error("Failed to reconcile cache profile", e);
					}

					// If shift is from a previous day and daily close is enforced,
					// force the user to close it before proceeding.
					if (r.message.requires_closing) {
						eventBus?.emit("register_pos_profile", r.message);
						eventBus?.emit("show_message", {
							title: "You must close the previous day's shift before opening a new one.",
							color: "warning",
						});
						get_closing_data({ force_close: true });
						return;
					}

					if (pos_profile.value.taxes_and_charges) {
						frappe.call({
							method: "frappe.client.get",
							args: {
								doctype: "Sales Taxes and Charges Template",
								name: pos_profile.value.taxes_and_charges,
							},
							callback: (res) => {
								if (res.message) {
									setTaxTemplate(pos_profile.value.taxes_and_charges, res.message);
								}
							},
						});
					}
					eventBus?.emit("register_pos_profile", r.message);
					eventBus?.emit("set_company", r.message.company);
					try {
						frappe.realtime.emit("pos_profile_registered");
					} catch (e) {
						console.warn("Realtime emit failed", e);
					}
					console.info("LoadPosProfile");
					try {
						setOpeningStorage(r.message);
					} catch (e) {
						console.error("Failed to cache opening data", e);
					}
				} else {
					const data = cachedOpeningForThisTerminal();
					if (data) {
						pos_profile.value = data.pos_profile;
						pos_opening_shift.value = data.pos_opening_shift;
						eventBus?.emit("register_pos_profile", data);
						eventBus?.emit("set_company", data.company);
						try {
							frappe.realtime.emit("pos_profile_registered");
						} catch (e) {
							console.warn("Realtime emit failed", e);
						}
						console.info("LoadPosProfile (cached)");
						return;
					}
					openDialog && openDialog();
				}
			})
			.catch(() => {
				const data = cachedOpeningForThisTerminal();
				if (data) {
					pos_profile.value = data.pos_profile;
					pos_opening_shift.value = data.pos_opening_shift;
					eventBus?.emit("register_pos_profile", data);
					eventBus?.emit("set_company", data.company);
					try {
						frappe.realtime.emit("pos_profile_registered");
					} catch (e) {
						console.warn("Realtime emit failed", e);
					}
					console.info("LoadPosProfile (cached)");
					return;
				}
				openDialog && openDialog();
			});
	}

	async function get_closing_data(options = {}) {
		const cachedOpeningShift = getOpeningStorage()?.pos_opening_shift;
		if (!pos_opening_shift.value && cachedOpeningShift) {
			pos_opening_shift.value = cachedOpeningShift;
		}
		if (!pos_opening_shift.value) {
			return;
		}
		await syncCsrfToken();
		return frappe
			.call(
				"posawesome.posawesome.doctype.pos_closing_shift.pos_closing_shift.make_closing_shift_from_opening",
				{ opening_shift: pos_opening_shift.value },
			)
			.then((r) => {
				if (r.message) {
					if (options.force_close) {
						r.message.force_close = true;
					}
					eventBus?.emit("open_ClosingDialog", r.message);
				}
			});
	}

	async function submit_closing_pos(data) {
		await syncCsrfToken();
		frappe
			.call("posawesome.posawesome.doctype.pos_closing_shift.pos_closing_shift.submit_closing_shift", {
				closing_shift: data,
			})
			.then((r) => {
				if (r.message) {
					pos_opening_shift.value = null;
					pos_profile.value = null;
					clearOpeningStorage();
					eventBus?.emit("show_message", {
						title: `POS Shift Closed`,
						color: "success",
					});
					check_opening_entry();
				}
			});
	}

	return { pos_profile, pos_opening_shift, check_opening_entry, get_closing_data, submit_closing_pos };
}
