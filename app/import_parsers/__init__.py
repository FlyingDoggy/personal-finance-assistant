"""Strict, deterministic source adapters. Parsing never opens the ledger."""
from __future__ import annotations

import csv
import hashlib
import io

from app.domain import DomainError, major_to_minor

MAX_UPLOAD = 10 * 1024 * 1024
MAX_ROWS = 10000
CSV_COLUMNS = {'source_id', 'date', 'description', 'amount', 'kind', 'category_code', 'counterparty_account_id'}
FORMATS = [
    {'id': 'standard_csv_v1', 'institution': '用户标准模板', 'account_kind': 'cash/credit', 'export_format': 'UTF-8 CSV', 'version': '1', 'status': 'tested', 'role': 'funding', 'label': '标准 CSV'},
    {'id': 'cmb_credit_pdf_v1', 'institution': '招商银行', 'account_kind': 'credit', 'export_format': '个人消费卡人民币文本 PDF', 'version': '1', 'status': 'tested', 'role': 'funding', 'label': '招商信用卡个人消费卡人民币对账单 PDF', 'sample_scope': '已核对 2026 年 1—10 月同版型样本；扫描件、美元账户及其他版型不支持'},
    {'id': 'alipay_purchase_spreadsheetml_v1', 'institution': '支付宝', 'account_kind': 'evidence', 'export_format': '买入交易 Excel 2003 XML (.xls)', 'version': '1', 'status': 'tested', 'role': 'enrich', 'label': '支付宝买入明细 XML XLS（仅商品证据）'},
]


def standard_csv(content, account_id):
    try:
        reader = csv.DictReader(io.StringIO(content.decode('utf-8-sig'), newline=''))
        fields = reader.fieldnames
        if not fields or len(fields) != len(set(fields)) or not CSV_COLUMNS.issubset(fields) or set(fields) - CSV_COLUMNS - {'refund_of', 'budget_line_id'}:
            raise DomainError('未识别标准 CSV 表头。', 'unsupported_format')
        rows = []
        for row in reader:
            if len(rows) >= MAX_ROWS:
                raise DomainError('单批最多 10000 行，未截断文件。', 'batch_too_large')
            item = {'row_number': reader.line_num, 'raw': row, 'source_id': (row.get('source_id') or '').strip(), 'identity_quality': 'source_id', 'role': 'funding'}
            try:
                if None in row or any(v is None for v in row.values()):
                    raise DomainError('CSV 列数与表头不一致。')
                if not item['source_id'] or len(item['source_id']) > 160:
                    raise DomainError('source_id 须为 1–160 字符的稳定源标识，不能用同日同额代替。')
                item['payload'] = {'source_id': item['source_id'], 'date': row['date'].strip(), 'description': row['description'], 'account_id': account_id, 'amount_minor': major_to_minor(row['amount'].strip()), 'currency': 'CNY', 'kind': row['kind'].strip() or 'unknown', 'category_code': row['category_code'].strip() or None, 'counterparty_account_id': row['counterparty_account_id'].strip() or None, 'refund_of': row.get('refund_of') or None, 'budget_line_id': row.get('budget_line_id') or None}
            except DomainError as exc:
                item['error'] = {'code': exc.code, 'message': exc.message}
            rows.append(item)
        return {'parser_version': 'standard_csv_v1', 'source_namespace': 'standard_csv_v1', 'encoding': 'utf-8-sig', 'field_mapping': {x:x for x in fields}, 'role': 'funding', 'rows': rows, 'warnings': []}
    except (UnicodeDecodeError, csv.Error) as exc:
        raise DomainError('文件须为有效 UTF-8 标准 CSV。', 'unsupported_format') from exc


def parse(content, filename, account_id):
    if len(content) > MAX_UPLOAD:
        raise DomainError('文件超过 10 MiB 上限。', 'upload_too_large', 413)
    sha = hashlib.sha256(content).hexdigest()
    if content.startswith(b'%PDF-'):
        from .cmb_credit import parse_pdf
        result = parse_pdf(content, account_id, sha)
    elif content.lstrip(b'\xef\xbb\xbf \t\r\n').startswith(b'<'):
        from .alipay import parse_xml
        result = parse_xml(content, account_id)
    else:
        result = standard_csv(content, account_id)
    if len(result['rows']) > MAX_ROWS:
        raise DomainError('单批最多 10000 行，未截断文件。', 'batch_too_large')
    return result
