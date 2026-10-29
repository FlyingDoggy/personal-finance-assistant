"""Purchase evidence matching checks using invented records and temp hash sources.

The reader is mocked; the mocked deduplicator wraps its pure implementation.
No financial data is read, no processing main() is called, and no money flows
are created. Run only this file with:
    PYTHONDONTWRITEBYTECODE=1 python3 tests/test_purchase_reference_enrichment.py -v
"""

from copy import deepcopy
import hashlib
import importlib.util
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch


sys.dont_write_bytecode = True
SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPTS))
SPEC = importlib.util.spec_from_file_location(
    "purchase_reference_enrichment_under_test", SCRIPTS / "purchase_reference_enrichment.py"
)
enrichment = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(enrichment)


def synthetic_order(**changes):
    record = {
        "创建时间": "2026-01-10 10:00:00",
        "支付宝交易号": "synthetic-order-1",
        "商户订单号": "synthetic-merchant-order-1",
        "商品名称": "合成测试商品",
        "订单金额(元)": "100.00",
        "交易状态": "成功",
        "对方名称": "合成生活用品商店",
        "买卖标志": "买入",
        "优惠(元)": "",
        "累计退款总额(元)": "0.00",
        "支付渠道": "快捷支付-信用卡",
        "银行订单号": "synthetic-bank-order-1",
        "amount_minor": 10000,
        "discount_minor": None,
        "refund_minor": 0,
        "source_row": 5,
        "source_file": "synthetic-source.xls",
        "source_sha256": "synthetic-hash",
    }
    record.update(changes)
    return record


def synthetic_event(**changes):
    event = {
        "id": "synthetic-event-1",
        "in_scope": 1,
        "family": "cmb_credit",
        "date": "2026-01-10",
        "amount_minor": 10000,
        "expense_minor": 10000,
        "description": "快捷支付 / 合成生活用品商店",
        "fields": {},
    }
    event.update(changes)
    return event


class PurchaseReferenceEnrichmentTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory(prefix="purchase-reference-test-")
        self.addCleanup(directory.cleanup)
        path = Path(directory.name) / "synthetic-source.xls"
        content = b"synthetic hash source only; reader is mocked\n"
        path.write_bytes(content)
        self.source = {"archived_path": str(path), "sha256": hashlib.sha256(content).hexdigest()}

    def match(self, records, events, prior_matches=()):
        before_records, before_events, before_prior = deepcopy((records, events, prior_matches))
        actual_deduplicate = enrichment.deduplicate_details
        with patch.object(enrichment, "read_purchase_details", return_value=records) as read, \
                patch.object(enrichment, "deduplicate_details", wraps=actual_deduplicate) as deduplicate:
            result = enrichment.purchase_reference_enrichments([self.source], prior_matches, events)
            read.assert_called_once_with(Path(self.source["archived_path"]))
            deduplicate.assert_called_once_with(records)
        self.assertEqual(records, before_records)
        self.assertEqual(events, before_events)
        self.assertEqual(prior_matches, before_prior)
        self.assertTrue(all(match["id"] in {event["id"] for event in events} for match in result["matches"]))
        return result

    def test_existing_transaction_id_bridges_previously_matched_evidence(self):
        event = synthetic_event(description="仅含银行通道摘要", date="2026-01-20")
        prior = [{"id": event["id"], "evidence": {"交易订单号": "synthetic-order-1"}}]
        result = self.match([synthetic_order(**{"支付渠道": "未列明"})], [event], prior)
        self.assertEqual([match["id"] for match in result["matches"]], [event["id"]])
        self.assertIn("交易号", result["matches"][0]["match_basis"])

    def test_existing_alipay_event_transaction_id_can_bridge(self):
        event = synthetic_event(family="alipay", description="未补充商品的通道记录",
                                fields={"交易订单号": "synthetic-order-1"})
        result = self.match([synthetic_order()], [event])
        self.assertEqual([match["id"] for match in result["matches"]], [event["id"]])

    def test_explicit_discount_allows_gross_or_net_payment(self):
        record = synthetic_order(discount_minor=1200, **{"优惠(元)": "12.00"})
        for amount in (10000, 8800):
            with self.subTest(amount=amount):
                result = self.match([record], [synthetic_event(amount_minor=amount, expense_minor=amount)])
                self.assertEqual(len(result["matches"]), 1)
                self.assertEqual(result["matches"][0]["evidence"]["amount_minor"], 10000)

    def test_unstated_discount_does_not_explain_amount_difference(self):
        result = self.match([synthetic_order()], [synthetic_event(amount_minor=8800, expense_minor=8800)])
        self.assertEqual(result["matches"], [])

    def test_closed_and_uncompleted_orders_are_only_references(self):
        for status in ("关闭", "交易关闭", "等待买家付款"):
            with self.subTest(status=status):
                result = self.match([synthetic_order(**{"交易状态": status})], [synthetic_event()])
                self.assertEqual(result["matches"], [])
                self.assertIn("关闭或未完成", result["unresolved"][0]["reason"])

    def test_sell_or_unknown_direction_orders_are_only_references(self):
        for direction in ("卖出", "", "未知"):
            with self.subTest(direction=direction):
                result = self.match([synthetic_order(**{"买卖标志": direction})], [synthetic_event()])
                self.assertEqual(result["matches"], [])
                self.assertIn("不是买入", result["unresolved"][0]["reason"])

    def test_prior_reference_to_missing_event_is_explicitly_rejected(self):
        prior = [{"id": "missing-synthetic-event", "evidence": {"交易订单号": "synthetic-order-1"}}]
        with self.assertRaisesRegex(ValueError, "missing event"):
            self.match([synthetic_order()], [synthetic_event()], prior)

    def test_success_and_pending_receipt_are_supported(self):
        for status in ("成功", "待确认收货"):
            with self.subTest(status=status):
                self.assertEqual(len(self.match(
                    [synthetic_order(**{"交易状态": status})], [synthetic_event()]
                )["matches"]), 1)

    def test_order_without_existing_payment_never_creates_a_flow(self):
        result = self.match([synthetic_order()], [])
        self.assertEqual(result["matches"], [])
        self.assertEqual(result["unique_orders"], 1)
        self.assertIn("未新增费用", result["unresolved"][0]["reason"])

    def test_fallback_requires_full_merchant_channel_and_date_window(self):
        for family, channel in (("cmb_credit", "快捷支付-信用卡"), ("cmb_debit", "快捷支付-借记卡")):
            for date in ("2026-01-08", "2026-01-10", "2026-01-12"):
                with self.subTest(family=family, date=date):
                    result = self.match([synthetic_order(**{"支付渠道": channel})],
                                        [synthetic_event(family=family, date=date)])
                    self.assertEqual(len(result["matches"]), 1)
                    self.assertIn("完整商户名", result["matches"][0]["match_basis"])

    def test_wrong_channel_date_scope_or_nonexpense_cannot_match(self):
        for changes in (
            {"family": "cmb_debit"}, {"date": "2026-01-07"}, {"date": "2026-01-13"},
            {"in_scope": 0}, {"expense_minor": 0}, {"expense_minor": -10000},
        ):
            with self.subTest(changes=changes):
                self.assertEqual(self.match([synthetic_order()], [synthetic_event(**changes)])["matches"], [])

    def test_same_amount_different_or_incomplete_merchant_is_rejected(self):
        for description in ("另一家合成商店", "合成生活用品", "支付宝快捷支付"):
            with self.subTest(description=description):
                self.assertEqual(self.match(
                    [synthetic_order()], [synthetic_event(description=description)]
                )["matches"], [])

    def test_generic_channel_name_is_not_a_full_merchant_match(self):
        for merchant in ("支付宝", "微信支付", "平台商户", "特约商户"):
            with self.subTest(merchant=merchant):
                result = self.match([synthetic_order(**{"对方名称": merchant})],
                                    [synthetic_event(description=f"快捷支付 / {merchant}")])
                self.assertEqual(result["matches"], [])

    def test_one_order_matching_multiple_payments_is_rejected(self):
        events = [synthetic_event(id=f"synthetic-event-{number}") for number in (1, 2)]
        self.assertEqual(self.match([synthetic_order()], events)["matches"], [])

    def test_multiple_orders_matching_one_payment_are_all_rejected(self):
        orders = [synthetic_order(**{"支付宝交易号": f"synthetic-order-{number}"}) for number in (1, 2)]
        result = self.match(orders, [synthetic_event()])
        self.assertEqual(result["matches"], [])
        self.assertEqual(len(result["unresolved"]), 2)
        self.assertTrue(all("多订单" in row["reason"] for row in result["unresolved"]))

    def test_gross_and_discount_net_each_matching_payment_is_ambiguous(self):
        record = synthetic_order(discount_minor=1200, **{"优惠(元)": "12.00"})
        events = [synthetic_event(), synthetic_event(id="synthetic-event-2", amount_minor=8800, expense_minor=8800)]
        self.assertEqual(self.match([record], events)["matches"], [])

    def test_transaction_id_bridge_with_wrong_amount_does_not_fallback_elsewhere(self):
        events = [synthetic_event(amount_minor=9999), synthetic_event(id="synthetic-event-2")]
        prior = [{"id": "synthetic-event-1", "evidence": {"交易订单号": "synthetic-order-1"}}]
        self.assertEqual(self.match([synthetic_order()], events, prior)["matches"], [])

    def test_identical_duplicate_order_is_one_reference_with_source_counts(self):
        result = self.match([synthetic_order(), synthetic_order(source_row=9)], [synthetic_event()])
        self.assertEqual((result["source_rows"], result["unique_orders"], result["duplicate_rows"]), (2, 1, 1))
        self.assertEqual(len(result["matches"]), 1)
        self.assertEqual(len(result["records"][0]["source_evidence"]), 2)

    def test_same_order_with_conflicting_amount_is_rejected(self):
        orders = [synthetic_order(), synthetic_order(amount_minor=10100, **{"订单金额(元)": "101.00"})]
        with self.assertRaisesRegex(ValueError, "Conflicting purchase details"):
            self.match(orders, [synthetic_event()])

    def test_cumulative_refund_does_not_create_or_match_a_new_refund(self):
        record = synthetic_order(refund_minor=2000, **{"累计退款总额(元)": "20.00"})
        purchase = synthetic_event()
        refund = synthetic_event(id="synthetic-refund", amount_minor=2000, expense_minor=-2000)
        result = self.match([record], [purchase, refund])
        self.assertEqual([match["id"] for match in result["matches"]], [purchase["id"]])
        self.assertEqual(result["matches"][0]["evidence"]["refund_minor"], 2000)
        self.assertEqual(self.match([record], [refund])["matches"], [])
        self.assertEqual(self.match([record], [synthetic_event(amount_minor=8000, expense_minor=8000)])["matches"], [])

    def test_changed_source_hash_is_rejected_before_reader_runs(self):
        self.source["sha256"] = "incorrect"
        with patch.object(enrichment, "read_purchase_details") as read, \
                patch.object(enrichment, "deduplicate_details") as deduplicate:
            with self.assertRaisesRegex(ValueError, "hash changed"):
                enrichment.purchase_reference_enrichments([self.source], [], [])
            read.assert_not_called()
            deduplicate.assert_not_called()


if __name__ == "__main__":
    unittest.main()
