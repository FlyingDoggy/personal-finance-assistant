"""Synthetic, file-free checks for the financial interpretation functions.

Run from the project root:
    PYTHONDONTWRITEBYTECODE=1 python3 -m unittest discover -s tests -v

This module never calls main(), loads a configuration, or opens a finance source.
All names, dates, identifiers, and amounts below are invented test fixtures.
"""

import importlib.util
import json
from pathlib import Path
import sys
import unittest
from unittest.mock import patch


sys.dont_write_bytecode = True
SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "process_finance.py"
SPEC = importlib.util.spec_from_file_location("finance_processing_under_test", SCRIPT)
processing = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(processing)

SYNTHETIC_CONFIG = {"employer_keyword": "合成雇主", "owner_name": "合成用户"}


def source_row(row_id, *, family="cmb_credit", amount=10000,
               description="合成商户", transaction_date="2026-01-10",
               posting_date="2026-01-10", raw_date=None, status="",
               fields=None, account="****1111"):
    """Construct the complete raw-row contract without touching external data."""
    return {
        "transaction_row_id": row_id,
        "file_id": "synthetic-file",
        "filename": "synthetic-statement.csv",
        "source_locator": "synthetic-row:" + row_id,
        "source_family": family,
        "account_identifier_raw": account,
        "amount_minor": amount,
        "description_raw": description,
        "transaction_date_iso": transaction_date,
        "posting_date_iso": posting_date,
        "transaction_date_raw": raw_date if raw_date is not None else transaction_date,
        "event_status_raw": status,
        "raw_fields_json": json.dumps(fields or {}, ensure_ascii=False),
    }


def event(row_id, *, payroll_ids=(), **kwargs):
    result = processing.normalize(source_row(row_id, **kwargs))
    processing.decide(result, set(payroll_ids), SYNTHETIC_CONFIG)
    return result


def effective_cash(events):
    return sum(e["cash_minor"] for e in events if e["cash_included"])


class ProcessingTests(unittest.TestCase):
    def setUp(self):
        # Any new source/config/database access inside a pure function fails fast.
        for target in (
            "pathlib.Path.read_text",
            "pathlib.Path.read_bytes",
            "sqlite3.connect",
        ):
            guard = patch(target, side_effect=AssertionError("Pure tests forbid finance I/O"))
            guard.start()
            self.addCleanup(guard.stop)

    def test_installment_principal_does_not_repeat_purchase_expense(self):
        purchase = event("purchase", amount=120000, description="家电购买")
        for description in ("分期本金 第1期", "每月分摊本金"):
            with self.subTest(description=description):
                principal = event("principal", amount=10000, description=description)
                self.assertEqual(principal["event"], "分期本金出账")
                self.assertEqual(principal["expense_minor"], 0)
                self.assertEqual(principal["cash_minor"], 0)
                self.assertIn(principal["debt_delta_minor"], (None, 0))
                self.assertEqual(purchase["expense_minor"] + principal["expense_minor"], 120000)

    def test_installment_interest_and_subsidy_keep_their_signs(self):
        interest = event("interest", amount=500, description="分期利息")
        subsidy = event("subsidy", amount=-200, description="财政贴息")
        self.assertEqual(interest["code"], 5012)
        self.assertEqual(subsidy["code"], 5012)
        self.assertEqual(interest["expense_minor"] + subsidy["expense_minor"], 300)
        self.assertEqual(interest["debt_delta_minor"] + subsidy["debt_delta_minor"], 300)
        self.assertEqual(effective_cash([interest, subsidy]), 0)

    def test_installment_return_adjustment_is_not_a_second_refund(self):
        adjustment = event("adjustment", amount=10000, description="分期退货-合成商品")
        merchant_refund = event("merchant-refund", amount=-10000, description="合成商户退款")
        self.assertEqual(adjustment["expense_minor"], 0)
        self.assertEqual(adjustment["income_minor"], 0)
        self.assertEqual(adjustment["expense_minor"] + merchant_refund["expense_minor"], -10000)

    def test_repayment_two_sources_produce_one_cash_outflow(self):
        bank = event("bank-payment", family="cmb_debit", amount=-10000,
                     description="信用卡还款")
        card = event("card-receipt", amount=-10000, description="自动还款")
        links = processing.link_events([bank, card])
        self.assertEqual(bank["related_id"], card["id"])
        self.assertEqual(card["related_id"], bank["id"])
        self.assertEqual(len(links), 1)
        self.assertEqual(effective_cash([bank, card]), -10000)
        self.assertEqual(card["cash_minor"], 0)
        self.assertEqual(sum(e["income_minor"] for e in (bank, card)), 0)
        self.assertEqual(sum(e["expense_minor"] for e in (bank, card)), 0)

    def test_single_bank_repayment_still_counts_cash_once(self):
        bank = event("bank-only", family="cmb_debit", amount=-10000,
                     description="信用卡还款")
        processing.link_events([bank])
        self.assertEqual(effective_cash([bank]), -10000)
        self.assertEqual(bank["expense_minor"], 0)
        self.assertIsNone(bank["related_id"])

    def test_ambiguous_bank_repayments_are_not_arbitrarily_linked(self):
        banks = [event("bank-" + str(i), family="cmb_debit", amount=-10000,
                       description="信用卡还款") for i in (1, 2)]
        card = event("card", amount=-10000, description="自动还款")
        processing.link_events(banks + [card])
        self.assertIsNone(card["related_id"])
        self.assertEqual(effective_cash(banks + [card]), -20000)

    def test_unknown_year_is_not_assigned_to_report_year(self):
        unknown = event("unknown-year", transaction_date=None, posting_date=None,
                        raw_date="01/10")
        self.assertIsNone(unknown["date"])
        self.assertEqual(unknown["in_scope"], 0)
        self.assertEqual(unknown["date_basis"], "日期待确认")

    def test_month_day_resolves_across_year_boundary_from_posting_date(self):
        prior_year = event("prior-year", transaction_date=None,
                           posting_date="2026-01-02", raw_date="12/31")
        self.assertEqual(prior_year["date"], "2025-12-31")
        self.assertEqual(prior_year["in_scope"], 0)

    def test_credit_refund_uses_credit_posting_date(self):
        refund = event("refund-date", amount=-10000, description="合成商户退款",
                       transaction_date="2025-12-20", posting_date="2026-01-10")
        self.assertEqual(refund["date"], "2026-01-10")
        self.assertEqual(refund["in_scope"], 1)

    def test_closed_order_with_a_successful_payment_is_a_real_refund(self):
        order = {"商家订单号": "synthetic-paid-order"}
        purchase = event("paid", family="alipay", status="交易成功", fields=order,
                         transaction_date="2026-01-05", posting_date="2026-01-05")
        closed = event("closed", family="alipay", status="交易关闭", fields=order,
                       transaction_date="2026-02-05", posting_date="2026-02-05")
        refund = event("refunded", family="alipay", status="退款成功", amount=-10000,
                       fields=order, transaction_date="2026-02-05", posting_date="2026-02-05")
        processing.link_events([purchase, closed, refund])
        self.assertEqual(purchase["expense_minor"], 10000)
        self.assertEqual(closed["expense_minor"], 0)
        self.assertEqual(refund["expense_minor"], -10000)
        self.assertEqual(refund["related_id"], purchase["id"])
        self.assertEqual(sum(e["expense_minor"] for e in (purchase, closed, refund)), 0)

    def test_partial_huabei_refund_matches_successful_order(self):
        order = {"商家订单号": "synthetic-partial-order"}
        purchase = event("paid", family="alipay", status="交易成功", fields=order)
        refund = event("refund", family="alipay", status="退款成功", amount=-4000,
                       fields=order, transaction_date="2026-01-11", posting_date="2026-01-11")
        processing.link_events([purchase, refund])
        self.assertEqual(refund["related_id"], purchase["id"])
        self.assertEqual(refund["expense_minor"], -4000)
        self.assertEqual(purchase["expense_minor"] + refund["expense_minor"], 6000)

    def test_closed_only_order_does_not_create_a_negative_expense(self):
        order = {"商家订单号": "synthetic-cancelled-order"}
        closed = event("closed", family="alipay", status="交易关闭", fields=order)
        refund = event("refund", family="alipay", status="退款成功", amount=-10000,
                       fields=order)
        processing.link_events([closed, refund])
        self.assertEqual(sum(e["expense_minor"] for e in (closed, refund)), 0)
        self.assertEqual(sum(e["income_minor"] for e in (closed, refund)), 0)

    def test_cumulative_refund_cannot_exceed_original_purchase(self):
        purchase = event("purchase", amount=10000)
        first = event("refund-one", amount=-6000, description="合成商户退款",
                      transaction_date="2026-01-11", posting_date="2026-01-11")
        second = event("refund-two", amount=-6000, description="合成商户退款",
                       transaction_date="2026-01-12", posting_date="2026-01-12")
        processing.link_events([purchase, first, second])
        self.assertEqual(first["related_id"], purchase["id"])
        self.assertIsNone(second["related_id"])
        self.assertIn("待确认", second["review"])
        self.assertEqual(second["expense_minor"], 0)
        self.assertEqual(purchase["expense_minor"] + first["expense_minor"], 4000)

    def test_two_equal_real_purchases_are_not_merged_by_amount_and_date(self):
        purchases = [event("purchase-" + str(i), fields={"交易号": "synthetic-tx-" + str(i)})
                     for i in (1, 2)]
        processing.link_events(purchases)
        self.assertEqual(sum(e["expense_minor"] for e in purchases), 20000)
        self.assertTrue(all(e["related_id"] is None for e in purchases))

    def test_ambiguous_refund_preserves_distinct_original_purchases(self):
        purchases = [event("purchase-" + str(i)) for i in (1, 2)]
        refund = event("refund", amount=-5000, description="合成商户退款",
                       transaction_date="2026-01-11", posting_date="2026-01-11")
        processing.link_events(purchases + [refund])
        self.assertIsNone(refund["related_id"])
        self.assertEqual(sum(e["expense_minor"] for e in purchases), 20000)

    def test_owned_internal_transfer_cancels_only_after_both_ends_match(self):
        fields = {"对方户名": "合成用户"}
        outgoing = event("transfer-out", family="cmb_debit", amount=-10000,
                         description="转出", fields=fields)
        incoming = event("transfer-in", family="citic_debit", amount=10000,
                         description="转入", fields=fields)
        processing.link_events([outgoing, incoming])
        self.assertEqual(effective_cash([outgoing, incoming]), 0)
        self.assertEqual(outgoing["cash_included"], 0)
        self.assertEqual(incoming["cash_included"], 0)
        self.assertEqual(sum(e["income_minor"] + e["expense_minor"] for e in (outgoing, incoming)), 0)

    def test_unmatched_owned_transfer_keeps_known_cash_fact(self):
        outgoing = event("transfer-only", family="cmb_debit", amount=-10000,
                         description="转出", fields={"对方户名": "合成用户"})
        processing.link_events([outgoing])
        self.assertEqual(effective_cash([outgoing]), -10000)
        self.assertEqual(outgoing["income_minor"], 0)
        self.assertEqual(outgoing["expense_minor"], 0)

    def test_employer_receipt_needs_explicit_payroll_id(self):
        unknown = event("employer-unknown", family="cmb_debit", amount=100000,
                        description="合成雇主来款")
        salary = event("salary", family="cmb_debit", amount=60000,
                       description="合成雇主来款", payroll_ids={"salary"})
        self.assertEqual(unknown["income_minor"], 0)
        self.assertEqual(unknown["review"], "待确认")
        self.assertEqual(salary["income_minor"], 60000)
        self.assertEqual(salary["code"], 4001)

    def test_taxi_description_does_not_imply_reimbursement(self):
        taxi = event("taxi", family="cmb_debit", amount=-8000,
                     description="滴滴快捷支付")
        self.assertEqual(taxi["expense_minor"], 8000)
        self.assertNotEqual(taxi["code"], 1004)
        self.assertEqual(taxi["reimb"], "通勤/工作待区分")

    def test_user_rule_classifies_small_employer_receipt_as_reimbursement(self):
        receipt = processing.normalize(source_row("synthetic-reimbursement", family="cmb_debit", amount=52000, description="合成雇主来款"))
        config = {**SYNTHETIC_CONFIG, "small_employer_receipts_are_reimbursement": True}
        processing.decide(receipt, set(), config)
        self.assertEqual(receipt["event"], "报销回款")
        self.assertEqual(receipt["code"], 4005)
        self.assertEqual(receipt["income_minor"], 52000)
        self.assertEqual(receipt["expense_minor"], 0)
        self.assertEqual(receipt["cash_minor"], 52000)

    def test_salary_exception_is_explicitly_visible_for_confirmation(self):
        receipt = processing.normalize(source_row("synthetic-exception", family="cmb_debit", amount=60000, description="合成雇主来款"))
        config = {**SYNTHETIC_CONFIG, "income_review_ids": ["synthetic-exception"]}
        processing.decide(receipt, {"synthetic-exception"}, config)
        self.assertEqual(receipt["review"], "收入判断待确认")
        self.assertEqual(receipt["income_minor"], 60000)


if __name__ == "__main__":
    unittest.main()
