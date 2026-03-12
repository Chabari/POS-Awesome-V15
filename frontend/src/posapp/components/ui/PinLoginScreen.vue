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

				<!-- Cashier Grid (when no cashier selected) -->
				<div v-if="!selectedCashier" class="cashier-section">
					<div v-if="cashiersLoading" class="cashier-loading">
						<v-progress-circular indeterminate color="white" size="32" />
						<span>Loading users...</span>
					</div>
					<div v-else-if="cashiers.length === 0" class="cashier-empty">
						<p>No users with PIN configured found.</p>
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
		</div>
	</transition>
</template>

<script>
/* global frappe */

const INACTIVITY_TIMEOUT = 1.5 * 60 * 1000; // 3 minutes

export default {
	name: "PinLoginScreen",
	props: {
		posProfile: { type: Object, default: () => ({}) },
	},
	emits: ["authenticated"],
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
		};
	},
	computed: {
		enabled() {
			return !!this.posProfile?.custom_enable_pin_login;
		},
	},
	watch: {
		enabled: {
			immediate: true,
			handler(val) {
				if (val) {
					this.visible = true;
					this.isLocked = true;
					this.isAuthenticated = false;
					this.fetchCashiers();
				} else {
					this.visible = false;
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
		async fetchCashiers() {
			this.cashiersLoading = true;
			try {
				const r = await frappe.call({
					method: "posawesome.posawesome.api.pin_login.get_cashiers",
					args: { pos_profile: this.posProfile?.name },
				});
				this.cashiers = r.message || [];
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

		async _refreshCsrfToken() {
			try {
				const resp = await fetch(
					"/api/method/posawesome.posawesome.api.pin_login.get_session_csrf",
					{ method: "GET", credentials: "same-origin", cache: "no-store" },
				);
				if (resp.ok) {
					const data = await resp.json();
					if (data.message?.csrf_token) {
						frappe.csrf_token = data.message.csrf_token;
					}
					if (data.message?.user) {
						frappe.session.user = data.message.user;
					}
				}
			} catch (err) {
				console.warn("Failed to refresh CSRF token", err);
			}
		},

		async submitPin() {
			if (this.isLoggingIn || !this.selectedCashier) return;
			this.isLoggingIn = true;
			this.pinError = "";
			try {
				const r = await frappe.call({
					method: "posawesome.posawesome.api.pin_login.pin_login",
					args: {
						user: this.selectedCashier.name,
						pin: this.pin,
					},
				});
				if (r.message && r.message.csrf_token) {
					frappe.csrf_token = r.message.csrf_token;
				}
				if (r.message?.user) {
					frappe.session.user = r.message.user;
				}
				// Verify CSRF token is in sync with the new session
				await this._refreshCsrfToken();
				this.isAuthenticated = true;
				this.isLocked = false;
				this.visible = false;
				this.$emit("authenticated", {
					user: this.selectedCashier.name,
					full_name: r.message?.full_name || this.selectedCashier.full_name,
				});
				this._resetInactivityTimer();
			} catch (e) {
				// If pin_login partially succeeded (server switched session but
				// response was lost), the sid cookie may already point to the
				// new session while frappe.csrf_token is stale.  Refresh it so
				// subsequent requests don't fail with "Invalid Request".
				await this._refreshCsrfToken();
				const msg = e?.message || e?._server_messages || "Incorrect PIN";
				let errorText = "Incorrect PIN";
				try {
					const parsed = JSON.parse(msg);
					errorText = typeof parsed === "string" ? parsed : parsed[0] || errorText;
					errorText = errorText.replace(/<[^>]*>/g, "").trim();
				} catch {
					if (typeof msg === "string" && !msg.startsWith("{")) {
						errorText = msg.replace(/<[^>]*>/g, "").trim();
					}
				}
				this.pinError = errorText;
				this.pin = "";
			} finally {
				this.isLoggingIn = false;
			}
		},

		lockSession() {
			if (!this.enabled) return;
			this.isLocked = true;
			this.visible = true;
			this.pin = "";
			this.pinError = "";
			this.selectedCashier = null;
			this.fetchCashiers();
		},

		_onKeyDown(e) {
			if (!this.visible || !this.selectedCashier) return;
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
