"""The observed CMB personal-card RMB text statement, with no inferred peer."""
from __future__ import annotations

import io
import re
from datetime import date, timedelta

from app.domain import DomainError, major_to_minor

TITLE = re.compile(r'招商银行信用卡对账单[（(]个人消费卡账户\s*(\d{4})年(\d{2})月[）)]')
ROW = re.compile(r'^(?:(\d{2}/\d{2})\s+)?(\d{2}/\d{2})\s+(.+?)\s+(-?(?:\d{1,3}(?:,\d{3})*|\d+)\.\d{2})\s+(\d{4})\s+(-?(?:\d{1,3}(?:,\d{3})*|\d+)\.\d{2})(?:[（(]([^）)]+)[）)])?$')
SECTIONS = {'消费', '还款', '退款', '分期'}


def _resolve_day(mmdd, anchor, days):
    candidates = []
    for year in (anchor.year-1, anchor.year):
        try:
            value = date(year, *map(int, mmdd.split('/')))
        except ValueError:
            continue
        if timedelta(0) <= anchor-value <= timedelta(days=days):
            candidates.append(value)
    if len(candidates) != 1:
        raise DomainError('月日无法在账单日期前唯一定位，保留待确认。', 'date_ambiguous')
    return candidates[0]


def parse_text_pages(pages, account_id, sha256):
    """Pure adapter; page/line is a physical observation ID, never a payment ID."""
    text = '\n'.join(pages)
    titles = set(TITLE.findall(text))
    if len(titles) != 1 or '人民币账户 RMB A/C' not in text or re.search(r'美元账户\s+USD', text):
        raise DomainError('仅识别已验证的招商个人消费卡人民币文本对账单；扫描件及其他版型待支持。', 'unsupported_format')
    statement_dates = set(re.findall(r'账单日\s+(\d{4})年(\d{2})月(\d{2})日', text))
    if len(statement_dates) != 1:
        raise DomainError('账单日期缺失或冲突，未解释交易。', 'unsupported_format')
    try:
        statement_day = date(*map(int, next(iter(statement_dates))))
    except ValueError as exc:
        raise DomainError('账单日期无效。', 'unsupported_format') from exc
    if statement_day.strftime('%Y %m').split() != list(next(iter(titles))):
        raise DomainError('账单标题月份与账单日冲突。', 'unsupported_format')
    required = ('交易日', '记账日', '交易摘要', '人民币金额', '卡号末四位', '交易地金额')
    if not all(token in text for token in required):
        raise DomainError('招商对账单字段与已验证版型不同。', 'unsupported_format')
    rows = []
    section = None
    card_suffixes = set()
    for page_no, page in enumerate(pages, 1):
        for line_no, original in enumerate(page.splitlines(), 1):
            line = original.replace('\xa0', ' ').strip()
            if line in SECTIONS:
                section = line
                continue
            row_candidate = re.match(r'^\d{1,4}[/-]\d', line) or (section and re.search(r'\s-?\d[\d,]*\.\d+\s+\d{4}\s+-?\d', line))
            if not row_candidate:
                # A new unknown Chinese section heading invalidates the old
                # section; never silently classify its rows under the prior one.
                if section and re.fullmatch(r'[\u4e00-\u9fff]{1,20}', line):
                    section = None
                continue
            match = ROW.fullmatch(line)
            if not match or not section:
                raise DomainError(f'第 {page_no} 页第 {line_no} 行不符合已验证的金额列结构，整份文件未应用。', 'unsupported_format')
            transaction_mmdd, posting_mmdd, description, amount, suffix, original_amount, original_currency = match.groups()
            card_suffixes.add(suffix)
            source_id = f'observation:{sha256}:{page_no}:{line_no}'
            signed = -int(major_to_minor(amount.replace(',', '')))
            raw = {'source_page': page_no, 'source_line': line_no, 'source_locator': f'page:{page_no}:line:{line_no}', 'transaction_date_raw': transaction_mmdd, 'posting_date_raw': posting_mmdd, 'description_raw': description, 'amount_raw': amount, 'card_last4': suffix, 'original_amount_raw': original_amount, 'original_currency_raw': original_currency, 'section': section, 'raw_text': original}
            item = {'row_number': len(rows)+1, 'source_id': source_id, 'identity_quality': 'observation', 'role': 'funding', 'raw': raw}
            try:
                posting_day = _resolve_day(posting_mmdd, statement_day, 62)
                day = _resolve_day(transaction_mmdd, posting_day, 45) if transaction_mmdd and section == '消费' else posting_day
                if section == '消费' and signed >= 0 or section in {'还款', '退款'} and signed <= 0:
                    raise DomainError('交易分段与金额方向冲突，未自动更改符号。', 'direction_conflict')
                kind = {'消费':'expense', '还款':'repayment', '退款':'refund', '分期':'unknown'}[section]
                item['payload'] = {'source_id': source_id, 'date': day.isoformat(), 'cash_date': posting_day.isoformat(), 'description': description, 'account_id': account_id, 'amount_minor': str(signed), 'currency':'CNY', 'kind':kind, 'category_code': '5016' if kind=='expense' else None, 'counterparty_account_id':None, 'refund_of':None, 'budget_line_id':None}
                if section == '分期':
                    item['warning'] = '分期出账仅保留本端账户变动；本金、费用和原消费关系待确认，不重复认定消费。'
            except DomainError as exc:
                item['error'] = {'code':exc.code, 'message':exc.message}
            rows.append(item)
    if not rows:
        raise DomainError('未读取到可核对的交易行；扫描件或空账单未导入。', 'unsupported_format')
    return {'parser_version':'cmb_credit_pdf_v1', 'source_namespace':'cmb_credit', 'encoding':'PDF text', 'field_mapping':{'交易日':'date', '记账日':'cash_date', '人民币金额':'amount_minor（银行金额取反）'}, 'role':'funding', 'rows':rows, 'account_kind':'credit', 'source_account_options':sorted(card_suffixes), 'statement_date':statement_day.isoformat(), 'warnings':['此版型没有稳定交易号，页/行仅标识原件观察；跨文件相似记录需要核对。', '仅计可证实本端，信用还款不自动生成银行对端。', '分期出账可能已有融资余额或原消费，需要人工核对，不能再次直接确认为费用。']}


def parse_pdf(content, account_id, sha256):
    try:
        from pypdf import PdfReader
        reader = PdfReader(io.BytesIO(content), strict=True)
        if reader.is_encrypted or len(reader.pages) > 100:
            raise DomainError('加密 PDF 或超过 100 页的文件暂不支持。', 'unsupported_format')
        pages = [page.extract_text() for page in reader.pages]
    except DomainError:
        raise
    except Exception as exc:
        raise DomainError('无法安全读取 PDF 文本，原件保留，未写入交易。', 'unsupported_format') from exc
    return parse_text_pages(pages, account_id, sha256)
