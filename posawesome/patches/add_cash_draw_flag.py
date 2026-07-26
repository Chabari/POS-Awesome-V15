import frappe
from frappe.custom.doctype.custom_field.custom_field import create_custom_field


def execute():
    if frappe.db.exists("Custom Field", "POS Profile-posa_enable_cash_draw"):
        return

    create_custom_field(
        "POS Profile",
        {
            "fieldname": "posa_enable_cash_draw",
            "label": "Enable Cash Draw",
            "fieldtype": "Check",
            "insert_after": "posa_cash_mode_of_payment",
            "default": "0",
            "description": "Allow cash expenses to be recorded against an open POS shift.",
            "module": "POSAwesome",
        },
    )
