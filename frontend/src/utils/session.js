/* global frappe */
/**
 * Session gate + CSRF self-heal.
 *
 * "Invalid Request" is Frappe's CSRF failure (frappe/auth.py: validate_csrf_token
 * throws it when X-Frappe-CSRF-Token does not match the session's saved token).
 * Three things in this app produce a mismatch:
 *
 *   (a) The service worker can replay a cached /app/posapp shell carrying a
 *       csrf_token from a long-dead session.  Handled in posawesome/www/sw.js
 *       plus `ensureFreshCsrf()` below.
 *
 *   (b) Frappe re-sets the `sid` cookie on *every* response (auth.py:
 *       LoginManager -> set_user_info(resume=True) -> init_cookies()).  A request
 *       already in flight when pin_login() calls login_as() resumes the OLD
 *       session, and its response reverts the browser cookie to the old sid
 *       while frappe.csrf_token now holds the new token.
 *
 *   (c) frappe/app.py registers `session.update` in after_response on every
 *       request, and Session.update rewrites the WHOLE session blob to db+redis
 *       (last writer wins).  A concurrent request holding a pre-CSRF snapshot
 *       silently overwrites a freshly minted csrf_token.
 *
 * (b) and (c) are both races against in-flight traffic, so the fix for both is
 * the same: quiesce the app before switching sessions and do not let anything
 * out again until the new session is verified.  That is what `withSessionSwitch`
 * does.  Aborting is not enough — the server has already committed those
 * responses — so we wait for them to drain instead.
 */

const CSRF_ENDPOINT = "/api/method/posawesome.posawesome.api.pin_login.get_session_csrf";
const CHANNEL_NAME = "posa_session";

// Methods that are safe to replay automatically after a token refresh.
// Anything that creates or submits a document is deliberately absent: a blind
// replay there risks a duplicate sale.
const REPLAY_SAFE = /(^|\.)(get|search|check|fetch|load|validate)[_.]|\.get_value$|\.get_list$|\.get$/i;

// Hard ceiling on a session switch. A POS terminal must never sit on a spinner
// with no way forward, so any hang surfaces as a retryable error instead.
const SWITCH_TIMEOUT_MS = 15000;

let gateClosed = false;
let queued = [];
let refreshInFlight = null;
let channel = null;

function getChannel() {
	if (channel !== null) return channel;
	try {
		channel = typeof BroadcastChannel !== "undefined" ? new BroadcastChannel(CHANNEL_NAME) : false;
	} catch {
		channel = false;
	}
	return channel;
}

function applyToken(token, user) {
	if (typeof frappe === "undefined") return;
	if (token) frappe.csrf_token = token;
	if (user && frappe.session) frappe.session.user = user;
}

/**
 * Fetch the live CSRF token for whatever session the sid cookie currently
 * points at.  GET, so it is never itself CSRF-validated.
 *
 * Concurrent callers share one request — without this, the burst of refreshes
 * that follows a failure would itself race (c).
 *
 * @param {boolean} broadcast tell sibling tabs about the new token
 * @returns {Promise<{csrf_token: string, user: string}|null>}
 */
export function refreshCsrfToken({ broadcast = true } = {}) {
	if (refreshInFlight) return refreshInFlight;

	refreshInFlight = (async () => {
		try {
			const resp = await fetch(CSRF_ENDPOINT, {
				method: "GET",
				credentials: "same-origin",
				cache: "no-store",
			});
			if (!resp.ok) return null;
			const data = await resp.json();
			const message = data?.message;
			if (!message) return null;

			applyToken(message.csrf_token, message.user);

			if (broadcast) {
				const ch = getChannel();
				if (ch) {
					try {
						ch.postMessage({ type: "csrf", ...message });
					} catch (e) {
						console.warn("Failed to broadcast session token", e);
					}
				}
			}
			return message;
		} catch (e) {
			console.warn("Failed to refresh CSRF token", e);
			return null;
		} finally {
			refreshInFlight = null;
		}
	})();

	return refreshInFlight;
}

/**
 * Guarantee we hold a usable token before any write goes out.  Called at boot,
 * where the page may have come from the service worker's cached shell with the
 * token deliberately blanked out.
 */
export async function ensureFreshCsrf() {
	if (typeof frappe === "undefined") return null;
	if (frappe.csrf_token && frappe.csrf_token !== "None") return null;
	return refreshCsrfToken();
}

/**
 * Call a whitelisted GET endpoint without going through frappe.call.
 *
 * GET requests are never CSRF-validated (frappe/auth.py checks unsafe methods
 * only), so anything on the lock screen's recovery path uses this: the branch
 * picker and the cashier list must keep working even when the token is stale,
 * since they are what a stranded terminal uses to get unstuck.
 *
 * @param {string} method dotted path to the whitelisted method
 * @param {object} params query parameters
 */
export async function apiGet(method, params = {}) {
	const url = new URL(`/api/method/${method}`, window.location.origin);
	Object.entries(params).forEach(([k, v]) => {
		if (v !== undefined && v !== null) url.searchParams.set(k, v);
	});

	const resp = await fetch(url.toString(), {
		method: "GET",
		credentials: "same-origin",
		cache: "no-store",
		headers: { Accept: "application/json" },
	});

	if (!resp.ok) {
		throw new Error(`${method} failed with ${resp.status}`);
	}
	const data = await resp.json();
	return data?.message;
}

export function isInvalidRequestError(err) {
	if (!err) return false;
	const candidates = [err.message, err._server_messages, err.responseText, err.exc_type];
	for (const raw of candidates) {
		if (typeof raw !== "string" || !raw) continue;
		if (/invalid request|csrftokenerror/i.test(raw)) return true;
		try {
			const parsed = JSON.parse(raw);
			const values = Array.isArray(parsed) ? parsed : [parsed];
			if (values.some((v) => /invalid request|csrftokenerror/i.test(String(v)))) return true;
		} catch {
			/* not JSON, already checked as a string */
		}
	}
	return false;
}

function methodOf(callArgs) {
	const first = callArgs[0];
	if (typeof first === "string") return first;
	return first?.method || "";
}

/** Wait until Frappe reports no outstanding XHRs, or until we time out. */
async function waitForQuiescence(timeoutMs = 4000) {
	const started = Date.now();
	while (Date.now() - started < timeoutMs) {
		const inFlight = frappe?.request?.ajax_count || 0;
		if (!inFlight) return true;
		await new Promise((r) => setTimeout(r, 50));
	}
	return false;
}

let originalCall = null;

/**
 * Install the gate.  While open, a request goes straight out and comes back as
 * the CSRF-heal chain with the jqXHR control surface (.abort(), .status, ...)
 * re-exposed on top of it — Frappe core cancels in-flight calls through
 * .abort(), so that surface has to survive the wrapping.  Only while the gate
 * is closed do requests queue instead of being sent.
 */
export function installSessionGate() {
	if (typeof frappe === "undefined" || originalCall) return;
	originalCall = frappe.call;

	frappe.call = function posaGatedCall(...callArgs) {
		if (!gateClosed) {
			return withCsrfHeal(this, callArgs);
		}
		// No XHR exists yet, so there is nothing to adopt an .abort() from — but
		// callers still expect one (query_report.js aborts the previous run before
		// starting the next). Cancelling here just drops the entry from the queue.
		let entry;
		const pending = new Promise((resolve, reject) => {
			entry = { ctx: this, callArgs, resolve, reject };
			queued.push(entry);
		});
		pending.abort = () => {
			const i = queued.indexOf(entry);
			if (i !== -1) queued.splice(i, 1);
			entry.reject({ statusText: "abort", readyState: 0 });
			return pending;
		};
		return pending;
	};

	const resync = () => {
		if (document.visibilityState === "visible") refreshCsrfToken({ broadcast: false });
	};
	window.addEventListener("pageshow", (e) => {
		// Restored from the back/forward cache: frappe.csrf_token is whatever it
		// was when the page was frozen, which may be several sessions stale.
		if (e.persisted) refreshCsrfToken({ broadcast: false });
	});
	document.addEventListener("visibilitychange", resync);

	const ch = getChannel();
	if (ch) {
		ch.onmessage = (event) => {
			const data = event?.data;
			if (data?.type === "csrf") applyToken(data.csrf_token, data.user);
		};
	}
}

// jQuery 3's .then()/.catch() hand back a FRESH Deferred promise: it carries
// done/fail/always but none of the jqXHR control surface. Frappe cancels the
// previous in-flight request with `this.last_ajax.abort()` (see
// frappe/public/js/frappe/views/reports/query_report.js), so a healed promise
// without .abort() leaves every report stuck on its loading screen.
const XHR_METHODS = [
	"abort",
	"getResponseHeader",
	"getAllResponseHeaders",
	"setRequestHeader",
	"overrideMimeType",
	"statusCode",
];
const XHR_PROPS = ["readyState", "status", "statusText", "responseText", "responseJSON"];

function adoptXhrSurface(target, xhr) {
	for (const name of XHR_METHODS) {
		if (typeof xhr[name] !== "function") continue;
		target[name] = (...args) => {
			const out = xhr[name](...args);
			// jqXHR returns itself for chaining; keep the chain on the wrapper.
			return out === xhr ? target : out;
		};
	}
	for (const name of XHR_PROPS) {
		if (!(name in xhr)) continue;
		// Live getters — these mutate as the request progresses.
		Object.defineProperty(target, name, { configurable: true, get: () => xhr[name] });
	}
	return target;
}

/**
 * Delegate to the real frappe.call, and on a CSRF failure repair the token.
 *
 * The token refresh always happens, so whatever the user does next works.  The
 * request itself is only replayed when it is safe to repeat — a read, or an
 * explicit opt-in via `posa_replay_on_csrf` — because replaying a submit could
 * ring up the same sale twice.
 */
function withCsrfHeal(ctx, callArgs) {
	const result = originalCall.apply(ctx, callArgs);
	if (!result || typeof result.then !== "function") return result;

	const healed = result.catch(async (err) => {
		if (!isInvalidRequestError(err)) throw err;

		await refreshCsrfToken();

		const opts = typeof callArgs[0] === "object" ? callArgs[0] : null;
		const optedIn = opts?.posa_replay_on_csrf === true;
		if (!optedIn && !REPLAY_SAFE.test(methodOf(callArgs))) {
			throw err;
		}
		return originalCall.apply(ctx, callArgs);
	});

	if (typeof result.abort === "function") adoptXhrSurface(healed, result);
	return healed;
}

/**
 * Issue a frappe.call that bypasses the gate.
 *
 * The switch operation itself must never be queued — it is the thing the queue
 * is waiting on.  `withSessionSwitch` hands this to its callback so the correct
 * usage is the obvious one.
 */
function callUngated(callArgs) {
	const call = originalCall || (typeof frappe !== "undefined" ? frappe.call : null);
	if (!call) {
		return Promise.reject(new Error("frappe.call is unavailable"));
	}
	return call.apply(typeof frappe !== "undefined" ? frappe : null, callArgs);
}

function withTimeout(promise, ms, code) {
	let timer;
	const timeout = new Promise((_, reject) => {
		timer = setTimeout(() => {
			const err = new Error(code);
			err.posaTimeout = true;
			reject(err);
		}, ms);
	});
	return Promise.race([promise, timeout]).finally(() => clearTimeout(timer));
}

/** Read the live session identity, bypassing the refresh dedupe. */
async function readSession() {
	try {
		const message = await apiGet("posawesome.posawesome.api.pin_login.get_session_csrf");
		if (message) applyToken(message.csrf_token, message.user);
		return message || null;
	} catch (e) {
		console.warn("Failed to read session", e);
		return null;
	}
}

function drainQueue() {
	const pending = queued;
	queued = [];
	for (const item of pending) {
		try {
			Promise.resolve(withCsrfHeal(item.ctx, item.callArgs)).then(item.resolve, item.reject);
		} catch (e) {
			item.reject(e);
		}
	}
}

export function isSessionPaused() {
	return gateClosed;
}

/**
 * Run a session-changing operation with the app quiesced.
 *
 *   close gate -> wait for in-flight requests to drain -> run fn
 *   -> poll until the server agrees we are the expected user -> reopen
 *
 * The verification step is what turns a silent wrong-user session into a
 * detectable failure.  Without it, a reverted sid cookie (cause (b) above)
 * leaves the till selling as the previous cashier.
 *
 * @param {() => Promise<any>} fn the switch itself, e.g. the pin_login call
 * @param {{expectUser?: string, attempts?: number}} options
 */
/**
 * Run a session-changing operation with the app quiesced.
 *
 *   close gate -> let in-flight requests drain -> refresh token -> run fn
 *   -> confirm the server agrees who we are -> reopen and replay
 *
 * `fn` receives an **ungated** call function and must use it. Reaching for
 * frappe.call directly would queue the request behind the very gate that is
 * waiting for it to finish, and the login would hang forever.
 *
 * @param {(call: Function) => Promise<any>} fn
 * @param {{expectUser?: string|((result:any)=>string|null), attempts?: number}} options
 *        expectUser may be a function of the result, so a soft failure (say a
 *        wrong PIN, which deliberately returns 200) skips verification.
 */
export async function withSessionSwitch(fn, { expectUser = null, attempts = 3 } = {}) {
	gateClosed = true;
	try {
		await waitForQuiescence();

		// Mint/confirm the token now that nothing else is in flight. Doing it
		// here is what stops a concurrent request's after_response session write
		// from clobbering it between the refresh and the call.
		await refreshCsrfToken({ broadcast: false });

		const result = await withTimeout(
			Promise.resolve(fn((...callArgs) => callUngated(callArgs))),
			SWITCH_TIMEOUT_MS,
			"SESSION_SWITCH_TIMEOUT",
		);

		const wanted = typeof expectUser === "function" ? expectUser(result) : expectUser;

		if (wanted) {
			let verified = false;
			for (let i = 0; i < attempts && !verified; i++) {
				const info = await readSession();
				if (info?.user === wanted) {
					verified = true;
					break;
				}
				await new Promise((r) => setTimeout(r, 150 * (i + 1)));
			}

			if (!verified) {
				const err = new Error("SESSION_NOT_VERIFIED");
				err.posaSessionUnverified = true;
				throw err;
			}
		}

		return result;
	} finally {
		gateClosed = false;
		drainQueue();
	}
}
