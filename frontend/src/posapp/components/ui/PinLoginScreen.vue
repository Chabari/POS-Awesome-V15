<template>
	<transition name="fade">
		<div v-if="visible" class="pin-login-overlay">
			<div class="pin-login-bg">
				<div class="bg-circle top-right"></div>
				<div class="bg-circle bottom-left"></div>
			</div>

			<div class="pin-login-container">
				<!-- Logo / Header -->
				<div class="pin-login-header">
					<div class="pin-logo">
						<v-icon size="32" color="white">mdi-lock</v-icon>
					</div>
					<h2 class="pin-title">POS Login</h2>
					<p class="pin-subtitle">Select your account and enter your PIN</p>
				</div>

				<!-- Branch chip: always visible, in both the grid and the PIN view.
					     This is the escape hatch. A terminal left on another branch's
					     profile used to be a dead end with no way back. -->
				<button class="branch-chip" @click="openBranchPicker">
					<v-icon size="20" color="rgba(255,255,255,0.75)">mdi-store-outline</v-icon>
					<span class="branch-chip-text">
						<span class="branch-chip-name">{{ activeBranchLabel }}</span>
						<span class="branch-chip-hint">{{ __("Tap to change branch") }}</span>
					</span>
					<v-icon size="22" color="rgba(255,255,255,0.75)">mdi-chevron-down</v-icon>
				</button>

				<!-- Cashier Grid (when no cashier selected) -->
				<div v-if="!selectedCashier" class="cashier-section">
					<div v-if="cashiersLoading" class="cashier-loading">
						<v-progress-circular indeterminate color="white" size="32" />
						<span>Loading users...</span>
					</div>
					<div v-else-if="cashiers.length === 0" class="cashier-empty">
						<p>{{ __("No cashiers are assigned to") }} <strong>{{ activeBranchLabel }}</strong>.</p>
						<button class="branch-change-btn" @click="openBranchPicker">
							{{ __("Choose a different branch") }}
						</button>
					</div>
					<div v-else class="cashier-grid">
						<button
							v-for="cashier in cashiers"
							:key="cashier.name"
							class="cashier-card"
							:class="{ 'no-pin': !cashier.has_pin }"
							:disabled="!cashier.has_pin"
							@click="selectCashier(cashier)"
						>
							<div class="cashier-avatar">
								<img
									v-if="cashier.user_image"
									:src="cashier.user_image"
									:alt="cashier.full_name"
									@error="$event.target.style.display = 'none'"
								/>
								<span v-else class="avatar-initials">{{ getInitials(cashier.full_name) }}</span>
							</div>
							<span class="cashier-name">{{ cashier.full_name }}</span>
							<span v-if="!cashier.has_pin" class="no-pin-badge">No PIN</span>
						</button>
					</div>
				</div>

				<!-- PIN Entry (when cashier is selected) -->
				<div v-else class="pin-entry-section">
					<!-- Selected user button -->
					<button class="selected-user-btn" @click="clearSelection">
						<v-icon size="16" color="rgba(255,255,255,0.6)">mdi-arrow-left</v-icon>
						<div class="selected-avatar-small">
							<img
								v-if="selectedCashier.user_image"
								:src="selectedCashier.user_image"
								:alt="selectedCashier.full_name"
								@error="$event.target.style.display = 'none'"
							/>
							<span v-else class="avatar-initials-sm">{{ getInitials(selectedCashier.full_name) }}</span>
						</div>
						<span class="selected-name">{{ selectedCashier.full_name }}</span>
					</button>

					<!-- PIN dots -->
					<div class="pin-dots">
						<div
							v-for="i in 4"
							:key="i"
							class="pin-dot"
							:class="{
								filled: pin.length >= i,
								error: pinError && pin.length >= i,
							}"
						/>
					</div>

					<!-- Error message -->
					<p v-if="pinError" class="pin-error">{{ pinError }}</p>

					<!-- Loading indicator -->
					<div v-if="isLoggingIn" class="pin-verifying">
						<v-progress-circular indeterminate color="white" size="16" width="2" />
						<span>Verifying...</span>
					</div>

					<!-- Keypad -->
					<div class="pin-keypad">
						<button
							v-for="key in keypadKeys"
							:key="key"
							class="keypad-btn"
							:class="keypadBtnClass(key)"
							:disabled="isLoggingIn"
							@click="handleKeyPress(key)"
						>
							<v-icon v-if="key === 'delete'" size="20" color="rgba(255,255,255,0.7)">mdi-backspace</v-icon>
							<span v-else-if="key === 'clear'" class="key-label">Clear</span>
							<span v-else class="key-digit">{{ key }}</span>
						</button>
					</div>
				</div>
			</div>

			<!-- Branch picker -->
			<div v-if="branchPickerOpen" class="branch-picker-overlay" @click.self="closeBranchPicker">
				<div class="branch-picker">
					<div class="branch-picker-header">
						<h3>{{ __("Select your branch") }}</h3>
						<button class="branch-picker-close" @click="closeBranchPicker">
							<v-icon size="22" color="rgba(255,255,255,0.7)">mdi-close</v-icon>
						</button>
					</div>

					<p v-if="pendingWarning" class="branch-pending-warning">
						<v-icon size="16" color="#fbbf24">mdi-cloud-upload-outline</v-icon>
						{{ pendingWarning }}
					</p>

					<div v-if="branchesLoading" class="branch-picker-loading">
						<v-progress-circular indeterminate color="white" size="28" />
						<span>{{ __("Loading branches...") }}</span>
					</div>
					<p v-else-if="branchError" class="branch-picker-error">{{ branchError }}</p>
					<div v-else class="branch-list">
						<button
							v-for="branch in branches"
							:key="branch.name"
							class="branch-item"
							:class="{ active: branch.name === activeBranchName }"
							:disabled="switching"
							@click="selectBranch(branch)"
						>
							<div class="branch-item-text">
								<span class="branch-item-name">{{ branch.name }}</span>
								<span v-if="branch.company" class="branch-item-company">{{ branch.company }}</span>
							</div>
							<v-icon v-if="branch.name === activeBranchName" size="20" color="#4ade80">mdi-check-circle</v-icon>
						</button>
					</div>

					<div v-if="switching" class="branch-switching">
						<v-progress-circular indeterminate color="white" size="18" width="2" />
						<span>{{ switchingLabel }}</span>
					</div>
				</div>
			</div>
		</div>
	</transition>
</template>

<script>
/* global frappe */
import {
	getTerminalBinding,
	setTerminalBinding,
	getBoundProfileName,
} from "../../../utils/terminal.js";
import { switchPosProfile, getPendingWorkCount } from "../../../utils/profileSwitch.js";
import {
	withSessionSwitch,
	refreshCsrfToken,
	isInvalidRequestError,
	apiGet,
} from "../../../utils/session.js";

const INACTIVITY_TIMEOUT = 3 * 60 * 1000; // 3 minutes

export default {
	name: "PinLoginScreen",
	props: {
		posProfile: { type: Object, default: () => ({}) },
	},
	emits: ["authenticated", "auth-state-change", "lock-state-change", "branch-changed"],
	data() {
		return {
			visible: false,
			cashiers: [],
			cashiersLoading: false,
			selectedCashier: null,
			pin: "",
			pinError: "",
			isLoggingIn: false,
			isAuthenticated: false,
			isLocked: true,
			inactivityTimer: null,
			keypadKeys: ["1", "2", "3", "4", "5", "6", "7", "8", "9", "clear", "0", "delete"],
			// Branch (POS Profile) the terminal is bound to.  Resolved from the
			// terminal binding first so a stale pos_opening_storage left behind by
			// the previous user can no longer decide what this screen shows.
			// Read here rather than in created(): the immediate `enabled` watcher
			// runs before created(), so a null binding would hide the lock screen
			// on a terminal that has a binding but no cached profile yet.
			boundBranch: getTerminalBinding(),
			branches: [],
			branchesLoading: false,
			branchError: "",
			branchPickerOpen: false,
			switching: false,
			switchingLabel: "",
			pendingWarning: "",
		};
	},
	computed: {
		/**
		 * PIN login stays on for a terminal once it has been bound to a PIN-enabled
		 * branch.  Reading it only from the incoming posProfile meant the POS
		 * rendered unlocked whenever the cache was empty.
		 */
		enabled() {
			if (this.posProfile?.custom_enable_pin_login) return true;
			return !!this.boundBranch?.pin_login;
		},
		activeBranchName() {
			return this.boundBranch?.pos_profile || this.posProfile?.name || null;
		},
		activeBranchLabel() {
			return this.activeBranchName || this.__("No branch selected");
		},
	},
	watch: {
		posProfile: {
			immediate: true,
			deep: true,
			handler(profile) {
				if (!profile?.name) return;
				// Adopt the live profile as the terminal's branch when nothing is
				// bound yet (first run, or an upgrade from before binding existed).
				const bound = getBoundProfileName();
				if (!bound || bound === profile.name) {
					this.boundBranch = setTerminalBinding({
						pos_profile: profile.name,
						company: profile.company,
						pin_login: !!profile.custom_enable_pin_login,
					});
				}
			},
		},
		enabled: {
			immediate: true,
			handler(val) {
				if (val) {
					this.visible = true;
					this.isLocked = true;
					this.isAuthenticated = false;
					this.$emit("lock-state-change", { locked: true });
					this.refreshCashiers();
				} else {
					this.visible = false;
					this.$emit("lock-state-change", { locked: false });
				}
			},
		},
		pin(newVal) {
			if (newVal.length >= 4 && this.selectedCashier && !this.isLoggingIn) {
				this.submitPin();
			}
		},
	},
	mounted() {
		this._handleKeyDown = this._onKeyDown.bind(this);
		window.addEventListener("keydown", this._handleKeyDown);

		this._handleActivity = this._resetInactivityTimer.bind(this);
		const events = ["mousedown", "mousemove", "keydown", "touchstart", "scroll", "click"];
		events.forEach((evt) => window.addEventListener(evt, this._handleActivity, { passive: true }));
		this._activityEvents = events;
	},
	beforeUnmount() {
		window.removeEventListener("keydown", this._handleKeyDown);
		if (this._activityEvents) {
			this._activityEvents.forEach((evt) => window.removeEventListener(evt, this._handleActivity));
		}
		if (this.inactivityTimer) {
			clearTimeout(this.inactivityTimer);
		}
	},
	methods: {
		// ---- Branch (POS Profile) ------------------------------------------

		async fetchBranches() {
			this.branchesLoading = true;
			this.branchError = "";
			try {
				this.branches = (await apiGet("posawesome.posawesome.api.pin_login.get_pos_branches")) || [];
				if (!this.branches.length) {
					this.branchError = this.__("No POS Profiles are available.");
				}
			} catch (e) {
				console.error("Failed to fetch branches", e);
				this.branches = [];
				this.branchError = this.__("Could not load branches. Check the network and try again.");
			} finally {
				this.branchesLoading = false;
			}
		},

		openBranchPicker() {
			this.branchPickerOpen = true;
			const pending = getPendingWorkCount();
			this.pendingWarning = pending.total
				? this.__("{0} sale(s) from {1} have not been uploaded yet. They will be kept and uploaded when this terminal is back online.").replace(
						"{0}",
						pending.total,
					).replace("{1}", this.activeBranchLabel)
				: "";
			this.fetchBranches();
		},

		closeBranchPicker() {
			if (this.switching) return;
			this.branchPickerOpen = false;
		},

		async selectBranch(branch) {
			if (!branch?.name || this.switching) return;
			if (branch.name === this.activeBranchName) {
				this.branchPickerOpen = false;
				return;
			}

			this.switching = true;
			this.switchingLabel = this.__("Switching branch...");
			try {
				const result = await switchPosProfile(branch, {
					onProgress: (stage) => {
						if (stage === "syncing") this.switchingLabel = this.__("Uploading pending sales...");
						else if (stage === "clearing") this.switchingLabel = this.__("Clearing branch data...");
					},
				});

				this.boundBranch = setTerminalBinding({
					pos_profile: branch.name,
					company: branch.company,
					pin_login: true,
				});

				this.selectedCashier = null;
				this.pin = "";
				this.pinError = "";
				this.branchPickerOpen = false;
				this.$emit("branch-changed", { ...result, branch });
				await this.refreshCashiers();
			} catch (e) {
				console.error("Failed to switch branch", e);
				this.branchError = this.__("Could not switch branch. Please try again.");
			} finally {
				this.switching = false;
				this.switchingLabel = "";
			}
		},

		// ---- Cashiers -------------------------------------------------------

		async refreshCashiers() {
			if (!this.activeBranchName) {
				// Nothing to show and nothing to guess from: open the picker rather
				// than render an empty grid the cashier cannot escape.
				this.cashiers = [];
				this.openBranchPicker();
				return;
			}
			await this.fetchCashiers();
		},

		async fetchCashiers() {
			this.cashiersLoading = true;
			try {
				this.cashiers =
					(await apiGet("posawesome.posawesome.api.pin_login.get_cashiers", {
						pos_profile: this.activeBranchName,
					})) || [];
			} catch (e) {
				console.error("Failed to fetch cashiers", e);
				this.cashiers = [];
			} finally {
				this.cashiersLoading = false;
			}
		},

		selectCashier(cashier) {
			if (!cashier.has_pin) return;
			this.selectedCashier = cashier;
			this.pin = "";
			this.pinError = "";
		},

		clearSelection() {
			this.selectedCashier = null;
			this.pin = "";
			this.pinError = "";
		},

		handleKeyPress(key) {
			if (key === "clear") {
				this.pin = "";
				this.pinError = "";
			} else if (key === "delete") {
				this.pin = this.pin.slice(0, -1);
				this.pinError = "";
			} else {
				if (this.pin.length < 6) {
					this.pin += key;
					this.pinError = "";
				}
			}
		},

		keypadBtnClass(key) {
			return {
				"key-action": key === "clear" || key === "delete",
				"key-number": key !== "clear" && key !== "delete",
			};
		},

		// ---- Authentication --------------------------------------------------

		async submitPin() {
			if (this.isLoggingIn || !this.selectedCashier) return;
			this.isLoggingIn = true;
			this.pinError = "";
			this.$emit("auth-state-change", { inProgress: true });

			const targetUser = this.selectedCashier.name;
			try {
				// withSessionSwitch quiesces the app first, then verifies the server
				// really is us before letting anything else out.  Both of the races
				// that produced "Invalid Request" are in-flight-traffic races.
				//
				// `call` is the UNGATED caller. Using frappe.call here would queue
				// the request behind the gate that is waiting on it, and hang.
				const r = await withSessionSwitch(
					(call) =>
						call({
							method: "posawesome.posawesome.api.pin_login.pin_login",
							args: {
								user: targetUser,
								pin: this.pin,
								pos_profile: this.activeBranchName,
							},
						}),
					{
						// A refused PIN comes back 200 with success:false; there is no
						// new session to verify in that case.
						expectUser: (res) => (res?.message?.success ? targetUser : null),
					},
				);

				if (!r?.message?.success) {
					this.pinError = r?.message?.error || this.__("Incorrect PIN");
					this.pin = "";
					return;
				}

				if (r.message?.csrf_token) frappe.csrf_token = r.message.csrf_token;
				if (r.message?.user) frappe.session.user = r.message.user;

				this.isAuthenticated = true;
				this.isLocked = false;
				this.visible = false;
				this.$emit("lock-state-change", { locked: false });
				this.$emit("authenticated", {
					user: targetUser,
					full_name: r.message?.full_name || this.selectedCashier.full_name,
					pos_profile: this.activeBranchName,
				});
				this._resetInactivityTimer();
			} catch (e) {
				this.pinError = await this._describeLoginError(e);
				this.pin = "";
			} finally {
				this.isLoggingIn = false;
				this.$emit("auth-state-change", { inProgress: false });
			}
		},

		async _describeLoginError(e) {
			if (e?.posaTimeout) {
				return this.__("Login timed out. Check the connection and try again.");
			}
			if (e?.posaSessionUnverified) {
				return this.__("Session could not be confirmed. Please reload this page and try again.");
			}
			if (isInvalidRequestError(e)) {
				// Token has already been repaired by the gate; the next attempt works.
				await refreshCsrfToken();
				return this.__("Session expired. Please enter your PIN again.");
			}

			const msg = e?.message || e?._server_messages || "";
			let errorText = this.__("Incorrect PIN");
			try {
				const parsed = JSON.parse(msg);
				errorText = typeof parsed === "string" ? parsed : parsed[0] || errorText;
				errorText = errorText.replace(/<[^>]*>/g, "").trim();
			} catch {
				if (typeof msg === "string" && msg && !msg.startsWith("{")) {
					errorText = msg.replace(/<[^>]*>/g, "").trim();
				}
			}
			return errorText;
		},

		lockSession() {
			if (!this.enabled) return;
			this.isLocked = true;
			this.visible = true;
			this.pin = "";
			this.pinError = "";
			this.selectedCashier = null;
			this.$emit("lock-state-change", { locked: true });
			this.refreshCashiers();
		},

		_onKeyDown(e) {
			if (!this.visible || this.branchPickerOpen) return;
			if (!this.selectedCashier) return;
			if (e.key >= "0" && e.key <= "9") {
				this.handleKeyPress(e.key);
			} else if (e.key === "Backspace") {
				this.handleKeyPress("delete");
			} else if (e.key === "Escape") {
				this.clearSelection();
			} else if (e.key === "Enter" && this.pin.length >= 4) {
				this.submitPin();
			}
		},

		_resetInactivityTimer() {
			if (this.inactivityTimer) {
				clearTimeout(this.inactivityTimer);
			}
			if (this.isAuthenticated && !this.isLocked && this.enabled) {
				this.inactivityTimer = setTimeout(() => {
					this.lockSession();
				}, INACTIVITY_TIMEOUT);
			}
		},

		getInitials(name) {
			return (name || "?")
				.split(" ")
				.map((w) => w[0])
				.slice(0, 2)
				.join("")
				.toUpperCase();
		},
	},
};
</script>

<style scoped>
.pin-login-overlay {
	position: fixed;
	inset: 0;
	z-index: 9999;
	display: flex;
	align-items: center;
	justify-content: center;
	background: linear-gradient(135deg, #0f172a 0%, #1e3a5f 50%, #312e81 100%);
}

.pin-login-bg {
	position: absolute;
	inset: 0;
	overflow: hidden;
	pointer-events: none;
}

.bg-circle {
	position: absolute;
	width: 320px;
	height: 320px;
	border-radius: 50%;
	filter: blur(80px);
}

.bg-circle.top-right {
	top: -160px;
	right: -160px;
	background: rgba(59, 130, 246, 0.1);
}

.bg-circle.bottom-left {
	bottom: -160px;
	left: -160px;
	background: rgba(139, 92, 246, 0.1);
}

.pin-login-container {
	position: relative;
	z-index: 1;
	width: 100%;
	max-width: 480px;
	padding: 24px;
	display: flex;
	flex-direction: column;
	align-items: center;
	gap: 24px;
}

/* Header */
.pin-login-header {
	display: flex;
	flex-direction: column;
	align-items: center;
	gap: 8px;
}

.pin-logo {
	width: 56px;
	height: 56px;
	border-radius: 16px;
	background: rgba(255, 255, 255, 0.1);
	backdrop-filter: blur(8px);
	display: flex;
	align-items: center;
	justify-content: center;
	border: 1px solid rgba(255, 255, 255, 0.1);
	box-shadow: 0 8px 32px rgba(0, 0, 0, 0.3);
}

.pin-title {
	color: white;
	font-size: 20px;
	font-weight: 600;
	margin: 0;
}

.pin-subtitle {
	color: rgba(255, 255, 255, 0.6);
	font-size: 13px;
	margin: 0;
}

/* Cashier Grid */
.cashier-section {
	width: 100%;
}

.cashier-loading,
.cashier-empty {
	display: flex;
	flex-direction: column;
	align-items: center;
	gap: 12px;
	color: rgba(255, 255, 255, 0.7);
	font-size: 14px;
	padding: 32px 0;
}

.cashier-grid {
	display: grid;
	grid-template-columns: repeat(3, 1fr);
	gap: 12px;
	max-width: 400px;
	margin: 0 auto;
}

.cashier-card {
	display: flex;
	flex-direction: column;
	align-items: center;
	gap: 8px;
	padding: 16px 8px;
	border-radius: 12px;
	background: rgba(255, 255, 255, 0.1);
	border: 1px solid rgba(255, 255, 255, 0.1);
	backdrop-filter: blur(4px);
	cursor: pointer;
	transition: all 0.2s ease;
	position: relative;
}

.cashier-card:hover:not(:disabled) {
	background: rgba(255, 255, 255, 0.2);
	border-color: rgba(255, 255, 255, 0.3);
	transform: scale(1.05);
}

.cashier-card:active:not(:disabled) {
	transform: scale(0.95);
}

.cashier-card.no-pin {
	opacity: 0.4;
	cursor: not-allowed;
}

.cashier-avatar {
	width: 56px;
	height: 56px;
	border-radius: 50%;
	background: linear-gradient(135deg, #3b82f6, #8b5cf6);
	display: flex;
	align-items: center;
	justify-content: center;
	overflow: hidden;
	box-shadow: 0 4px 12px rgba(0, 0, 0, 0.3);
}

.cashier-avatar img {
	width: 100%;
	height: 100%;
	object-fit: cover;
	border-radius: 50%;
}

.avatar-initials {
	color: white;
	font-weight: 700;
	font-size: 18px;
}

.cashier-name {
	color: rgba(255, 255, 255, 0.9);
	font-size: 13px;
	font-weight: 500;
	text-align: center;
	line-height: 1.3;
}

.no-pin-badge {
	position: absolute;
	top: 4px;
	right: 4px;
	background: rgba(239, 68, 68, 0.7);
	color: white;
	font-size: 9px;
	padding: 2px 6px;
	border-radius: 8px;
}

/* PIN Entry */
.pin-entry-section {
	display: flex;
	flex-direction: column;
	align-items: center;
	gap: 20px;
	width: 100%;
	max-width: 320px;
}

.selected-user-btn {
	display: flex;
	align-items: center;
	gap: 10px;
	padding: 6px 16px 6px 12px;
	border-radius: 999px;
	background: rgba(255, 255, 255, 0.1);
	border: 1px solid rgba(255, 255, 255, 0.1);
	cursor: pointer;
	transition: background 0.2s;
}

.selected-user-btn:hover {
	background: rgba(255, 255, 255, 0.15);
}

.selected-avatar-small {
	width: 28px;
	height: 28px;
	border-radius: 50%;
	background: linear-gradient(135deg, #3b82f6, #8b5cf6);
	display: flex;
	align-items: center;
	justify-content: center;
	overflow: hidden;
}

.selected-avatar-small img {
	width: 100%;
	height: 100%;
	object-fit: cover;
	border-radius: 50%;
}

.avatar-initials-sm {
	color: white;
	font-weight: 700;
	font-size: 10px;
}

.selected-name {
	color: white;
	font-weight: 500;
	font-size: 14px;
}

/* PIN Dots */
.pin-dots {
	display: flex;
	gap: 12px;
}

.pin-dot {
	width: 14px;
	height: 14px;
	border-radius: 50%;
	background: rgba(255, 255, 255, 0.2);
	border: 1px solid rgba(255, 255, 255, 0.3);
	transition: all 0.2s;
}

.pin-dot.filled {
	background: white;
	border-color: white;
	transform: scale(1.1);
}

.pin-dot.error {
	background: #f87171;
	border-color: #f87171;
}

/* Error */
.pin-error {
	color: #fca5a5;
	font-size: 13px;
	text-align: center;
	animation: pulse 1.5s ease-in-out infinite;
	margin: 0;
}

/* Verifying */
.pin-verifying {
	display: flex;
	align-items: center;
	gap: 8px;
	color: rgba(255, 255, 255, 0.7);
	font-size: 13px;
}

/* Keypad */
.pin-keypad {
	display: grid;
	grid-template-columns: repeat(3, 1fr);
	gap: 10px;
	width: 100%;
	max-width: 280px;
}

.keypad-btn {
	height: 56px;
	border-radius: 12px;
	border: 1px solid rgba(255, 255, 255, 0.05);
	cursor: pointer;
	display: flex;
	align-items: center;
	justify-content: center;
	transition: all 0.15s ease;
}

.keypad-btn:disabled {
	opacity: 0.3;
	cursor: not-allowed;
}

.keypad-btn.key-number {
	background: rgba(255, 255, 255, 0.1);
	backdrop-filter: blur(4px);
}

.keypad-btn.key-number:hover:not(:disabled) {
	background: rgba(255, 255, 255, 0.2);
	transform: scale(1.05);
}

.keypad-btn.key-number:active:not(:disabled) {
	transform: scale(0.95);
}

.keypad-btn.key-action {
	background: rgba(255, 255, 255, 0.05);
}

.keypad-btn.key-action:hover:not(:disabled) {
	background: rgba(255, 255, 255, 0.1);
}

.key-digit {
	color: white;
	font-size: 22px;
	font-weight: 600;
}

.key-label {
	color: rgba(255, 255, 255, 0.5);
	font-size: 12px;
	font-weight: 500;
}

/* Branch chip + picker */
.branch-chip {
	display: flex;
	align-items: center;
	gap: 12px;
	width: 100%;
	max-width: 400px;
	padding: 12px 16px;
	border-radius: 14px;
	background: rgba(255, 255, 255, 0.12);
	border: 1px solid rgba(255, 255, 255, 0.18);
	backdrop-filter: blur(6px);
	cursor: pointer;
	transition: all 0.2s ease;
	text-align: left;
}

.branch-chip:hover {
	background: rgba(255, 255, 255, 0.2);
	border-color: rgba(255, 255, 255, 0.35);
}

.branch-chip:active {
	transform: scale(0.98);
}

.branch-chip-text {
	display: flex;
	flex-direction: column;
	flex: 1;
	min-width: 0;
}

.branch-chip-name {
	color: white;
	font-size: 16px;
	font-weight: 600;
	white-space: nowrap;
	overflow: hidden;
	text-overflow: ellipsis;
}

.branch-chip-hint {
	color: rgba(255, 255, 255, 0.55);
	font-size: 11px;
	letter-spacing: 0.02em;
}

.branch-change-btn {
	margin-top: 4px;
	padding: 10px 20px;
	border-radius: 999px;
	background: rgba(255, 255, 255, 0.15);
	border: 1px solid rgba(255, 255, 255, 0.25);
	color: white;
	font-size: 13px;
	font-weight: 600;
	cursor: pointer;
	transition: background 0.2s;
}

.branch-change-btn:hover {
	background: rgba(255, 255, 255, 0.25);
}

.branch-picker-overlay {
	position: fixed;
	inset: 0;
	z-index: 10000;
	display: flex;
	align-items: center;
	justify-content: center;
	padding: 24px;
	background: rgba(2, 6, 23, 0.75);
	backdrop-filter: blur(4px);
}

.branch-picker {
	width: 100%;
	max-width: 420px;
	max-height: 80vh;
	display: flex;
	flex-direction: column;
	gap: 12px;
	padding: 20px;
	border-radius: 18px;
	background: #111c33;
	border: 1px solid rgba(255, 255, 255, 0.12);
	box-shadow: 0 24px 64px rgba(0, 0, 0, 0.5);
}

.branch-picker-header {
	display: flex;
	align-items: center;
	justify-content: space-between;
}

.branch-picker-header h3 {
	color: white;
	font-size: 17px;
	font-weight: 600;
	margin: 0;
}

.branch-picker-close {
	background: transparent;
	border: none;
	cursor: pointer;
	display: flex;
	padding: 4px;
}

.branch-pending-warning {
	display: flex;
	align-items: flex-start;
	gap: 8px;
	margin: 0;
	padding: 10px 12px;
	border-radius: 10px;
	background: rgba(251, 191, 36, 0.12);
	border: 1px solid rgba(251, 191, 36, 0.3);
	color: #fde68a;
	font-size: 12px;
	line-height: 1.45;
}

.branch-picker-loading {
	display: flex;
	flex-direction: column;
	align-items: center;
	gap: 12px;
	padding: 28px 0;
	color: rgba(255, 255, 255, 0.7);
	font-size: 14px;
}

.branch-picker-error {
	color: #fca5a5;
	font-size: 13px;
	text-align: center;
	margin: 0;
	padding: 16px 0;
}

.branch-list {
	display: flex;
	flex-direction: column;
	gap: 8px;
	overflow-y: auto;
}

.branch-item {
	display: flex;
	align-items: center;
	justify-content: space-between;
	gap: 12px;
	width: 100%;
	padding: 16px;
	border-radius: 12px;
	background: rgba(255, 255, 255, 0.07);
	border: 1px solid rgba(255, 255, 255, 0.1);
	cursor: pointer;
	transition: all 0.15s ease;
	text-align: left;
}

.branch-item:hover:not(:disabled) {
	background: rgba(255, 255, 255, 0.14);
	border-color: rgba(255, 255, 255, 0.28);
}

.branch-item:disabled {
	opacity: 0.5;
	cursor: not-allowed;
}

.branch-item.active {
	border-color: rgba(74, 222, 128, 0.5);
	background: rgba(74, 222, 128, 0.1);
}

.branch-item-text {
	display: flex;
	flex-direction: column;
	min-width: 0;
}

.branch-item-name {
	color: white;
	font-size: 15px;
	font-weight: 600;
}

.branch-item-company {
	color: rgba(255, 255, 255, 0.5);
	font-size: 12px;
}

.branch-switching {
	display: flex;
	align-items: center;
	justify-content: center;
	gap: 8px;
	color: rgba(255, 255, 255, 0.75);
	font-size: 13px;
	padding-top: 4px;
}

/* Transitions */
.fade-enter-active,
.fade-leave-active {
	transition: opacity 0.3s ease;
}

.fade-enter-from,
.fade-leave-to {
	opacity: 0;
}

@keyframes pulse {
	0%,
	100% {
		opacity: 1;
	}
	50% {
		opacity: 0.5;
	}
}

/* Responsive */
@media (max-width: 400px) {
	.cashier-grid {
		grid-template-columns: repeat(2, 1fr);
	}

	.cashier-avatar {
		width: 48px;
		height: 48px;
	}

	.keypad-btn {
		height: 48px;
	}

	.key-digit {
		font-size: 18px;
	}
}
</style>
