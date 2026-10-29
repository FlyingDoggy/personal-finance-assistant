"""Synthetic, file-free budget policy checks; never call processing main().

Run just these tests from the project root:
    PYTHONDONTWRITEBYTECODE=1 python3 tests/test_budget_policy.py -v
"""

from decimal import Decimal, localcontext
import importlib.util
from pathlib import Path
import sys
import unittest
from unittest.mock import patch


sys.dont_write_bytecode = True
SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "budget_policy.py"
SPEC = importlib.util.spec_from_file_location("budget_policy_under_test", SCRIPT)
policy = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(policy)


class BudgetPolicyTests(unittest.TestCase):
    def setUp(self):
        for target in (
            "builtins.open",
            "pathlib.Path.read_text",
            "pathlib.Path.read_bytes",
            "sqlite3.connect",
        ):
            guard = patch(target, side_effect=AssertionError("Pure tests forbid finance I/O"))
            guard.start()
            self.addCleanup(guard.stop)

    def test_window_includes_january_and_partial_october_but_excludes_boundaries(self):
        events = [
            {"id": "prior-year", "date": "2030-12-31"},
            {"id": "october-receipt", "date": "2031-10-26"},
            {"id": "start", "date": "2031-01-01"},
            {"id": "october-start", "date": "2031-10-01"},
            {"id": "october-end", "date": "2031-10-31"},
            {"id": "next-month", "date": "2031-11-01"},
        ]
        original = [dict(event) for event in events]
        selected = policy.select_budget_window(events, "2031-01-01", "2031-11-01")
        self.assertEqual(
            [event["id"] for event in selected],
            ["october-receipt", "start", "october-start", "october-end"],
        )
        self.assertIs(selected[0], events[1])
        self.assertEqual(events, original)

    def test_window_excludes_missing_invalid_and_noncanonical_event_dates(self):
        invalid_dates = (
            None, "", "2031-02-29", "2031-13-01", "20311026",
            "2031-1-01", "2031-10-26T12:00:00", "2031-W01-1",
            " 2031-10-26", 20311026, True,
        )
        events = [{"date": value} for value in invalid_dates] + [{}]
        self.assertEqual(
            policy.select_budget_window(events, "2031-01-01", "2031-11-01"), [],
        )
        leap_day = {"date": "2032-02-29"}
        self.assertEqual(
            policy.select_budget_window([leap_day], "2032-02-01", "2032-03-01"),
            [leap_day],
        )

    def test_window_rejects_invalid_or_empty_boundaries(self):
        for start, end in (
            ("2031-01-01", "2031-01-01"),
            ("2031-11-01", "2031-01-01"),
            ("2031-02-29", "2031-11-01"),
            ("2031-01-01", "2031-13-01"),
            ("20310101", "2031-11-01"),
            ("2031-01-01", "2031-11-01T00:00:00"),
        ):
            with self.subTest(start=start, end=end):
                with self.assertRaises(ValueError):
                    policy.select_budget_window([], start, end)
        for start, end in ((None, "2031-11-01"), ("2031-01-01", True)):
            with self.subTest(start=start, end=end):
                with self.assertRaises(TypeError):
                    policy.select_budget_window([], start, end)

    def test_ten_observed_months_include_october_receipts_and_preserve_surplus(self):
        # Synthetic January-October window; October is observed only through 26th.
        # Only June and October receive reimbursements; all ten months count.
        events = [
            {"date": f"2031-{month:02}-10", "event": "salary",
             "income_minor": 20000, "expense_minor": 10000}
            for month in range(1, 11)
        ]
        events += [
            {"date": "2031-01-15", "event": "sponsorship",
             "income_minor": 10000, "expense_minor": 0},
            {"date": "2031-06-20", "event": "reimbursement",
             "income_minor": 9000, "expense_minor": 0},
            {"date": "2031-10-26", "event": "reimbursement",
             "income_minor": 6000, "expense_minor": 0},
            {"date": "2031-11-01", "event": "reimbursement",
             "income_minor": 5000, "expense_minor": 0},
        ]
        selected = policy.select_budget_window(events, "2031-01-01", "2031-11-01")
        income = sum(event["income_minor"] for event in selected)
        expense = sum(event["expense_minor"] for event in selected)
        reimbursement = sum(
            event["income_minor"] for event in selected
            if event["event"] == "reimbursement"
        )
        basis = policy.normalize_budget_basis(income, expense, reimbursement, 10)
        self.assertEqual((income, expense, reimbursement), (225000, 100000, 15000))
        self.assertEqual(basis["covered_months"], 10)
        self.assertEqual(basis["monthly_reimbursement_minor"], Decimal(1500))
        self.assertEqual(basis["annualized_reimbursement_minor"], Decimal(18000))
        self.assertEqual(basis["adjusted_income_minor"], 210000)
        self.assertEqual(basis["adjusted_expense_minor"], 85000)
        self.assertEqual(basis["surplus_minor"], 125000)
        self.assertEqual(
            basis["adjusted_income_minor"] - basis["adjusted_expense_minor"],
            income - expense,
        )
        self.assertEqual(policy.scaled_personal_expense_minor(expense, reimbursement, 10, 1), 102000)

    def test_same_reimbursement_deduction_preserves_actual_surplus(self):
        # W=100000, G=20000, R=15000, E=90000; sponsorship stays in income.
        basis = policy.normalize_budget_basis(135000, 90000, 15000, 6)
        self.assertEqual(basis["adjusted_income_minor"], 120000)
        self.assertEqual(basis["adjusted_expense_minor"], 75000)
        self.assertEqual(basis["surplus_minor"], 45000)
        self.assertEqual(
            basis["adjusted_income_minor"] - basis["adjusted_expense_minor"],
            basis["surplus_minor"],
        )
        self.assertEqual(basis["warnings"], ())
        for key in ("adjusted_income_minor", "adjusted_expense_minor", "surplus_minor"):
            self.assertIs(type(basis[key]), int)

    def test_zero_reimbursement_months_stay_in_common_denominator(self):
        # Only two receipts occurred, but all six calendar months are covered.
        basis = policy.normalize_budget_basis(84000, 60000, 12000, 6)
        self.assertEqual(basis["covered_months"], 6)
        self.assertEqual(basis["monthly_reimbursement_minor"], Decimal("2000"))
        self.assertEqual(basis["annualized_reimbursement_minor"], Decimal("24000"))
        self.assertIsInstance(basis["monthly_reimbursement_minor"], Decimal)
        self.assertIsInstance(basis["annualized_reimbursement_minor"], Decimal)

    def test_missing_months_are_not_padded_with_zero(self):
        # Caller has a complete three-month window, not a complete calendar year.
        basis = policy.normalize_budget_basis(42000, 30000, 6000, 3)
        self.assertEqual(basis["monthly_reimbursement_minor"], Decimal("2000"))
        self.assertEqual(basis["annualized_reimbursement_minor"], Decimal("24000"))
        self.assertEqual(policy.scaled_personal_expense_minor(30000, 6000, 3, 1), 96000)

    def test_zero_receipts_still_use_declared_complete_window(self):
        basis = policy.normalize_budget_basis(30000, 18000, 0, 3)
        self.assertEqual(basis["monthly_reimbursement_minor"], Decimal(0))
        self.assertEqual(basis["annualized_reimbursement_minor"], Decimal(0))
        self.assertEqual(basis["adjusted_expense_minor"], 18000)
        self.assertEqual(basis["surplus_minor"], 12000)

    def test_coverage_range_includes_one_and_twelve(self):
        for months, annual in ((1, Decimal(14400)), (12, Decimal(1200))):
            with self.subTest(months=months):
                basis = policy.normalize_budget_basis(20000, 10000, 1200, months)
                self.assertEqual(basis["annualized_reimbursement_minor"], annual)

    def test_missing_or_out_of_range_coverage_is_rejected(self):
        for months in (0, -1, 13):
            with self.subTest(months=months):
                with self.assertRaises(ValueError):
                    policy.normalize_budget_basis(10000, 8000, 1000, months)
                with self.assertRaises(ValueError):
                    policy.scaled_personal_expense_minor(8000, 1000, months, 1)

    def test_month_count_must_be_an_integer_not_bool_or_coerced_value(self):
        for months in (True, False, None, 6.0, Decimal(6), "6"):
            with self.subTest(months=months):
                with self.assertRaises(TypeError):
                    policy.normalize_budget_basis(10000, 8000, 1000, months)
                with self.assertRaises(TypeError):
                    policy.scaled_personal_expense_minor(8000, 1000, months, 1)

    def test_cross_year_excess_receipt_warns_without_clipping(self):
        basis = policy.normalize_budget_basis(26000, 4000, 6000, 1)
        self.assertEqual(basis["adjusted_income_minor"], 20000)
        self.assertEqual(basis["adjusted_expense_minor"], -2000)
        self.assertEqual(basis["surplus_minor"], 22000)
        self.assertEqual(basis["warnings"], ("reimbursement_exceeds_expense",))
        self.assertEqual(policy.scaled_personal_expense_minor(4000, 6000, 1, 1), -24000)

    def test_equal_reimbursement_and_expense_has_zero_basis_without_warning(self):
        basis = policy.normalize_budget_basis(12000, 2000, 2000, 12)
        self.assertEqual(basis["adjusted_expense_minor"], 0)
        self.assertEqual(basis["warnings"], ())

    def test_negative_reimbursement_and_noninteger_amounts_are_rejected(self):
        with self.assertRaises(ValueError):
            policy.normalize_budget_basis(10000, 8000, -1, 6)
        with self.assertRaises(ValueError):
            policy.scaled_personal_expense_minor(8000, -1, 6, 1)
        for amount in (True, 100.0, Decimal(100), "100"):
            for field in range(3):
                values = [10000, 8000, 1000]
                values[field] = amount
                with self.subTest(amount=amount, field=field):
                    with self.assertRaises(TypeError):
                        policy.normalize_budget_basis(*values, 6)

    def test_scenario_multiplier_applies_after_reimbursement_deduction(self):
        result = policy.scaled_personal_expense_minor(12000, 2000, 12, "1.5")
        self.assertEqual(result, 15000)
        # Scaling gross expenses before deducting unscaled R would give 16000.
        self.assertNotEqual(result, 16000)
        self.assertEqual(policy.scaled_personal_expense_minor(60000, 12000, 6, 1), 96000)

    def test_rounding_occurs_only_at_final_annual_minor_unit(self):
        # Rounding monthly or dividing before multiplication can lose half a cent.
        self.assertEqual(policy.scaled_personal_expense_minor(1, 0, 8, 1), 2)
        self.assertEqual(policy.scaled_personal_expense_minor(0, 1, 8, 1), -2)
        self.assertEqual(policy.scaled_personal_expense_minor(1, 0, 7, "0.875"), 2)
        self.assertEqual(policy.scaled_personal_expense_minor(0, 1, 7, "0.875"), -2)

    def test_estimates_do_not_round_to_monthly_cents_before_annualizing(self):
        basis = policy.normalize_budget_basis(100, 50, 1, 3)
        self.assertGreater(basis["monthly_reimbursement_minor"], Decimal("0.3333333333333333"))
        self.assertEqual(basis["annualized_reimbursement_minor"], Decimal(4))
        self.assertEqual(policy.scaled_personal_expense_minor(1, 0, 3, 1), 4)

    def test_results_are_independent_of_low_caller_decimal_precision(self):
        with localcontext() as context:
            context.prec = 3
            basis = policy.normalize_budget_basis(900000, 600000, 123456, 6)
            result = policy.scaled_personal_expense_minor(123456, 12345, 6, Decimal("1.25"))
        self.assertEqual(basis["monthly_reimbursement_minor"], Decimal(20576))
        self.assertEqual(basis["annualized_reimbursement_minor"], Decimal(246912))
        self.assertEqual(result, 277778)

    def test_multiplier_must_be_finite_nonnegative_and_decimal_safe(self):
        for multiplier in ("NaN", "Infinity", "-Infinity", "-0.1", "not-a-number"):
            with self.subTest(multiplier=multiplier):
                with self.assertRaises(ValueError):
                    policy.scaled_personal_expense_minor(10000, 1000, 12, multiplier)
        for multiplier in (True, 1.5, None):
            with self.subTest(multiplier=multiplier):
                with self.assertRaises(TypeError):
                    policy.scaled_personal_expense_minor(10000, 1000, 12, multiplier)
        self.assertEqual(policy.scaled_personal_expense_minor(10000, 1000, 12, 0), 0)


if __name__ == "__main__":
    unittest.main()
