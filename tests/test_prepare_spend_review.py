"""Only invented transaction fixtures; no private files or workbook I/O."""

from copy import deepcopy
from pathlib import Path
import sys
import unittest
from unittest.mock import patch


sys.dont_write_bytecode = True
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from prepare_spend_review import build_spend_review


def event(identifier, amount, code=5001, **changes):
    record = {
        "id": identifier, "date": "2031-01-10", "event": "消费",
        "expense_minor": amount, "base_minor": amount, "income_minor": 0,
        "code": code, "description": "合成项目", "source": "合成来源",
        "locator": "合成行", "related_id": None, "link_type": None,
    }
    record.update(changes)
    return record


class SpendReviewTests(unittest.TestCase):
    def setUp(self):
        self.events = [
            event("purchase", 300000),
            event("refund", -100000, event="消费退款", related_id="purchase", link_type="退款对应原消费"),
            event("returned", 300000, 5009, base_minor=0, behavior="冲动消费"),
            event("returned-refund", -300000, 5009, base_minor=0, event="消费退款", related_id="returned", link_type="退款对应原消费"),
            event("lease", 250000, 5004, base_minor=0, event="车辆租赁付款"),
            event("reimbursement", 0, 4005, base_minor=0, event="报销回款", income_minor=60000),
            event("small", 100000),
            event("interest", 1000, 5012, base_minor=0, event="利息或贴息"),
        ]
        self.config = {
            "start": "2031-01-01", "end_exclusive": "2031-04-01",
            "latest_observed_date": "2031-03-20", "threshold_minor": 200000,
            "budget_multiplier": .9, "reimbursement_minor": 60000,
            "proposal_monthly_caps": {"5001": 700}, "savings_target": 15000,
            "annotations": {"purchase": {"display_name": "合成用途说明", "judgment": "合成判断", "action": "合成行动", "basis": "合成依据"}},
        }
        for target in ("builtins.open", "pathlib.Path.read_text", "pathlib.Path.read_bytes", "sqlite3.connect"):
            guard = patch(target, side_effect=AssertionError("Pure tests forbid finance I/O"))
            guard.start()
            self.addCleanup(guard.stop)

    def build(self, events=None, config=None):
        data = self.events if events is None else events
        return build_spend_review(data, self.config if config is None else config, len(data) + 4, 3, 12000, -1000)

    def test_return_contract_and_independent_savings_bridge(self):
        sheets, checks, stats = self.build()
        self.assertEqual([sheet["name"] for sheet in sheets], ["大额消费", "消费分析", "节省方案"])
        self.assertEqual(stats["large_count"], 3)
        self.assertEqual(stats["large_gross_minor"], 850000)
        self.assertEqual(stats["large_refund_minor"], 400000)
        self.assertEqual(stats["large_net_minor"], 450000)
        self.assertEqual(stats["fully_refunded_count"], 1)
        self.assertEqual(stats["net_above_threshold_count"], 2)
        self.assertAlmostEqual(stats["current_net_monthly"], 720)
        self.assertAlmostEqual(stats["proposal_net_monthly"], 520)
        self.assertAlmostEqual(stats["annual_additional_savings"], 2400)
        self.assertAlmostEqual(stats["proposed_savings"], 14400)
        self.assertAlmostEqual(stats["proposed_cash_growth"], 1400)
        values = {(check["sheet"], check["cell"]): check["expectedNumber"] for check in checks}
        self.assertAlmostEqual(values["节省方案", "D29"], -600)
        self.assertAlmostEqual(values["消费分析", "C21"], 5510)
        self.assertAlmostEqual(values["消费分析", "C23"], 4910)
        self.assertNotEqual(values["消费分析", "C21"], values["消费分析", "C29"])

    def test_large_formulas_link_exact_id_and_refunds_in_same_window(self):
        sheets, _, _ = self.build()
        row_index, row = next((index + 5, row) for index, row in enumerate(sheets[0]["rows"]) if row[10] == "purchase")
        self.assertIn(f"$K{row_index}", row[3]["formula"])
        self.assertIn("'交易加工'!$A$5:$A$12", row[3]["formula"])
        refund_formula = row[4]["formula"]
        for fragment in ("'交易加工'!$T$5:$T$12", "'交易加工'!$U$5:$U$12", '"退款对应原消费"', '"<0"', "DATE(2031,1,1)", "DATE(2031,4,1)"):
            self.assertIn(fragment, refund_formula)
        self.assertEqual(row[5]["formula"], f"=D{row_index}-E{row_index}")
        self.assertEqual(row[2], "合成用途说明")
        self.assertEqual(row[7], "合成判断")
        self.assertEqual(row[9], "合成行动")
        self.assertIn("合成依据", row[12])

    def test_full_refund_and_prior_exclusions_are_not_additional_savings(self):
        self.config["annotations"]["returned"] = {"judgment": "保留人工解释"}
        sheets, _, _ = self.build()
        returned = next(row for row in sheets[0]["rows"] if row[10] == "returned")
        self.assertTrue(returned[7].startswith("已全额退款，不计新增节省"))
        self.assertIn("保留人工解释", returned[7])
        lease = next(row for row in sheets[0]["rows"] if row[10] == "lease")
        self.assertIn("固定安排", lease[8])
        proposal = sheets[2]
        self.assertEqual(proposal["editableCells"], ["D5"])
        self.assertEqual(proposal["rows"][8][3], {"formula": "=C13"})
        self.assertEqual(proposal["rows"][17][3], {"formula": "=C22"})
        self.assertEqual(proposal["rows"][21][3], {"formula": "=C26+F23"})
        self.assertEqual(proposal["rows"][22][3], {"formula": "=C27+F23"})

    def test_dynamic_large_count_and_summary_ranges(self):
        events = [event(f"synthetic-{index}", 200000) for index in range(27)]
        config = {**self.config, "reimbursement_minor": 0}
        sheets, checks, stats = self.build(events=events, config=config)
        self.assertEqual(len(sheets[0]["rows"]), 27)
        self.assertEqual(stats["large_count"], 27)
        self.assertEqual(sheets[1]["rows"][21][2], {"formula": "=COUNT('大额消费'!A5:A31)"})
        self.assertEqual(sum(check["sheet"] == "大额消费" for check in checks), 81)

    def test_empty_large_list_uses_zero_summary_without_reversed_ranges(self):
        sheets, checks, stats = self.build(events=[], config={**self.config, "reimbursement_minor": 0, "proposal_monthly_caps": {}})
        self.assertEqual(sheets[0]["rows"], [])
        self.assertEqual(stats["large_count"], 0)
        self.assertTrue(all(row[2] == {"formula": "=0"} for row in sheets[1]["rows"][21:27]))
        self.assertFalse(any(check["sheet"] == "大额消费" for check in checks))

    def test_inputs_unchanged_and_all_sheet_rows_and_metadata_are_valid(self):
        original_events, original_config = deepcopy(self.events), deepcopy(self.config)
        first = self.build()
        second = self.build()
        self.assertEqual(first, second)
        self.assertEqual(self.events, original_events)
        self.assertEqual(self.config, original_config)
        for sheet in first[0]:
            self.assertTrue(all(len(row) == len(sheet["headers"]) for row in sheet["rows"]))
            self.assertTrue(all(5 <= int(row) <= len(sheet["rows"]) + 4 for row in sheet.get("rowStyles", {})))
        self.assertEqual(first[0][1]["rows"][0][0], "5001")

    def test_duplicate_event_ids_and_invalid_caps_fail_before_authoring(self):
        with self.assertRaisesRegex(ValueError, "Duplicate event ID"):
            self.build(events=[self.events[0], self.events[0]])
        with self.assertRaisesRegex(ValueError, "unknown expense code"):
            self.build(config={**self.config, "proposal_monthly_caps": {"9999": 10}})
        with self.assertRaisesRegex(ValueError, "nonnegative"):
            self.build(config={**self.config, "proposal_monthly_caps": {"5001": -10}})


if __name__ == "__main__":
    unittest.main()
