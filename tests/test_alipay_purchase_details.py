"""Synthetic SpreadsheetML tests; no private source files are opened or created."""

from copy import deepcopy
import hashlib
import importlib.util
from pathlib import Path
import sys
import unittest
from unittest.mock import patch
import xml.etree.ElementTree as ET


sys.dont_write_bytecode = True
SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "alipay_purchase_details.py"
SPEC = importlib.util.spec_from_file_location("purchase_details_under_test", SCRIPT)
details = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(details)

TAG = "{" + details.SPREADSHEET_NS + "}"
HEADERS = ["序号", *details.REQUIRED_HEADERS, "对方账户", "费率", "服务费(元)"]


def purchase(**changes):
    result = {
        "序号": "1", "创建时间": "2026-01-10 12:00:00", "商户订单号": "000123456789",
        "商品名称": "合成商品", "订单金额(元)": "100.00", "交易状态": "交易成功（原文）",
        "对方名称": "合成商户", "支付宝交易号": "000987654321", "买卖标志": "买入",
        "优惠(元)": "", "累计退款总额(元)": "0.00", "支付渠道": "合成银行信用卡(1111)",
        "银行订单号": "000001234567", "对方账户": "synthetic@example.invalid",
        "费率": "", "服务费(元)": "0.00",
    }
    result.update(changes)
    return result


def add_row(table, values, *, index=None, sparse=False, formula_column=None):
    row = ET.SubElement(table, TAG + "Row")
    if index is not None:
        row.set(TAG + "Index", str(index))
    for column, value in enumerate(values, 1):
        if sparse and value == "":
            continue
        cell = ET.SubElement(row, TAG + "Cell")
        if sparse:
            cell.set(TAG + "Index", str(column))
        if formula_column == column:
            cell.set(TAG + "Formula", "=1+1")
        data = ET.SubElement(cell, TAG + "Data", {TAG + "Type": "String"})
        data.text = value
    return row


def xml_source(records=None, *, headers=None, sparse=False, footer=True, first_data_index=None):
    headers = HEADERS if headers is None else headers
    records = [purchase()] if records is None else records
    root = ET.Element(TAG + "Workbook")
    table = ET.SubElement(ET.SubElement(root, TAG + "Worksheet"), TAG + "Table")
    add_row(table, ["合成支付宝购买明细"])
    add_row(table, ["查询期间", "2026-01-01 至 2026-01-31"])
    add_row(table, headers)
    for number, record in enumerate(records):
        add_row(
            table, [record.get(field, "") for field in headers], sparse=sparse,
            index=first_data_index if number == 0 else None,
        )
    if footer:
        add_row(table, ["#导出时间：2026-02-01 12:00:00"])
    return root


class PurchaseDetailsTests(unittest.TestCase):
    def parse(self, root, path="/synthetic-only/purchase-details.xls"):
        raw = ET.tostring(root, encoding="utf-8", xml_declaration=True)
        with patch.object(Path, "read_bytes", return_value=raw) as read_bytes, \
                patch.object(Path, "read_text", side_effect=AssertionError("No finance reads")):
            result = details.read_purchase_details(path)
        read_bytes.assert_called_once_with()
        return result, raw

    def test_original_strings_status_amounts_and_provenance_are_retained(self):
        source = purchase(**{"交易状态": " 交易成功（保持状态原文） "})
        records, raw = self.parse(xml_source([source]))
        self.assertEqual(len(records), 1)
        record = records[0]
        self.assertEqual({key: record[key] for key in source}, source)
        self.assertEqual(record["source_row"], 4)
        self.assertEqual(record["source_file"], "purchase-details.xls")
        self.assertEqual(record["source_sha256"], hashlib.sha256(raw).hexdigest())
        self.assertEqual(record["amount_minor"], 10000)
        self.assertEqual(record["refund_minor"], 0)
        self.assertIsNone(record["discount_minor"])

    def test_empty_table_null_is_ignored_without_changing_source_row(self):
        root = xml_source()
        table = root.find("ss:Worksheet/ss:Table", details.NS)
        table.insert(0, ET.Element(TAG + "Null"))
        record = self.parse(root)[0][0]
        self.assertEqual(record["source_row"], 4)
        self.assertEqual(record["amount_minor"], 10000)

    def test_table_null_with_attributes_text_or_children_is_rejected(self):
        for kind in ("attributes", "text", "children"):
            root = xml_source()
            table = root.find("ss:Worksheet/ss:Table", details.NS)
            null = ET.Element(TAG + "Null")
            if kind == "attributes":
                null.set(TAG + "Index", "1")
            elif kind == "text":
                null.text = "unexpected content"
            else:
                ET.SubElement(null, TAG + "Cell")
            table.insert(0, null)
            with self.subTest(kind=kind), self.assertRaisesRegex(ValueError, "Null"):
                self.parse(root)

    def test_nondefault_table_offsets_are_rejected(self):
        for attribute in ("TopCell", "LeftCell"):
            root = xml_source()
            table = root.find("ss:Worksheet/ss:Table", details.NS)
            table.set(TAG + attribute, "10")
            with self.subTest(attribute=attribute), self.assertRaisesRegex(ValueError, "offsets"):
                self.parse(root)
        root = xml_source()
        table = root.find("ss:Worksheet/ss:Table", details.NS)
        table.set(TAG + "TopCell", "1")
        table.set(TAG + "LeftCell", "1")
        self.assertEqual(self.parse(root)[0][0]["source_row"], 4)

    def test_sparse_cells_and_rows_keep_positions_and_leading_zeros(self):
        record = self.parse(xml_source(sparse=True, first_data_index=8))[0][0]
        self.assertEqual(record["source_row"], 8)
        self.assertEqual(record["优惠(元)"], "")
        self.assertIsNone(record["discount_minor"])
        self.assertEqual(record["银行订单号"], "000001234567")
        self.assertEqual(record["支付宝交易号"], "000987654321")
        self.assertEqual(record["refund_minor"], 0)

    def test_explicit_zero_discount_is_distinct_from_blank(self):
        records, _ = self.parse(xml_source([
            purchase(), purchase(**{"支付宝交易号": "0000002", "优惠(元)": "0.00"}),
        ]))
        self.assertIsNone(records[0]["discount_minor"])
        self.assertEqual(records[1]["discount_minor"], 0)

    def test_decimal_conversion_is_exact_and_preserves_source_text(self):
        original = purchase(**{"订单金额(元)": "1,234.50", "优惠(元)": "0.01", "累计退款总额(元)": "-2.30"})
        record = self.parse(xml_source([original]))[0][0]
        self.assertEqual(record["amount_minor"], 123450)
        self.assertEqual(record["discount_minor"], 1)
        self.assertEqual(record["refund_minor"], -230)
        self.assertEqual(record["订单金额(元)"], "1,234.50")

    def test_subcent_or_invalid_values_fail_in_each_money_field(self):
        for field in ("订单金额(元)", "优惠(元)", "累计退款总额(元)"):
            for value in ("0.001", "NaN", "Infinity", "1e2", "12,34.50", "￥100"):
                with self.subTest(field=field, value=value), self.assertRaises(ValueError):
                    self.parse(xml_source([purchase(**{field: value})]))

    def test_long_decimal_does_not_round_away_fractional_cents(self):
        amount = "123456789012345678901234567890.001"
        with self.assertRaisesRegex(ValueError, "sub-cent"):
            self.parse(xml_source([purchase(**{"订单金额(元)": amount})]))

    def test_empty_required_amount_or_refund_fails(self):
        for field in ("订单金额(元)", "累计退款总额(元)"):
            with self.subTest(field=field), self.assertRaises(ValueError):
                self.parse(xml_source([purchase(**{field: ""})]))

    def test_missing_or_whitespace_transaction_id_fails(self):
        for identifier in ("", "   "):
            with self.subTest(identifier=identifier), self.assertRaisesRegex(ValueError, "transaction ID"):
                self.parse(xml_source([purchase(**{"支付宝交易号": identifier})], sparse=True))

    def test_all_required_headers_can_be_reordered_optional_fields_absent(self):
        headers = list(reversed(details.REQUIRED_HEADERS))
        record = self.parse(xml_source(headers=headers))[0][0]
        self.assertEqual(record["支付宝交易号"], "000987654321")
        self.assertEqual(record["amount_minor"], 10000)
        self.assertNotIn("序号", record)

    def test_duplicate_unknown_missing_or_repeated_header_fails(self):
        variants = [HEADERS + ["商品名称"], HEADERS + ["未知字段"], HEADERS[2:]]
        for headers in variants:
            with self.subTest(headers=headers), self.assertRaises(ValueError):
                self.parse(xml_source(headers=headers))
        root = xml_source(footer=False)
        add_row(root.find("ss:Worksheet/ss:Table", details.NS), HEADERS)
        with self.assertRaisesRegex(ValueError, "header"):
            self.parse(root)

    def test_formula_in_data_or_metadata_fails(self):
        for row_index in (0, 3):
            root = xml_source()
            rows = root.findall("ss:Worksheet/ss:Table/ss:Row", details.NS)
            rows[row_index][0].set(TAG + "Formula", "=1+1")
            with self.subTest(row_index=row_index), self.assertRaisesRegex(ValueError, "Formulas"):
                self.parse(root)

    def test_formula_like_plain_text_remains_inert_original_text(self):
        record = self.parse(xml_source([purchase(**{"商品名称": "=literal text"})]))[0][0]
        self.assertEqual(record["商品名称"], "=literal text")

    def test_only_explicit_footer_is_ignored_and_data_cannot_follow_footer(self):
        for footer in ("#导出时间：2026-02-01", "说明：仅供核对", "注：合成说明"):
            root = xml_source(footer=False)
            add_row(root.find("ss:Worksheet/ss:Table", details.NS), [footer])
            with self.subTest(footer=footer):
                self.assertEqual(len(self.parse(root)[0]), 1)
        root = xml_source(footer=False)
        table = root.find("ss:Worksheet/ss:Table", details.NS)
        add_row(table, ["2026-01-11 12:00:00"])
        with self.assertRaises(ValueError):
            self.parse(root)
        root = xml_source()
        table = root.find("ss:Worksheet/ss:Table", details.NS)
        add_row(table, [purchase().get(field, "") for field in HEADERS])
        with self.assertRaisesRegex(ValueError, "after the footer"):
            self.parse(root)

    def test_unknown_namespace_multiple_sheets_and_invalid_cell_indices_fail(self):
        with self.assertRaisesRegex(ValueError, "Workbook"):
            self.parse(ET.Element("Workbook"))
        root = xml_source()
        ET.SubElement(root, TAG + "Worksheet")
        with self.assertRaisesRegex(ValueError, "worksheet"):
            self.parse(root)
        for value in ("0", "-1", "1.5", "1", "16385"):
            root = xml_source()
            row = root.findall("ss:Worksheet/ss:Table/ss:Row", details.NS)[3]
            row[1].set(TAG + "Index", value)
            with self.subTest(index=value), self.assertRaisesRegex(ValueError, "index"):
                self.parse(root)

    def test_merged_data_and_extra_columns_fail(self):
        root = xml_source()
        row = root.findall("ss:Worksheet/ss:Table/ss:Row", details.NS)[3]
        row[0].set(TAG + "MergeAcross", "1")
        with self.assertRaises(ValueError):
            self.parse(root)
        root = xml_source()
        row = root.findall("ss:Worksheet/ss:Table/ss:Row", details.NS)[3]
        extra = ET.SubElement(row, TAG + "Cell")
        ET.SubElement(extra, TAG + "Data", {TAG + "Type": "String"}).text = "未知多列"
        with self.assertRaises(ValueError):
            self.parse(root)

    def test_identical_ids_merge_all_sources_without_mutating_inputs(self):
        first = self.parse(xml_source())[0][0]
        second = deepcopy(first)
        second.update({"序号": "8", "source_file": "another-synthetic.xls", "source_row": 22, "source_sha256": "synthetic-other-hash"})
        originals = deepcopy([first, second, first])
        merged = details.deduplicate_details([first, second, first])
        self.assertEqual([first, second, first], originals)
        self.assertEqual(len(merged), 1)
        self.assertEqual(merged[0]["序号"], "1")
        self.assertEqual(len(merged[0]["source_evidence"]), 2)
        self.assertEqual({item["source_row"] for item in merged[0]["source_evidence"]}, {4, 22})
        self.assertEqual(details.deduplicate_details(merged), merged)

    def test_same_id_conflicts_are_rejected_even_if_one_source_is_newer(self):
        first = self.parse(xml_source())[0][0]
        for field, changed in (("商品名称", "不同合成商品"), ("累计退款总额(元)", "1.00"), ("交易状态", "交易关闭"), ("discount_minor", 0)):
            second = deepcopy(first)
            second.update({field: changed, "source_file": "newer-synthetic.xls"})
            with self.subTest(field=field), self.assertRaisesRegex(ValueError, "Conflicting"):
                details.deduplicate_details([first, second])

    def test_different_ids_with_same_amount_are_not_deduplicated(self):
        first = self.parse(xml_source())[0][0]
        second = deepcopy(first)
        second["支付宝交易号"] = "000987654322"
        self.assertEqual(len(details.deduplicate_details([first, second])), 2)

    def test_deduplication_requires_id_and_complete_provenance(self):
        first = self.parse(xml_source())[0][0]
        for removed in ("支付宝交易号", "source_row", "source_sha256"):
            record = deepcopy(first)
            del record[removed]
            with self.subTest(removed=removed), self.assertRaises(ValueError):
                details.deduplicate_details([record])


if __name__ == "__main__":
    unittest.main()
