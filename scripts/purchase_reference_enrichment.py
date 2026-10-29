"""Add purchase-order evidence to existing events, without creating money flows."""
from collections import Counter, defaultdict
from datetime import date
from pathlib import Path
import hashlib

from alipay_purchase_details import read_purchase_details, deduplicate_details
from source_enrichment import merchant_matches


def purchase_reference_enrichments(sources, prior_matches, events):
    records = []
    for source in sources:
        path = Path(source['archived_path'])
        if hashlib.sha256(path.read_bytes()).hexdigest() != source['sha256']:
            raise ValueError('Purchase reference source hash changed')
        records.extend(read_purchase_details(path))
    unique = deduplicate_details(records)
    by_order = defaultdict(set)
    for match in prior_matches:
        key = match['evidence'].get('交易订单号', '').strip()
        if key:
            by_order[key].add(match['id'])
    for event in events:
        if event['family'] == 'alipay':
            key = event.get('fields', {}).get('交易订单号', '').strip()
            if key:
                by_order[key].add(event['id'])
    by_id = {event['id']: event for event in events}
    if any(key not in by_id for keys in by_order.values() for key in keys):
        raise ValueError('Prior payment reference points to a missing event')
    matches, unresolved = [], []
    for record in unique:
        order_id = record['支付宝交易号'].strip()
        if record['买卖标志'].strip() != '买入':
            unresolved.append({'order_id': order_id, 'reason': '不是买入记录，仅保留参考'})
            continue
        candidates = [by_id[key] for key in by_order[order_id]
                      if by_id[key]['expense_minor'] > 0]
        basis = '支付宝交易号与已匹配付款证据一致'
        if record['交易状态'].strip() not in ('成功', '待确认收货'):
            unresolved.append({'order_id': order_id, 'reason': '关闭或未完成订单，仅保留参考'})
            continue
        amounts = {record['amount_minor']}
        if record['discount_minor'] is not None:
            amounts.add(record['amount_minor'] - record['discount_minor'])
        if candidates:
            # Order totals and bank debits can differ by an explicit discount.
            candidates = [e for e in candidates if e['amount_minor'] in amounts]
        else:
            channel = record['支付渠道'].strip()
            suffix = '_credit' if channel == '快捷支付-信用卡' else '_debit' if channel == '快捷支付-借记卡' else None
            if suffix:
                created = date.fromisoformat(record['创建时间'][:10])
                candidates = [e for e in events if e['in_scope'] and e['expense_minor'] > 0
                              and e['family'].endswith(suffix) and e['amount_minor'] in amounts
                              and abs((date.fromisoformat(e['date']) - created).days) <= 2
                              and merchant_matches(e['description'], record['对方名称'])]
                basis = '完整商户名、支付渠道及金额一致，创建日期与付款日期相差最多2天，双向唯一'
        if len(candidates) == 1:
            matches.append({'id': candidates[0]['id'], 'order_id': order_id,
                            'match_basis': basis, 'evidence': record})
        else:
            unresolved.append({'order_id': order_id, 'reason': '无唯一既有付款匹配；未新增费用'})
    counts = Counter(m['id'] for m in matches)
    accepted = []
    for match in matches:
        if counts[match['id']] == 1:
            accepted.append(match)
        else:
            unresolved.append({'order_id': match['order_id'], 'reason': '多订单指向同一付款，保留疑点'})
    return {'source_rows': len(records), 'unique_orders': len(unique),
            'duplicate_rows': len(records) - len(unique), 'records': unique,
            'matches': accepted, 'unresolved': unresolved}
