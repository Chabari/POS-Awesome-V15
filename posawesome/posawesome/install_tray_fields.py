"""
Create custom fields for tray/seedling data persistence on Sales Invoice and Sales Invoice Item.
Run: bench --site <site> execute posawesome.posawesome.install_tray_fields.install
"""
import frappe
from frappe.custom.doctype.custom_field.custom_field import create_custom_fields


def install():
    """Create all tray-related custom fields."""
    custom_fields = {
        "Sales Invoice Item": [
            dict(
                fieldname="posa_tray_section",
                label="Tray Picking Data",
                fieldtype="Section Break",
                insert_after="posa_notes",
                collapsible=1,
            ),
            dict(
                fieldname="posa_propagation_batch",
                label="Propagation Batch",
                fieldtype="Link",
                options="Propagation Batch",
                insert_after="posa_tray_section",
                read_only=1,
                allow_on_submit=1,
            ),
            dict(
                fieldname="posa_picked_trays",
                label="Picked Trays",
                fieldtype="Small Text",
                insert_after="posa_propagation_batch",
                read_only=1,
                hidden=1,
                allow_on_submit=1,
            ),
            dict(
                fieldname="posa_tray_summary",
                label="Tray Summary",
                fieldtype="Small Text",
                insert_after="posa_picked_trays",
                read_only=1,
                hidden=1,
                allow_on_submit=1,
            ),
            dict(
                fieldname="posa_tray_col_break",
                fieldtype="Column Break",
                insert_after="posa_tray_summary",
            ),
            dict(
                fieldname="posa_total_whole_trays",
                label="Whole Trays",
                fieldtype="Int",
                insert_after="posa_tray_col_break",
                read_only=1,
                allow_on_submit=1,
            ),
            dict(
                fieldname="posa_total_loose_pieces",
                label="Loose Pieces",
                fieldtype="Int",
                insert_after="posa_total_whole_trays",
                read_only=1,
                allow_on_submit=1,
            ),
            dict(
                fieldname="posa_tray_deposit",
                label="Tray Deposit",
                fieldtype="Currency",
                insert_after="posa_total_loose_pieces",
                read_only=1,
                allow_on_submit=1,
            ),
        ],
        "POS Invoice Item": [
            dict(
                fieldname="posa_tray_section",
                label="Tray Picking Data",
                fieldtype="Section Break",
                insert_after="posa_notes",
                collapsible=1,
            ),
            dict(
                fieldname="posa_propagation_batch",
                label="Propagation Batch",
                fieldtype="Link",
                options="Propagation Batch",
                insert_after="posa_tray_section",
                read_only=1,
                allow_on_submit=1,
            ),
            dict(
                fieldname="posa_picked_trays",
                label="Picked Trays",
                fieldtype="Small Text",
                insert_after="posa_propagation_batch",
                read_only=1,
                hidden=1,
                allow_on_submit=1,
            ),
            dict(
                fieldname="posa_tray_summary",
                label="Tray Summary",
                fieldtype="Small Text",
                insert_after="posa_picked_trays",
                read_only=1,
                hidden=1,
                allow_on_submit=1,
            ),
            dict(
                fieldname="posa_tray_col_break",
                fieldtype="Column Break",
                insert_after="posa_tray_summary",
            ),
            dict(
                fieldname="posa_total_whole_trays",
                label="Whole Trays",
                fieldtype="Int",
                insert_after="posa_tray_col_break",
                read_only=1,
                allow_on_submit=1,
            ),
            dict(
                fieldname="posa_total_loose_pieces",
                label="Loose Pieces",
                fieldtype="Int",
                insert_after="posa_total_whole_trays",
                read_only=1,
                allow_on_submit=1,
            ),
            dict(
                fieldname="posa_tray_deposit",
                label="Tray Deposit",
                fieldtype="Currency",
                insert_after="posa_total_loose_pieces",
                read_only=1,
                allow_on_submit=1,
            ),
        ],
        "Sales Invoice": [
            dict(
                fieldname="posa_tray_deposit_section",
                label="Tray Deposit",
                fieldtype="Section Break",
                insert_after="posa_delivery_charges_rate",
                collapsible=1,
            ),
            dict(
                fieldname="posa_tray_deposit_received",
                label="Tray Deposit Received",
                fieldtype="Currency",
                insert_after="posa_tray_deposit_section",
                read_only=1,
                allow_on_submit=1,
            ),
            dict(
                fieldname="posa_tray_deposit_calculated",
                label="Tray Deposit Calculated",
                fieldtype="Currency",
                insert_after="posa_tray_deposit_received",
                read_only=1,
                allow_on_submit=1,
            ),
            dict(
                fieldname="posa_tray_col_break",
                fieldtype="Column Break",
                insert_after="posa_tray_deposit_calculated",
            ),
            dict(
                fieldname="posa_tray_deposit_summary",
                label="Tray Deposit Summary",
                fieldtype="Small Text",
                insert_after="posa_tray_col_break",
                read_only=1,
                hidden=1,
                allow_on_submit=1,
            ),
            dict(
                fieldname="posa_tray_deposit_payment_entry",
                label="Tray Deposit Payment Entry",
                fieldtype="Link",
                options="Payment Entry",
                insert_after="posa_tray_deposit_summary",
                read_only=1,
                allow_on_submit=1,
            ),
        ],
        "POS Invoice": [
            dict(
                fieldname="posa_tray_deposit_section",
                label="Tray Deposit",
                fieldtype="Section Break",
                insert_after="posa_delivery_charges_rate",
                collapsible=1,
            ),
            dict(
                fieldname="posa_tray_deposit_received",
                label="Tray Deposit Received",
                fieldtype="Currency",
                insert_after="posa_tray_deposit_section",
                read_only=1,
                allow_on_submit=1,
            ),
            dict(
                fieldname="posa_tray_deposit_calculated",
                label="Tray Deposit Calculated",
                fieldtype="Currency",
                insert_after="posa_tray_deposit_received",
                read_only=1,
                allow_on_submit=1,
            ),
            dict(
                fieldname="posa_tray_col_break",
                fieldtype="Column Break",
                insert_after="posa_tray_deposit_calculated",
            ),
            dict(
                fieldname="posa_tray_deposit_summary",
                label="Tray Deposit Summary",
                fieldtype="Small Text",
                insert_after="posa_tray_col_break",
                read_only=1,
                hidden=1,
                allow_on_submit=1,
            ),
            dict(
                fieldname="posa_tray_deposit_payment_entry",
                label="Tray Deposit Payment Entry",
                fieldtype="Link",
                options="Payment Entry",
                insert_after="posa_tray_deposit_summary",
                read_only=1,
                allow_on_submit=1,
            ),
        ],
    }

    create_custom_fields(custom_fields, update=True)
    frappe.db.commit()
    print("Tray custom fields created successfully.")
