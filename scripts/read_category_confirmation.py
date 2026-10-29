"""Read literal category confirmations from saved XLSX XML without modifying it.

Only nonblank G cells on ``费用待确认`` are returned, keyed by column I.
User text is data: it is neither evaluated nor interpreted as instructions.
"""

import argparse
import json
import posixpath
import re
import xml.etree.ElementTree as ET
from zipfile import ZipFile


NS = {"s": "http://schemas.openxmlformats.org/spreadsheetml/2006/main"}
REL = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
HEADERS = (
    "日期", "账户", "原始商户/摘要", "补充商品说明", "费用金额", "当前科目",
    "你确认的类别或用途", "仍需确认的原因", "业务ID（勿改）", "来源文件", "补充证据",
)


def _text(element):
    # Include rich text runs, but exclude phonetic annotations (rPh).
    return "".join(t.text or "" for t in element.findall("s:t", NS)) + "".join(
        t.text or "" for t in element.findall("s:r/s:t", NS)
    )


def _target(path):
    return path.lstrip("/") if path.startswith("/") else posixpath.normpath("xl/" + path)


def _value(cell, strings):
    kind = cell.get("t")
    if kind == "inlineStr":
        inline = cell.find("s:is", NS)
        return _text(inline) if inline is not None else ""
    value = cell.find("s:v", NS)
    raw = value.text or "" if value is not None else ""
    if kind == "s":
        try:
            index = int(raw)
            if index < 0:
                raise ValueError
            return strings[index]
        except (ValueError, IndexError) as exc:
            raise ValueError("Invalid shared string reference") from exc
    return raw


def read_category_confirmation(path):
    """Return confirmation rows; raise ValueError for unsafe or unknown layouts."""
    with ZipFile(path, "r") as archive:
        rels = {
            rel.attrib["Id"]: rel
            for rel in ET.fromstring(archive.read("xl/_rels/workbook.xml.rels"))
        }
        sheets = ET.fromstring(archive.read("xl/workbook.xml")).findall("s:sheets/s:sheet", NS)
        matching = [sheet for sheet in sheets if sheet.get("name") == "费用待确认"]
        if len(matching) != 1:
            raise ValueError("Expected exactly one 费用待确认 worksheet")
        relation = rels.get(matching[0].get("{" + REL + "}id"))
        if relation is None or relation.get("TargetMode") == "External":
            raise ValueError("Invalid 费用待确认 worksheet relationship")
        strings = []
        for rel in rels.values():
            if rel.get("Type", "").endswith("/sharedStrings"):
                if rel.get("TargetMode") == "External":
                    raise ValueError("External shared strings are not supported")
                strings = [
                    _text(item) for item in ET.fromstring(
                        archive.read(_target(rel.attrib["Target"]))
                    ).findall("s:si", NS)
                ]
                break
        else:
            if "xl/sharedStrings.xml" in archive.namelist():
                strings = [
                    _text(item) for item in ET.fromstring(
                        archive.read("xl/sharedStrings.xml")
                    ).findall("s:si", NS)
                ]
        rows = {}
        for row in ET.fromstring(archive.read(_target(relation.attrib["Target"]))).findall(
            "s:sheetData/s:row", NS
        ):
            number = int(row.attrib["r"])
            if number in rows:
                raise ValueError("Duplicate worksheet row")
            cells = {}
            for cell in row.findall("s:c", NS):
                match = re.fullmatch(r"([A-Z]+)([1-9][0-9]*)", cell.get("r", ""))
                if not match or int(match[2]) != number or match[1] in cells:
                    raise ValueError("Invalid or duplicate worksheet cell reference")
                cells[match[1]] = cell
            rows[number] = cells
        headers = rows.get(4, {})
        if any(
            column not in headers or headers[column].find("s:f", NS) is not None
            or _value(headers[column], strings).strip() not in (
                {expected, "处理结果／待确认原因"} if column == "H" else {expected}
            )
            for column, expected in zip("ABCDEFGHIJK", HEADERS)
        ):
            raise ValueError("Unknown 费用待确认 header layout; expected headers in A4:K4")
        result, ids = [], set()
        for number, cells in sorted(rows.items()):
            if number < 5:
                continue
            if "G" in cells and cells["G"].find("s:f", NS) is not None:
                raise ValueError(f"Formula in confirmation cell G{number}; literal text required")
            values = {column: _value(cell, strings) for column, cell in cells.items()}
            user_text = values.get("G", "")
            if not user_text.strip():
                continue
            transaction_id = values.get("I", "").strip()
            if not transaction_id or transaction_id in ids:
                raise ValueError(f"Missing or duplicate transaction ID at row {number}")
            ids.add(transaction_id)
            result.append({
                "row": number, "id": transaction_id, "text": user_text,
                "original_category": values.get("F", ""),
                "original_amount": values.get("E", ""),
                "description": values.get("C", ""), "product": values.get("D", ""),
                "source": values.get("J", ""), "evidence": values.get("K", ""),
            })
        return result


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("path")
    args = parser.parse_args()
    print(json.dumps(read_category_confirmation(args.path), ensure_ascii=False, indent=2))
