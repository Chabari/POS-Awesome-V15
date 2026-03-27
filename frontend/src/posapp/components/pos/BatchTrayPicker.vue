<template>
	<v-dialog v-model="dialogVisible" max-width="900" persistent scrollable>
		<v-card class="batch-tray-picker-card">
			<v-card-title class="d-flex align-center justify-space-between pa-4">
				<div class="d-flex align-center">
					<v-icon color="green-darken-2" class="mr-2">mdi-sprout</v-icon>
					<span class="text-h6">{{ __("Pick Batch Trays") }}</span>
				</div>
				<v-btn icon variant="text" @click="close">
					<v-icon>mdi-close</v-icon>
				</v-btn>
			</v-card-title>

			<v-divider></v-divider>

			<v-card-text class="pa-4" style="max-height: 70vh; overflow-y: auto;">
				<!-- Batch Filter -->
				<div class="mb-4">
					<v-autocomplete
						v-model="selectedBatch"
						:items="batches"
						item-title="display_label"
						item-value="name"
						variant="outlined"
						density="compact"
						:label="__('Select Propagation Batch')"
						:loading="loadingBatches"
						prepend-inner-icon="mdi-package-variant-closed"
						clearable
						@update:model-value="onBatchSelected"
					>
						<template v-slot:item="{ props, item }">
							<v-list-item v-bind="props">
								<v-list-item-title class="font-weight-bold">
									{{ item.raw.batch_id || item.raw.name }}
								</v-list-item-title>
								<v-list-item-subtitle>
									{{ item.raw.seedling_variety }} &mdash;
									{{ __("Available: {0}", [item.raw.total_available]) }}
									&mdash; {{ item.raw.status }}
								</v-list-item-subtitle>
							</v-list-item>
						</template>
					</v-autocomplete>
				</div>

				<!-- Batch Info Banner -->
				<div v-if="selectedBatchData" class="batch-info-banner mb-4">
					<div class="d-flex flex-wrap gap-3">
						<div class="info-chip">
							<span class="info-label">{{ __("Batch") }}</span>
							<span class="info-value">{{ selectedBatchData.batch_id || selectedBatchData.name }}</span>
						</div>
						<div class="info-chip">
							<span class="info-label">{{ __("Variety") }}</span>
							<span class="info-value">{{ selectedBatchData.seedling_variety }}</span>
						</div>
						<div class="info-chip">
							<span class="info-label">{{ __("Available") }}</span>
							<span class="info-value text-green font-weight-bold">{{ selectedBatchData.total_available }}</span>
						</div>
						<div class="info-chip">
							<span class="info-label">{{ __("Status") }}</span>
							<span class="info-value">{{ selectedBatchData.status }}</span>
						</div>
					</div>
				</div>

				<!-- Search Trays -->
				<div v-if="trays.length" class="mb-3">
					<v-text-field
						v-model="traySearch"
						variant="outlined"
						density="compact"
						:placeholder="__('Search trays...')"
						prepend-inner-icon="mdi-magnify"
						clearable
						hide-details
					></v-text-field>
				</div>

				<!-- Trays Table -->
				<div v-if="loadingTrays" class="text-center pa-6">
					<v-progress-circular indeterminate color="primary"></v-progress-circular>
					<div class="text-caption mt-2">{{ __("Loading trays...") }}</div>
				</div>

				<div v-else-if="trays.length" class="trays-table-wrapper">
					<table class="trays-table">
						<thead>
							<tr>
								<th style="width: 50px;">{{ __("Pick") }}</th>
								<th>{{ __("Tray") }}</th>
								<th>{{ __("Type") }}</th>
								<th style="text-align: center;">{{ __("Available") }}</th>
								<th style="text-align: center; width: 120px;">{{ __("Qty to Pick") }}</th>
								<th style="text-align: center;">{{ __("Deposit") }}</th>
							</tr>
						</thead>
						<tbody>
							<tr
								v-for="tray in filteredTrays"
								:key="tray.name"
								class="tray-row"
								:class="{ 'tray-row-selected': tray.picked }"
							>
								<td style="text-align: center;">
									<v-checkbox
									:model-value="tray.picked"
										hide-details
										density="compact"
										color="success"
										@update:model-value="(val) => onTrayCheckToggle(tray, val)"
									></v-checkbox>
								</td>
								<td class="font-weight-medium">
									{{ tray.tray_label || tray.name }}
								</td>
								<td class="text-medium-emphasis">{{ tray.tray_type }}</td>
								<td style="text-align: center;" class="font-weight-bold">
									{{ tray.current_occupancy }}
								</td>
								<td style="text-align: center;">
									<v-text-field
									:model-value="tray.pick_qty"
									type="number"
									variant="outlined"
									density="compact"
									hide-details
									:min="0"
									:max="tray.current_occupancy"
									style="max-width: 100px; margin: 0 auto;"
									class="pick-qty-input"
									:class="{ 'qty-active': tray.pick_qty > 0 }"
									@change="onQtyInput(tray, $event)"
									></v-text-field>
								</td>
								<td style="text-align: center;" class="text-medium-emphasis">
									{{ formatCurrency(tray.deposit_amount || tray.default_deposit || 0) }}
								</td>
							</tr>
						</tbody>
					</table>
				</div>

				<div v-else-if="selectedBatch && !loadingTrays" class="text-center pa-6 text-medium-emphasis">
					<v-icon size="48" color="grey">mdi-tray-remove</v-icon>
					<div class="mt-2">{{ __("No trays with seedlings found in this batch") }}</div>
				</div>

				<!-- Summary Section -->
				<div v-if="pickedTrays.length" class="summary-section mt-4">
					<v-divider class="mb-3"></v-divider>
					<div class="d-flex flex-wrap gap-4">
						<div class="summary-card">
							<div class="summary-label">{{ __("Whole Trays") }}</div>
							<div class="summary-value text-blue font-weight-bold">{{ totalWholeTrays }}</div>
						</div>
						<div class="summary-card">
							<div class="summary-label">{{ __("Loose Pieces") }}</div>
							<div class="summary-value text-orange font-weight-bold">{{ totalLoosePieces }}</div>
						</div>
						<div class="summary-card">
							<div class="summary-label">{{ __("Total Seedlings") }}</div>
							<div class="summary-value text-green font-weight-bold">{{ totalSeedlings }}</div>
						</div>
						<div class="summary-card">
							<div class="summary-label">{{ __("Tray Deposit") }}</div>
							<div class="summary-value text-purple font-weight-bold">
								{{ formatCurrency(totalTrayDeposit) }}
							</div>
						</div>
					</div>
				</div>
			</v-card-text>

			<v-divider></v-divider>

			<v-card-actions class="pa-4">
				<v-btn variant="text" @click="close">{{ __("Cancel") }}</v-btn>
				<v-spacer></v-spacer>
				<v-btn
					color="success"
					variant="flat"
					:disabled="!pickedTrays.length"
					@click="confirmPick"
				>
					<v-icon class="mr-1">mdi-check</v-icon>
					{{ __("Confirm Pick ({0} seedlings)", [totalSeedlings]) }}
				</v-btn>
			</v-card-actions>
		</v-card>
	</v-dialog>
</template>

<script>
export default {
	name: "BatchTrayPicker",
	props: {
		modelValue: Boolean,
		itemCode: String,
		formatCurrency: {
			type: Function,
			default: (v) => v,
		},
	},
	emits: ["update:modelValue", "trays-picked"],
	data() {
		return {
			batches: [],
			selectedBatch: null,
			trays: [],
			traySearch: "",
			loadingBatches: false,
			loadingTrays: false,
		};
	},
	computed: {
		dialogVisible: {
			get() {
				return this.modelValue;
			},
			set(val) {
				this.$emit("update:modelValue", val);
			},
		},
		selectedBatchData() {
			if (!this.selectedBatch) return null;
			return this.batches.find((b) => b.name === this.selectedBatch) || null;
		},
		filteredTrays() {
			if (!this.traySearch) return this.trays;
			const q = this.traySearch.toLowerCase();
			return this.trays.filter((t) => {
				const label = (t.tray_label || t.name || "").toLowerCase();
				const type = (t.tray_type || "").toLowerCase();
				return label.includes(q) || type.includes(q);
			});
		},
		pickedTrays() {
			return this.trays.filter((t) => t.pick_qty > 0);
		},
		totalWholeTrays() {
			return this.pickedTrays.filter(
				(t) => t.picked && t.pick_qty === t.current_occupancy
			).length;
		},
		totalLoosePieces() {
			let loose = 0;
			this.pickedTrays.forEach((t) => {
				if (!t.picked || t.pick_qty < t.current_occupancy) {
					loose += t.pick_qty;
				}
			});
			return loose;
		},
		totalSeedlings() {
			return this.pickedTrays.reduce((sum, t) => sum + (t.pick_qty || 0), 0);
		},
		totalTrayDeposit() {
			return this.pickedTrays
				.filter((t) => t.picked && t.pick_qty === t.current_occupancy)
				.reduce((sum, t) => sum + (t.deposit_amount || t.default_deposit || 0), 0);
		},
	},
	watch: {
		modelValue(val) {
			if (val) {
				this.loadBatches();
			}
		},
	},
	methods: {
		async loadBatches() {
			this.loadingBatches = true;
			try {
				const r = await frappe.call({
					method: "posawesome.posawesome.api.batch_trays.get_available_batches",
					args: { item_code: this.itemCode || "" },
				});
				const raw = r.message || [];
				this.batches = raw.map((b) => ({
					...b,
					display_label: `${b.batch_id || b.name} — ${b.seedling_variety || ""} (${b.total_available} available)`,
				}));
			} catch (e) {
				console.error("Failed to load batches:", e);
			} finally {
				this.loadingBatches = false;
			}
		},

		async onBatchSelected(batchName) {
			this.trays = [];
			if (!batchName) return;

			this.loadingTrays = true;
			try {
				const r = await frappe.call({
					method: "posawesome.posawesome.api.batch_trays.get_batch_trays",
					args: { propagation_batch: batchName },
				});
				this.trays = (r.message || []).map((t) => ({
					...t,
					picked: false,
					pick_qty: 0,
				}));
			} catch (e) {
				console.error("Failed to load trays:", e);
			} finally {
				this.loadingTrays = false;
			}
		},

		updateTray(tray, updates) {
			// Force Vue reactivity by replacing the object in the array
			const idx = this.trays.indexOf(tray);
			if (idx === -1) return;
			this.trays.splice(idx, 1, Object.assign({}, tray, updates));
		},

		onTrayCheckToggle(tray, newPicked) {
			if (newPicked) {
				this.updateTray(tray, { picked: true, pick_qty: tray.current_occupancy });
			} else {
				this.updateTray(tray, { picked: false, pick_qty: 0 });
			}
		},

		onQtyInput(tray, event) {
			let qty = parseInt(event?.target?.value ?? event, 10) || 0;
			if (qty < 0) qty = 0;
			if (qty > tray.current_occupancy) qty = tray.current_occupancy;

			let picked = false;
			if (qty === tray.current_occupancy && qty > 0) {
				picked = true;
			}

			this.updateTray(tray, { pick_qty: qty, picked });
		},

		confirmPick() {
			const picked = this.pickedTrays.map((t) => ({
				tray: t.name,
				tray_label: t.tray_label || t.name,
				tray_type: t.tray_type,
				qty: t.pick_qty,
				current_occupancy: t.current_occupancy,
				is_whole_tray: t.picked && t.pick_qty === t.current_occupancy,
				deposit_amount: t.deposit_amount || t.default_deposit || 0,
				propagation_batch: this.selectedBatch,
				seedling_variety: t.seedling_variety,
			}));

			const summary = {
				propagation_batch: this.selectedBatchData.batch_id || this.selectedBatch,
				batch_data: this.selectedBatchData,
				picked_trays: picked,
				total_whole_trays: this.totalWholeTrays,
				total_loose_pieces: this.totalLoosePieces,
				total_seedlings: this.totalSeedlings,
				total_tray_deposit: this.totalTrayDeposit,
			};

			this.$emit("trays-picked", summary);
			this.close();
		},

		close() {
			this.dialogVisible = false;
			this.selectedBatch = null;
			this.trays = [];
			this.traySearch = "";
		},
	},
};
</script>

<style scoped>
.batch-tray-picker-card {
	border-radius: 12px;
}

.batch-info-banner {
	background: var(--subtle-accent, #f0fdf4);
	border: 1px solid var(--green-200, #bbf7d0);
	border-radius: 8px;
	padding: 12px 16px;
}

.info-chip {
	display: flex;
	flex-direction: column;
	gap: 2px;
}

.info-label {
	font-size: 11px;
	color: var(--text-muted);
	text-transform: uppercase;
	letter-spacing: 0.5px;
}

.info-value {
	font-size: 14px;
	font-weight: 600;
}

.trays-table-wrapper {
	max-height: 350px;
	overflow-y: auto;
	border: 1px solid var(--border-color, #e2e8f0);
	border-radius: 8px;
}

.trays-table {
	width: 100%;
	border-collapse: collapse;
}

.trays-table thead {
	position: sticky;
	top: 0;
	z-index: 1;
	background: var(--subtle-accent, #f8fafc);
}

.trays-table th {
	padding: 10px 12px;
	font-size: 12px;
	font-weight: 600;
	text-transform: uppercase;
	letter-spacing: 0.5px;
	color: var(--text-muted);
	border-bottom: 2px solid var(--border-color, #e2e8f0);
}

.tray-row {
	transition: background 0.15s;
}

.tray-row td {
	padding: 8px 12px;
	border-bottom: 1px solid var(--border-color, #f1f5f9);
}

.tray-row:hover {
	background: var(--subtle-fg, #f8fafc);
}

.tray-row-selected {
	background: var(--green-50, #f0fdf4) !important;
	border-left: 3px solid var(--green-500, #22c55e);
}

.pick-qty-input :deep(.v-field) {
	font-weight: 600;
	text-align: center;
}

.qty-active :deep(.v-field) {
	border-color: var(--red-500, #ef4444) !important;
}

.summary-section {
	background: var(--subtle-accent, #fafafa);
	border-radius: 8px;
	padding: 16px;
}

.summary-card {
	display: flex;
	flex-direction: column;
	gap: 4px;
	min-width: 120px;
}

.summary-label {
	font-size: 11px;
	color: var(--text-muted);
	text-transform: uppercase;
	letter-spacing: 0.5px;
}

.summary-value {
	font-size: 20px;
	font-weight: 700;
}
</style>
