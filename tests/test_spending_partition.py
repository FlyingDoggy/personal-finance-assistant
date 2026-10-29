"""Synthetic, file-free expense partition checks.

Run from the project root:
    PYTHONDONTWRITEBYTECODE=1 python3 tests/test_spending_partition.py -v
"""

from copy import deepcopy
from decimal import Decimal
import importlib.util
from pathlib import Path
import sys
import unittest
from unittest.mock import patch


sys.dont_write_bytecode = True
SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "spending_partition.py"
SPEC = importlib.util.spec_from_file_location("spending_partition_under_test", SCRIPT)
partition = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(partition)


def expense(event_id, amount=1000, observed_date="2031-01-15", **changes):
    return {
        "id": event_id,
        "date": observed_date,
        "expense_minor": amount,
        **changes,
    }


def refund(event_id, original_id, amount=-200, **changes):
    return expense(
        event_id,
        amount,
        link_type="退款对应原消费",
        related_id=original_id,
        **changes,
    )


def calculate(events, **changes):
    arguments = {
        "start": "2031-01-01",
        "end_exclusive": "2031-03-01",
        "trip_ids": set(),
        "durable_ids": set(),
        "fixed_ids": set(),
        **changes,
    }
    return partition.partition_expenses(events, **arguments)


class SpendingPartitionTests(unittest.TestCase):
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

    def assert_conserved(self, result, expected):
        self.assertEqual(result["total_expense_minor"], expected)
        self.assertEqual(sum(result["bucket_totals_minor"].values()), expected)
        self.assertEqual(sum(result["monthly_totals_minor"].values()), expected)
        for month, buckets in result["monthly_bucket_totals_minor"].items():
            self.assertEqual(sum(buckets.values()), result["monthly_totals_minor"][month])
            self.assertEqual(tuple(buckets), partition.BUCKETS)
            self.assertTrue(all(type(amount) is int for amount in buckets.values()))
        for bucket, total in result["bucket_totals_minor"].items():
            self.assertEqual(
                sum(month[bucket] for month in result["monthly_bucket_totals_minor"].values()),
                total,
            )

    def test_half_open_window_uses_actual_posting_dates(self):
        events = [
            expense("before", 1, "2030-12-31"),
            expense("start", 2, "2031-01-01"),
            expense("last-day", 3, "2031-02-28"),
            expense("end", 4, "2031-03-01"),
            expense("later", 5, "2031-03-02"),
            expense("zero", 0),
        ]
        result = calculate(events)
        self.assertEqual(result["event_buckets"], {"start": "daily", "last-day": "daily"})
        self.assertEqual(result["monthly_totals_minor"], {"2031-01": 2, "2031-02": 3})
        self.assert_conserved(result, 5)

    def test_disjoint_explicit_buckets_precede_daily_without_behavior_exclusion(self):
        events = [
            expense("trip", 100, behavior="冲动", category="固定支出"),
            expense("durable", 200, behavior="冲动"),
            expense("fixed", 300),
            expense("impulse", 400, behavior="冲动"),
            expense("ordinary", 500),
        ]
        result = calculate(
            events, trip_ids={"trip"}, durable_ids={"durable"}, fixed_ids={"fixed"}
        )
        self.assertEqual(result["event_buckets"], {
            "trip": "trip", "durable": "durable", "fixed": "fixed",
            "impulse": "daily", "ordinary": "daily",
        })
        self.assertEqual(result["bucket_totals_minor"], {
            "trip": 100, "durable": 200, "fixed": 300, "daily": 900,
        })
        self.assert_conserved(result, 1500)

    def test_overlapping_explicit_sets_raise_instead_of_double_counting(self):
        for first, second in (
            ("trip_ids", "durable_ids"),
            ("trip_ids", "fixed_ids"),
            ("durable_ids", "fixed_ids"),
        ):
            with self.subTest(first=first, second=second):
                with self.assertRaisesRegex(ValueError, "multiple explicit buckets"):
                    calculate([expense("purchase")], **{first: {"purchase"}, second: {"purchase"}})

    def test_cross_period_refund_inherits_original_without_importing_original_amount(self):
        events = [
            refund("later-refund", "old-trip", -250, observed_date="2031-02-11"),
            expense("old-trip", 1000, "2030-12-15"),
            expense("current", 500),
            refund("future-refund", "current", -300, observed_date="2031-03-01"),
        ]
        result = calculate(events, trip_ids={"old-trip"})
        self.assertEqual(result["event_buckets"], {"later-refund": "trip", "current": "daily"})
        self.assertEqual(result["bucket_totals_minor"]["trip"], -250)
        self.assertEqual(result["monthly_totals_minor"], {"2031-01": 500, "2031-02": -250})
        self.assert_conserved(result, 250)

    def test_refund_posts_once_even_when_also_explicitly_assigned(self):
        events = [expense("purchase", 1000), refund("part-one", "purchase", -200),
                  refund("part-two", "purchase", -300, observed_date="2031-02-01")]
        result = calculate(events, durable_ids={"purchase", "part-one"})
        self.assertEqual(result["event_buckets"], {
            "purchase": "durable", "part-one": "durable", "part-two": "durable",
        })
        self.assertEqual(result["monthly_totals_minor"], {"2031-01": 800, "2031-02": -300})
        self.assert_conserved(result, 500)

    def test_fully_refunded_purchase_leaves_zero_bucket_without_extra_deduction(self):
        events = [expense("purchase", 12345), refund("full-refund", "purchase", -12345),
                  expense("daily", 789)]
        result = calculate(events, durable_ids={"purchase", "full-refund"})
        self.assertEqual(result["bucket_totals_minor"]["durable"], 0)
        self.assertEqual(len(result["event_buckets"]), 3)
        self.assert_conserved(result, 789)

    def test_explicit_refund_conflicting_with_original_raises(self):
        events = [expense("purchase"), refund("refund", "purchase")]
        for original_bucket, refund_bucket in (
            ("trip_ids", "durable_ids"),
            ("durable_ids", "trip_ids"),
            ("fixed_ids", "trip_ids"),
        ):
            with self.subTest(original_bucket=original_bucket, refund_bucket=refund_bucket):
                with self.assertRaisesRegex(ValueError, "refund bucket conflicts"):
                    calculate(events, **{original_bucket: {"purchase"}, refund_bucket: {"refund"}})
        with self.assertRaisesRegex(ValueError, "refund bucket conflicts"):
            calculate(events, trip_ids={"refund"})

    def test_unknown_linked_refunds_stay_daily_and_report_window_ids(self):
        events = [
            refund("missing", "absent", -200),
            refund("non-expense-original", "income", -100),
            expense("income", 0, income_minor=1000),
            refund("missing-reference", None, -50),
            refund("outside", "absent", -500, observed_date="2030-12-31"),
        ]
        result = calculate(events)
        self.assertEqual(result["event_buckets"], {
            "missing": "daily", "non-expense-original": "daily", "missing-reference": "daily",
        })
        self.assertEqual(result["unresolved_refund_ids"],
                         ("missing", "non-expense-original", "missing-reference"))
        self.assert_conserved(result, -350)

    def test_unknown_refund_cannot_silently_override_explicit_assignment(self):
        with self.assertRaisesRegex(ValueError, "refund bucket conflicts"):
            calculate([refund("unknown", "absent")], trip_ids={"unknown"})

    def test_other_relationship_does_not_move_expense_into_original_bucket(self):
        events = [expense("purchase"), expense("other", -100, link_type="其他关联",
                                                  related_id="purchase")]
        result = calculate(events, trip_ids={"purchase"})
        self.assertEqual(result["event_buckets"], {"purchase": "trip", "other": "daily"})
        self.assert_conserved(result, 900)

    def test_rejects_duplicate_event_ids_across_entire_collection(self):
        for duplicate in (expense("same", 0), expense("same", 100, "2030-12-31")):
            with self.subTest(duplicate=duplicate):
                with self.assertRaisesRegex(ValueError, "Duplicate event ID"):
                    calculate([expense("same"), duplicate])

    def test_missing_and_zero_expense_explicit_ids_raise(self):
        for field in ("trip_ids", "durable_ids", "fixed_ids"):
            with self.subTest(field=field):
                with self.assertRaisesRegex(ValueError, "missing event ID"):
                    calculate([expense("known")], **{field: {"unknown"}})
                with self.assertRaisesRegex(ValueError, "non-expense event"):
                    calculate([expense("income", 0)], **{field: {"income"}})

    def test_rejects_invalid_nonzero_expense_dates_even_outside_window(self):
        for invalid in (None, "", "2031-02-29", "20310102", "2031-1-02",
                        "2031-01-02T10:00:00", "2031-W01-2", 20310102, True):
            with self.subTest(invalid=invalid):
                with self.assertRaises((TypeError, ValueError)):
                    calculate([expense("bad-date", observed_date=invalid)])
        result = calculate([expense("undated-income", 0, None), expense("valid", 100)])
        self.assertEqual(result["event_buckets"], {"valid": "daily"})

    def test_window_boundaries_must_be_canonical_valid_and_ordered(self):
        for start, end in (
            ("2031-01-01", "2031-01-01"),
            ("2031-03-01", "2031-01-01"),
            ("2031-02-29", "2031-03-01"),
            ("20310101", "2031-03-01"),
            ("2031-01-01", "2031-13-01"),
            (None, "2031-03-01"),
            ("2031-01-01", True),
        ):
            with self.subTest(start=start, end=end):
                with self.assertRaises((TypeError, ValueError)):
                    calculate([], start=start, end_exclusive=end)

    def test_zero_months_and_partial_months_crossing_year_are_retained(self):
        result = calculate([], start="2030-12-31", end_exclusive="2031-02-02")
        self.assertEqual(result["monthly_totals_minor"],
                         {"2030-12": 0, "2031-01": 0, "2031-02": 0})
        self.assert_conserved(result, 0)
        same_month = calculate([], start="2031-01-15", end_exclusive="2031-01-16")
        self.assertEqual(same_month["monthly_totals_minor"], {"2031-01": 0})

    def test_integer_cents_are_required_without_coercion(self):
        for invalid in (True, False, 100.0, Decimal(100), "100", None):
            with self.subTest(invalid=invalid):
                with self.assertRaises(TypeError):
                    calculate([expense("invalid-amount", invalid)])

    def test_input_events_and_collections_unchanged_and_iterators_supported(self):
        events = [expense("trip", 1000, metadata={"tags": ["a", "b"]}),
                  refund("refund", "trip", -200)]
        trip_ids = ["trip", "trip"]
        original = deepcopy((events, trip_ids))
        result = calculate(iter(events), trip_ids=iter(trip_ids))
        self.assertEqual((events, trip_ids), original)
        self.assertEqual(result["event_buckets"], {"trip": "trip", "refund": "trip"})
        result["event_buckets"]["trip"] = "daily"
        result["monthly_bucket_totals_minor"]["2031-01"]["trip"] = 0
        self.assertEqual((events, trip_ids), original)

    def test_cyclic_refund_relationships_raise(self):
        for events in ([refund("self", "self")],
                       [refund("one", "two"), refund("two", "one")]):
            with self.subTest(events=events):
                with self.assertRaisesRegex(ValueError, "Cyclic refund"):
                    calculate(events)

    def test_invalid_ids_and_accidental_string_collections_are_rejected(self):
        for event_id in (None, "", 1, True):
            with self.subTest(event_id=event_id):
                with self.assertRaises(ValueError):
                    calculate([expense(event_id)])
        with self.assertRaises(TypeError):
            calculate([expense("trip")], trip_ids="trip")
        with self.assertRaises(ValueError):
            calculate([expense("trip")], trip_ids=[None])


if __name__ == "__main__":
    unittest.main()
