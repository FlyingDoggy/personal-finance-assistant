"""Explicit immutable report captures, not arbitrary historical reconstruction."""
from __future__ import annotations

import json

from . import vouchers

CALCULATOR_VERSION = "management-reports-v0.5.1"
from .domain import DomainError, month_bounds
from .reports import report
from .storage import encode, now, revision, transactions, uid


def initialize(db):
    db.execute('''CREATE TABLE IF NOT EXISTS report_snapshots(
        id TEXT PRIMARY KEY, month TEXT NOT NULL, data_revision INTEGER NOT NULL,
        rule_version TEXT NOT NULL, dimension_version INTEGER NOT NULL,
        payload TEXT NOT NULL, created_at TEXT NOT NULL)''')


def _dimension_version(db):
    from .catalog import dimension_version
    return dimension_version(db)


def _dto(db, row, include_report=False):
    result = {key: row[key] for key in ('id', 'month', 'data_revision', 'rule_version', 'dimension_version', 'created_at')}
    frozen = json.loads(row['payload'])
    from .coverage import version as coverage_version
    result['stale'] = (frozen.get('calculator_version') != CALCULATOR_VERSION
                       or frozen.get('coverage_version') != coverage_version(db) or row['data_revision'] != revision(db) or row['rule_version'] != vouchers.RULE_VERSION
                       or row['dimension_version'] != _dimension_version(db))
    if include_report:
        result.update(frozen)
    return result


def capture(db, month):
    month_bounds(month)
    frozen = report(db, month)
    txs = transactions(db)
    budget = db.execute('SELECT MAX(version) FROM budget_versions WHERE year=?', (int(month[:4]),)).fetchone()[0]
    from .coverage import version as coverage_version
    payload = {'calculator_version': CALCULATOR_VERSION, 'coverage_version': coverage_version(db), 'report': frozen, 'transaction_versions': {tid: t['version'] for tid, t in txs.items()},
               'budget_version': budget,
               'reproduction_basis': '保存时冻结的报表结果；不声称能够生成未保存过的历史时点报表。'}
    sid = uid()
    db.execute('INSERT INTO report_snapshots VALUES(?,?,?,?,?,?,?)', (
        sid, month, revision(db), vouchers.RULE_VERSION, _dimension_version(db), encode(payload), now()))
    return get(db, sid)


def get(db, sid):
    row = db.execute('SELECT * FROM report_snapshots WHERE id=?', (sid,)).fetchone()
    if not row:
        raise DomainError('报表快照不存在。', 'not_found', 404)
    return _dto(db, row, True)


def list_snapshots(db, month=None, limit=50, offset=0):
    if month:
        month_bounds(month)
    if not 1 <= limit <= 200 or offset < 0:
        raise DomainError('分页 limit 须为 1–200，offset 不可为负。')
    where = ' WHERE month=?' if month else ''
    args = (month,) if month else ()
    total = db.execute('SELECT COUNT(*) FROM report_snapshots' + where, args).fetchone()[0]
    rows = db.execute('SELECT * FROM report_snapshots' + where + ' ORDER BY created_at DESC,id DESC LIMIT ? OFFSET ?', (*args, limit, offset))
    return {'items': [_dto(db, row) for row in rows], 'total': total, 'limit': limit, 'offset': offset}
