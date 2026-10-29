"""Rebuildable, versioned projection of current source-backed journal lines.

The projection does not create accounting facts. In particular, legacy paired
evidence rows with no journal remain empty: their peer already carries the
accounting lines. Reports continue to use their explicit income/expense/cash
bases rather than treating journal debit totals as personal spending.
"""
from __future__ import annotations

from collections import Counter
import hashlib
import json

from . import historical
from .domain import DomainError, ledger, money, month_bounds, review_reasons
from .storage import accounts, transactions, revision, encode
from .transaction_search import business_kind, filter_transactions

RULE_VERSION = 'finance-rules-v1'
PROJECTION_VERSION = 'source-journal-v1'
STATUSES = {'posted', 'review', 'excluded', 'no_journal', 'evidence_only'}
PAIRED_TYPES = {'还款双源', '范围内转账双边抵消', '关闭订单退款'}
SUMMARY_NOTE = '当前筛选的来源分录行合计，不等于收入、费用或现金收付。配对的补充证据不重复生成分录；报表仍按各自业务口径统计。'


def initialize(db):
    # Use individual statements: executescript would commit a caller's migration.
    statements = [
        '''CREATE TABLE IF NOT EXISTS voucher_headers(
            transaction_id TEXT PRIMARY KEY, transaction_version INTEGER NOT NULL,
            rule_version TEXT NOT NULL, data_revision INTEGER NOT NULL,
            date TEXT, cash_date TEXT, status TEXT NOT NULL,
            account_id TEXT NOT NULL, category_code TEXT, description TEXT NOT NULL,
            business_kind TEXT NOT NULL, source_count INTEGER NOT NULL,
            related_ids TEXT NOT NULL, group_id TEXT NOT NULL,
            ledger_basis TEXT NOT NULL, status_note TEXT NOT NULL)''',
        '''CREATE TABLE IF NOT EXISTS voucher_lines(
            transaction_id TEXT NOT NULL REFERENCES voucher_headers(transaction_id) ON DELETE CASCADE,
            sequence INTEGER NOT NULL, code TEXT NOT NULL, name TEXT NOT NULL,
            account_id TEXT, debit_minor TEXT NOT NULL, credit_minor TEXT NOT NULL,
            basis TEXT NOT NULL, PRIMARY KEY(transaction_id,sequence))''',
        'CREATE INDEX IF NOT EXISTS voucher_period ON voucher_headers(date,status)',
        'CREATE INDEX IF NOT EXISTS voucher_line_code ON voucher_lines(code,transaction_id)',
        '''CREATE TABLE IF NOT EXISTS voucher_projection_state(
            id INTEGER PRIMARY KEY CHECK(id=1), data_revision INTEGER NOT NULL,
            rule_version TEXT NOT NULL, projection_version TEXT NOT NULL)''',
    ]
    for statement in statements:
        db.execute(statement)


def _relations(txs):
    related = {tid: set() for tid in txs}
    paired = {tid: set() for tid in txs}
    for tid, p in txs.items():
        h = p.get('historical', {})
        peer = h.get('related_id') or p.get('refund_of')
        if peer in txs and peer != tid:
            related[tid].add(peer)
            related[peer].add(tid)
            if h.get('link_type') in PAIRED_TYPES:
                paired[tid].add(peer)
                paired[peer].add(tid)
    return related, paired


def rebuild(db, force=False):
    """Refresh within the caller's write transaction, without touching sources."""
    current_revision = revision(db)
    state = db.execute('SELECT * FROM voucher_projection_state WHERE id=1').fetchone()
    if not force and state and (state['data_revision'], state['rule_version'], state['projection_version']) == (current_revision, RULE_VERSION, PROJECTION_VERSION):
        return
    txs = transactions(db)
    acc = accounts(db)
    related, paired = _relations(txs)
    counts = dict(db.execute('SELECT transaction_id,COUNT(*) FROM source_rows WHERE transaction_id IS NOT NULL GROUP BY transaction_id'))
    projected = {tid: ledger(p, acc, txs) for tid, p in txs.items()}
    db.execute('DELETE FROM voucher_lines')
    db.execute('DELETE FROM voucher_headers')
    for tid, p in txs.items():
        lines = projected[tid]
        if not historical.scoped(p):
            status, note = 'excluded', '统计范围外，保留来源与历史，不生成当前分录。'
        elif not lines and any(projected[peer] for peer in paired[tid]):
            status, note = 'evidence_only', '补充来源证据；记账分录已在关联记录体现，避免重复。'
        elif review_reasons(p):
            status, note = 'review', '仍有待确认事项；已有来源金额保留，未确认部分不强造平衡分录。'
        elif lines:
            status, note = 'posted', '按当前有效交易版本生成。'
        else:
            status, note = 'no_journal', '当前解释无记账分录，可能是关闭订单或其他非记账记录。'
        group_ids = sorted({tid, *paired[tid]})
        group_id = 'journal-group-' + hashlib.sha256(encode(group_ids).encode()).hexdigest()[:24]
        db.execute('INSERT INTO voucher_headers VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)', (
            tid, p['version'], RULE_VERSION, current_revision, p.get('date'), p.get('cash_date'), status,
            p['account_id'], p.get('category_code'), p.get('description', ''), business_kind(p), counts.get(tid, 0),
            encode(sorted(related[tid])), group_id, 'source_interpretations', note))
        debit = credit = 0
        for sequence, line in enumerate(lines, 1):
            d, c = int(line['debit_minor']), int(line['credit_minor'])
            if d < 0 or c < 0 or (d and c):
                raise DomainError('派生分录借贷格式无效，未保存。', 'ledger_unbalanced', 409)
            debit += d
            credit += c
            db.execute('INSERT INTO voucher_lines VALUES(?,?,?,?,?,?,?,?)', (
                tid, sequence, str(line['code']), str(line.get('name') or line['code']),
                line.get('account_id'), money(d), money(c), str(line.get('basis') or '当前交易版本派生')))
        if debit != credit:
            raise DomainError('派生分录借贷不平衡，未保存。', 'ledger_unbalanced', 409)
    db.execute('INSERT OR REPLACE INTO voucher_projection_state VALUES(1,?,?,?)', (current_revision, RULE_VERSION, PROJECTION_VERSION))


def _lines_by_transaction(db, chosen_ids=None):
    result = {}
    for row in db.execute('SELECT * FROM voucher_lines ORDER BY transaction_id,sequence'):
        if chosen_ids is None or row['transaction_id'] in chosen_ids:
            result.setdefault(row['transaction_id'], []).append(dict(row))
    return result


def _dto(row, acc, lines):
    result = dict(row)
    result['related_transaction_ids'] = json.loads(result.pop('related_ids'))
    result['account_name'] = acc[result['account_id']]['name']
    result['line_count'] = len(lines)
    result['debit_minor'] = money(sum(int(line['debit_minor']) for line in lines))
    result['credit_minor'] = money(sum(int(line['credit_minor']) for line in lines))
    return result


def query(db, *, month=None, q=None, account_id=None, category_code=None, status=None, limit=50, offset=0, data_revision=None):
    if not 1 <= limit <= 200 or offset < 0:
        raise DomainError('分页 limit 须为 1–200，offset 不可为负。')
    if month:
        month_bounds(month)
    if status and status not in STATUSES:
        raise DomainError('未知凭证状态。')
    if data_revision is not None and data_revision != revision(db):
        raise DomainError('查询修订已过期，请从第一页刷新。', 'revision_conflict', 409)
    acc = accounts(db)
    txs = transactions(db)
    # The category selector means a journal line's account, including its cash
    # or liability side. It is intentionally not a transaction category filter.
    selected_ids = {t['id'] for t in filter_transactions(db, txs, acc, month=month, q=q, account_id=account_id)}
    if category_code:
        selected_ids &= {row[0] for row in db.execute('SELECT DISTINCT transaction_id FROM voucher_lines WHERE code=?', (category_code,))}
    headers = [row for row in db.execute('SELECT * FROM voucher_headers ORDER BY COALESCE(date,\'\') DESC,transaction_id DESC')
               if row['transaction_id'] in selected_ids and (not status or row['status'] == status)]
    ids = {row['transaction_id'] for row in headers}
    all_lines = _lines_by_transaction(db, ids)
    flattened = [line for tid in ids for line in all_lines.get(tid, [])]
    status_counts = dict(Counter(row['status'] for row in headers))
    _, paired = _relations(txs)
    return {
        'items': [_dto(row, acc, all_lines.get(row['transaction_id'], [])) for row in headers[offset:offset + limit]],
        'total': len(headers), 'limit': limit, 'offset': offset,
        'summary': {
            'transaction_count': len(headers), 'line_count': len(flattened),
            'debit_minor': money(sum(int(line['debit_minor']) for line in flattened)),
            'credit_minor': money(sum(int(line['credit_minor']) for line in flattened)),
            'paired_source_count': sum(bool(paired[row['transaction_id']]) for row in headers),
            'status_counts': status_counts, 'basis': 'source_interpretations', 'note': SUMMARY_NOTE,
        },
        'rule_version': RULE_VERSION, 'projection_version': PROJECTION_VERSION,
    }


def detail(db, tid):
    row = db.execute('SELECT * FROM voucher_headers WHERE transaction_id=?', (tid,)).fetchone()
    if not row:
        raise DomainError('凭证来源记录不存在。', 'not_found', 404)
    lines = _lines_by_transaction(db, {tid}).get(tid, [])
    return {'voucher': _dto(row, accounts(db), lines), 'lines': lines, 'basis_note': SUMMARY_NOTE}
