"""Reuse the validated SpreadsheetML cell and money readers as evidence only."""
from __future__ import annotations

import xml.etree.ElementTree as ET

from app.domain import DomainError, minor, valid_date
from scripts.alipay_purchase_details import TAG, NS, REQUIRED_HEADERS, KNOWN_HEADERS, _rows, _footer, _minor_units


def parse_xml(content, account_id):
    if b'\x00' in content or b'<!DOCTYPE' in content.upper() or b'<!ENTITY' in content.upper():
        raise DomainError('XML 不允许 DTD 或实体。', 'unsupported_format')
    try:
        root = ET.fromstring(content)
        if root.tag != TAG+'Workbook':
            raise ValueError('不是 Excel 2003 XML')
        if any(k.rsplit('}',1)[-1] in {'Formula', 'HRef'} for e in root.iter() for k in e.attrib):
            raise ValueError('不接受公式或外部链接')
        sheets = root.findall('ss:Worksheet', NS)
        if len(sheets) != 1 or len(sheets[0].findall('ss:Table', NS)) != 1:
            raise ValueError('需要唯一工作表和表格')
        table = sheets[0].find('ss:Table', NS)
        if any(table.get(TAG+k) not in (None, '1') for k in ('TopCell', 'LeftCell')):
            raise ValueError('不支持偏移表格')
        physical = list(_rows(table))
        heads = [i for i, (_, cells, _) in enumerate(physical) if set(REQUIRED_HEADERS).issubset(v.strip() for v in cells.values())]
        if len(heads) != 1:
            raise ValueError('买入明细表头不唯一或缺失')
        _, cells, merged = physical[heads[0]]
        headers = [cells.get(i, '').strip() for i in range(1, max(cells)+1)]
        if merged or not all(headers) or len(headers) != len(set(headers)) or set(headers)-KNOWN_HEADERS:
            raise ValueError('买入明细表头结构不同')
        rows = []
        footer = False
        for line, cells, merged in physical[heads[0]+1:]:
            if not any(v.strip() for v in cells.values()):
                continue
            if _footer(cells):
                footer = True
                continue
            if footer or merged or any(k > len(headers) for k in cells):
                raise ValueError(f'第 {line} 行结构不同')
            raw = {field:cells.get(i, '') for i,field in enumerate(headers,1)}
            item = {'row_number':line, 'raw':raw, 'source_id':raw['支付宝交易号'].strip(), 'identity_quality':'source_id', 'role':'enrich'}
            try:
                if not item['source_id'] or len(item['source_id']) > 160:
                    raise ValueError('支付宝交易号缺失或过长')
                item['evidence'] = {'date':valid_date(raw['创建时间'][:10]), 'description':raw['商品名称'], 'amount_minor':str(_minor_units(raw['订单金额(元)'], '订单金额', line)), 'refund_minor':str(_minor_units(raw['累计退款总额(元)'], '累计退款', line)), 'discount_minor':None if not raw['优惠(元)'].strip() else str(_minor_units(raw['优惠(元)'], '优惠', line)), 'currency':'CNY'}
                for k in ('amount_minor', 'refund_minor', 'discount_minor'):
                    if item['evidence'][k] is not None:
                        minor(item['evidence'][k])
            except (ValueError, DomainError) as exc:
                item['error'] = {'code':'invalid_evidence', 'message':str(exc)}
            rows.append(item)
    except (ValueError, ET.ParseError) as exc:
        raise DomainError('支付宝买入明细 XML 解析失败：'+str(exc), 'unsupported_format') from exc
    return {'parser_version':'alipay_purchase_spreadsheetml_v1', 'source_namespace':'alipay_purchase', 'encoding':'SpreadsheetML XML', 'field_mapping':{'支付宝交易号':'source_id', '商品名称':'商品证据', '订单金额(元)':'订单参考金额'}, 'role':'enrich', 'rows':rows, 'warnings':['订单金额、优惠和累计退款仅作商品参考，不生成消费、现金收付或退款。无强标识关联时等待核对。']}
