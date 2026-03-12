import frappe
from frappe import _


@frappe.whitelist(allow_guest=True, methods=["GET"])
def get_session_csrf():
    """Return the current session's CSRF token (GET-safe, no CSRF required)."""
    return {
        "csrf_token": frappe.sessions.get_csrf_token(),
        "user": frappe.session.user,
    }


@frappe.whitelist(allow_guest=True)
def get_cashiers(pos_profile=None):
    """Return list of users allowed for the given POS Profile."""
    if not pos_profile:
        frappe.throw(_("POS Profile is required"))

    # Get users assigned to this POS profile via POS Profile User child table
    profile_users = frappe.db.get_all(
        "POS Profile User",
        filters={"parent": pos_profile, "parenttype": "POS Profile"},
        fields=["user"],
    )

    if not profile_users:
        # Fallback: get users with POS-related roles
        roles = ("Sales User", "Accounts User", "Sales Manager")
        cashiers = frappe.db.sql(
            """
            SELECT DISTINCT u.name, u.full_name, u.user_image
            FROM `tabUser` u
            INNER JOIN `tabHas Role` hr ON hr.parent = u.name
            WHERE hr.role IN %(roles)s
            AND u.enabled = 1
            AND u.name NOT IN ('Administrator', 'Guest')
            ORDER BY u.full_name ASC
            """,
            {"roles": roles},
            as_dict=True,
        )
    else:
        user_list = [u.user for u in profile_users]
        cashiers = frappe.db.get_all(
            "User",
            filters={"name": ("in", user_list), "enabled": 1},
            fields=["name", "full_name", "user_image"],
            order_by="full_name asc",
        )

    # Check which users have a PIN set
    result = []
    for c in cashiers:
        has_pin = frappe.utils.password.get_decrypted_password(
            "User", c.name, fieldname="ury_pos_pin", raise_exception=False
        )
        c["has_pin"] = bool(has_pin)
        result.append(c)

    return result


@frappe.whitelist(allow_guest=True)
def pin_login(user, pin):
    """Validate user PIN and create a session."""
    if not user or not pin:
        frappe.throw(_("User and PIN are required"), frappe.AuthenticationError)

    user_doc = frappe.db.get_value(
        "User", user, ["name", "enabled", "full_name"], as_dict=True
    )
    if not user_doc:
        frappe.throw(_("Invalid user"), frappe.AuthenticationError)
    if not user_doc.enabled:
        frappe.throw(_("User is disabled"), frappe.AuthenticationError)

    stored_pin = frappe.utils.password.get_decrypted_password(
        "User", user, fieldname="ury_pos_pin", raise_exception=False
    )

    if not stored_pin:
        frappe.throw(
            _("PIN not set for this user. Please set a PIN in the User settings."),
            frappe.AuthenticationError,
        )

    if str(pin) != str(stored_pin):
        frappe.throw(_("Incorrect PIN"), frappe.AuthenticationError)

    frappe.local.login_manager.login_as(user)
    frappe.db.commit()

    csrf_token = frappe.sessions.get_csrf_token()
    frappe.db.commit()

    return {
        "message": "Login successful",
        "user": user,
        "full_name": user_doc.full_name,
        "csrf_token": csrf_token,
    }
