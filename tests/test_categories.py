"""Synthetic-only checks for category rules and refund category inheritance.

This module imports only the processing script. It never calls main(), loads a
configuration, or opens financial sources. Every transaction fixture is invented.
"""

import importlib.util
from pathlib import Path
import sys
import unittest
from unittest.mock import patch


sys.dont_write_bytecode = True
SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "process_finance.py"
SPEC = importlib.util.spec_from_file_location("finance_category_rules_under_test", SCRIPT)
processing = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(processing)


def synthetic_purchase(*, code=5009):
    return {
        "id": "synthetic-purchase",
        "event": "消费",
        "code": code,
        "amount_minor": 10000,
        "expense_minor": 10000,
        "income_minor": 0,
        "cash_minor": 0,
        "debt_delta_minor": 10000,
        "related_id": None,
        "link_type": None,
        "review": "科目待补" if code == 5016 else "人工修订",
        "behavior": "待确认" if code == 5016 else "用途已识别，消费性质待确认",
        "reason": "合成原单最终分类",
        "reimb": "未确认",
    }


def synthetic_refund(*, linked=True, code=5016):
    return {
        "id": "synthetic-refund",
        "event": "消费退款",
        "code": code,
        "amount_minor": 4000,
        "expense_minor": -4000,
        "income_minor": 0,
        "cash_minor": 0,
        "debt_delta_minor": -4000,
        "related_id": "synthetic-purchase" if linked else None,
        "link_type": "退款对应原消费" if linked else None,
        "review": "科目待补" if code == 5016 else "自动通过",
        "behavior": "待确认" if code == 5016 else "用途已识别，消费性质待确认",
        "reason": "合成退款分类",
        "reimb": "未确认",
    }


def amounts(event):
    return tuple(event[key] for key in (
        "amount_minor", "expense_minor", "income_minor", "cash_minor", "debt_delta_minor"
    ))


class CategoryTests(unittest.TestCase):
    def setUp(self):
        for target in (
            "pathlib.Path.read_text",
            "pathlib.Path.read_bytes",
            "sqlite3.connect",
        ):
            guard = patch(target, side_effect=AssertionError("Category tests forbid finance I/O"))
            guard.start()
            self.addCleanup(guard.stop)

    def test_food_supermarkets_and_convenience_stores_are_dining(self):
        for merchant in ("盒马", "沃尔玛", "山姆", "7-11", "柒一拾壹", "果蔬好", "便利店"):
            with self.subTest(merchant=merchant):
                self.assertEqual(processing.classify_category(merchant)[0], 5001)

    def test_confirmed_home_merchants_are_household(self):
        for merchant in ("MUJI", "宜家"):
            with self.subTest(merchant=merchant):
                self.assertEqual(processing.classify_category(merchant)[0], 5002)

    def test_explicit_housekeeping_service_overrides_supermarket_default(self):
        self.assertEqual(processing.classify_purchase("盒马", "12次保洁服务")[0], 5002)

    def test_pet_supermarket_is_pet_expense(self):
        self.assertEqual(processing.classify_category("宠物超市")[0], 5007)

    def test_appliances_and_hardware_are_not_food_transport_or_subscriptions(self):
        for product in ("烧水壶", "电烤箱", "充电器", "GOOGLE PIXEL手机"):
            with self.subTest(product=product):
                self.assertEqual(processing.classify_category(product)[0], 5009)

    def test_shoe_cabinet_is_household(self):
        self.assertEqual(processing.classify_category("鞋柜")[0], 5002)

    def test_vehicle_care_is_transport(self):
        self.assertEqual(processing.classify_category("汽车护理服务")[0], 5004)

    def test_named_google_subscription_is_digital_service(self):
        self.assertEqual(processing.classify_category("Google One订阅")[0], 5010)

    def test_payment_channels_and_generic_platforms_do_not_supply_a_category(self):
        for description in ("支付宝", "微信", "财付通", "平台商户", "北京钱袋宝"):
            with self.subTest(description=description):
                self.assertEqual(processing.classify_category(description)[0], 5016)

    def test_linked_partial_refund_inherits_original_category_without_changing_amounts(self):
        purchase = synthetic_purchase(code=5009)
        refund = synthetic_refund()
        before = [amounts(purchase), amounts(refund)]

        # Relationship lookup must not depend on event ordering.
        processing.inherit_refund_categories([refund, purchase])

        self.assertEqual(refund["code"], 5009)
        self.assertEqual(refund["related_id"], purchase["id"])
        self.assertEqual([amounts(purchase), amounts(refund)], before)

    def test_refund_follows_original_category_after_manual_reclassification(self):
        purchase = synthetic_purchase(code=5009)
        refund = synthetic_refund()
        processing.inherit_refund_categories([purchase, refund])
        self.assertEqual(refund["code"], 5009)

        purchase.update(code=5002, review="人工修订", reason="合成用户修订原单用途")
        before = [amounts(purchase), amounts(refund)]
        processing.inherit_refund_categories([purchase, refund])

        self.assertEqual(refund["code"], 5002)
        self.assertEqual([amounts(purchase), amounts(refund)], before)

    def test_unlinked_refund_is_not_assigned_an_unrelated_purchase_category(self):
        purchase = synthetic_purchase(code=5009)
        refund = synthetic_refund(linked=False, code=5001)
        before = dict(refund)

        processing.inherit_refund_categories([purchase, refund])

        self.assertEqual(refund, before)

    def test_missing_original_does_not_change_a_refund(self):
        refund = synthetic_refund(code=5001)
        before = dict(refund)

        processing.inherit_refund_categories([refund])

        self.assertEqual(refund, before)

    def test_unclassified_original_keeps_linked_refund_awaiting_classification(self):
        purchase = synthetic_purchase(code=5016)
        refund = synthetic_refund(code=5009)
        before = [amounts(purchase), amounts(refund)]

        processing.inherit_refund_categories([purchase, refund])

        self.assertEqual(refund["code"], 5016)
        self.assertIn("待", refund["review"])
        self.assertEqual([amounts(purchase), amounts(refund)], before)

    def test_nonnegative_event_does_not_inherit_a_refund_category(self):
        purchase = synthetic_purchase(code=5009)
        refund = synthetic_refund(code=5001)
        refund.update(expense_minor=4000, debt_delta_minor=4000)
        before = dict(refund)

        processing.inherit_refund_categories([purchase, refund])

        self.assertEqual(refund, before)


if __name__ == "__main__":
    unittest.main()
