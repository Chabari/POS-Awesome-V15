<template>
	<v-dialog v-model="closingDialog" max-width="900px" persistent>
		<v-card elevation="8" class="closing-dialog-card">
			<!-- Enhanced White Header -->
			<v-card-title class="closing-header pa-6 d-flex align-center">
				<div class="header-content">
					<div class="header-icon-wrapper">
						<v-icon class="header-icon" size="40">mdi-store-clock-outline</v-icon>
					</div>
					<div class="header-text">
						<h3 class="header-title">{{ __("Closing POS Shift") }}</h3>
						<p class="header-subtitle">
							{{ __("Reconcile payment methods and close shift") }}
						</p>
					</div>
				</div>
				<v-spacer></v-spacer>
				<v-btn
					icon="mdi-close"
					variant="text"
					density="comfortable"
					class="header-close-btn"
					:title="__('Close')"
					@click="close_dialog"
				></v-btn>
			</v-card-title>

			<v-divider class="header-divider"></v-divider>

			<v-card-text class="pa-0 white-background">
				<v-container class="pa-6">
					<v-row class="mb-6" v-if="!hidePosTotals">
						<v-col cols="12" class="pa-1">
							<div class="table-header mb-4">
								<h4 class="text-h6 text-grey-darken-2 mb-1">
									{{ __("Shift Overview") }}
								</h4>
								<p class="text-body-2 text-grey">
									{{ __("Review shift totals before submitting the closing entry") }}
								</p>
							</div>

							<div class="overview-wrapper" v-if="overviewLoading">
								<v-progress-circular
									color="primary"
									indeterminate
									size="32"
								></v-progress-circular>
							</div>

							<div v-else class="overview-wrapper">
								<div class="insight-grid">
									<v-row dense>
										<v-col
											v-for="card in primaryInsights"
											:key="card.key"
											cols="12"
											sm="6"
											md="3"
											class="d-flex"
										>
											<div class="insight-card">
												<div class="insight-icon" :class="card.color">
													<v-icon size="22">{{ card.icon }}</v-icon>
												</div>
												<div class="insight-body">
													<div class="insight-label">{{ card.label }}</div>
													<div class="insight-value">{{ card.value }}</div>
													<div class="insight-caption">{{ card.caption }}</div>
												</div>
											</div>
										</v-col>
									</v-row>
									<v-row dense class="mt-2" v-if="secondaryInsights.length">
										<v-col
											v-for="card in secondaryInsights"
											:key="card.key"
											cols="12"
											sm="6"
											md="3"
											class="d-flex"
										>
											<div class="insight-card compact">
												<div class="insight-icon" :class="card.color">
													<v-icon size="20">{{ card.icon }}</v-icon>
												</div>
												<div class="insight-body">
													<div class="insight-label">{{ card.label }}</div>
													<div class="insight-value">{{ card.value }}</div>
													<div class="insight-caption">{{ card.caption }}</div>
												</div>
											</div>
										</v-col>
									</v-row>
								</div>

								<div class="table-section mt-6">
									<div class="table-header mb-2">
										<h5 class="text-subtitle-1 text-grey-darken-2 mb-1">
											{{ __("Totals by Invoice Currency") }}
										</h5>
										<p class="text-body-2 text-grey">
											{{ __("Shows the distribution of invoices per currency") }}
										</p>
									</div>

									<div v-if="multiCurrencyTotals.length" class="overview-table-wrapper">
										<table class="overview-table">
											<thead>
												<tr>
													<th>{{ __("Currency") }}</th>
													<th class="text-end">
														{{ __("Total") }}
													</th>
													<th class="text-end">
														{{ __("Invoices") }}
													</th>
												</tr>
											</thead>
											<tbody>
												<tr v-for="row in multiCurrencyTotals" :key="row.currency">
													<td>{{ row.currency }}</td>
													<td class="text-end">
														<div class="amount-with-base">
															<div class="amount-primary">
																<span class="overview-amount">
																	{{
																		formatCurrencyWithSymbol(
																			row.total || 0,
																			row.currency ||
																				overviewCompanyCurrency,
																		)
																	}}
																</span>
																<span
																	v-if="
																		shouldShowCompanyEquivalent(
																			row,
																			row.currency,
																		)
																	"
																	class="company-equivalent"
																>
																	({{
																		formatCurrencyWithSymbol(
																			row.company_currency_total || 0,
																			overviewCompanyCurrency,
																		)
																	}})
																</span>
															</div>
															<div
																v-if="showExchangeRates(row, row.currency)"
																class="exchange-note"
															>
																{{
																	formatExchangeRates(
																		row.exchange_rates,
																		row.currency ||
																			overviewCompanyCurrency,
																		overviewCompanyCurrency,
																	)
																}}
															</div>
														</div>
													</td>
													<td class="text-end">{{ row.invoice_count || 0 }}</td>
												</tr>
											</tbody>
										</table>
									</div>
									<div v-else class="overview-empty text-body-2">
										{{ __("No invoices recorded for this shift.") }}
									</div>
								</div>

								<v-row dense class="mt-4">
									<v-col cols="12" md="6">
										<div class="table-section">
											<div class="table-header mb-2">
												<h5 class="text-subtitle-1 text-grey-darken-2 mb-1">
													{{ __("Outstanding Credit by Currency") }}
												</h5>
												<p class="text-body-2 text-grey">
													{{ __("Credit sales remaining to be collected") }}
												</p>
											</div>
											<div
												v-if="creditInvoicesByCurrency.length"
												class="overview-table-wrapper"
											>
												<table class="overview-table">
													<thead>
														<tr>
															<th>{{ __("Currency") }}</th>
															<th class="text-end">
																{{ __("Outstanding") }}
															</th>
															<th class="text-end">
																{{ __("Invoices") }}
															</th>
														</tr>
													</thead>
													<tbody>
														<tr
															v-for="row in creditInvoicesByCurrency"
															:key="`credit-${row.currency}`"
														>
															<td>{{ row.currency }}</td>
															<td class="text-end">
																<div class="amount-with-base">
																	<div class="amount-primary">
																		<span class="overview-amount">
																			{{
																				formatCurrencyWithSymbol(
																					row.total || 0,
																					row.currency ||
																						overviewCompanyCurrency,
																				)
																			}}
																		</span>
																		<span
																			v-if="
																				shouldShowCompanyEquivalent(
																					row,
																					row.currency,
																				)
																			"
																			class="company-equivalent"
																		>
																			({{
																				formatCurrencyWithSymbol(
																					row.company_currency_total ||
																						0,
																					overviewCompanyCurrency,
																				)
																			}})
																		</span>
																	</div>
																	<div
																		v-if="
																			showExchangeRates(
																				row,
																				row.currency,
																			)
																		"
																		class="exchange-note"
																	>
																		{{
																			formatExchangeRates(
																				row.exchange_rates,
																				row.currency ||
																					overviewCompanyCurrency,
																				overviewCompanyCurrency,
																			)
																		}}
																	</div>
																</div>
															</td>
															<td class="text-end">
																{{ row.invoice_count || 0 }}
															</td>
														</tr>
													</tbody>
												</table>
											</div>
											<div v-else class="overview-empty text-body-2">
												{{ __("No outstanding credit invoices for this shift.") }}
											</div>
										</div>
									</v-col>
									<v-col cols="12" md="6">
										<div class="table-section">
											<div class="table-header mb-2">
												<h5 class="text-subtitle-1 text-grey-darken-2 mb-1">
													{{ __("Returns by Currency") }}
												</h5>
												<p class="text-body-2 text-grey">
													{{ __("Processed returns impacting the shift totals") }}
												</p>
											</div>
											<div
												v-if="returnsByCurrency.length"
												class="overview-table-wrapper"
											>
												<table class="overview-table">
													<thead>
														<tr>
															<th>{{ __("Currency") }}</th>
															<th class="text-end">
																{{ __("Returns") }}
															</th>
															<th class="text-end">
																{{ __("Count") }}
															</th>
														</tr>
													</thead>
													<tbody>
														<tr
															v-for="row in returnsByCurrency"
															:key="`return-${row.currency}`"
														>
															<td>{{ row.currency }}</td>
															<td class="text-end">
																<div class="amount-with-base">
																	<div class="amount-primary">
																		<span class="overview-amount">
																			{{
																				formatCurrencyWithSymbol(
																					row.total || 0,
																					row.currency ||
																						overviewCompanyCurrency,
																				)
																			}}
																		</span>
																		<span
																			v-if="
																				shouldShowCompanyEquivalent(
																					row,
																					row.currency,
																				)
																			"
																			class="company-equivalent"
																		>
																			({{
																				formatCurrencyWithSymbol(
																					row.company_currency_total ||
																						0,
																					overviewCompanyCurrency,
																				)
																			}})
																		</span>
																	</div>
																	<div
																		v-if="
																			showExchangeRates(
																				row,
																				row.currency,
																			)
																		"
																		class="exchange-note"
																	>
																		{{
																			formatExchangeRates(
																				row.exchange_rates,
																				row.currency ||
																					overviewCompanyCurrency,
																				overviewCompanyCurrency,
																			)
																		}}
																	</div>
																</div>
															</td>
															<td class="text-end">
																{{ row.invoice_count || 0 }}
															</td>
														</tr>
													</tbody>
												</table>
											</div>
											<div v-else class="overview-empty text-body-2">
												{{ __("No returns were processed in this shift.") }}
											</div>
										</div>
									</v-col>
								</v-row>

								<v-row dense class="mt-4">
									<v-col cols="12" md="6">
										<div class="table-section">
											<div class="table-header mb-2">
												<h5 class="text-subtitle-1 text-grey-darken-2 mb-1">
													{{ __("Change Returned") }}
												</h5>
												<p class="text-body-2 text-grey">
													{{
														__("Track how much cash was handed back to customers")
													}}
												</p>
											</div>
											<div
												v-if="changeReturnedRows.length"
												class="overview-table-wrapper"
											>
												<table class="overview-table">
													<thead>
														<tr>
															<th>{{ __("Currency") }}</th>
															<th class="text-end">
																{{ __("Invoice Change") }}
															</th>
															<th class="text-end">
																{{ __("Overpayment Change") }}
															</th>
															<th class="text-end">
																{{ __("Total Change") }}
															</th>
														</tr>
													</thead>
													<tbody>
														<tr
															v-for="row in changeReturnedRows"
															:key="`change-returned-${row.currency}`"
														>
															<td>{{ row.currency }}</td>
															<td class="text-end">
																<div class="amount-with-base">
																	<div class="amount-primary">
																		<span class="overview-amount">
																			{{
																				formatCurrencyWithSymbol(
																					row.invoice_total,
																					row.currency ||
																						overviewCompanyCurrency,
																				)
																			}}
																		</span>
																		<span
																			v-if="
																				shouldShowCompanyEquivalent(
																					{
																						currency:
																							row.currency,
																						total: row.invoice_total,
																						company_currency_total:
																							row.invoice_company_currency_total,
																					},
																					row.currency,
																				)
																			"
																			class="company-equivalent"
																		>
																			({{
																				formatCurrencyWithSymbol(
																					row.invoice_company_currency_total,
																					overviewCompanyCurrency,
																				)
																			}})
																		</span>
																	</div>
																	<div
																		v-if="
																			showExchangeRates(
																				row,
																				row.currency,
																			)
																		"
																		class="exchange-note"
																	>
																		{{
																			formatExchangeRates(
																				row.exchange_rates,
																				row.currency ||
																					overviewCompanyCurrency,
																				overviewCompanyCurrency,
																			)
																		}}
																	</div>
																</div>
															</td>
															<td class="text-end">
																<div class="amount-with-base">
																	<div class="amount-primary">
																		<span class="overview-amount">
																			{{
																				formatCurrencyWithSymbol(
																					row.overpayment_total,
																					row.currency ||
																						overviewCompanyCurrency,
																				)
																			}}
																		</span>
																		<span
																			v-if="
																				shouldShowCompanyEquivalent(
																					{
																						currency:
																							row.currency,
																						total: row.overpayment_total,
																						company_currency_total:
																							row.overpayment_company_currency_total,
																					},
																					row.currency,
																				)
																			"
																			class="company-equivalent"
																		>
																			({{
																				formatCurrencyWithSymbol(
																					row.overpayment_company_currency_total,
																					overviewCompanyCurrency,
																				)
																			}})
																		</span>
																	</div>
																	<div
																		v-if="
																			showExchangeRates(
																				row,
																				row.currency,
																			)
																		"
																		class="exchange-note"
																	>
																		{{
																			formatExchangeRates(
																				row.exchange_rates,
																				row.currency ||
																					overviewCompanyCurrency,
																				overviewCompanyCurrency,
																			)
																		}}
																	</div>
																</div>
															</td>
															<td class="text-end">
																<div class="amount-with-base">
																	<div class="amount-primary">
																		<span class="overview-amount">
																			{{
																				formatCurrencyWithSymbol(
																					row.total,
																					row.currency ||
																						overviewCompanyCurrency,
																				)
																			}}
																		</span>
																		<span
																			v-if="
																				shouldShowCompanyEquivalent(
																					row.company_currency_total,
																					row.currency,
																				)
																			"
																			class="company-equivalent"
																		>
																			({{
																				formatCurrencyWithSymbol(
																					row.company_currency_total,
																					overviewCompanyCurrency,
																				)
																			}})
																		</span>
																	</div>
																	<div
																		v-if="
																			showExchangeRates(
																				row,
																				row.currency,
																			)
																		"
																		class="exchange-note"
																	>
																		{{
																			formatExchangeRates(
																				row.exchange_rates,
																				row.currency ||
																					overviewCompanyCurrency,
																				overviewCompanyCurrency,
																			)
																		}}
																	</div>
																</div>
															</td>
														</tr>
													</tbody>
												</table>
											</div>
											<div v-else class="overview-empty text-body-2">
												{{ __("No change returned recorded for this shift.") }}
											</div>
										</div>
										<!-- End: Change Returned -->
										<div class="table-section">
											<div class="table-header mb-2">
												<h5 class="text-subtitle-1 text-grey-darken-2 mb-1">
													{{ __("Cash Drawer Snapshot") }}
												</h5>
												<p class="text-body-2 text-grey">
													{{ __("Expected cash on hand grouped by currency") }}
												</p>
											</div>
											<div
												v-if="cashExpectedByCurrency.length"
												class="overview-table-wrapper"
											>
												<table class="overview-table">
													<thead>
														<tr>
															<th>{{ __("Currency") }}</th>
															<th class="text-end">
																{{ __("Expected Cash") }}
															</th>
														</tr>
													</thead>
													<tbody>
														<tr
															v-for="row in cashExpectedByCurrency"
															:key="`cash-${row.currency}`"
														>
															<td>{{ row.currency }}</td>
															<td class="text-end">
																<div class="amount-with-base">
																	<div class="amount-primary">
																		<span class="overview-amount">
																			{{
																				formatCurrencyWithSymbol(
																					row.total || 0,
																					row.currency ||
																						overviewCompanyCurrency,
																				)
																			}}
																		</span>
																		<span
																			v-if="
																				shouldShowCompanyEquivalent(
																					row,
																					row.currency,
																				)
																			"
																			class="company-equivalent"
																		>
																			({{
																				formatCurrencyWithSymbol(
																					row.company_currency_total ||
																						0,
																					overviewCompanyCurrency,
																				)
																			}})
																		</span>
																	</div>
																	<div
																		v-if="
																			isCashMode(row.mode_of_payment) &&
																			overpaymentDeductionForCurrency(
																				row.currency,
																			)
																		"
																		class="exchange-note"
																	>
																		{{
																			__(
																				"Overpayment change deducted: {0}",
																				[
																					formatCurrencyWithSymbol(
																						overpaymentDeductionForCurrency(
																							row.currency,
																						),
																						row.currency ||
																							overviewCompanyCurrency,
																					),
																				],
																			)
																		}}
																	</div>
																	<div
																		v-if="
																			showExchangeRates(
																				row,
																				row.currency,
																			)
																		"
																		class="exchange-note"
																	>
																		{{
																			formatExchangeRates(
																				row.exchange_rates,
																				row.currency ||
																					overviewCompanyCurrency,
																				overviewCompanyCurrency,
																			)
																		}}
																	</div>
																</div>
															</td>
														</tr>
													</tbody>
												</table>
											</div>
											<div v-else class="overview-empty text-body-2">
												{{ __("No cash expected for this shift.") }}
											</div>
										</div>
									</v-col>
								</v-row>

								<div class="table-section mt-4">
									<div class="table-header mb-2">
										<h5 class="text-subtitle-1 text-grey-darken-2 mb-1">
											{{ __("Payments by Mode of Payment") }}
										</h5>
										<p class="text-body-2 text-grey">
											{{ __("Grouped totals for each payment method and currency") }}
										</p>
									</div>

									<div v-if="paymentsByMode.length" class="overview-table-wrapper">
										<table class="overview-table">
											<thead>
												<tr>
													<th>{{ __("Mode of Payment") }}</th>
													<th>{{ __("Currency") }}</th>
													<th class="text-end">
														{{ __("Amount") }}
													</th>
												</tr>
											</thead>
											<tbody>
												<tr
													v-for="row in paymentsByMode"
													:key="`${row.mode_of_payment}-${row.currency}`"
												>
													<td>{{ row.mode_of_payment }}</td>
													<td>{{ row.currency }}</td>
													<td class="text-end">
														<div class="amount-with-base">
															<div class="amount-primary">
																<span class="overview-amount">
																	{{
																		formatCurrencyWithSymbol(
																			row.total || 0,
																			row.currency ||
																				overviewCompanyCurrency,
																		)
																	}}
																</span>
																<span
																	v-if="
																		shouldShowCompanyEquivalent(
																			row,
																			row.currency,
																		)
																	"
																	class="company-equivalent"
																>
																	({{
																		formatCurrencyWithSymbol(
																			row.company_currency_total || 0,
																			overviewCompanyCurrency,
																		)
																	}})
																</span>
															</div>
															<div
																v-if="showExchangeRates(row, row.currency)"
																class="exchange-note"
															>
																{{
																	formatExchangeRates(
																		row.exchange_rates,
																		row.currency ||
																			overviewCompanyCurrency,
																		overviewCompanyCurrency,
																	)
																}}
															</div>
														</div>
													</td>
												</tr>
											</tbody>
										</table>
									</div>
									<div v-else class="overview-empty text-body-2">
										{{ __("No payments registered for this shift.") }}
									</div>
								</div>
							</div>
						</v-col>
					</v-row>
					<v-row>
						<v-col cols="12" class="pa-1">
							<div v-if="showCashDrawSummary" class="cash-draw-closing-section mb-6">
								<div class="table-header mb-3">
									<h4 class="text-h6 text-grey-darken-2 mb-1">
										{{ __("Cash Draws") }}
									</h4>
									<p class="text-body-2 text-grey">
										{{ __("Total deducted from expected shift collections") }}:
										<strong>{{ companyCurrencySymbol }} {{ formatCurrency(cashDrawGrandTotal) }}</strong>
									</p>
								</div>
								<div class="overview-table-wrapper">
									<table class="overview-table cash-draw-closing-table">
										<thead>
											<tr>
												<th>{{ __("Time") }}</th>
												<th>{{ __("Mode of Payment") }}</th>
												<th>{{ __("Narration") }}</th>
												<th class="text-end">{{ __("Amount") }}</th>
											</tr>
										</thead>
										<tbody>
											<tr v-for="draw in dialogCashDraws" :key="draw.name">
												<td>{{ formatCashDrawTime(draw.posting_time) }}</td>
												<td>{{ draw.mode_of_payment }}</td>
												<td>{{ draw.narration }}</td>
												<td class="text-end overview-amount">
													{{ companyCurrencySymbol }} {{ formatCurrency(draw.amount) }}
												</td>
											</tr>
										</tbody>
									</table>
								</div>
							</div>

							<div class="table-header mb-4" v-if="showFuelClosingReadingsTable">
								<h4 class="text-h6 text-grey-darken-2 mb-1">
									{{ __("Nozzle Closing Readings") }}
								</h4>
								<p class="text-body-2 text-grey">
									{{ __("Capture final nozzle readings before payment reconciliation") }}
								</p>
							</div>

							<v-data-table
								v-if="showFuelClosingReadingsTable"
								:headers="nozzleClosingHeaders"
								:items="nozzleClosingReadings"
								item-key="row_key"
								class="elevation-0 rounded-lg white-table mb-6"
								:items-per-page="nozzleClosingReadings.length || 50"
								hide-default-footer
								density="compact"
							>
								<template v-slot:item.opening_reading="{ item }">
									{{ formatFloat(item.opening_reading || 0, 3) }}
								</template>
								<template v-slot:item.closing_reading="{ item }">
									<v-text-field
										v-model="item.closing_reading"
										@focus="clearClosingReadingError(item)"
										@input="recomputeFuelExpected"
										@blur="enforceClosingReadingMin(item)"
										type="number"
										:min="Number(item.opening_reading || 0)"
										step="0.001"
										density="compact"
										variant="outlined"
										color="primary"
										:error="Boolean(nozzleClosingReadingErrors[item.row_key])"
										:error-messages="nozzleClosingReadingErrors[item.row_key] ? [nozzleClosingReadingErrors[item.row_key]] : []"
										:hint="closingReadingHint(item)"
										:persistent-hint="Boolean(closingReadingHint(item))"
										hide-details="auto"
										class="pos-themed-input"
									/>
								</template>
								<template v-slot:item.test_return_qty="{ item }">
									<v-text-field
										v-model="item.test_return_qty"
										@input="recomputeFuelExpected"
										@blur="enforceClosingReadingMin(item)"
										type="number"
										min="0"
										step="0.001"
										density="compact"
										variant="outlined"
										color="primary"
										hide-details
										class="pos-themed-input"
									/>
								</template>
							</v-data-table>

							<div
								v-if="showFuelClosingReadingsTable && fuelReconciliationSummary"
								class="fuel-summary mb-6"
							>
								<table class="fuel-summary-table">
									<thead>
										<tr>
											<th>{{ __("Fuel Item") }}</th>
											<th class="text-right">{{ __("Metered (L)") }}</th>
											<th class="text-right">{{ __("Invoiced (L)") }}</th>
											<th class="text-right">{{ __("Difference (L)") }}</th>
											<th class="text-right">{{ __("Difference Value") }}</th>
										</tr>
									</thead>
									<tbody>
										<tr
											v-for="row in fuelReconciliationSummary.items"
											:key="`fuel-sum-${row.fuel_item}`"
										>
											<td>{{ row.fuel_item }}</td>
											<td class="text-right">{{ formatFloat(row.metered, 3) }}</td>
											<td class="text-right">{{ formatFloat(row.invoiced, 3) }}</td>
											<td class="text-right" :class="fuelRowDifferenceClass(row.difference)">
												{{ formatFloat(row.difference, 3) }}
											</td>
											<td class="text-right" :class="fuelRowDifferenceClass(row.difference)">
												{{ companyCurrencySymbol }} {{ formatCurrency(row.value) }}
											</td>
										</tr>
										<tr class="fuel-summary-total">
											<td>{{ __("Total") }}</td>
											<td class="text-right">
												{{ formatFloat(fuelReconciliationSummary.metered, 3) }}
											</td>
											<td class="text-right">
												{{ formatFloat(fuelReconciliationSummary.invoiced, 3) }}
											</td>
											<td class="text-right" :class="fuelDifferenceClass">
												{{ formatFloat(fuelReconciliationSummary.difference, 3) }}
											</td>
											<td class="text-right" :class="fuelDifferenceClass">
												{{ companyCurrencySymbol }}
												{{ formatCurrency(fuelReconciliationSummary.value) }}
											</td>
										</tr>
									</tbody>
								</table>
								<div class="fuel-summary-note">
									{{
										__(
											"Fuel pumped but not billed is invoiced to this shift and must be handed over in cash.",
										)
									}}
								</div>
							</div>

							<div class="table-header mb-4">
								<h4 class="text-h6 text-grey-darken-2 mb-1">
									{{ __("Payment Reconciliation") }}
								</h4>
								<p class="text-body-2 text-grey">
									{{ __("Verify closing amounts for each payment method") }}
								</p>
							</div>

							<v-data-table
								:headers="headers"
								:items="dialog_data.payment_reconciliation"
								item-key="mode_of_payment"
								class="elevation-0 rounded-lg white-table"
								:items-per-page="itemsPerPage"
								hide-default-footer
								density="compact"
							>
								<template v-slot:item.closing_amount="props">
									<span v-if="props.item.is_credit_sales">
										{{ companyCurrencySymbol }}
										{{ formatCurrency(props.item.expected_amount) }}
									</span>
									<v-text-field
										v-else
										v-model="props.item.closing_amount"
										:rules="[closingAmountRule]"
										:label="frappe._('Edit')"
										single-line
										counter
										type="number"
										density="compact"
										variant="outlined"
										color="primary"
										class="pos-themed-input"
										hide-details
										:prefix="companyCurrencySymbol"
										@input="onClosingAmountInput(props.item)"
									></v-text-field>
								</template>
								<template v-slot:item.difference="{ item }">
									{{ companyCurrencySymbol }}
									{{ formatCurrency(calculateDifference(item)) }}
								</template>
								<template v-slot:item.opening_amount="{ item }">
									{{ companyCurrencySymbol }}
									{{ formatCurrency(item.opening_amount) }}</template
								>
								<template v-slot:item.cash_drawn="{ item }">
									{{ companyCurrencySymbol }}
									{{ formatCurrency(item.cash_drawn || 0) }}</template
								>
								<template v-slot:item.expected_amount="{ item }">
									{{ companyCurrencySymbol }}
									{{ formatCurrency(item.expected_amount) }}</template
								>
								<template v-slot:item.variance_percent="{ item }">
									<span :class="['variance-chip', varianceClass(item)]">
										{{ formatVariancePercent(item) }}
									</span>
								</template>
							</v-data-table>
						</v-col>
					</v-row>
				</v-container>
			</v-card-text>

			<v-divider></v-divider>
			<v-card-actions class="dialog-actions-container">
				<v-spacer></v-spacer>
				<v-btn
					theme="dark"
					@click="close_dialog"
					class="pos-action-btn cancel-action-btn"
					size="large"
					elevation="2"
				>
					<v-icon start>mdi-close-circle-outline</v-icon>
					<span>{{ __("Close") }}</span>
				</v-btn>
				<v-btn
					theme="dark"
					@click="submit_dialog"
					class="pos-action-btn submit-action-btn"
					size="large"
					elevation="2"
				>
					<v-icon start>mdi-check-circle-outline</v-icon>
					<span>{{ __("Submit") }}</span>
				</v-btn>
			</v-card-actions>
		</v-card>
	</v-dialog>
</template>

<script>
import format from "../../format";
export default {
	mixins: [format],
	data: () => ({
		closingDialog: false,
		forceClose: false,
		itemsPerPage: 20,
		dialog_data: {},
		pos_profile: "",
		overview: null,
		overviewLoading: false,
		headers: [],
		cashModeOfPayment: "",
		fuelCreditTotal: 0,
		fuelModeShiftSales: {},
		fuelSoldByItem: {},
		nozzleClosingReadings: [],
		nozzleClosingReadingErrors: {},
		nozzleClosingHeaders: [
			{ title: __("Nozzle"), value: "nozzle", align: "start", sortable: false },
			{ title: __("Fuel Item"), value: "fuel_item", align: "start", sortable: false },
			{ title: __("Fuel Pump"), value: "fuel_pump", align: "start", sortable: false },
			{ title: __("Fuel Tank"), value: "warehouse", align: "start", sortable: false },
			{ title: __("Opening Reading"), value: "opening_reading", align: "end", sortable: false },
			{ title: __("Closing Reading"), value: "closing_reading", align: "end", sortable: false },
			{ title: __("Test Return (L)"), value: "test_return_qty", align: "end", sortable: false },
		],
		baseHeaders: [
			{
				title: __("Mode of Payment"),
				value: "mode_of_payment",
				align: "start",
				sortable: true,
			},
			{
				title: __("Opening Amount"),
				align: "end",
				sortable: true,
				value: "opening_amount",
			},
			{
				title: __("Closing Amount"),
				value: "closing_amount",
				align: "end",
				sortable: true,
			},
		],
		cashDrawHeader: {
			title: __("Cash Drawn"),
			value: "cash_drawn",
			align: "end",
			sortable: false,
		},
		extendedHeaders: [
			{
				title: __("Expected Amount (In Company Currency)"),
				value: "expected_amount",
				align: "end",
				sortable: false,
			},
			{
				title: __("Difference (In Company Currency)"),
				value: "difference",
				align: "end",
				sortable: false,
			},
			{
				title: __("Variance %"),
				value: "variance_percent",
				align: "end",
				sortable: false,
			},
		],
		closingAmountRule: (v) => {
			if (v === "" || v === null || v === undefined) {
				return true;
			}

			const value = typeof v === "number" ? v : Number(String(v).trim());

			if (!Number.isFinite(value)) {
				return "Please enter a valid number";
			}

			const stringValue = String(v);
			const [integerPart, fractionalPart] = stringValue.split(".");

			if (integerPart.replace(/^-/, "").length > 20) {
				return "Number is too large";
			}

			if (fractionalPart && fractionalPart.length > 2) {
				return "Maximum of 2 decimal places";
			}

			return true;
		},
		pagination: {},
	}),
	watch: {},

	methods: {
		updateHeaders() {
			const headers = [...this.baseHeaders];
			if (this.cashDrawsEnabled) {
				headers.splice(2, 0, this.cashDrawHeader);
			}
			if (
				this.pos_profile &&
				!this.pos_profile.hide_expected_amount &&
				!this.pos_profile.custom_hide_pos_totals
			) {
				headers.push(...this.extendedHeaders);
			}
			this.headers = headers;
		},
		formatCashDrawTime(value) {
			return value ? String(value).slice(0, 5) : "";
		},
		handleKeydown(event) {
			if (event.key === "Escape" && this.closingDialog) {
				this.close_dialog();
			}
		},
		clearClosingReadingError(item) {
			if (!item || !item.row_key) {
				return;
			}

			if (this.nozzleClosingReadingErrors[item.row_key]) {
				const updatedErrors = { ...this.nozzleClosingReadingErrors };
				delete updatedErrors[item.row_key];
				this.nozzleClosingReadingErrors = updatedErrors;
			}
		},
		setClosingReadingError(item, message) {
			if (!item || !item.row_key) {
				return;
			}

			this.nozzleClosingReadingErrors = {
				...this.nozzleClosingReadingErrors,
				[item.row_key]: message,
			};
		},
		sharedTankReadingError(item) {
			return this.__(
				"This tank is served by {0} nozzles. Enter the actual meter reading for each nozzle before submitting.",
				[item?.shared_tank_nozzle_count || 0],
			);
		},
		closingReadingHint(item) {
			if (Number(item?.shared_tank_nozzle_count || 0) > 1) {
				return this.__("Shared tank. Enter this nozzle's own meter reading.");
			}
			return "";
		},
		meterDispensed(row) {
			const opening = Number(row?.opening_reading || 0);
			const closing = Number(row?.closing_reading);
			if (!Number.isFinite(closing)) {
				return 0;
			}
			if (closing >= opening) {
				return closing - opening;
			}
			// A meter that wraps past its last digit is a rollover, not a typo.
			const wrapAt = Math.pow(10, Number(row.meter_digits || 6));
			if (opening > wrapAt * 0.9 && closing < wrapAt * 0.1) {
				return wrapAt - opening + closing;
			}
			return 0;
		},
		netDispensed(row) {
			return Math.max(this.meterDispensed(row) - Number(row?.test_return_qty || 0), 0);
		},
		validateSharedTankClosingReadings() {
			const groupedRows = new Map();
			let hasInvalidGroup = false;

			for (const row of this.nozzleClosingReadings) {
				if (Number(row?.expected_sold_qty || 0) <= 0) {
					continue;
				}

				const key = `${row.fuel_item || ""}::${row.warehouse || ""}`;
				if (!groupedRows.has(key)) {
					groupedRows.set(key, []);
				}
				groupedRows.get(key).push(row);
			}

			for (const rows of groupedRows.values()) {
				const allUntouched = rows.every((row) => this.meterDispensed(row) <= 0);

				const errorMessage = this.sharedTankReadingError(rows[0]);
				if (allUntouched) {
					hasInvalidGroup = true;
					rows.forEach((row) => this.setClosingReadingError(row, errorMessage));
					continue;
				}

				rows.forEach((row) => {
					if (this.nozzleClosingReadingErrors[row.row_key] === errorMessage) {
						this.clearClosingReadingError(row);
					}
				});
			}

			return !hasInvalidGroup;
		},
		enforceClosingReadingMin(item) {
			if (!item) {
				return;
			}

			const openingReading = Number(item.opening_reading || 0);
			const closingReading = Number(item.closing_reading);

			if (!item.row_key) {
				if (Number.isFinite(closingReading) && closingReading < openingReading) {
					item.closing_reading = openingReading;
				}
				return;
			}

			if (item.closing_reading === null || item.closing_reading === "") {
				this.setClosingReadingError(item, this.__("Enter the meter reading."));
				return;
			}

			if (!Number.isFinite(closingReading)) {
				this.setClosingReadingError(item, this.__("Enter the meter reading."));
				return;
			}

			// A drop below the opening reading is only valid as a meter rollover.
			if (closingReading < openingReading && this.meterDispensed(item) <= 0) {
				item.closing_reading = openingReading;
				this.setClosingReadingError(item, this.__("Readings should be above opening reading."));
				return;
			}

			const testReturn = Number(item.test_return_qty || 0);
			if (testReturn < 0) {
				item.test_return_qty = 0;
			} else if (testReturn > this.meterDispensed(item)) {
				item.test_return_qty = this.meterDispensed(item);
			}

			this.clearClosingReadingError(item);
			this.recomputeFuelExpected();
		},
		recomputeFuelExpected() {
			if (!this.showFuelClosingReadingsTable) {
				return;
			}
			// The nozzle meters are the single source of truth for the money owed.
			// The shift total to reconcile is the invoiced sales plus the unbilled
			// fuel per item (metered minus invoiced litres, never negative), which
			// is exactly what the server invoices to the shift on submit.
			const summary = this.fuelReconciliationSummary;
			let unbilledFuel = 0;
			for (const row of summary?.items || []) {
				unbilledFuel += Math.max(Number(row.difference) || 0, 0) * (Number(row.rate) || 0);
			}
			const shiftTotal = (Number(this.dialog_data.grand_total) || 0) + unbilledFuel;
			const credit = Number(this.fuelCreditTotal || 0);
			const rows = this.dialog_data.payment_reconciliation || [];

			const cashRow = rows.find((r) => r.mode_of_payment === this.cashModeOfPayment);

			// Round to currency precision (2 dp) so floating-point noise never
			// leaks a fake variance into the reconciliation.
			const round2 = (value) => Math.round((Number(value) || 0) * 100) / 100;

			// Reconcile every OTHER (non-cash) mode of payment - e.g. Mpesa.
			//
			// Each mode's total collection is closing + cash_drawn - opening. The
			// seed is the recorded sales through that mode less what was drawn out
			// of it, floored at zero: a draw larger than the recorded sales was
			// funded by unbilled fuel, so it cannot push the mode negative. The
			// cashier may overwrite the closing (e.g. unrecorded sales received
			// through Mpesa); the mode always reconciles to a zero difference and
			// whatever it carries is removed from the cash remainder.
			let otherCollected = 0;
			for (const row of rows) {
				if (row.is_credit_sales || row === cashRow) {
					continue;
				}

				const firstTime = row.shift_sales === undefined || row.shift_sales === null;
				if (firstTime) {
					// Clean per-mode sales from the server, captured before cash
					// draws were subtracted from expected_amount.
					const modeSales = this.fuelModeShiftSales[row.mode_of_payment];
					row.shift_sales =
						modeSales !== undefined && modeSales !== null
							? Number(modeSales) || 0
							: (Number(row.expected_amount) || 0) + (Number(row.cash_drawn) || 0);
				}
				const shiftSales = Number(row.shift_sales) || 0;

				let closing;
				if (firstTime) {
					closing = Math.max(
						round2(
							(Number(row.opening_amount) || 0) + shiftSales - (Number(row.cash_drawn) || 0),
						),
						0,
					);
					row.closing_amount = closing;
				} else {
					closing = round2(Number(row.closing_amount) || 0);
				}

				row.expected_amount = closing;
				otherCollected +=
					closing - (Number(row.opening_amount) || 0) + (Number(row.cash_drawn) || 0);
			}

			if (cashRow && shiftTotal > 0) {
				// Cash carries the balancing remainder so that
				//   sum(closing - opening + cash_drawn) + credit = shift total.
				const cashExpected = round2(
					(Number(cashRow.opening_amount) || 0) +
						shiftTotal -
						credit -
						(Number(cashRow.cash_drawn) || 0) -
						otherCollected,
				);
				cashRow.expected_amount = cashExpected;
				// Editable: the counted cash is a declaration. Auto-fill only while
				// the cashier has not typed a figure, so a real cash shortage shows
				// up as a difference instead of being hidden.
				if (!cashRow.fuel_cash_closing_touched) {
					cashRow.closing_amount = cashExpected;
				}
			}

			const creditRow = rows.find((r) => r.is_credit_sales);
			if (credit > 0) {
				if (creditRow) {
					creditRow.expected_amount = credit;
					creditRow.closing_amount = credit;
				} else {
					rows.push({
						mode_of_payment: this.__("Credit Sales"),
						is_credit_sales: true,
						opening_amount: 0,
						expected_amount: credit,
						closing_amount: credit,
					});
				}
			} else if (creditRow) {
				rows.splice(rows.indexOf(creditRow), 1);
			}
		},
		onClosingAmountInput(item) {
			if (item && item.mode_of_payment === this.cashModeOfPayment) {
				item.fuel_cash_closing_touched = true;
			}
			this.recomputeFuelExpected();
		},
		close_dialog() {
			if (this.forceClose) {
				alert(this.__("You must close the previous day's shift before continuing."));
				return;
			}
			this.closingDialog = false;
			this.overview = null;
			this.overviewLoading = false;
		},
		submit_dialog() {
			const invalid = (this.dialog_data.payments || []).some((p) =>
				isNaN(parseFloat(p.closing_amount)),
			);
			if (invalid) {
				alert(this.__("Invalid closing amount"));
				return;
			}

			const invalidNozzles = this.nozzleClosingReadings.some((row) => {
				if (!this.showFuelClosingReadingsTable) {
					return false;
				}

				if (row.closing_reading === null || row.closing_reading === "") {
					this.setClosingReadingError(row, this.__("Enter the meter reading."));
					return true;
				}

				const closingReading = Number(row.closing_reading);
				if (!Number.isFinite(closingReading)) {
					this.setClosingReadingError(row, this.__("Enter the meter reading."));
					return true;
				}

				return closingReading < Number(row.opening_reading || 0) && this.meterDispensed(row) <= 0;
			});

			if (invalidNozzles) {
				alert(
					this.__(
						"Every nozzle needs an actual meter reading, and it cannot be below the opening reading.",
					),
				);
				return;
			}

			if (!this.validateSharedTankClosingReadings()) {
				alert(
					this.__(
						"Tanks that sold fuel require actual nozzle closing readings before submitting.",
					),
				);
				return;
			}

			const payload = {
				...this.dialog_data,
				payment_reconciliation: (this.dialog_data.payment_reconciliation || []).filter(
					(row) => !row.is_credit_sales,
				),
				nozzle_closing_readings: this.nozzleClosingReadings.map((row) => ({
					nozzle: row.nozzle,
					fuel_item: row.fuel_item,
					fuel_pump: row.fuel_pump,
					warehouse: row.warehouse,
					meter_digits: Number(row.meter_digits || 6),
					opening_reading: Number(row.opening_reading || 0),
					closing_reading: Number(row.closing_reading || 0),
					test_return_qty: Number(row.test_return_qty || 0),
				})),
			};

			this.eventBus.emit("submit_closing_pos", payload);
			this.closingDialog = false;
		},
		fetchOverview(openingShift) {
			this.overviewLoading = true;
			this.overview = null;
			if (!openingShift) {
				this.overviewLoading = false;
				return;
			}

			const toNumber = (value) => {
				const number = Number(value);
				return Number.isFinite(number) ? number : 0;
			};

			const normalizeRates = (rates) => {
				if (Array.isArray(rates)) {
					return rates
						.map((rate) => Number(rate))
						.filter((rate) => Number.isFinite(rate) && rate > 0);
				}
				if (rates === null || rates === undefined || rates === "") {
					return [];
				}
				const numeric = Number(rates);
				return Number.isFinite(numeric) && numeric > 0 ? [numeric] : [];
			};

			const normalizeCurrencyRows = (value, options = {}) => {
				if (!Array.isArray(value)) {
					return [];
				}

				const { includeCount = false, includeExchangeRates = false } = options;

				return value.map((row) => {
					const record = {
						currency: row?.currency || "",
						total: toNumber(row?.total),
						company_currency_total: toNumber(row?.company_currency_total),
						exchange_rates: includeExchangeRates ? normalizeRates(row?.exchange_rates) : [],
					};

					if (includeCount) {
						record.invoice_count = toNumber(row?.invoice_count);
					}

					return record;
				});
			};

			const normalizePayments = (value) => {
				if (!Array.isArray(value)) {
					return [];
				}

				return value.map((row) => ({
					mode_of_payment: row?.mode_of_payment || "",
					currency: row?.currency || "",
					total: toNumber(row?.total),
					company_currency_total: toNumber(row?.company_currency_total),
					exchange_rates: normalizeRates(row?.exchange_rates),
				}));
			};

			const normalizeCredit = (credit = {}) => ({
				count: toNumber(credit?.count),
				company_currency_total: toNumber(credit?.company_currency_total),
				by_currency: normalizeCurrencyRows(credit?.by_currency, {
					includeCount: true,
					includeExchangeRates: true,
				}),
			});

			const normalizeChangeReturned = (change = {}) => {
				const normalizeBranch = (branch = {}) => ({
					company_currency_total: toNumber(branch?.company_currency_total),
					by_currency: normalizeCurrencyRows(branch?.by_currency, {
						includeExchangeRates: true,
					}),
				});

				const invoiceChange = normalizeBranch(change?.invoice_change || change || {});
				const overpaymentChange = normalizeBranch(change?.overpayment_change || {});

				const primaryByCurrency = normalizeCurrencyRows(change?.by_currency, {
					includeExchangeRates: true,
				});

				const totalCompanyCurrencyValue = change?.company_currency_total;
				const totalCompanyCurrency = toNumber(totalCompanyCurrencyValue);
				const derivedTotalCompanyCurrency =
					invoiceChange.company_currency_total + overpaymentChange.company_currency_total;
				const hasTotalCompanyCurrency =
					totalCompanyCurrencyValue !== undefined &&
					totalCompanyCurrencyValue !== null &&
					totalCompanyCurrencyValue !== "";

				return {
					company_currency_total: hasTotalCompanyCurrency
						? totalCompanyCurrency
						: derivedTotalCompanyCurrency,
					by_currency: primaryByCurrency.length ? primaryByCurrency : invoiceChange.by_currency,
					invoice_change: invoiceChange,
					overpayment_change: overpaymentChange,
				};
			};

			const normalize = (payload = {}) => ({
				total_invoices: toNumber(payload.total_invoices),
				company_currency: payload.company_currency || this.pos_profile?.currency || "",
				company_currency_total: toNumber(payload.company_currency_total),
				multi_currency_totals: normalizeCurrencyRows(payload.multi_currency_totals, {
					includeCount: true,
					includeExchangeRates: true,
				}),
				payments_by_mode: normalizePayments(payload.payments_by_mode),
				credit_invoices: normalizeCredit(payload.credit_invoices),
				sales_summary: {
					gross_company_currency_total: toNumber(
						payload.sales_summary?.gross_company_currency_total,
					),
					net_company_currency_total: toNumber(
						payload.sales_summary?.net_company_currency_total ?? payload.company_currency_total,
					),
					average_invoice_value: toNumber(payload.sales_summary?.average_invoice_value),
					sale_invoices_count: toNumber(payload.sales_summary?.sale_invoices_count),
				},
				returns: {
					count: toNumber(payload.returns?.count),
					company_currency_total: toNumber(payload.returns?.company_currency_total),
					by_currency: normalizeCurrencyRows(payload.returns?.by_currency, {
						includeCount: true,
						includeExchangeRates: true,
					}),
				},
				change_returned: normalizeChangeReturned(payload.change_returned),
				cash_expected: {
					mode_of_payment: payload.cash_expected?.mode_of_payment || "",
					company_currency_total: toNumber(payload.cash_expected?.company_currency_total),
					by_currency: normalizeCurrencyRows(payload.cash_expected?.by_currency, {
						includeExchangeRates: true,
					}),
				},
			});

			const request = frappe.call(
				"posawesome.posawesome.doctype.pos_closing_shift.pos_closing_shift.get_closing_shift_overview",
				{
					pos_opening_shift: openingShift,
				},
			);

			const finalize = () => {
				this.overviewLoading = false;
			};

			const onSuccess = (r) => {
				this.overview = normalize(r && r.message ? r.message : {});
			};

			const onError = (err) => {
				console.error("Failed to load shift overview", err);
				this.overview = normalize();
			};

			if (request && typeof request.then === "function") {
				request.then(onSuccess, onError);

				if (typeof request.always === "function") {
					request.always(finalize);
				} else if (typeof request.finally === "function") {
					request.finally(finalize);
				} else {
					request.then(finalize, finalize);
				}
			} else {
				finalize();
			}
		},
		formatCount(value) {
			return this.formatFloat(value || 0, 0);
		},
		formatCurrencyWithSymbol(amount, currency) {
			const resolvedCurrency = currency || this.overviewCompanyCurrency || "";
			const symbol = this.currencySymbol(resolvedCurrency);
			const formatted = this.formatCurrency(amount || 0);
			if (symbol) {
				return `${symbol} ${formatted}`;
			}
			return `${resolvedCurrency} ${formatted}`.trim();
		},
		shouldShowCompanyEquivalent(row, currency) {
			const resolvedCurrency = currency || row?.currency || "";
			if (!resolvedCurrency) {
				return false;
			}

			if (resolvedCurrency !== this.overviewCompanyCurrency) {
				return true;
			}

			const companyTotal = Number(row?.company_currency_total);
			if (!Number.isFinite(companyTotal)) {
				return false;
			}

			const amount = Number(row?.total);
			if (Number.isFinite(amount) && Math.abs(amount - companyTotal) < 0.005) {
				return false;
			}

			return Math.abs(companyTotal) > 0.0001;
		},
		showExchangeRates(row, currency) {
			const resolvedCurrency = currency || row?.currency || "";
			if (!resolvedCurrency || resolvedCurrency === this.overviewCompanyCurrency) {
				return false;
			}
			return Array.isArray(row?.exchange_rates) && row.exchange_rates.length > 0;
		},
		formatExchangeRates(rates, sourceCurrency, targetCurrency) {
			if (!sourceCurrency || !targetCurrency) {
				return "";
			}

			const validRates = Array.isArray(rates)
				? rates.map((rate) => Number(rate)).filter((rate) => Number.isFinite(rate) && rate > 0)
				: [];

			if (!validRates.length) {
				return "";
			}

			const targetSymbol = this.currencySymbol(targetCurrency) || targetCurrency;
			const formattedRates = validRates.map((rate) => {
				const formattedRate = this.formatCurrency(rate, 4);
				return `1 ${sourceCurrency} = ${targetSymbol} ${formattedRate}`;
			});

			return `${this.__("Exchange Rate")}: ${formattedRates.join(" • ")}`;
		},
		isCashMode(modeOfPayment) {
			const cashMode = this.cashExpectedSummary?.mode_of_payment || "";
			return Boolean(cashMode && modeOfPayment === cashMode);
		},
		overpaymentDeductionForCurrency(currency) {
			const key = currency || this.overviewCompanyCurrency || "";
			const entry = this.overpaymentChangeByCurrencyMap.get(key);
			return entry?.total || 0;
		},
		calculateDifference(item) {
			const closing = Number(item?.closing_amount) || 0;
			const expected = Number(item?.expected_amount) || 0;
			return expected - closing;
		},
		formatVariancePercent(item) {
			const expected = Number(item?.expected_amount) || 0;
			if (!expected) {
				const closing = Number(item?.closing_amount) || 0;
				return closing ? this.__("N/A") : "0%";
			}
			const variance = (this.calculateDifference(item) / expected) * 100;
			const prefix = variance > 0 ? "+" : variance < 0 ? "" : "";
			return `${prefix}${this.formatFloat(variance, 2)}%`;
		},
		varianceClass(item) {
			const expected = Number(item?.expected_amount) || 0;
			if (!expected) {
				return "variance-neutral";
			}
			const variance = (this.calculateDifference(item) / expected) * 100;
			if (!variance) {
				return "variance-neutral";
			}
			return variance > 0 ? "variance-negative" : "variance-positive";
		},
		fuelRowDifferenceClass(difference) {
			if (Math.abs(Number(difference) || 0) < 0.005) {
				return "fuel-diff-ok";
			}
			return Number(difference) > 0 ? "fuel-diff-warn" : "fuel-diff-bad";
		},
	},

	computed: {
		cashDrawsEnabled() {
			return Boolean(
				this.dialog_data?.cash_draws_enabled ||
					Number(this.pos_profile?.posa_enable_cash_draw) === 1,
			);
		},
		dialogCashDraws() {
			return Array.isArray(this.dialog_data?.cash_draws) ? this.dialog_data.cash_draws : [];
		},
		showCashDrawSummary() {
			return this.cashDrawsEnabled && this.dialogCashDraws.length > 0;
		},
		cashDrawGrandTotal() {
			return this.dialogCashDraws.reduce((total, row) => total + Number(row.amount || 0), 0);
		},
		showFuelClosingReadingsTable() {
			return (
				!!(this.pos_profile && this.pos_profile.custom_enable_fuel_customization) &&
				Array.isArray(this.nozzleClosingReadings) &&
				this.nozzleClosingReadings.length > 0
			);
		},
		fuelReconciliationSummary() {
			if (!this.showFuelClosingReadingsTable) {
				return null;
			}

			// Grouped per fuel item: AGO and PMS carry different rates, so litres
			// and money can only be compared item by item, never blended.
			const byItem = new Map();
			const hasSoldMap = Object.keys(this.fuelSoldByItem || {}).length > 0;
			const invoicedByTank = new Map();

			for (const row of this.nozzleClosingReadings) {
				const itemCode = row.fuel_item || "";
				if (!byItem.has(itemCode)) {
					byItem.set(itemCode, {
						fuel_item: itemCode,
						metered: 0,
						invoiced: hasSoldMap ? Number(this.fuelSoldByItem[itemCode] || 0) : 0,
						rate: 0,
					});
				}
				const entry = byItem.get(itemCode);
				entry.metered += this.netDispensed(row);
				if (!entry.rate && Number(row.rate || 0) > 0) {
					entry.rate = Number(row.rate);
				}
				if (!hasSoldMap) {
					// Fallback: expected_sold_qty is a per tank figure repeated on
					// every nozzle of that tank, so count it once per tank.
					const key = `${itemCode}::${row.warehouse || ""}`;
					if (!invoicedByTank.has(key)) {
						invoicedByTank.set(key, Number(row.expected_sold_qty || 0));
						entry.invoiced += Number(row.expected_sold_qty || 0);
					}
				}
			}

			const items = Array.from(byItem.values()).map((entry) => ({
				...entry,
				difference: entry.metered - entry.invoiced,
				value: (entry.metered - entry.invoiced) * entry.rate,
			}));

			const totals = items.reduce(
				(acc, entry) => {
					acc.metered += entry.metered;
					acc.invoiced += entry.invoiced;
					acc.difference += entry.difference;
					acc.value += entry.value;
					return acc;
				},
				{ metered: 0, invoiced: 0, difference: 0, value: 0 },
			);

			return { items, ...totals };
		},
		fuelDifferenceClass() {
			return this.fuelRowDifferenceClass(this.fuelReconciliationSummary?.difference || 0);
		},
		hidePosTotals() {
			return !!(this.pos_profile && this.pos_profile.custom_hide_pos_totals);
		},
		companyCurrencySymbol() {
			const currency =
				this.overviewCompanyCurrency ||
				this.pos_profile?.currency ||
				this.dialog_data?.currency ||
				"";
			const symbol = this.currencySymbol(currency);
			return symbol || currency || "";
		},
		multiCurrencyTotals() {
			return Array.isArray(this.overview?.multi_currency_totals)
				? this.overview.multi_currency_totals
				: [];
		},
		paymentsByMode() {
			return Array.isArray(this.overview?.payments_by_mode) ? this.overview.payments_by_mode : [];
		},
		creditInvoices() {
			return this.overview?.credit_invoices || { count: 0, company_currency_total: 0, by_currency: [] };
		},
		creditInvoicesByCurrency() {
			return Array.isArray(this.creditInvoices.by_currency) ? this.creditInvoices.by_currency : [];
		},
		returnsSummary() {
			return this.overview?.returns || { count: 0, company_currency_total: 0, by_currency: [] };
		},
		returnsByCurrency() {
			return Array.isArray(this.returnsSummary.by_currency) ? this.returnsSummary.by_currency : [];
		},
		changeReturnedSummary() {
			return (
				this.overview?.change_returned || {
					company_currency_total: 0,
					by_currency: [],
					invoice_change: { company_currency_total: 0, by_currency: [] },
					overpayment_change: { company_currency_total: 0, by_currency: [] },
				}
			);
		},
		invoiceChangeReturnedSummary() {
			return (
				this.changeReturnedSummary?.invoice_change || {
					company_currency_total: 0,
					by_currency: [],
				}
			);
		},
		changeReturnedByCurrency() {
			return Array.isArray(this.changeReturnedSummary.by_currency)
				? this.changeReturnedSummary.by_currency
				: [];
		},
		invoiceChangeReturnedByCurrency() {
			return Array.isArray(this.invoiceChangeReturnedSummary.by_currency)
				? this.invoiceChangeReturnedSummary.by_currency
				: [];
		},
		overpaymentChangeReturnedSummary() {
			return (
				this.changeReturnedSummary?.overpayment_change || {
					company_currency_total: 0,
					by_currency: [],
				}
			);
		},
		overpaymentChangeReturnedByCurrency() {
			return Array.isArray(this.overpaymentChangeReturnedSummary.by_currency)
				? this.overpaymentChangeReturnedSummary.by_currency
				: [];
		},
		overpaymentChangeByCurrencyMap() {
			const map = new Map();
			(this.overpaymentChangeReturnedByCurrency || []).forEach((item) => {
				const currency = item.currency || this.overviewCompanyCurrency || "";
				map.set(currency, {
					total: item.total || 0,
					company_currency_total: item.company_currency_total || 0,
				});
			});
			return map;
		},
		changeReturnedRows() {
			const buildCurrencyMap = (items) => {
				const map = new Map();
				(items || []).forEach((item) => {
					const currency = item.currency || this.overviewCompanyCurrency || "";
					const existing = map.get(currency) || {
						currency,
						total: 0,
						company_currency_total: 0,
						exchange_rates: new Set(),
					};

					existing.total += item.total || 0;
					existing.company_currency_total += item.company_currency_total || 0;
					(item.exchange_rates || []).forEach((rate) => existing.exchange_rates.add(rate));
					map.set(currency, existing);
				});
				return map;
			};

			const invoiceMap = buildCurrencyMap(this.invoiceChangeReturnedByCurrency);
			const overpaymentMap = buildCurrencyMap(this.overpaymentChangeReturnedByCurrency);
			const totalMap = buildCurrencyMap(this.changeReturnedByCurrency);

			const currencies = new Set([...invoiceMap.keys(), ...overpaymentMap.keys(), ...totalMap.keys()]);

			const rows = Array.from(currencies).map((currency) => {
				const invoiceEntry = invoiceMap.get(currency);
				const overpaymentEntry = overpaymentMap.get(currency);
				const totalEntry = totalMap.get(currency);

				const invoiceTotal = invoiceEntry?.total || 0;
				const invoiceCompanyTotal = invoiceEntry?.company_currency_total || 0;
				const invoiceExchangeRates = new Set(invoiceEntry?.exchange_rates || []);

				const overpaymentTotal = overpaymentEntry?.total || 0;
				const overpaymentCompanyTotal = overpaymentEntry?.company_currency_total || 0;

				const exchangeRates = new Set([
					...invoiceExchangeRates,
					...(overpaymentEntry?.exchange_rates || []),
					...(totalEntry?.exchange_rates || []),
				]);

				const total = totalEntry ? totalEntry.total || 0 : invoiceTotal + overpaymentTotal;
				const companyTotal = totalEntry
					? totalEntry.company_currency_total || 0
					: invoiceCompanyTotal + overpaymentCompanyTotal;

				return {
					currency,
					invoice_total: invoiceTotal,
					invoice_company_currency_total: invoiceCompanyTotal,
					overpayment_total: overpaymentTotal,
					overpayment_company_currency_total: overpaymentCompanyTotal,
					total,
					company_currency_total: companyTotal,
					exchange_rates: Array.from(exchangeRates).sort((a, b) => a - b),
				};
			});

			return rows.sort((a, b) => (a.currency || "").localeCompare(b.currency || ""));
		},
		cashExpectedSummary() {
			return (
				this.overview?.cash_expected || {
					mode_of_payment: "",
					company_currency_total: 0,
					by_currency: [],
				}
			);
		},
		cashExpectedByCurrency() {
			return Array.isArray(this.cashExpectedSummary.by_currency)
				? this.cashExpectedSummary.by_currency
				: [];
		},
		salesSummary() {
			return (
				this.overview?.sales_summary || {
					gross_company_currency_total: 0,
					net_company_currency_total: 0,
					average_invoice_value: 0,
					sale_invoices_count: 0,
				}
			);
		},
		overviewCompanyCurrency() {
			return (
				this.overview?.company_currency ||
				this.pos_profile?.currency ||
				this.dialog_data?.currency ||
				""
			);
		},
		primaryInsights() {
			const netSales = this.formatCurrencyWithSymbol(
				this.salesSummary.net_company_currency_total,
				this.overviewCompanyCurrency,
			);
			const grossSales = this.formatCurrencyWithSymbol(
				this.salesSummary.gross_company_currency_total,
				this.overviewCompanyCurrency,
			);
			const avgInvoice = this.formatCurrencyWithSymbol(
				this.salesSummary.average_invoice_value,
				this.overviewCompanyCurrency,
			);

			return [
				{
					key: "total-invoices",
					label: this.__("Total Invoices"),
					value: this.formatCount(this.overview?.total_invoices || 0),
					caption: `${this.__("Sales processed")}: ${this.formatCount(
						this.salesSummary.sale_invoices_count || 0,
					)}`,
					icon: "mdi-receipt-text-multiple",
					color: "accent-primary",
				},
				{
					key: "net-sales",
					label: this.__("Net Sales"),
					value: netSales,
					caption: `${this.__("After returns")}: ${this.formatCurrency(
						this.salesSummary.net_company_currency_total,
					)}`,
					icon: "mdi-cash-multiple",
					color: "accent-success",
				},
				{
					key: "gross-sales",
					label: this.__("Gross Sales"),
					value: grossSales,
					caption: `${this.__("Before returns")}`,
					icon: "mdi-chart-bar",
					color: "accent-secondary",
				},
				{
					key: "average-ticket",
					label: this.__("Average Ticket"),
					value: avgInvoice,
					caption: `${this.__("Across")}: ${this.formatCount(
						this.salesSummary.sale_invoices_count || 0,
					)} ${this.__("sales")}`,
					icon: "mdi-chart-donut",
					color: "accent-info",
				},
			];
		},
		secondaryInsights() {
			const creditValue = this.formatCurrencyWithSymbol(
				this.creditInvoices.company_currency_total,
				this.overviewCompanyCurrency,
			);
			const returnsValue = this.formatCurrencyWithSymbol(
				this.returnsSummary.company_currency_total,
				this.overviewCompanyCurrency,
			);
			const changeValue = this.formatCurrencyWithSymbol(
				this.changeReturnedSummary.company_currency_total,
				this.overviewCompanyCurrency,
			);
			const cashValue = this.formatCurrencyWithSymbol(
				this.cashExpectedSummary.company_currency_total,
				this.overviewCompanyCurrency,
			);

			return [
				{
					key: "credit-sales",
					label: this.__("Credit Outstanding"),
					value: creditValue,
					caption: `${this.__("Open invoices")}: ${this.formatCount(
						this.creditInvoices.count || 0,
					)}`,
					icon: "mdi-account-cash-outline",
					color: "accent-warning",
				},
				{
					key: "returns",
					label: this.__("Returns"),
					value: returnsValue,
					caption: `${this.__("Return count")}: ${this.formatCount(
						this.returnsSummary.count || 0,
					)}`,
					icon: "mdi-undo-variant",
					color: "accent-secondary",
				},
				{
					key: "change-returned",
					label: this.__("Change Returned"),
					value: changeValue,
					caption: `${this.__("Cash back to customers")}`,
					icon: "mdi-cash-refund",
					color: "accent-info",
				},
				{
					key: "cash-expected",
					label: this.__("Expected Cash"),
					value: cashValue,
					caption: this.cashExpectedSummary.mode_of_payment
						? `${this.__("Mode")}: ${this.cashExpectedSummary.mode_of_payment}`
						: this.__("No cash mode configured"),
					icon: "mdi-safe",
					color: "accent-success",
				},
			];
		},
	},

	created: function () {
		this.headers = [...this.baseHeaders];
		this.eventBus.on("open_ClosingDialog", (data) => {
			this.closingDialog = true;
			this.forceClose = !!data.force_close;
			this.dialog_data = data;
			this.nozzleClosingReadingErrors = {};
			this.updateHeaders();
			const nozzleRowMeta = Array.isArray(data.custom_nozzle_reading_meta)
				? data.custom_nozzle_reading_meta
				: [];
			this.nozzleClosingReadings = (data.custom_nozzle_readings || []).map((row, index) => {
				const rowMeta = nozzleRowMeta[index] || {};
				const openingReading = Number(
					row.opening_reading ?? row.current_reading ?? row.custom_current_reading ?? row.reading ?? 0,
				);

				return {
					row_key: `${row.nozzle || row.nozzle_name || "row"}-${index}`,
					nozzle: row.nozzle || row.nozzle_name || "",
					fuel_item: row.fuel_item || "",
					fuel_pump: row.fuel_pump || "",
					warehouse: row.warehouse || "",
					opening_reading: openingReading,
					// Left empty on purpose: the attendant must read the meter.
					closing_reading: null,
					test_return_qty: 0,
					meter_digits: Number(rowMeta.meter_digits || row.meter_digits || 6),
					rate: Number(rowMeta.rate || 0),
					expected_sold_qty: Number(rowMeta.expected_sold_qty || 0),
					shared_tank_nozzle_count: Number(rowMeta.shared_tank_nozzle_count || 0),
					require_manual_closing: rowMeta.require_manual_closing !== false,
				};
			});
			this.cashModeOfPayment = data.custom_fuel_cash_mode_of_payment || "";
			this.fuelCreditTotal = Number(data.custom_fuel_credit_total || 0);
			this.fuelModeShiftSales =
				data.custom_fuel_mode_shift_sales && typeof data.custom_fuel_mode_shift_sales === "object"
					? data.custom_fuel_mode_shift_sales
					: {};
			this.fuelSoldByItem =
				data.custom_fuel_sold_by_item && typeof data.custom_fuel_sold_by_item === "object"
					? data.custom_fuel_sold_by_item
					: {};
			this.recomputeFuelExpected();
			this.fetchOverview(data.pos_opening_shift);
		});
		this.eventBus.on("register_pos_profile", (data) => {
			this.pos_profile = data.pos_profile;
			this.updateHeaders();
		});
	},
	mounted() {
		window.addEventListener("keydown", this.handleKeydown);
	},
	beforeUnmount() {
		this.eventBus.off("open_ClosingDialog");
		this.eventBus.off("register_pos_profile");
		window.removeEventListener("keydown", this.handleKeydown);
	},
};
</script>

<style scoped>
/* Enhanced Header Styles */
.closing-header {
	background: linear-gradient(135deg, #ffffff 0%, #f8f9fa 100%);
	border-bottom: 1px solid #e0e0e0;
	padding: 24px !important;
}

.header-content {
	display: flex;
	align-items: center;
	gap: 20px;
	width: 100%;
}

.header-close-btn {
	color: #5f6368 !important;
	margin-left: 12px;
}

.header-close-btn:hover {
	color: #1f2937 !important;
}

.header-icon-wrapper {
	display: flex;
	align-items: center;
	justify-content: center;
	width: 64px;
	height: 64px;
	background: linear-gradient(135deg, #1976d2 0%, #1565c0 100%);
	border-radius: 16px;
	box-shadow: 0 4px 12px rgba(25, 118, 210, 0.3);
}

.header-icon {
	color: white !important;
}

.header-text {
	flex: 1;
}

.header-title {
	font-size: 1.5rem;
	font-weight: 600;
	color: #1a1a1a;
	margin: 0 0 4px 0;
	line-height: 1.2;
}

.v-theme--dark .closing-dialog-card .header-title {
	color: #ffffff;
}

.header-subtitle {
	font-size: 0.95rem;
	color: #666;
	margin: 0;
	font-weight: 400;
}

.header-divider {
	border-color: #e0e0e0;
}

.white-background {
	background-color: #ffffff;
}

.table-header {
	padding: 0 4px;
}

.white-table {
	background-color: white;
	border: 1px solid #e0e0e0;
}

.overview-wrapper {
	display: flex;
	flex-direction: column;
	gap: 18px;
}

.insight-grid {
	display: flex;
	flex-direction: column;
	gap: 12px;
}

.insight-card {
	background: var(--pos-card-bg);
	border: 1px solid var(--pos-border);
	border-radius: 12px;
	padding: 12px 14px;
	display: flex;
	align-items: center;
	gap: 12px;
	width: 100%;
	min-height: 88px;
	transition: box-shadow 0.2s ease;
}

.insight-card.compact {
	min-height: 78px;
}

.insight-card:hover {
	box-shadow: 0 10px 24px rgba(15, 23, 42, 0.08);
}

.insight-icon {
	width: 40px;
	height: 40px;
	border-radius: 12px;
	display: flex;
	align-items: center;
	justify-content: center;
	color: white;
	flex-shrink: 0;
}

.insight-card.compact .insight-icon {
	width: 36px;
	height: 36px;
	border-radius: 10px;
}

.insight-body {
	display: flex;
	flex-direction: column;
	gap: 4px;
	width: 100%;
}

.insight-label {
	font-size: 0.82rem;
	letter-spacing: 0.04em;
	text-transform: uppercase;
	color: var(--pos-text-secondary);
	font-weight: 600;
}

.insight-value {
	font-size: 1.2rem;
	font-weight: 600;
	color: var(--pos-text-primary);
	line-height: 1.3;
}

.insight-caption {
	font-size: 0.78rem;
	color: var(--pos-text-secondary);
	white-space: normal;
}

.accent-primary {
	background: linear-gradient(135deg, #4f46e5 0%, #4338ca 100%);
}

.accent-success {
	background: linear-gradient(135deg, #2e7d32 0%, #1b5e20 100%);
}

.accent-secondary {
	background: linear-gradient(135deg, #546e7a 0%, #37474f 100%);
}

.accent-info {
	background: linear-gradient(135deg, #0288d1 0%, #0277bd 100%);
}

.accent-warning {
	background: linear-gradient(135deg, #f59e0b 0%, #d97706 100%);
}

.table-section {
	display: flex;
	flex-direction: column;
	gap: 12px;
}

.overview-amount {
	font-family: "Inter", "Roboto", sans-serif;
	font-weight: 600;
}

.overview-table-wrapper {
	border: 1px solid var(--pos-border);
	border-radius: 12px;
	overflow: hidden;
}

.overview-table {
	width: 100%;
	border-collapse: collapse;
	background: var(--pos-card-bg);
}

.overview-table th,
.overview-table td {
	padding: 12px 16px;
	border-bottom: 1px solid var(--pos-border);
	color: var(--pos-text-primary);
}

.overview-table th {
	font-weight: 600;
	background: var(--pos-table-header-bg, rgba(0, 0, 0, 0.04));
	color: var(--pos-text-secondary);
}

.overview-table tr:last-child td {
	border-bottom: none;
}

.overview-table .text-end {
	text-align: right;
}

.amount-with-base {
	display: flex;
	flex-direction: column;
	align-items: flex-end;
	gap: 4px;
}

.amount-primary {
	display: flex;
	align-items: baseline;
	justify-content: flex-end;
	gap: 6px;
	flex-wrap: wrap;
}

.company-equivalent {
	color: var(--pos-text-secondary);
	font-size: 0.85rem;
}

.exchange-note {
	color: var(--pos-text-secondary);
	font-size: 0.75rem;
}

.overview-empty {
	padding: 12px 4px;
	color: var(--pos-text-secondary);
}

.cash-draw-closing-table th:first-child,
.cash-draw-closing-table td:first-child {
	width: 90px;
}
.cash-draw-closing-table th:last-child,
.cash-draw-closing-table td:last-child {
	width: 150px;
}

.fuel-summary {
	display: flex;
	flex-wrap: wrap;
	align-items: center;
	gap: 24px;
	padding: 12px 16px;
	border: 1px solid var(--pos-border);
	border-radius: 12px;
	background: var(--pos-card-bg);
}

.fuel-summary-table {
	width: 100%;
	border-collapse: collapse;
}

.fuel-summary-table th {
	font-size: 0.72rem;
	text-transform: uppercase;
	letter-spacing: 0.05em;
	color: var(--pos-text-secondary);
	font-weight: 600;
	padding: 4px 12px;
	text-align: left;
}

.fuel-summary-table td {
	font-size: 0.95rem;
	font-weight: 600;
	color: var(--pos-text-primary);
	padding: 4px 12px;
}

.fuel-summary-table th.text-right,
.fuel-summary-table td.text-right {
	text-align: right;
}

.fuel-summary-total td {
	border-top: 1px solid var(--pos-border);
	font-weight: 700;
}

.fuel-summary-cell {
	display: flex;
	flex-direction: column;
	gap: 2px;
}

.fuel-summary-label {
	font-size: 0.72rem;
	text-transform: uppercase;
	letter-spacing: 0.05em;
	color: var(--pos-text-secondary);
	font-weight: 600;
}

.fuel-summary-value {
	font-size: 1.05rem;
	font-weight: 700;
	color: var(--pos-text-primary);
}

.fuel-summary-note {
	flex: 1 1 100%;
	font-size: 0.75rem;
	color: var(--pos-text-secondary);
}

.fuel-diff-ok {
	color: #1b5e20;
}

.fuel-diff-warn {
	color: #a04000;
}

.fuel-diff-bad {
	color: #b71c1c;
}

.overview-wrapper .v-progress-circular {
	align-self: center;
}

.variance-chip {
	display: inline-flex;
	align-items: center;
	justify-content: center;
	min-width: 56px;
	padding: 2px 8px;
	border-radius: 999px;
	font-size: 0.75rem;
	font-weight: 600;
}

.variance-positive {
	background: rgba(46, 125, 50, 0.12);
	color: #1b5e20;
}

.variance-negative {
	background: rgba(211, 47, 47, 0.12);
	color: #b71c1c;
}

.variance-neutral {
	background: rgba(96, 125, 139, 0.12);
	color: #37474f;
}

/* Action Buttons */
.dialog-actions-container {
	background: linear-gradient(135deg, #ffffff 0%, #f8f9fa 100%);
	border-top: 1px solid #e0e0e0;
	padding: 16px 24px;
	gap: 12px;
}

.pos-action-btn {
	border-radius: 12px;
	text-transform: none;
	font-weight: 600;
	padding: 12px 32px;
	min-width: 120px;
	transition: all 0.3s ease;
	color: white !important;
	/* Add this line */
}

/* Add these new rules: */
.pos-action-btn .v-icon {
	color: white !important;
}

.pos-action-btn span {
	color: white !important;
}

.pos-action-btn:disabled .v-icon,
.pos-action-btn:disabled span {
	color: white !important;
}

.cancel-action-btn {
	background: linear-gradient(135deg, #d32f2f 0%, #c62828 100%) !important;
}

.submit-action-btn {
	background: linear-gradient(135deg, #388e3c 0%, #2e7d32 100%) !important;
}

.submit-action-btn:hover {
	transform: translateY(-2px);
	box-shadow: 0 6px 20px rgba(46, 125, 50, 0.4);
}

.cancel-action-btn:hover {
	transform: translateY(-2px);
	box-shadow: 0 6px 20px rgba(211, 47, 47, 0.4);
}

.submit-action-btn:disabled {
	opacity: 0.6;
	transform: none;
}

/* Theme-aware dialog styling */
.closing-dialog-card,
.closing-header,
.white-background,
.white-table,
.dialog-actions-container {
	background: var(--pos-card-bg) !important;
	color: var(--pos-text-primary) !important;
}

.closing-header {
	border-bottom: 1px solid var(--pos-border);
}

.dialog-actions-container {
	border-top: 1px solid var(--pos-border);
}

/* And the responsive section: */
@media (max-width: 768px) {
	.dialog-actions-container {
		flex-direction: column;
		gap: 12px;
	}

	.pos-action-btn {
		width: 100%;
	}

	.insight-card {
		min-height: 72px;
	}
}
</style>
