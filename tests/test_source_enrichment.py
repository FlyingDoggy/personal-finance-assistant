"""Source enrichment checks with entirely synthetic CSV returned by a mock.

No source files are created or read. All merchants, account tails, transactions,
paths, hashes, and CSV bytes below are invented fixtures.
"""

import csv
import importlib.util
import io
from pathlib import Path
import sys
import unittest
from unittest.mock import patch


sys.dont_write_bytecode = True
SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "source_enrichment.py"
SPEC = importlib.util.spec_from_file_location("source_enrichment_under_test", SCRIPT)
enrichment = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(enrichment)


def synthetic_source(number=1):
    return {
        "source_family": "alipay",
        "filename": f"synthetic-channel-{number}.csv",
        "archived_path": f"/synthetic-only/channel-{number}.csv",
        "sha256": f"synthetic-hash-{number}",
    }


def synthetic_row(**changes):
    row = {
        "交易时间": "2026-01-10 12:00:00",
        "交易对方": "合成家居门店",
        "商品说明": "合成商品说明",
        "收/支": "支出",
        "金额": "100.00",
        "收/付款方式": "招商银行信用卡(1111)",
    }
    row.update(changes)
    return row


def synthetic_event(**changes):
    event = {
        "id": "synthetic-event-1",
        "in_scope": 1,
        "family": "cmb_credit",
        "account_last4": "1111",
        "date": "2026-01-10",
        "amount_minor": 10000,
        "expense_minor": 10000,
        "description": "合成银行交易",
    }
    event.update(changes)
    return event


def csv_bytes(rows):
    output = io.StringIO()
    writer = csv.DictWriter(output, fieldnames=(
        "交易时间", "交易对方", "商品说明", "收/支", "金额", "收/付款方式"
    ))
    writer.writeheader()
    writer.writerows(rows)
    return output.getvalue().encode("utf-8-sig")


class SourceEnrichmentTests(unittest.TestCase):
    def matches(self, rows, events, sources=None):
        sources = [synthetic_source()] if sources is None else sources
        with patch.object(Path, "read_bytes", return_value=csv_bytes(rows)) as read_bytes, \
                patch.object(Path, "read_text", side_effect=AssertionError("No finance reads")):
            result = enrichment.alipay_enrichments({"source_files": sources}, events)
            self.assertEqual(read_bytes.call_count, len(sources))
        return result

    def test_exact_date_amount_card_and_bank_match_without_changing_event(self):
        event = synthetic_event()
        before = dict(event)

        matches = self.matches([synthetic_row()], [event])

        self.assertEqual(len(matches), 1)
        self.assertEqual(matches[0]["id"], event["id"])
        self.assertEqual(matches[0]["evidence"]["交易对方"], "合成家居门店")
        self.assertEqual(matches[0]["evidence"]["minor"], 10000)
        self.assertEqual(matches[0]["source_sha256"], "synthetic-hash-1")
        self.assertEqual(event, before)

    def test_amount_or_card_mismatch_is_rejected(self):
        for changes in ({"金额": "100.01"}, {"收/付款方式": "招商银行信用卡(2222)"}):
            with self.subTest(changes=changes):
                self.assertEqual(self.matches([synthetic_row(**changes)], [synthetic_event()]), [])

    def test_card_tail_requires_the_complete_tail_in_parentheses(self):
        for method in ("招商银行信用卡(11110)", "招商银行信用卡(01111)"):
            with self.subTest(method=method):
                self.assertEqual(self.matches(
                    [synthetic_row(**{"收/付款方式": method})], [synthetic_event()]
                ), [])

    def test_date_outside_window_is_rejected_even_with_matching_merchant(self):
        event = synthetic_event(date="2026-01-13", description="合成家居门店")
        self.assertEqual(self.matches([synthetic_row()], [event]), [])

    def test_two_source_candidates_are_not_arbitrarily_selected(self):
        matches = self.matches(
            [synthetic_row()], [synthetic_event()],
            sources=[synthetic_source(1), synthetic_source(2)],
        )
        self.assertEqual(matches, [])

    def test_one_source_row_matching_two_bank_events_rejects_both(self):
        events = [synthetic_event(id=f"synthetic-event-{number}") for number in (1, 2)]
        self.assertEqual(self.matches([synthetic_row()], events), [])

    def test_one_day_difference_requires_complete_merchant_name(self):
        for event_date in ("2026-01-09", "2026-01-11"):
            with self.subTest(date=event_date):
                event = synthetic_event(date=event_date, description="快捷支付 / 合成家居门店")
                matches = self.matches([synthetic_row()], [event])
                self.assertEqual(len(matches), 1)
                self.assertIn("商户完整名称", matches[0]["match_basis"])

        for description in ("合成家居", "另一合成门店", "支付宝快捷支付"):
            with self.subTest(description=description):
                event = synthetic_event(date="2026-01-11", description=description)
                self.assertEqual(self.matches([synthetic_row()], [event]), [])

    def test_channel_names_do_not_count_as_merchant_evidence_for_date_difference(self):
        for channel in ("支付宝", "微信", "微信支付", "财付通", "平台商户", "特约商户"):
            with self.subTest(channel=channel):
                row = synthetic_row(**{"交易对方": channel})
                event = synthetic_event(date="2026-01-11", description=f"快捷支付 / {channel}")
                self.assertEqual(self.matches([row], [event]), [])

    def test_different_bank_is_rejected_even_when_date_amount_and_card_match(self):
        for event_date in ("2026-01-10", "2026-01-11"):
            with self.subTest(date=event_date):
                row = synthetic_row(**{"收/付款方式": "中信银行信用卡(1111)"})
                event = synthetic_event(date=event_date, description="合成家居门店")
                self.assertEqual(self.matches([row], [event]), [])


if __name__ == "__main__":
    unittest.main()
