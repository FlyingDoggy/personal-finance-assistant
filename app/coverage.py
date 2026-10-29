"""Versioned account/month evidence declarations, never inferred from activity.

Services use the caller's SQLite transaction. A declaration freezes the relevant
financial facts, so a new row only makes its affected account/month/basis stale.
Changing a description or assigning a category does not erase source coverage.
Coverage says nothing about opening balances or balance reconciliation.
"""
from __future__ import annotations

from fastapi import Request

from .domain import DomainError, month_bounds, movements, recognition_date
from .storage import accounts, transactions, digest, now, revision, operation, finish_operation
from . import historical

BASES = {'consumption', 'payment'}
STATUSES = {'complete', 'partial', 'missing'}


def initialize(db):
    db.execute('''CREATE TABLE IF NOT EXISTS coverage_declarations(
        account_id TEXT NOT NULL REFERENCES accounts(id),month TEXT NOT NULL,basis TEXT NOT NULL,
        version INTEGER NOT NULL,status TEXT NOT NULL,evidence TEXT NOT NULL,confirmed INTEGER NOT NULL,
        facts_digest TEXT NOT NULL,created_at TEXT NOT NULL,
        PRIMARY KEY(account_id,month,basis,version))''')
    db.execute("INSERT OR IGNORE INTO metadata(key,value) VALUES('coverage_version',0)")


def version(db):
    row = db.execute("SELECT value FROM metadata WHERE key='coverage_version'").fetchone()
    return row[0] if row else 0


def _validate_key(db, account_id, month, basis):
    month_bounds(month)
    if not isinstance(account_id, str) or account_id not in accounts(db):
        raise DomainError('覆盖声明的账户不存在。')
    if not isinstance(basis, str) or basis not in BASES:
        raise DomainError('覆盖口径须为 consumption 或 payment。')


def _facts_digest(txs, account_id, month, basis):
    facts = []
    for tid, t in txs.items():
        if not historical.scoped(t):
            continue
        day = t.get('cash_date') if basis == 'payment' else recognition_date(t)
        if not day or day[:7] != month:
            continue
        changes = movements(t)
        if t['account_id'] != account_id and account_id not in changes:
            continue
        h = t.get('historical', {})
        facts.append({'id': tid, 'account_id': t['account_id'], 'day': day,
                      'amount_minor': t['amount_minor'], 'kind': t['kind'],
                      'movement': changes.get(account_id), 'refund_of': historical.refund_parent(t),
                      'event': h.get('event'), 'income_minor': h.get('income_minor'),
                      'expense_minor': h.get('expense_minor'), 'cash_included': h.get('cash_included')})
    return digest(sorted(facts, key=lambda t: t['id']))


def get_declaration(db, account_id, month, basis, *, txs=None):
    _validate_key(db, account_id, month, basis)
    exists = db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='coverage_declarations'").fetchone()
    row = db.execute('''SELECT * FROM coverage_declarations
        WHERE account_id=? AND month=? AND basis=? ORDER BY version DESC LIMIT 1''',
        (account_id, month, basis)).fetchone() if exists else None
    if not row:
        return {'account_id': account_id, 'month': month, 'basis': basis, 'version': 0,
                'status': 'missing', 'declared_status': 'missing', 'evidence': '',
                'confirmed': False, 'stale': False, 'complete': False}
    item = dict(row)
    txs = transactions(db) if txs is None else txs
    item['stale'] = item.pop('facts_digest') != _facts_digest(txs, account_id, month, basis)
    item['confirmed'] = bool(item['confirmed'])
    item['declared_status'] = item['status']
    if item['stale']:
        item['status'] = 'partial'
    item['complete'] = item['status'] == 'complete' and item['confirmed'] and not item['stale']
    return item


def set_declaration(db, account_id, month, basis, *, status, evidence, confirmed, expected_version):
    """Append one immutable declaration; caller controls revision and operation."""
    _validate_key(db, account_id, month, basis)
    if not isinstance(status, str) or status not in STATUSES:
        raise DomainError('覆盖状态须为 complete、partial 或 missing。')
    if not isinstance(evidence, str) or len(evidence) > 4000:
        raise DomainError('覆盖证据说明须为不超过 4000 字的文本。')
    if type(confirmed) is not bool:
        raise DomainError('confirmed 须为布尔值。')
    if status == 'complete' and (not confirmed or not evidence.strip()):
        raise DomainError('完整覆盖必须有证据说明并明确确认；零交易也不能自动视为完整。', 'coverage_evidence_required')
    txs = transactions(db)
    current = get_declaration(db, account_id, month, basis, txs=txs)
    if type(expected_version) is not int or expected_version != current['version']:
        raise DomainError('覆盖声明版本已变化，请刷新后重试。', 'version_conflict', 409)
    db.execute('INSERT INTO coverage_declarations VALUES(?,?,?,?,?,?,?,?,?)',
               (account_id, month, basis, current['version'] + 1, status, evidence.strip(),
                int(confirmed), _facts_digest(txs, account_id, month, basis), now()))
    db.execute("UPDATE metadata SET value=value+1 WHERE key='coverage_version'")
    return get_declaration(db, account_id, month, basis, txs=txs)


def month_quality(db, month, basis, account_ids=None, *, txs=None):
    month_bounds(month)
    if not isinstance(basis, str) or basis not in BASES:
        raise DomainError('覆盖口径须为 consumption 或 payment。')
    txs = transactions(db) if txs is None else txs
    ids = sorted(accounts(db) if account_ids is None else set(account_ids))
    declarations = [get_declaration(db, aid, month, basis, txs=txs) for aid in ids]
    complete = bool(declarations) and all(item['complete'] for item in declarations)
    status = 'complete' if complete else ('missing' if not declarations or all(item['status'] == 'missing' for item in declarations) else 'partial')
    reasons = []
    if not complete:
        reasons.append('所选账户和月份的资料覆盖尚未全部确认，已知金额不代表完整金额。')
    if any(item['stale'] for item in declarations):
        reasons.append('声明后相关流水发生变化，需要重新核对受影响的账户月份。')
    return {'month': month, 'basis': basis, 'status': status, 'complete': complete,
            'coverage_version': version(db), 'account_ids': ids,
            'declarations': declarations, 'reasons': reasons,
            'balance_reconciled': False}


def register_routes(app, store):
    @app.get('/api/v1/coverage')
    def get_coverage(month: str, basis: str = 'consumption', account_id: str | None = None):
        with store.read() as db:
            quality = month_quality(db, month, basis, [account_id] if account_id else None)
            return {**quality, 'items': quality['declarations'], 'data_revision': revision(db)}

    @app.post('/api/v1/coverage')
    async def post_coverage(request: Request):
        body = await request.json()
        allowed = {'request_id', 'account_id', 'month', 'basis', 'status', 'evidence', 'confirmed', 'expected_version'}
        if not isinstance(body, dict) or set(body) - allowed:
            raise DomainError('覆盖声明包含不支持的字段。')
        with store.write() as db:
            fingerprint, cached = operation(db, body.get('request_id'), 'coverage_declare', body)
            if cached is not None:
                return cached
            item = set_declaration(db, body.get('account_id'), body.get('month'), body.get('basis'),
                                   status=body.get('status'), evidence=body.get('evidence', ''),
                                   confirmed=body.get('confirmed', False), expected_version=body.get('expected_version'))
            response = {'declaration': item, 'coverage_version': version(db), 'data_revision': revision(db)}
            return finish_operation(db, body['request_id'], 'coverage_declare', fingerprint, response)
