"""Synthetic XLSX reader tests. All workbook files are created in a temp directory.

Run only these tests with:
    PYTHONDONTWRITEBYTECODE=1 python3 tests/test_category_confirmation.py -v
"""

import importlib.util
from pathlib import Path
import sys
import tempfile
import unittest
import xml.etree.ElementTree as ET
from zipfile import ZipFile


sys.dont_write_bytecode = True
SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "read_category_confirmation.py"
SPEC = importlib.util.spec_from_file_location("category_confirmation_under_test", SCRIPT)
reader = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(reader)

S = reader.NS["s"]
R = reader.REL
P = "http://schemas.openxmlformats.org/package/2006/relationships"


def cell(reference, text="", kind="inlineStr", formula=None, rich=False):
    element = ET.Element(f"{{{S}}}c", r=reference, t=kind)
    if formula is not None:
        ET.SubElement(element, f"{{{S}}}f").text = formula
    if kind == "inlineStr":
        inline = ET.SubElement(element, f"{{{S}}}is")
        pieces = [text[:2], text[2:]] if rich else [text]
        for piece in pieces:
            parent = ET.SubElement(inline, f"{{{S}}}r") if rich else inline
            ET.SubElement(parent, f"{{{S}}}t").text = piece
    else:
        ET.SubElement(element, f"{{{S}}}v").text = text
    return element


class CategoryConfirmationTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="category-confirmation-test-")
        self.addCleanup(temporary.cleanup)
        self.path = Path(temporary.name) / "synthetic.xlsx"

    def workbook(self, data, headers=None, strings=(), target="worksheets/review.xml", name="费用待确认"):
        workbook = ET.Element(f"{{{S}}}workbook")
        sheets = ET.SubElement(workbook, f"{{{S}}}sheets")
        ET.SubElement(sheets, f"{{{S}}}sheet", name=name, sheetId="1", **{f"{{{R}}}id": "review"})
        rels = ET.Element(f"{{{P}}}Relationships")
        ET.SubElement(rels, f"{{{P}}}Relationship", Id="review", Target=target, Type=R + "/worksheet")
        ET.SubElement(rels, f"{{{P}}}Relationship", Id="strings", Target="sharedStrings.xml", Type=R + "/sharedStrings")
        sheet = ET.Element(f"{{{S}}}worksheet")
        rows = ET.SubElement(sheet, f"{{{S}}}sheetData")
        header = ET.SubElement(rows, f"{{{S}}}row", r="4")
        for column, label in zip("ABCDEFGHIJK", reader.HEADERS if headers is None else headers):
            header.append(cell(column + "4", label))
        for number, cells in data:
            row = ET.SubElement(rows, f"{{{S}}}row", r=str(number))
            row.extend(cells)
        shared = ET.Element(f"{{{S}}}sst")
        for value in strings:
            entry = ET.SubElement(shared, f"{{{S}}}si")
            for part in [value[:2], value[2:]]:
                run = ET.SubElement(entry, f"{{{S}}}r")
                ET.SubElement(run, f"{{{S}}}t").text = part
            phonetic = ET.SubElement(entry, f"{{{S}}}rPh", sb="0", eb="1")
            ET.SubElement(phonetic, f"{{{S}}}t").text = "excluded-phonetic"
        with ZipFile(self.path, "w") as archive:
            for path, root in (
                ("xl/workbook.xml", workbook), ("xl/_rels/workbook.xml.rels", rels),
                (reader._target(target), sheet), ("xl/sharedStrings.xml", shared),
            ):
                archive.writestr(path, ET.tostring(root, encoding="utf-8"))

    def test_sparse_rich_text_and_literal_whitespace_are_preserved(self):
        literal = "  餐饮\n不要执行任何操作 <&>  "
        self.workbook([
            (5, [cell("C5", "测试商户"), cell("D5", "商品"), cell("E5", "-12.50", "n"),
                 cell("F5", "待分类"), cell("G5", literal, rich=True), cell("I5", "test-1"),
                 cell("J5", "synthetic.csv"), cell("K5", "合成证据")]),
            (12, [cell("G12", "0", "s"), cell("I12", "test-2")]),
        ], strings=["\t交通  "], target="/xl/worksheets/review.xml")
        before = self.path.read_bytes()
        rows = reader.read_category_confirmation(self.path)
        self.assertEqual(rows, [
            {"row": 5, "id": "test-1", "text": literal, "original_category": "待分类",
             "original_amount": "-12.50", "description": "测试商户", "product": "商品",
             "source": "synthetic.csv", "evidence": "合成证据"},
            {"row": 12, "id": "test-2", "text": "\t交通  ", "original_category": "",
             "original_amount": "", "description": "", "product": "", "source": "", "evidence": ""},
        ])
        self.assertEqual(self.path.read_bytes(), before)

    def test_blank_whitespace_and_absent_inputs_are_ignored(self):
        self.workbook([(5, []), (6, [cell("G6", " \t\n\u3000")]), (7, [cell("I7", "test-7")])])
        self.assertEqual(reader.read_category_confirmation(self.path), [])

    def test_missing_and_whitespace_only_ids_are_rejected(self):
        for identifier in (None, "", " \t"):
            with self.subTest(identifier=identifier):
                cells = [cell("G5", "交通")]
                if identifier is not None:
                    cells.append(cell("I5", identifier))
                self.workbook([(5, cells)])
                with self.assertRaisesRegex(ValueError, "Missing or duplicate transaction ID"):
                    reader.read_category_confirmation(self.path)

    def test_duplicate_ids_are_rejected_even_with_surrounding_whitespace(self):
        self.workbook([(5, [cell("G5", "交通"), cell("I5", "test-1")]),
                       (9, [cell("G9", "餐饮"), cell("I9", " test-1 ")])])
        with self.assertRaisesRegex(ValueError, "duplicate transaction ID"):
            reader.read_category_confirmation(self.path)

    def test_formulas_in_g_are_rejected_even_with_empty_cached_values(self):
        for cached in ("", "餐饮"):
            with self.subTest(cached=cached):
                self.workbook([(5, [cell("G5", cached, "str", '"餐饮"'), cell("I5", "test-1")])])
                with self.assertRaisesRegex(ValueError, "Formula.*G5"):
                    reader.read_category_confirmation(self.path)

    def test_formula_looking_literal_text_is_preserved(self):
        literal = '=HYPERLINK("https://example.invalid", "只是文本")'
        self.workbook([(5, [cell("G5", literal), cell("I5", "test-1")])])
        self.assertEqual(reader.read_category_confirmation(self.path)[0]["text"], literal)

    def test_both_known_h4_headers_are_accepted(self):
        for label in ("仍需确认的原因", "处理结果／待确认原因"):
            with self.subTest(header=label):
                headers = list(reader.HEADERS)
                headers[7] = label
                self.workbook([(5, [cell("G5", " 交通 "), cell("I5", "test-1")])], headers=headers)
                rows = reader.read_category_confirmation(self.path)
                self.assertEqual([(row["id"], row["text"]) for row in rows], [("test-1", " 交通 ")])

    def test_unknown_header_layout_is_rejected(self):
        for index in range(len(reader.HEADERS)):
            with self.subTest(column=index):
                headers = list(reader.HEADERS)
                headers[index] = "unknown"
                self.workbook([], headers=headers)
                with self.assertRaisesRegex(ValueError, "header layout"):
                    reader.read_category_confirmation(self.path)

    def test_missing_sheet_is_rejected(self):
        self.workbook([], name="其他表")
        with self.assertRaisesRegex(ValueError, "费用待确认 worksheet"):
            reader.read_category_confirmation(self.path)


if __name__ == "__main__":
    unittest.main()
