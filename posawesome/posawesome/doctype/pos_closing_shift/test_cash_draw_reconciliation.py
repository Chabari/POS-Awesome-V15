from unittest.mock import patch

import frappe
from frappe.tests import UnitTestCase

from posawesome.posawesome.doctype.pos_cash_draw.pos_cash_draw import get_cash_draw_totals
from posawesome.posawesome.doctype.pos_closing_shift.pos_closing_shift import (
    _apply_cash_draw_reconciliation,
    _apply_fuel_payment_reconciliation,
    _create_cash_draw_journal_entry,
)


class FakeClosingShift:
    def __init__(self, rows):
        self.payment_reconciliation = [frappe._dict(row) for row in rows]
        self.total_cash_drawn = 0

    def get(self, fieldname):
        return getattr(self, fieldname, None)

    def append(self, fieldname, values):
        row = frappe._dict(values)
        getattr(self, fieldname).append(row)
        return row


class FakeJournalEntry:
    def __init__(self):
        self.name = "JV-CASH-DRAW"
        self.flags = frappe._dict()
        self.accounts = []
        self.inserted = False
        self.submitted = False

    def append(self, fieldname, values):
        self.accounts.append(frappe._dict(values))

    def insert(self):
        self.inserted = True

    def submit(self):
        self.submitted = True


class TestCashDrawReconciliation(UnitTestCase):
    def test_totals_are_grouped_by_mode(self):
        totals = get_cash_draw_totals(
            [
                frappe._dict(mode_of_payment="Cash", amount=20),
                frappe._dict(mode_of_payment="Cash", amount=30),
                frappe._dict(mode_of_payment="Card", amount=10),
            ]
        )

        self.assertEqual(totals["Cash"], 50)
        self.assertEqual(totals["Card"], 10)

    def test_draws_reduce_expected_amount_by_mode(self):
        closing_shift = FakeClosingShift(
            [
                {
                    "mode_of_payment": "Cash",
                    "opening_amount": 100,
                    "expected_amount": 500,
                    "cash_drawn": 0,
                }
            ]
        )
        cash_draws = [
            frappe._dict(mode_of_payment="Cash", amount=50),
            frappe._dict(mode_of_payment="Card", amount=20),
        ]

        _apply_cash_draw_reconciliation(closing_shift, cash_draws)

        rows = {row.mode_of_payment: row for row in closing_shift.payment_reconciliation}
        self.assertEqual(rows["Cash"].cash_drawn, 50)
        self.assertEqual(rows["Cash"].expected_amount, 450)
        self.assertEqual(rows["Card"].cash_drawn, 20)
        self.assertEqual(rows["Card"].expected_amount, -20)
        self.assertEqual(closing_shift.total_cash_drawn, 70)

    def test_server_refresh_does_not_double_deduct(self):
        closing_shift = FakeClosingShift(
            [
                {
                    "mode_of_payment": "Cash",
                    "opening_amount": 0,
                    "expected_amount": 450,
                    "cash_drawn": 50,
                }
            ]
        )

        _apply_cash_draw_reconciliation(
            closing_shift,
            [frappe._dict(mode_of_payment="Cash", amount=60)],
            expected_includes_existing_draw=True,
        )

        row = closing_shift.payment_reconciliation[0]
        self.assertEqual(row.cash_drawn, 60)
        self.assertEqual(row.expected_amount, 440)

    def test_overdraw_is_rejected_at_closing(self):
        closing_shift = FakeClosingShift(
            [{"mode_of_payment": "Cash", "expected_amount": 10, "cash_drawn": 0}]
        )

        with self.assertRaises(frappe.ValidationError):
            _apply_cash_draw_reconciliation(
                closing_shift,
                [frappe._dict(mode_of_payment="Cash", amount=20)],
                validate_available=True,
            )

    def test_fuel_expected_amount_keeps_cash_draw_deduction(self):
        closing_shift = FakeClosingShift(
            [{"mode_of_payment": "Cash", "expected_amount": 0, "cash_drawn": 30}]
        )
        profile = frappe._dict(posa_cash_mode_of_payment="Cash")

        _apply_fuel_payment_reconciliation(closing_shift, profile, 200, credit_total=50)

        self.assertEqual(closing_shift.payment_reconciliation[0].expected_amount, 120)

    def test_journal_entry_groups_accounts_and_links_draws(self):
        module = "posawesome.posawesome.doctype.pos_closing_shift.pos_closing_shift"
        closing_shift = frappe._dict(
            name="POSA-CS-TEST",
            pos_opening_shift="POSA-OS-TEST",
            pos_profile="Test POS Profile",
            company="Test Company",
            posting_date="2026-07-26",
            cash_draw_journal_entry=None,
        )
        cash_draws = [
            frappe._dict(
                name="POSA-CD-1",
                amount=20,
                expense_account="Meals - TC",
                payment_account="Cash - TC",
            ),
            frappe._dict(
                name="POSA-CD-2",
                amount=30,
                expense_account="Meals - TC",
                payment_account="Mobile Money - TC",
            ),
        ]
        journal_entry = FakeJournalEntry()

        with (
            patch(f"{module}.get_unposted_cash_draws", return_value=cash_draws),
            patch(f"{module}.frappe.new_doc", return_value=journal_entry),
            patch(f"{module}.frappe.get_cached_doc", return_value=frappe._dict(cost_center="Main - TC")),
            patch(f"{module}.frappe.db.set_value") as set_value,
        ):
            result = _create_cash_draw_journal_entry(closing_shift)

        self.assertIs(result, journal_entry)
        self.assertTrue(journal_entry.inserted)
        self.assertTrue(journal_entry.submitted)
        self.assertEqual(journal_entry.voucher_type, "Journal Entry")
        self.assertEqual(journal_entry.accounts[0].account, "Meals - TC")
        self.assertEqual(journal_entry.accounts[0].debit_in_account_currency, 50)
        self.assertEqual(journal_entry.accounts[0].cost_center, "Main - TC")
        self.assertEqual(
            {row.account: row.credit_in_account_currency for row in journal_entry.accounts[1:]},
            {"Cash - TC": 20, "Mobile Money - TC": 30},
        )
        self.assertEqual(closing_shift.cash_draw_journal_entry, journal_entry.name)
        self.assertEqual(set_value.call_count, 3)
