<template>
	<v-dialog v-model="dialog" max-width="640px" persistent>
		<v-card>
			<v-card-title class="d-flex align-center">
				<v-icon class="mr-2" color="success">mdi-cellphone-check</v-icon>
				<span class="text-h6">{{ ctx.mode_of_payment }}</span>
				<v-spacer></v-spacer>
				<span class="text-caption text-medium-emphasis">{{ __("Account") }} {{ ctx.account_number }}</span>
			</v-card-title>

			<v-card-text>
				<v-alert v-if="error" type="error" density="compact" class="mb-3">{{ error }}</v-alert>

				<!-- Amount + how the customer pays -->
				<template v-if="step === 'choose'">
					<v-text-field
						v-model.number="amount"
						type="number"
						variant="outlined"
						density="comfortable"
						:label="__('Amount the customer pays by {0}', [ctx.mode_of_payment])"
						:prefix="currency"
						:hint="__('Maximum {0}', [formatCurrency(ctx.max_amount, 2)])"
						persistent-hint
						class="mb-4"
					></v-text-field>
					<v-alert v-if="!wholeAmount" type="warning" density="compact" class="mb-3">
						{{ __("Mobile payments are in whole shillings. Adjust the amount.") }}
					</v-alert>
					<v-row dense>
						<v-col cols="12" :sm="ctx.can_push ? 6 : 12">
							<v-btn block size="large" color="primary" :disabled="!validAmount" @click="start_match">
								<v-icon start>mdi-cash-check</v-icon>{{ __("Customer paid to till / paybill") }}
							</v-btn>
						</v-col>
						<v-col v-if="ctx.can_push" cols="12" sm="6">
							<v-btn block size="large" color="success" :disabled="!validAmount" @click="step = 'push'">
								<v-icon start>mdi-cellphone-message</v-icon>{{ __("Send payment prompt") }}
							</v-btn>
						</v-col>
					</v-row>
				</template>

				<!-- C2B: find the customer's payment by account + amount -->
				<template v-if="step === 'match'">
					<div class="d-flex align-center mb-2">
						<div class="text-body-1">
							{{ __("Looking for {0} paid to account {1}", [formatCurrency(amount, 2), ctx.account_number]) }}
						</div>
						<v-spacer></v-spacer>
						<v-switch
							v-model="parts"
							hide-details
							density="compact"
							color="primary"
							:label="__('Paid in parts')"
							@update:model-value="refresh_candidates"
						></v-switch>
					</div>

					<div v-if="!candidates.length" class="text-center py-6">
						<v-progress-circular indeterminate color="primary" class="mb-3"></v-progress-circular>
						<div class="text-body-2">
							{{ __("Waiting for the customer's payment. It usually arrives within 30 seconds.") }}
						</div>
						<div class="text-caption text-medium-emphasis">
							{{ __("Only payments of exactly this amount from the last {0} hours are shown.", [windowHours]) }}
						</div>
					</div>

					<v-list v-else density="compact" lines="two" class="border rounded">
						<v-list-item
							v-for="p in candidates"
							:key="p.name"
							:active="selected.includes(p.name)"
							color="success"
							@click="toggle(p)"
						>
							<template #prepend>
								<v-checkbox-btn :model-value="selected.includes(p.name)" @click.stop="toggle(p)"></v-checkbox-btn>
							</template>
							<v-list-item-title>
								<strong>{{ formatCurrency(p.amount, 2) }}</strong>
								&nbsp;·&nbsp;{{ p.payer_name || __("Unknown payer") }}
								<span class="text-medium-emphasis">&nbsp;{{ p.phone }}</span>
							</v-list-item-title>
							<v-list-item-subtitle>
								{{ p.receipt }} · {{ timeOnly(p.received_at) }}
								<v-chip v-if="p.payment_type === 'STK'" size="x-small" class="ml-1">{{ __("Prompt") }}</v-chip>
							</v-list-item-subtitle>
						</v-list-item>
					</v-list>
					<div v-if="candidates.length > 1 && !parts" class="text-caption mt-2">
						{{ __("More than one payment matches. Confirm the payer's name or phone with the customer.") }}
					</div>
					<div v-if="parts" class="text-caption mt-2">
						{{ __("Selected {0} of {1}", [formatCurrency(selectedTotal, 2), formatCurrency(amount, 2)]) }}
					</div>
				</template>

				<!-- STK: phone -->
				<template v-if="step === 'push'">
					<div class="text-body-1 mb-3">
						{{ __("Send a prompt for {0} to the customer's phone.", [formatCurrency(amount, 2)]) }}
					</div>
					<v-text-field
						v-model="phone"
						variant="outlined"
						density="comfortable"
						:label="__('Customer phone')"
						placeholder="07XXXXXXXX"
						autofocus
						@keydown.enter="send_push"
					></v-text-field>
				</template>

				<!-- STK: waiting / result -->
				<template v-if="step === 'waiting'">
					<div class="text-center py-4">
						<v-progress-circular
							:model-value="(countdown / pushSeconds) * 100"
							size="72"
							width="6"
							color="success"
							class="mb-3"
							>{{ countdown }}</v-progress-circular
						>
						<div class="text-body-1">{{ __("Ask the customer to enter their M-Pesa PIN.") }}</div>
						<div class="text-caption text-medium-emphasis">{{ phone }} · {{ formatCurrency(amount, 2) }}</div>
					</div>
				</template>

				<template v-if="step === 'failed'">
					<v-alert type="warning" density="compact">
						{{ push.push_result || __("The customer did not complete the payment.") }}
					</v-alert>
				</template>

				<template v-if="step === 'done'">
					<v-alert type="success" density="compact">
						{{ __("Payment {0} received.", [push.receipt || ""]) }}
					</v-alert>
				</template>
			</v-card-text>

			<v-card-actions>
				<v-btn v-if="['match', 'push', 'failed'].includes(step)" variant="text" @click="back">{{
					__("Back")
				}}</v-btn>
				<v-btn v-if="step === 'waiting'" variant="text" :loading="checking" @click="check_push">{{
					__("Check status")
				}}</v-btn>
				<v-spacer></v-spacer>
				<v-btn color="error" variant="text" @click="close">{{ __("Close") }}</v-btn>
				<v-btn
					v-if="step === 'match'"
					color="success"
					variant="flat"
					:disabled="!canUseSelection"
					:loading="busy"
					@click="use_selected"
					>{{ __("Use payment") }}</v-btn
				>
				<v-btn
					v-if="step === 'push'"
					color="success"
					variant="flat"
					:disabled="!phone"
					:loading="busy"
					@click="send_push"
					>{{ __("Send prompt") }}</v-btn
				>
				<v-btn v-if="step === 'failed'" color="success" variant="flat" @click="step = 'push'">{{
					__("Try again")
				}}</v-btn>
			</v-card-actions>
		</v-card>
	</v-dialog>
</template>

<script>
/* global __, frappe */
import format from "../../format";

const API = "posawesome.posawesome.api.mobile_payments";
const POLL_MS = 4000;
const PUSH_SECONDS = 90;

export default {
	mixins: [format],
	data: () => ({
		dialog: false,
		step: "choose",
		ctx: {},
		amount: 0,
		currency: "KES",
		candidates: [],
		selected: [],
		parts: false,
		windowHours: 3,
		phone: "",
		push: {},
		countdown: PUSH_SECONDS,
		pushSeconds: PUSH_SECONDS,
		busy: false,
		checking: false,
		error: "",
		pollTimer: null,
		tickTimer: null,
	}),
	computed: {
		wholeAmount() {
			return Math.abs(this.amount - Math.round(this.amount)) < 0.001;
		},
		validAmount() {
			return this.amount > 0 && this.wholeAmount && this.amount <= this.flt(this.ctx.max_amount) + 0.05;
		},
		selectedTotal() {
			return this.candidates
				.filter((p) => this.selected.includes(p.name))
				.reduce((sum, p) => sum + this.flt(p.amount), 0);
		},
		canUseSelection() {
			return this.selected.length > 0 && Math.abs(this.selectedTotal - this.amount) <= 0.05;
		},
	},
	methods: {
		open(data) {
			this.stop_timers();
			this.ctx = data || {};
			this.currency = data.currency || "KES";
			this.amount = Math.round(this.flt(data.amount));
			this.candidates = [];
			this.selected = [];
			this.parts = false;
			this.push = {};
			this.error = "";
			this.busy = false;
			this.phone = "";
			this.step = "choose";
			this.dialog = true;
			frappe
				.call({ method: `${API}.get_push_context`, args: { invoice: data.invoice } })
				.then((r) => {
					this.phone = (r && r.message && r.message.phone) || "";
				})
				.catch(() => {});
		},
		back() {
			this.stop_timers();
			this.error = "";
			this.step = "choose";
		},
		close() {
			if (this.step === "waiting" && this.push.name) {
				// Closing is not a failure: hide the push, keep it pending (a late
				// payment is still applied to this sale automatically).
				frappe.call({ method: `${API}.cancel_push`, args: { name: this.push.name } }).catch(() => {});
				this.eventBus.emit("show_message", {
					title: __(
						"If the customer still completes this payment it will be added to the sale automatically. Do not take the money another way.",
					),
					color: "warning",
				});
			}
			this.stop_timers();
			this.dialog = false;
		},
		stop_timers() {
			clearInterval(this.pollTimer);
			clearInterval(this.tickTimer);
			this.pollTimer = null;
			this.tickTimer = null;
		},
		timeOnly(value) {
			return (value || "").slice(11, 16);
		},

		// ---- C2B ---------------------------------------------------------
		start_match() {
			this.error = "";
			this.step = "match";
			this.selected = [];
			this.refresh_candidates();
			this.stop_timers();
			this.pollTimer = setInterval(this.refresh_candidates, POLL_MS);
		},
		async refresh_candidates() {
			if (this.step !== "match") return;
			try {
				const r = await frappe.call({
					method: `${API}.find_payments`,
					args: {
						invoice: this.ctx.invoice,
						mode_of_payment: this.ctx.mode_of_payment,
						amount: this.amount,
						exact: this.parts ? 0 : 1,
					},
				});
				const msg = (r && r.message) || {};
				this.windowHours = msg.window_hours || this.windowHours;
				this.candidates = msg.payments || [];
				const names = this.candidates.map((p) => p.name);
				this.selected = this.selected.filter((n) => names.includes(n));
				if (!this.parts && !this.selected.length && this.candidates.length === 1) {
					this.selected = [this.candidates[0].name];
				}
			} catch (e) {
				this.error = __("Could not load payments. Check the connection.");
			}
		},
		toggle(p) {
			if (this.parts) {
				this.selected = this.selected.includes(p.name)
					? this.selected.filter((n) => n !== p.name)
					: [...this.selected, p.name];
			} else {
				this.selected = [p.name];
			}
		},
		async use_selected() {
			this.busy = true;
			this.error = "";
			try {
				const r = await frappe.call({
					method: `${API}.attach_payments`,
					args: {
						invoice: this.ctx.invoice,
						mode_of_payment: this.ctx.mode_of_payment,
						payments: this.selected,
						amount: this.amount,
					},
				});
				this.finish(r && r.message);
			} catch (e) {
				this.error = this.messageOf(e) || __("The payment could not be attached. Search again.");
				this.refresh_candidates();
			} finally {
				this.busy = false;
			}
		},

		// ---- STK ---------------------------------------------------------
		async send_push() {
			if (!this.phone || this.busy) return;
			this.busy = true;
			this.error = "";
			try {
				const r = await frappe.call({
					method: `${API}.initiate_push`,
					args: {
						invoice: this.ctx.invoice,
						mode_of_payment: this.ctx.mode_of_payment,
						phone: this.phone,
						amount: this.amount,
						context: "Till",
					},
				});
				this.push = (r && r.message) || {};
				this.step = "waiting";
				this.countdown = PUSH_SECONDS;
				this.stop_timers();
				this.tickTimer = setInterval(() => {
					this.countdown = Math.max(this.countdown - 1, 0);
					if (!this.countdown) {
						clearInterval(this.tickTimer);
					}
				}, 1000);
				this.pollTimer = setInterval(this.check_push, POLL_MS);
			} catch (e) {
				this.error = this.messageOf(e) || __("The prompt could not be sent.");
			} finally {
				this.busy = false;
			}
		},
		async check_push() {
			if (!this.push.name || this.step !== "waiting") return;
			this.checking = true;
			try {
				const r = await frappe.call({ method: `${API}.get_push_status`, args: { name: this.push.name } });
				this.apply_push_status((r && r.message) || {});
			} catch (e) {
				// keep waiting; the poll retries
			} finally {
				this.checking = false;
			}
		},
		async apply_push_status(row) {
			if (!row || row.name !== this.push.name || this.step !== "waiting") return;
			if (row.status === "Success") {
				this.stop_timers();
				this.push = { ...this.push, ...row };
				this.step = "done";
				const r = await frappe.call({ method: `${API}.get_attached`, args: { invoice: this.ctx.invoice } });
				this.finish(r && r.message, 1200);
			} else if (row.status === "Failed") {
				this.stop_timers();
				this.push = { ...this.push, ...row };
				this.step = "failed";
			}
		},

		// ---- shared ------------------------------------------------------
		finish(result, delay = 0) {
			this.eventBus.emit("mobile_payment_attached", { invoice: this.ctx.invoice, result: result || {} });
			this.stop_timers();
			setTimeout(() => {
				this.dialog = false;
			}, delay);
		},
		messageOf(e) {
			try {
				const raw = e && (e._server_messages || (e.responseJSON && e.responseJSON._server_messages));
				if (raw) {
					return JSON.parse(raw)
						.map((m) => JSON.parse(m).message)
						.join(" ")
						.replace(/<[^>]+>/g, " ");
				}
			} catch (err) {
				/* fall through */
			}
			return (e && e.message) || "";
		},
		on_payment_received(data) {
			if (this.dialog && this.step === "match" && data && data.account_number === this.ctx.account_number) {
				this.refresh_candidates();
			}
		},
		on_push_result(row) {
			this.apply_push_status(row);
		},
	},
	created() {
		this.eventBus.on("open_mobile_payment", this.open);
		if (frappe.realtime) {
			frappe.realtime.on("mobile_payment_received", this.on_payment_received);
			frappe.realtime.on("mobile_push_result", this.on_push_result);
		}
	},
	beforeUnmount() {
		this.stop_timers();
		this.eventBus.off("open_mobile_payment", this.open);
		if (frappe.realtime) {
			frappe.realtime.off("mobile_payment_received", this.on_payment_received);
			frappe.realtime.off("mobile_push_result", this.on_push_result);
		}
	},
};
</script>
