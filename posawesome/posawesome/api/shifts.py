# -*- coding: utf-8 -*-
# Copyright (c) 2020, Youssef Restom and contributors
# For license information, please see license.txt

from __future__ import unicode_literals
import json
import frappe
from frappe.utils import cint, nowdate, getdate, get_datetime
from frappe import _
from posawesome.utils import has_field

from .utilities import get_version


def _has_field(doctype, fieldname):
    return has_field(doctype, fieldname)


def _to_dict(value):
    if isinstance(value, dict):
        return value
    if hasattr(value, "as_dict"):
        return value.as_dict()
    return {}


def _extract_current_reading(row):
    row_dict = _to_dict(row)
    for key in (
        "opening_reading",
        "current_reading",
        "custom_current_reading",
        "reading",
        "custom_reading",
    ):
        if key in row_dict:
            return row_dict.get(key)
    return 0


def _build_nozzle_payload(rows):
    payload = []
    for row in rows or []:
        row_dict = _to_dict(row)
        payload.append(
            {
                "nozzle_name": row_dict.get("nozzle_name")
                or row_dict.get("custom_nozzle_name")
                or row_dict.get("nozzle")
                or "",
                "fuel_item": row_dict.get("fuel_item") or row_dict.get("custom_fuel_item") or "",
                "fuel_pump": row_dict.get("fuel_pump") or row_dict.get("custom_fuel_pump") or "",
                "warehouse": row_dict.get("warehouse") or row_dict.get("custom_warehouse") or "",
                "current_reading": _extract_current_reading(row_dict),
            }
        )
    return payload


def _prepare_opening_nozzle_rows(nozzle_readings, profile_nozzles=None):
    """Build opening nozzle rows.

    Readings are taken from the POS Profile, never from the browser: the opening
    reading is the previous shift's closing reading and must not be editable at
    the till. Fuel App re-asserts the same values in its POS Opening Shift
    `before_submit` hook, so this is defence in depth rather than the only gate.
    """
    profile_nozzle_map = {}
    for row in profile_nozzles or []:
        row_dict = _to_dict(row)
        key = (
            (row_dict.get("nozzle") or row_dict.get("nozzle_name") or row_dict.get("custom_nozzle_name") or "").strip(),
            (row_dict.get("fuel_item") or row_dict.get("custom_fuel_item") or "").strip(),
            (row_dict.get("fuel_pump") or row_dict.get("custom_fuel_pump") or "").strip(),
        )
        profile_nozzle_map[key] = {
            "warehouse": row_dict.get("warehouse") or row_dict.get("custom_warehouse") or "",
            "current_reading": _extract_current_reading(row_dict),
        }

    rows = []
    for row in nozzle_readings or []:
        row_dict = _to_dict(row)
        key = (
            (row_dict.get("nozzle") or row_dict.get("nozzle_name") or "").strip(),
            (row_dict.get("fuel_item") or "").strip(),
            (row_dict.get("fuel_pump") or "").strip(),
        )
        profile_row = profile_nozzle_map.get(key) or {}
        rows.append(
            {
                "nozzle": row_dict.get("nozzle") or row_dict.get("nozzle_name") or "",
                "fuel_item": row_dict.get("fuel_item") or "",
                "fuel_pump": row_dict.get("fuel_pump") or "",
                "warehouse": row_dict.get("warehouse") or profile_row.get("warehouse") or "",
                "opening_reading": profile_row.get("current_reading") or 0,
            }
        )
    return rows


@frappe.whitelist()
def get_opening_dialog_data():
    data = {}

    # Get only POS Profiles where current user is defined in POS Profile User table
    pos_profiles_data = frappe.db.sql(
        """
        SELECT DISTINCT p.name, p.company, p.currency 
        FROM `tabPOS Profile` p
        INNER JOIN `tabPOS Profile User` u ON u.parent = p.name
        WHERE p.disabled = 0 AND u.user = %s
        ORDER BY p.name
    """,
        frappe.session.user,
        as_dict=1,
    )

    data["pos_profiles_data"] = pos_profiles_data

    include_fuel_customization = _has_field("POS Profile", "custom_enable_fuel_customization")
    include_nozzle_table_on_profile = _has_field("POS Profile", "custom_pump_nozzles")
    data["fuel_customization"] = {}

    if include_fuel_customization and include_nozzle_table_on_profile:
        for profile in pos_profiles_data:

            profile_name = profile.name
            pos_profile_doc = frappe.get_doc("POS Profile", profile_name)
            enabled = cint(pos_profile_doc.get("custom_enable_fuel_customization") or 0) == 1
            data["fuel_customization"][profile_name] = {
                "enabled": enabled,
                # Opening readings carry forward from the previous close and are
                # displayed, not captured.
                "readings_readonly": enabled,
                "nozzle_readings": _build_nozzle_payload(pos_profile_doc.get("custom_pump_nozzles") or []),
            }
           
    # Derive companies from accessible POS Profiles
    company_names = []
    for profile in pos_profiles_data:
        if profile.company and profile.company not in company_names:
            company_names.append(profile.company)
    data["companies"] = [{"name": c} for c in company_names]

    pos_profiles_list = []
    for i in data["pos_profiles_data"]:
        pos_profiles_list.append(i.name)

    payment_method_table = "POS Payment Method" if get_version() == 13 else "Sales Invoice Payment"
    data["payments_method"] = frappe.get_list(
        payment_method_table,
        filters={"parent": ["in", pos_profiles_list]},
        fields=["*"],
        limit_page_length=0,
        order_by="parent",
        ignore_permissions=True,
    )
    # set currency from pos profile
    for mode in data["payments_method"]:
        mode["currency"] = frappe.get_cached_value("POS Profile", mode["parent"], "currency")

    return data


@frappe.whitelist()
def create_opening_voucher(pos_profile, company, balance_details, nozzle_readings=None):
    balance_details = json.loads(balance_details)
    parsed_nozzle_readings = []
    if nozzle_readings:
        parsed_nozzle_readings = json.loads(nozzle_readings)

    new_pos_opening = frappe.get_doc(
        {
            "doctype": "POS Opening Shift",
            "period_start_date": frappe.utils.get_datetime(),
            "posting_date": frappe.utils.getdate(),
            "user": frappe.session.user,
            "pos_profile": pos_profile,
            "company": company,
            "docstatus": 1,
        }
    )
    new_pos_opening.set("balance_details", balance_details)

    can_save_nozzles_in_opening = _has_field("POS Opening Shift", "custom_nozzle_readings")
    if can_save_nozzles_in_opening and parsed_nozzle_readings:
        pos_profile_doc = frappe.get_doc("POS Profile", pos_profile)
        new_pos_opening.set(
            "custom_nozzle_readings",
            _prepare_opening_nozzle_rows(
                parsed_nozzle_readings,
                pos_profile_doc.get("custom_pump_nozzles") or [],
            ),
        )

    new_pos_opening.insert(ignore_permissions=True)

    # The POS Profile nozzle `current_reading` is only ever advanced by a shift
    # close or by a Nozzle Meter Correction, never by what the till posted here.

    data = {}
    data["pos_opening_shift"] = new_pos_opening.as_dict()
    update_opening_shift_data(data, new_pos_opening.pos_profile)
    return data


@frappe.whitelist()
def check_opening_shift(user):
    open_vouchers = frappe.db.get_all(
        "POS Opening Shift",
        filters={
            "user": user,
            "pos_closing_shift": ["is", "not set"],
            "docstatus": 1,
            "status": "Open",
        },
        fields=["name", "pos_profile", "period_start_date"],
        order_by="period_start_date desc",
    )

    # If no open shift for this user, check for an open shift on the user's default POS Profile
    if not open_vouchers:
        default_profiles = frappe.get_all(
            "POS Profile User",
            filters={"user": user, "default": 1, "parenttype": "POS Profile"},
            fields=["parent"],
        )
        if default_profiles:
            default_profile_names = [d.parent for d in default_profiles]
            open_vouchers = frappe.db.get_all(
                "POS Opening Shift",
                filters={
                    "pos_profile": ["in", default_profile_names],
                    "pos_closing_shift": ["is", "not set"],
                    "docstatus": 1,
                    "status": "Open",
                },
                fields=["name", "pos_profile", "period_start_date"],
                order_by="period_start_date desc",
            )

    data = ""
    if len(open_vouchers) > 0:
        data = {}
        data["pos_opening_shift"] = frappe.get_doc("POS Opening Shift", open_vouchers[0]["name"])
        update_opening_shift_data(data, open_vouchers[0]["pos_profile"])

        # Check if shift must be closed daily
        pos_profile_doc = data["pos_profile"]
        if pos_profile_doc.get("custom_close_shift_daily"):
            shift_date = getdate(open_vouchers[0]["period_start_date"])
            today = getdate(nowdate())
            if shift_date < today:
                data["requires_closing"] = True
    return data


def update_opening_shift_data(data, pos_profile):
    data["pos_profile"] = frappe.get_doc("POS Profile", pos_profile)
    if data["pos_profile"].get("posa_language"):
        frappe.local.lang = data["pos_profile"].posa_language
    data["company"] = frappe.get_doc("Company", data["pos_profile"].company)
    allow_negative_stock = cint(frappe.db.get_single_value("Stock Settings", "allow_negative_stock") or 0)
    data["stock_settings"] = {}
    data["stock_settings"].update({"allow_negative_stock": bool(allow_negative_stock)})
