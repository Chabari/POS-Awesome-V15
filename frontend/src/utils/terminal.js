/**
 * Terminal binding.
 *
 * The POS Profile (branch) a till sells under belongs to the *machine*, not to
 * whoever last logged in.  Before this module the lock screen read the branch
 * out of `pos_opening_storage` — the cache left behind by the previous user —
 * so a cashier from another branch could strand a terminal on their own branch
 * with no way back.
 *
 * The binding deliberately lives in localStorage under a `posawesome_` prefix
 * rather than `posa_`, so the `posa_`-prefix sweeps in `forceClearAllCache()`
 * (offline/cache.js) leave it alone.  A cache clear must not un-bind the till.
 */

const BINDING_KEY = "posawesome_terminal_binding";
const DEVICE_KEY = "posawesome_device_id";

function hasStorage() {
	return typeof localStorage !== "undefined";
}

function readJson(key) {
	if (!hasStorage()) return null;
	try {
		const raw = localStorage.getItem(key);
		return raw ? JSON.parse(raw) : null;
	} catch (e) {
		console.warn("Failed to read", key, e);
		return null;
	}
}

/**
 * Stable per-browser identifier, so a terminal can be recognised (and later
 * re-pointed) from the server side.  Generated once and then never changes
 * unless the user wipes site data.
 */
export function getDeviceId() {
	if (!hasStorage()) return null;
	let id = localStorage.getItem(DEVICE_KEY);
	if (!id) {
		const rand =
			typeof crypto !== "undefined" && crypto.randomUUID
				? crypto.randomUUID()
				: `${Date.now().toString(36)}-${Math.random().toString(36).slice(2, 10)}`;
		id = `TILL-${rand.replace(/-/g, "").slice(0, 8).toUpperCase()}`;
		try {
			localStorage.setItem(DEVICE_KEY, id);
		} catch (e) {
			console.warn("Failed to persist device id", e);
			return id;
		}
	}
	return id;
}

/**
 * @returns {{pos_profile: string, company: string, bound_at: string}|null}
 */
export function getTerminalBinding() {
	const binding = readJson(BINDING_KEY);
	if (!binding || !binding.pos_profile) return null;
	return binding;
}

export function getBoundProfileName() {
	return getTerminalBinding()?.pos_profile || null;
}

export function setTerminalBinding({ pos_profile, company, pin_login } = {}) {
	if (!pos_profile) return null;
	const previous = getTerminalBinding();
	const binding = {
		pos_profile,
		company: company || null,
		// Whether this branch uses PIN login. Kept on the binding so the lock
		// screen still appears when the profile cache is empty — previously the
		// POS rendered unlocked in that window.
		pin_login:
			pin_login === undefined
				? previous?.pos_profile === pos_profile
					? !!previous?.pin_login
					: false
				: !!pin_login,
		device_id: getDeviceId(),
		bound_at: new Date().toISOString(),
	};
	if (hasStorage()) {
		try {
			localStorage.setItem(BINDING_KEY, JSON.stringify(binding));
		} catch (e) {
			console.error("Failed to persist terminal binding", e);
		}
	}
	return binding;
}

export function clearTerminalBinding() {
	if (!hasStorage()) return;
	try {
		localStorage.removeItem(BINDING_KEY);
	} catch (e) {
		console.warn("Failed to clear terminal binding", e);
	}
}

/**
 * Keys that must survive every cache-clearing path.  Consumed by
 * utils/clearAllCaches.js and Navbar.vue's clearCache().
 */
export const PRESERVED_TERMINAL_KEYS = [BINDING_KEY, DEVICE_KEY];
