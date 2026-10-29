"""Search current interpretations and their immutable source evidence together."""
from __future__ import annotations

import json

from . import historical
from .domain import DomainError, month_bounds, review_reasons, valid_date


def business_kind(transaction):
    return transaction['historical'].get('event', 'historical') if historical.is_historical(transaction) else transaction['kind']


def filter_transactions(db, txs, acc, *, month=None, account_id=None, review_only=False,
                        q=None, category_code=None, kind=None, start_date=None, end_date=None,
                        scope='all', ids=None):
    if month:
        month_bounds(month)
    for day in (start_date, end_date):
        if day:
            valid_date(day)
    if start_date and end_date and start_date > end_date:
        raise DomainError('开始日期不能晚于结束日期。')
    if scope not in {'all', 'included', 'excluded'}:
        raise DomainError('统计范围须为 all、included 或 excluded。')
    if account_id and account_id not in acc:
        raise DomainError('账户不存在。', 'not_found', 404)
    if q and len(q) > 200:
        raise DomainError('搜索内容最多 200 字。')
    selected_ids = set(ids.split(',')) if ids else None
    if selected_ids and (len(selected_ids) > 10000 or any(len(item) > 160 for item in selected_ids)):
        raise DomainError('交易 ID 筛选超出上限。')
    query = (q or '').strip().casefold()
    source_matches = set()
    if query:
        # Search every linked evidence row, not just the current results page.
        for row in db.execute('SELECT s.transaction_id,s.raw,s.source_id,i.filename FROM source_rows s JOIN imports i ON i.id=s.import_id WHERE s.transaction_id IS NOT NULL'):
            text = json.dumps(json.loads(row['raw']), ensure_ascii=False)
            if query in (' '.join([text, row['filename'], row['source_id'] or ''])).casefold():
                source_matches.add(row['transaction_id'])
    chosen = []
    for t in txs.values():
        if selected_ids is not None and t['id'] not in selected_ids:
            continue
        day = t.get('date')
        if month and (not day or not day.startswith(month + '-')):
            continue
        if start_date and (not day or day < start_date):
            continue
        if end_date and (not day or day > end_date):
            continue
        if account_id and account_id not in {t['account_id'], t.get('counterparty_account_id')}:
            continue
        if review_only and not review_reasons(t):
            continue
        # Refund categories follow their original purchase's current category.
        code = txs.get(t.get('refund_of'), t).get('category_code') if t['kind'] == 'refund' else t.get('category_code')
        if category_code and code != category_code:
            continue
        if kind and kind != business_kind(t):
            continue
        if scope != 'all' and historical.scoped(t) != (scope == 'included'):
            continue
        if query and t['id'] not in source_matches:
            text = json.dumps(t, ensure_ascii=False) + ' ' + acc[t['account_id']]['name']
            if query not in text.casefold():
                continue
        chosen.append(t)
    chosen.sort(key=lambda t: (t.get('date') or '', t['id']), reverse=True)
    return chosen
