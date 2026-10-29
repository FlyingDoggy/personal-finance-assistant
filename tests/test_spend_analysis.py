"""Synthetic, file-free spending analysis checks.

All identifiers and amounts below are invented. Run from the project root:
    PYTHONDONTWRITEBYTECODE=1 python3 tests/test_spend_analysis.py -v
"""

from copy import deepcopy
import importlib.util
from pathlib import Path
import sys
import unittest
from unittest.mock import patch


sys.dont_write_bytecode = True
SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPTS))
SPEC = importlib.util.spec_from_file_location(
    "spend_analysis_under_test", SCRIPTS / "spend_analysis.py"
)
analysis = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(analysis)


def synthetic_event(event_id, amount=200000, **changes):
    event = {
        "id": event_id,
        "event": "消费",
        "date": "2031-01-10",
        "expense_minor": amount,
        "related_id": None,
        "link_type": None,
    }
    event.update(changes)
    return event


def synthetic_refund(event_id, original_id, amount, **changes):
    event = synthetic_event(
        event_id,
        -amount,
        event="消费退款",
        related_id=original_id,
        link_type="退款对应原消费",
        date="2031-02-10",
    )
    event.update(changes)
    return event


def records(events, **kwargs):
    return analysis.large_expense_records(events, "2031-01-01", "2031-03-01", **kwargs)


class SpendAnalysisTests(unittest.TestCase):
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

    def test_threshold_is_inclusive_and_uses_expense_not_source_or_cash(self):
        below = synthetic_event("below", 199999, raw_amount_minor=-900000, cash_minor=-900000)
        boundary = synthetic_event("boundary", 200000, raw_amount_minor=-200000, cash_minor=0)
        above = synthetic_event("above", 200001)
        self.assertEqual(
            [r["event"]["id"] for r in records([below, boundary, above])],
            ["above", "boundary"],
        )

    def test_window_includes_start_and_excludes_end_or_invalid_dates(self):
        events = [
            synthetic_event("start", date="2031-01-01"),
            synthetic_event("before", date="2030-12-31"),
            synthetic_event("end", date="2031-03-01"),
            synthetic_event("missing", date=None),
            synthetic_event("invalid", date="2031-02-30"),
        ]
        self.assertEqual([r["event"]["id"] for r in records(events)], ["start"])

    def test_multiple_linked_refunds_use_event_facts_once(self):
        purchase = synthetic_event("purchase", 300000)
        events = [
            synthetic_refund("refund-b", "purchase", 20000),
            purchase,
            synthetic_refund("refund-a", "purchase", 30000),
            synthetic_refund("unrelated", "other-purchase", 9000),
            synthetic_refund("unlinked", "purchase", 9000, link_type=None),
            synthetic_refund("positive", "purchase", -9000),
        ]
        result = records(events)[0]
        self.assertIs(result["event"], purchase)
        self.assertEqual(result["gross_minor"], 300000)
        self.assertEqual(result["refund_minor"], 50000)
        self.assertEqual(result["net_minor"], 250000)
        self.assertEqual(result["refund_ids"], ["refund-a", "refund-b"])

    def test_fully_refunded_purchase_remains_above_original_threshold(self):
        events = [
            synthetic_event("purchase"),
            synthetic_refund("refund", "purchase", 200000),
        ]
        result = records(events)
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]["net_minor"], 0)
        self.assertEqual(result[0]["refund_ids"], ["refund"])

    def test_refunds_outside_window_do_not_change_displayed_net(self):
        events = [
            synthetic_event("purchase", date="2031-01-01"),
            synthetic_refund("before", "purchase", 10000, date="2030-12-31"),
            synthetic_refund("start", "purchase", 20000, date="2031-01-01"),
            synthetic_refund("end", "purchase", 30000, date="2031-03-01"),
        ]
        result = records(events)[0]
        self.assertEqual(result["refund_minor"], 20000)
        self.assertEqual(result["net_minor"], 180000)
        self.assertEqual(result["refund_ids"], ["start"])

    def test_only_purchase_and_vehicle_events_qualify_even_if_other_expense_is_positive(self):
        excluded_kinds = [
            "分期本金出账", "信用还款", "转分期", "本人账户划转",
            "房贷还款待拆分", "手续费", "利息或贴息", "性质待确认",
        ]
        events = [synthetic_event(f"excluded-{i}", event=kind) for i, kind in enumerate(excluded_kinds)]
        events += [synthetic_event("vehicle", event="车辆租赁付款"), synthetic_event("purchase")]
        self.assertEqual([r["event"]["id"] for r in records(events)], ["purchase", "vehicle"])

    def test_nonpositive_purchase_never_qualifies_even_with_zero_threshold(self):
        events = [synthetic_event("zero", 0), synthetic_event("negative", -200000), synthetic_event("positive", 1)]
        self.assertEqual([r["event"]["id"] for r in records(events, threshold_minor=0)], ["positive"])

    def test_order_is_descending_gross_then_date_and_id(self):
        events = [
            synthetic_event("b", date="2031-02-01"),
            synthetic_event("a", date="2031-02-01"),
            synthetic_event("earlier", date="2031-01-01"),
            synthetic_event("largest", 300000, date="2031-02-20"),
        ]
        expected = ["largest", "earlier", "a", "b"]
        for input_events in (events, list(reversed(events))):
            self.assertEqual([r["event"]["id"] for r in records(input_events)], expected)

    def test_duplicate_ids_are_rejected_including_out_of_window(self):
        for duplicate in (
            synthetic_event("duplicate"),
            synthetic_event("duplicate", date="2030-12-31"),
            synthetic_refund("duplicate", "purchase", 10000),
        ):
            with self.subTest(duplicate=duplicate), self.assertRaisesRegex(ValueError, "Duplicate event ID"):
                records([synthetic_event("duplicate"), duplicate])

    def test_repeated_calls_are_read_only_and_keep_original_reference(self):
        purchase = synthetic_event("purchase", metadata={"source": ["synthetic"]})
        events = [synthetic_refund("refund", "purchase", 10000), purchase]
        before = deepcopy(events)
        first = records(events)
        second = records(iter(events))
        self.assertEqual(events, before)
        self.assertEqual(first, second)
        self.assertIs(first[0]["event"], purchase)
        self.assertIs(second[0]["event"], purchase)
        self.assertIsNot(first[0]["refund_ids"], second[0]["refund_ids"])

    def test_invalid_threshold_or_window_is_rejected(self):
        for value in (True, 1.5, "200000"):
            with self.subTest(value=value), self.assertRaises(TypeError):
                records([], threshold_minor=value)
        with self.assertRaises(ValueError):
            records([], threshold_minor=-1)
        with self.assertRaises(ValueError):
            analysis.large_expense_records([], "2031-03-01", "2031-01-01")


if __name__ == "__main__":
    unittest.main()
