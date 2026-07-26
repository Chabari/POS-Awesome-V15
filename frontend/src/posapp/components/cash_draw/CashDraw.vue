<template>
	<div class="cash-draw-page">
		<header class="cash-draw-header">
			<div>
				<p class="cash-draw-eyebrow">{{ __("Current Shift") }}</p>
				<h1>{{ __("Cash Draw") }}</h1>
			</div>
			<div v-if="context.pos_opening_shift" class="shift-reference">
				<v-icon size="18">mdi-clock-outline</v-icon>
				<span>{{ context.pos_opening_shift }}</span>
			</div>
		</header>

		<v-alert v-if="errorMessage" type="error" variant="tonal" closable class="mb-4" @click:close="errorMessage = ''">
			{{ errorMessage }}
		</v-alert>

		<div v-if="loading" class="cash-draw-loading">
			<v-progress-circular indeterminate color="primary" />
		</div>

		<template v-else>
			<section class="entry-section">
				<form class="entry-grid" @submit.prevent="submitCashDraw">
					<v-select
						v-model="form.mode_of_payment"
						:items="modeItems"
						:label="__('Mode of Payment')"
						prepend-inner-icon="mdi-wallet-outline"
						variant="outlined"
						density="comfortable"
						hide-details="auto"
						:disabled="saving"
					/>
					<v-text-field
						v-model="form.amount"
						:label="__('Amount')"
						:suffix="context.company_currency"
						prepend-inner-icon="mdi-cash"
						type="number"
						min="0.01"
						step="0.01"
						variant="outlined"
						density="comfortable"
						hide-details="auto"
						:disabled="saving"
					/>
					<v-textarea
						v-model="form.narration"
						:label="__('Narration')"
						prepend-inner-icon="mdi-text-box-outline"
						variant="outlined"
						density="comfortable"
						rows="2"
						auto-grow
						hide-details="auto"
						:disabled="saving"
					/>
					<v-btn
						type="submit"
						color="primary"
						variant="flat"
						size="large"
						prepend-icon="mdi-plus-circle-outline"
						:loading="saving"
						:disabled="!canSubmit"
					>
						{{ __("Record Draw") }}
					</v-btn>
				</form>
			</section>

			<section class="summary-strip">
				<div class="summary-total">
					<span>{{ __("Shift Cash Drawn") }}</span>
					<strong>{{ formatAmount(context.grand_total) }}</strong>
				</div>
				<div v-for="total in context.totals" :key="total.mode_of_payment" class="mode-total">
					<span>{{ total.mode_of_payment }}</span>
					<strong>{{ formatAmount(total.amount) }}</strong>
				</div>
			</section>

			<section class="history-section">
				<div class="section-heading">
					<h2>{{ __("Recorded Draws") }}</h2>
					<v-btn
						icon="mdi-refresh"
						variant="text"
						size="small"
						:title="__('Refresh')"
						:loading="refreshing"
						@click="loadContext(true)"
					/>
				</div>

				<div v-if="!context.cash_draws.length" class="empty-state">
					<v-icon size="34">mdi-cash-off</v-icon>
					<span>{{ __("No cash draws recorded for this shift") }}</span>
				</div>

				<div v-else class="draw-table-wrap">
					<table class="draw-table">
						<thead>
							<tr>
								<th>{{ __("Time") }}</th>
								<th>{{ __("Mode of Payment") }}</th>
								<th>{{ __("Narration") }}</th>
								<th class="text-end">{{ __("Amount") }}</th>
								<th class="action-column"><span class="sr-only">{{ __("Actions") }}</span></th>
							</tr>
						</thead>
						<tbody>
							<tr v-for="draw in context.cash_draws" :key="draw.name">
								<td>{{ formatTime(draw.posting_time) }}</td>
								<td><span class="mode-badge">{{ draw.mode_of_payment }}</span></td>
								<td class="narration-cell">{{ draw.narration }}</td>
								<td class="amount-cell">{{ formatAmount(draw.amount) }}</td>
								<td class="action-column">
									<v-btn
										icon="mdi-delete-outline"
										variant="text"
										color="error"
										size="small"
										:title="__('Cancel Cash Draw')"
										:loading="cancellingName === draw.name"
										@click="confirmCancel(draw)"
									/>
								</td>
							</tr>
						</tbody>
					</table>
				</div>
			</section>
		</template>
	</div>
</template>

<script>
/* global frappe */
import { getOpeningStorage } from "../../../offline/index.js";

const EMPTY_CONTEXT = () => ({
	enabled: false,
	pos_opening_shift: "",
	company_currency: "",
	modes: [],
	cash_draws: [],
	totals: [],
	grand_total: 0,
});

export default {
	name: "CashDraw",
	data() {
		return {
			context: EMPTY_CONTEXT(),
			form: {
				mode_of_payment: "",
				amount: "",
				narration: "",
			},
			loading: true,
			refreshing: false,
			saving: false,
			cancellingName: "",
			errorMessage: "",
			requestId: "",
		};
	},
	computed: {
		modeItems() {
			return (this.context.modes || []).map((row) => ({
				title: row.mode_of_payment,
				value: row.mode_of_payment,
			}));
		},
		canSubmit() {
			return (
				!this.saving &&
				this.context.enabled &&
				this.form.mode_of_payment &&
				Number(this.form.amount) > 0 &&
				this.form.narration.trim()
			);
		},
	},
	mounted() {
		this.loadContext();
	},
	methods: {
		openingShiftName() {
			return getOpeningStorage()?.pos_opening_shift?.name || "";
		},
		async loadContext(refreshOnly = false) {
			const openingShift = this.openingShiftName();
			if (!openingShift) {
				this.loading = false;
				this.errorMessage = this.__("Open a POS shift before recording a cash draw.");
				return;
			}

			this.errorMessage = "";
			if (refreshOnly) {
				this.refreshing = true;
			} else {
				this.loading = true;
			}

			try {
				const response = await frappe.call(
					"posawesome.posawesome.doctype.pos_cash_draw.pos_cash_draw.get_cash_draw_context",
					{ pos_opening_shift: openingShift },
				);
				this.context = { ...EMPTY_CONTEXT(), ...(response.message || {}) };
				if (!this.form.mode_of_payment && this.modeItems.length) {
					this.form.mode_of_payment = this.modeItems[0].value;
				}
			} catch (error) {
				this.errorMessage = this.getErrorMessage(error);
			} finally {
				this.loading = false;
				this.refreshing = false;
			}
		},
		async submitCashDraw() {
			if (!this.canSubmit) {
				return;
			}

			this.saving = true;
			this.errorMessage = "";
			this.requestId = this.requestId || this.createRequestId();
			try {
				await frappe.call(
					"posawesome.posawesome.doctype.pos_cash_draw.pos_cash_draw.create_cash_draw",
					{
						pos_opening_shift: this.context.pos_opening_shift,
						mode_of_payment: this.form.mode_of_payment,
						amount: Number(this.form.amount),
						narration: this.form.narration.trim(),
						client_request_id: this.requestId,
					},
				);
				this.form.amount = "";
				this.form.narration = "";
				this.requestId = "";
				this.eventBus.emit("show_message", {
					title: this.__("Cash draw recorded"),
					color: "success",
				});
				await this.loadContext(true);
			} catch (error) {
				this.errorMessage = this.getErrorMessage(error);
			} finally {
				this.saving = false;
			}
		},
		confirmCancel(draw) {
			frappe.confirm(
				this.__("Cancel cash draw {0}?", [draw.name]),
				() => this.cancelCashDraw(draw),
			);
		},
		async cancelCashDraw(draw) {
			this.cancellingName = draw.name;
			this.errorMessage = "";
			try {
				await frappe.call(
					"posawesome.posawesome.doctype.pos_cash_draw.pos_cash_draw.cancel_cash_draw",
					{ name: draw.name },
				);
				this.eventBus.emit("show_message", {
					title: this.__("Cash draw cancelled"),
					color: "info",
				});
				await this.loadContext(true);
			} catch (error) {
				this.errorMessage = this.getErrorMessage(error);
			} finally {
				this.cancellingName = "";
			}
		},
		createRequestId() {
			if (typeof crypto !== "undefined" && typeof crypto.randomUUID === "function") {
				return crypto.randomUUID();
			}
			return `${Date.now()}-${Math.random().toString(36).slice(2)}`;
		},
		getErrorMessage(error) {
			return (
				error?.messages?.[0] ||
				error?.message ||
				this.__("Unable to complete the cash draw request.")
			);
		},
		formatAmount(value) {
			const amount = Number(value || 0);
			return `${this.context.company_currency || ""} ${amount.toLocaleString(undefined, {
				minimumFractionDigits: 2,
				maximumFractionDigits: 2,
			})}`.trim();
		},
		formatTime(value) {
			return value ? String(value).slice(0, 5) : "";
		},
	},
};
</script>

<style scoped>
.cash-draw-page {
	max-width: 1180px;
	margin: 0 auto;
	padding: 28px 20px 48px;
	color: var(--pos-text-primary);
}

.cash-draw-header,
.section-heading,
.summary-strip {
	display: flex;
	align-items: center;
}

.cash-draw-header {
	justify-content: space-between;
	margin-bottom: 24px;
}

.cash-draw-header h1 {
	font-size: 1.75rem;
	line-height: 1.2;
	letter-spacing: 0;
}

.cash-draw-eyebrow {
	margin: 0 0 4px;
	font-size: 0.78rem;
	font-weight: 700;
	text-transform: uppercase;
	color: var(--pos-text-secondary);
}

.shift-reference {
	display: inline-flex;
	align-items: center;
	gap: 8px;
	font-size: 0.85rem;
	color: var(--pos-text-secondary);
}

.cash-draw-loading {
	display: grid;
	place-items: center;
	min-height: 300px;
}

.entry-section,
.history-section {
	background: var(--pos-card-bg);
	border: 1px solid var(--pos-border);
	border-radius: 8px;
}

.entry-section {
	padding: 20px;
}

.entry-grid {
	display: grid;
	grid-template-columns: minmax(190px, 0.8fr) minmax(180px, 0.7fr) minmax(260px, 1.5fr) auto;
	align-items: start;
	gap: 14px;
}

.summary-strip {
	gap: 28px;
	margin: 18px 0;
	padding: 16px 20px;
	border-top: 1px solid var(--pos-border);
	border-bottom: 1px solid var(--pos-border);
	overflow-x: auto;
}

.summary-total,
.mode-total {
	display: flex;
	flex-direction: column;
	gap: 3px;
	white-space: nowrap;
}

.summary-total {
	padding-right: 28px;
	border-right: 1px solid var(--pos-border);
}

.summary-total span,
.mode-total span {
	font-size: 0.75rem;
	color: var(--pos-text-secondary);
}

.summary-total strong {
	font-size: 1.2rem;
}

.history-section {
	overflow: hidden;
}

.section-heading {
	justify-content: space-between;
	padding: 14px 18px;
	border-bottom: 1px solid var(--pos-border);
}

.section-heading h2 {
	font-size: 1rem;
	letter-spacing: 0;
}

.draw-table-wrap {
	overflow-x: auto;
}

.draw-table {
	width: 100%;
	border-collapse: collapse;
}

.draw-table th,
.draw-table td {
	padding: 12px 16px;
	border-bottom: 1px solid var(--pos-border);
	text-align: left;
}

.draw-table th {
	font-size: 0.76rem;
	font-weight: 700;
	color: var(--pos-text-secondary);
	background: var(--pos-table-header-bg, rgba(0, 0, 0, 0.035));
}

.draw-table tbody tr:last-child td {
	border-bottom: 0;
}

.draw-table .text-end,
.amount-cell {
	text-align: right;
}

.amount-cell {
	font-weight: 700;
	white-space: nowrap;
}

.narration-cell {
	min-width: 240px;
}

.mode-badge {
	display: inline-flex;
	padding: 3px 8px;
	border-radius: 6px;
	background: rgba(25, 118, 210, 0.1);
	color: #1565c0;
	font-size: 0.78rem;
	font-weight: 700;
}

.action-column {
	width: 52px;
	text-align: center !important;
}

.empty-state {
	display: flex;
	align-items: center;
	justify-content: center;
	gap: 12px;
	min-height: 150px;
	color: var(--pos-text-secondary);
}

.sr-only {
	position: absolute;
	width: 1px;
	height: 1px;
	padding: 0;
	margin: -1px;
	overflow: hidden;
	clip: rect(0, 0, 0, 0);
	white-space: nowrap;
	border: 0;
}

@media (max-width: 900px) {
	.entry-grid {
		grid-template-columns: 1fr 1fr;
	}

	.entry-grid :deep(.v-textarea),
	.entry-grid .v-btn {
		grid-column: 1 / -1;
	}
}

@media (max-width: 600px) {
	.cash-draw-page {
		padding: 18px 10px 32px;
	}

	.cash-draw-header {
		align-items: flex-start;
		gap: 12px;
	}

	.shift-reference {
		max-width: 45%;
		word-break: break-word;
	}

	.entry-grid {
		grid-template-columns: 1fr;
	}

	.entry-grid :deep(.v-textarea),
	.entry-grid .v-btn {
		grid-column: auto;
	}
}
</style>
