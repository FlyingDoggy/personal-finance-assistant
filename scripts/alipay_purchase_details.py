"""Read Alipay purchase-detail SpreadsheetML without classifying transactions.

Only Excel 2003 XML workbooks with the documented purchase-detail header are
accepted. Original field text is retained, including identifier leading zeros.
No formulas are evaluated, no financial files are written, and no matching or
accounting interpretation is performed.
"""

from copy import deepcopy
from decimal import Decimal, InvalidOperation, localcontext
import hashlib
from pathlib import Path
import re
import xml.etree.ElementTree as ET


SPREADSHEET_NS = "urn:schemas-microsoft-com:office:spreadsheet"
NS = {"ss": SPREADSHEET_NS}
TAG = "{" + SPREADSHEET_NS + "}"
REQUIRED_HEADERS = (
    "创建时间", "商户订单号", "商品名称", "订单金额(元)", "交易状态", "对方名称",
    "支付宝交易号", "买卖标志", "优惠(元)", "累计退款总额(元)", "支付渠道", "银行订单号",
)
OPTIONAL_HEADERS = ("序号", "对方账户", "费率", "服务费(元)")
KNOWN_HEADERS = frozenset(REQUIRED_HEADERS + OPTIONAL_HEADERS)
PROVENANCE_KEYS = frozenset(("source_row", "source_file", "source_sha256"))
MONEY_PATTERN = re.compile(
    r"[+-]?(?:(?:[0-9]+|[0-9]{1,3}(?:,[0-9]{3})+)(?:\.[0-9]*)?|\.[0-9]+)\Z"
)
FOOTER_PATTERN = re.compile(r"(?:说明|备注|注|注意|提示|温馨提示|免责声明)(?:[：:\s]|$)")


def _index(value, default, previous, limit, label):
    if value is None:
        result = default
    else:
        if not re.fullmatch(r"[1-9][0-9]*", value):
            raise ValueError(f"Invalid SpreadsheetML {label} index")
        result = int(value)
    if result <= previous or result > limit:
        raise ValueError(f"Out-of-order or out-of-range SpreadsheetML {label} index")
    return result


def _rows(table):
    """Yield (Excel row number, sparse text cells, has merged cells)."""
    previous_row = 0
    for element in table:
        if element.tag == TAG + "Null":
            if element.attrib or len(element) or element.text:
                raise ValueError("Only empty SpreadsheetML Null elements are supported")
            continue
        if element.tag == TAG + "Column":
            continue
        if element.tag != TAG + "Row":
            raise ValueError("Unsupported SpreadsheetML table structure")
        row_number = _index(
            element.get(TAG + "Index"), previous_row + 1, previous_row,
            1048576, "row",
        )
        previous_row = row_number
        cells = {}
        previous_column = 0
        merged = False
        for cell in element:
            if cell.tag != TAG + "Cell":
                raise ValueError(f"Unsupported SpreadsheetML row structure at row {row_number}")
            column = _index(
                cell.get(TAG + "Index"), previous_column + 1,
                previous_column, 16384, "cell",
            )
            merge_across = cell.get(TAG + "MergeAcross", "0")
            merge_down = cell.get(TAG + "MergeDown", "0")
            if not all(re.fullmatch(r"[0-9]+", value) for value in (merge_across, merge_down)):
                raise ValueError(f"Invalid merged cell at row {row_number}")
            previous_column = column + int(merge_across)
            if previous_column > 16384:
                raise ValueError(f"Merged cell exceeds column range at row {row_number}")
            merged = merged or int(merge_across) > 0 or int(merge_down) > 0
            children = list(cell)
            if len(children) > 1 or any(child.tag != TAG + "Data" for child in children):
                raise ValueError(f"Unsupported SpreadsheetML cell structure at row {row_number}")
            if not children:
                cells[column] = ""
                continue
            data = children[0]
            if data.get(TAG + "Type") not in {"String", "Number", "DateTime", "Boolean"}:
                raise ValueError(f"Unsupported SpreadsheetML data type at row {row_number}")
            cells[column] = "".join(data.itertext())
        yield row_number, cells, merged


def _minor_units(value, field, row_number, optional=False):
    text = value.strip()
    if not text and optional:
        return None
    if not text or not MONEY_PATTERN.fullmatch(text):
        raise ValueError(f"Invalid amount in {field} at row {row_number}")
    try:
        amount = Decimal(text.replace(",", ""))
        # Multiplication must not round long original decimal strings using the
        # process-wide Decimal precision before the sub-cent check is made.
        with localcontext() as context:
            context.prec = max(28, len(amount.as_tuple().digits) + 2)
            minor = amount * 100
            if not amount.is_finite() or minor != minor.to_integral_value():
                raise ValueError(f"Amount has sub-cent precision in {field} at row {row_number}")
            return int(minor)
    except InvalidOperation as error:
        raise ValueError(f"Invalid amount in {field} at row {row_number}") from error


def _footer(cells):
    populated = [(column, value.strip()) for column, value in cells.items() if value.strip()]
    if len(populated) != 1 or populated[0][0] != 1:
        return False
    text = populated[0][1]
    return text.startswith("#导出时间：") or bool(FOOTER_PATTERN.match(text))


def read_purchase_details(path):
    """Return original fields, source provenance, and integer monetary fields.

    ``source_row`` is the Excel row number, including sparse ``ss:Index`` rows.
    ``source_file`` is the filename, and ``source_sha256`` hashes the exact input
    bytes. A blank discount is ``None``; a stated zero discount is integer zero.
    Order and cumulative-refund amounts must be present. Monetary strings accept
    an optional sign and correctly grouped thousands separators, but not currency
    symbols, scientific notation, or fractional cents. Single-cell footer notes
    require an explicit note prefix or the export's ``#导出时间：`` prefix.
    """
    source_path = Path(path)
    raw = source_path.read_bytes()
    try:
        workbook = ET.fromstring(raw)
    except ET.ParseError as error:
        raise ValueError("Input is not valid Excel 2003 SpreadsheetML XML") from error
    if workbook.tag != TAG + "Workbook":
        raise ValueError("Expected an Excel 2003 SpreadsheetML Workbook")
    for element in workbook.iter():
        if any(attribute.rsplit("}", 1)[-1] == "Formula" for attribute in element.attrib):
            raise ValueError("Formulas are not allowed in purchase-detail sources")
    worksheets = workbook.findall("ss:Worksheet", NS)
    if len(worksheets) != 1:
        raise ValueError("Expected exactly one purchase-detail worksheet")
    tables = worksheets[0].findall("ss:Table", NS)
    if len(tables) != 1:
        raise ValueError("Expected exactly one purchase-detail table")
    if any(tables[0].get(TAG + attribute) not in (None, "1") for attribute in ("TopCell", "LeftCell")):
        raise ValueError("Non-default SpreadsheetML table offsets are not supported")
    rows = list(_rows(tables[0]))
    candidates = [
        index for index, (_, cells, _) in enumerate(rows)
        if set(REQUIRED_HEADERS).issubset(value.strip() for value in cells.values())
    ]
    if len(candidates) != 1:
        raise ValueError("Expected exactly one recognized purchase-detail header")
    header_index = candidates[0]
    _, header_cells, header_merged = rows[header_index]
    width = max(header_cells)
    headers = [header_cells.get(column, "").strip() for column in range(1, width + 1)]
    if len(headers) != len(set(headers)):
        raise ValueError("Duplicate purchase-detail header fields")
    if header_merged or not all(headers) or set(headers) - KNOWN_HEADERS:
        raise ValueError("Unsupported purchase-detail header structure")

    source = {"source_file": source_path.name, "source_sha256": hashlib.sha256(raw).hexdigest()}
    result = []
    footer_started = False
    for row_number, cells, merged in rows[header_index + 1:]:
        if not any(value.strip() for value in cells.values()):
            continue
        if _footer(cells):
            footer_started = True
            continue
        if footer_started:
            raise ValueError(f"Unexpected content after the footer at row {row_number}")
        if merged or any(column > width for column in cells):
            raise ValueError(f"Unsupported purchase-detail row structure at row {row_number}")
        record = {field: cells.get(column, "") for column, field in enumerate(headers, 1)}
        if not record["支付宝交易号"].strip():
            raise ValueError(f"Missing Alipay transaction ID at row {row_number}")
        record.update(
            source_row=row_number,
            **source,
            amount_minor=_minor_units(record["订单金额(元)"], "订单金额(元)", row_number),
            discount_minor=_minor_units(record["优惠(元)"], "优惠(元)", row_number, optional=True),
            refund_minor=_minor_units(record["累计退款总额(元)"], "累计退款总额(元)", row_number),
        )
        result.append(record)
    return result


def _evidence(record):
    if not PROVENANCE_KEYS.issubset(record):
        raise ValueError("Purchase-detail record is missing source provenance")
    result = [{key: record[key] for key in sorted(PROVENANCE_KEYS)}]
    existing = record.get("source_evidence", [])
    if not isinstance(existing, list):
        raise ValueError("Invalid purchase-detail source evidence")
    for evidence in existing:
        if not isinstance(evidence, dict) or not PROVENANCE_KEYS.issubset(evidence):
            raise ValueError("Incomplete purchase-detail source evidence")
        item = {key: evidence[key] for key in sorted(PROVENANCE_KEYS)}
        if item not in result:
            result.append(item)
    return result


def deduplicate_details(list_of_records):
    """Deduplicate transaction IDs while preserving all distinct source evidence.

    Compare original fields and derived monetary values exactly, excluding only
    ``序号`` and source provenance. Conflicting content raises ``ValueError``;
    recency never chooses a winner. The first record's presentation and sequence
    number remain, and ``source_evidence`` holds every distinct source location.
    Inputs are not mutated; deduplicating the output again is idempotent.
    """
    records_by_id = {}
    content_by_id = {}
    excluded = PROVENANCE_KEYS | {"source_evidence", "序号"}
    for record in list_of_records:
        identifier = record.get("支付宝交易号")
        if not isinstance(identifier, str) or not identifier.strip():
            raise ValueError("Missing Alipay transaction ID during deduplication")
        evidence = _evidence(record)
        content = {key: value for key, value in record.items() if key not in excluded}
        if identifier not in records_by_id:
            records_by_id[identifier] = deepcopy(record)
            records_by_id[identifier]["source_evidence"] = deepcopy(evidence)
            content_by_id[identifier] = deepcopy(content)
            continue
        if content_by_id[identifier] != content:
            raise ValueError("Conflicting purchase details for the same Alipay transaction ID")
        sources = records_by_id[identifier]["source_evidence"]
        for item in evidence:
            if item not in sources:
                sources.append(deepcopy(item))
    return list(records_by_id.values())
