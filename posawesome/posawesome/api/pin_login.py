import hmac

import frappe
from frappe import _
from frappe.rate_limiter import rate_limit

# The PIN field itself is owned by the `ury` app (ury/fixtures/custom_field.json),
# not by posawesome.  Without that app installed no user will report has_pin.
PIN_FIELD = "ury_pos_pin"


@frappe.whitelist(allow_guest=True, methods=["GET"])
def get_session_csrf():
    """Return the current session's CSRF token (GET-safe, no CSRF required)."""
    return {
        "csrf_token": frappe.sessions.get_csrf_token(),
        "user": frappe.session.user,
    }


@frappe.whitelist(allow_guest=True, methods=["GET"])
def get_pos_branches():
    """Return the POS Profiles a terminal may be bound to.

    Feeds the branch chip on the PIN screen.  This is intentionally readable
    before login: without it a terminal stranded on the wrong branch has no way
    to discover the right one, which is the lockout this endpoint exists to
    prevent.  Only the profile name and company are exposed.
    """
    profiles = frappe.db.get_all(
        "POS Profile",
        filters={"disabled": 0},
        fields=["name", "company"],
        order_by="company asc, name asc",
    )
    return profiles


def _users_with_pin(user_names):
    """Return the subset of user_names that have a POS PIN set.

    Reads the presence of the stored secret directly instead of decrypting
    every user's PIN just to test it for truthiness.
    """
    if not user_names:
        return set()

    auth = frappe.qb.Table("__Auth")
    rows = (
        frappe.qb.from_(auth)
        .select(auth.name)
        .where(
            (auth.doctype == "User")
            & (auth.fieldname == PIN_FIELD)
            & (auth.encrypted == 1)
            & (auth.name.isin(list(user_names)))
        )
    ).run()

    return {row[0] for row in rows if row and row[0]}


def _profile_user_names(pos_profile):
    rows = frappe.db.get_all(
        "POS Profile User",
        filters={"parent": pos_profile, "parenttype": "POS Profile"},
        fields=["user"],
    )
    return [row.user for row in rows if row.user]


@frappe.whitelist(allow_guest=True, methods=["GET"])
def get_cashiers(pos_profile=None):
    """Return the users assigned to the given POS Profile.

    Scoped strictly to the profile's POS Profile User child table.  There is
    deliberately no role-based fallback: the previous one returned every
    ``Sales User`` on the site whenever a profile had no assigned users, which
    showed cashiers from other branches on the lock screen.  An empty list is
    the correct, actionable answer — the UI offers the branch picker instead.
    """
    if not pos_profile:
        frappe.throw(_("POS Profile is required"))

    user_list = _profile_user_names(pos_profile)
    if not user_list:
        return []

    cashiers = frappe.db.get_all(
        "User",
        filters={"name": ("in", user_list), "enabled": 1},
        fields=["name", "full_name", "user_image"],
        order_by="full_name asc",
    )

    with_pin = _users_with_pin([c.name for c in cashiers])
    for c in cashiers:
        c["has_pin"] = c.name in with_pin

    return cashiers


def _fail(message):
    """An expected, non-exceptional login refusal.

    Deliberately NOT frappe.throw(..., AuthenticationError): that maps to HTTP
    401 (frappe/exceptions.py), and frappe reacts to a 401 by clearing the sid
    cookie (frappe/app.py) while the browser pops a session-expired dialog and
    redirects to /login.  A mistyped PIN on a keypad is an everyday event, not
    an authentication incident — it must not log the terminal out.
    """
    return {"success": False, "error": message}


@frappe.whitelist(allow_guest=True)
@rate_limit(key="user", limit=10, seconds=60, ip_based=False)
def pin_login(user, pin, pos_profile=None):
    """Validate a user's PIN and start a session for them.

    Always responds 200; check ``success`` on the payload.

    ``pos_profile`` is required and enforced.  Without it any PIN authenticated
    at any terminal, so a cashier could sell under a branch they are not
    assigned to.
    """
    if not user or not pin:
        return _fail(_("User and PIN are required"))

    if not pos_profile:
        return _fail(_("POS Profile is required"))

    user_doc = frappe.db.get_value("User", user, ["name", "enabled", "full_name"], as_dict=True)
    if not user_doc:
        return _fail(_("Invalid user"))
    if not user_doc.enabled:
        return _fail(_("User is disabled"))

    if user not in _profile_user_names(pos_profile):
        return _fail(_("{0} is not assigned to {1}.").format(user_doc.full_name or user, pos_profile))

    stored_pin = frappe.utils.password.get_decrypted_password(
        "User", user, fieldname=PIN_FIELD, raise_exception=False
    )

    if not stored_pin:
        return _fail(_("PIN not set for this user. Please set a PIN in the User settings."))

    if not hmac.compare_digest(str(pin), str(stored_pin)):
        return _fail(_("Incorrect PIN"))

    previous_sid = frappe.session.sid
    previous_user = frappe.session.user

    frappe.local.login_manager.login_as(user)

    # Frappe re-sets the `sid` cookie on every response (auth.py: set_user_info
    # -> init_cookies).  A request that was already in flight when we switched
    # will therefore revert the browser's cookie to the old session.  Dropping
    # the old session makes that revert fail closed to Guest, which the client
    # detects, instead of silently leaving the till selling as the previous
    # cashier.
    if previous_sid and previous_sid != frappe.session.sid and previous_user != "Guest":
        try:
            frappe.sessions.delete_session(previous_sid, reason="POS PIN login switch")
        except Exception:
            frappe.log_error(title="POS PIN login: failed to drop previous session")

    csrf_token = frappe.sessions.get_csrf_token()
    frappe.db.commit()

    return {
        "success": True,
        "message": "Login successful",
        "user": user,
        "full_name": user_doc.full_name,
        "pos_profile": pos_profile,
        "sid": frappe.session.sid,
        "csrf_token": csrf_token,
    }


@frappe.whitelist()
def pin_logout():
    """End the current PIN session without leaving the POS page."""
    frappe.local.login_manager.logout()
    frappe.db.commit()
    return {"message": "Logged out"}
