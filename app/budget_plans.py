"""Immutable budget drafts and explicit, optimistic adoption of annual plans."""
import json

from fastapi import Request

from .budget import compute_budget
from .domain import DomainError
from .storage import advance, encode, finish_operation, now, operation, transactions


def initialize(db):
    db.execute('''CREATE TABLE IF NOT EXISTS budget_states(
        year INTEGER NOT NULL,version INTEGER NOT NULL,status TEXT NOT NULL,
        base_adopted_version INTEGER NOT NULL,created_at TEXT NOT NULL,adopted_at TEXT,
        PRIMARY KEY(year,version),FOREIGN KEY(year,version) REFERENCES budget_versions(year,version))''')
    db.execute("CREATE UNIQUE INDEX IF NOT EXISTS one_adopted_budget ON budget_states(year) WHERE status='adopted'")
    db.execute('''CREATE TABLE IF NOT EXISTS budget_events(
        id INTEGER PRIMARY KEY,year INTEGER NOT NULL,version INTEGER NOT NULL,
        event TEXT NOT NULL,previous_adopted_version INTEGER NOT NULL,created_at TEXT NOT NULL)''')
    reconcile_legacy(db)


def reconcile_legacy(db):
    # Old migrations insert immutable versions directly. New service commands
    # always insert their state in the same transaction, so cannot be adopted here.
    db.execute('''INSERT OR IGNORE INTO budget_states
        SELECT b.year,b.version,
          CASE WHEN b.version=(SELECT MAX(x.version) FROM budget_versions x WHERE x.year=b.year)
          AND NOT EXISTS(SELECT 1 FROM budget_states s WHERE s.year=b.year AND s.status='adopted')
          THEN 'adopted' ELSE 'superseded' END,0,b.created_at,b.created_at
        FROM budget_versions b WHERE NOT EXISTS(
          SELECT 1 FROM budget_states s WHERE s.year=b.year AND s.version=b.version)''')


def adopted_row(db, year):
    return db.execute('''SELECT b.* FROM budget_versions b JOIN budget_states s
        ON s.year=b.year AND s.version=b.version WHERE b.year=? AND s.status='adopted' ''', (year,)).fetchone()


def latest_version(db, year):
    return db.execute('SELECT COALESCE(MAX(version),0) FROM budget_versions WHERE year=?', (year,)).fetchone()[0]


def adopted_version(db, year):
    row = adopted_row(db, year)
    return row['version'] if row else 0


def _dto(db, row):
    p = json.loads(row['payload'])
    current = adopted_row(db, row['year'])
    old_lines = json.loads(current['payload'])['lines'] if current else []
    removed = {l['id']: l['name'] for l in old_lines if l['id'] not in {n['id'] for n in p['lines']}}
    orphaned = [t['id'] for t in transactions(db).values() if t.get('budget_line_id') in removed
                and any((t.get(k) or '').startswith(f"{row['year']:04d}-") for k in ('date', 'cash_date'))]
    return {'year': row['year'], 'version': row['version'], 'status': row['status'],
            'base_adopted_version': row['base_adopted_version'], 'created_at': row['created_at'],
            'adopted_at': row['adopted_at'], 'payload': p, 'plan': compute_budget(p),
            'impact': {'removed_line_names': list(removed.values()), 'orphaned_link_count': len(orphaned)},
            'can_adopt': row['status'] == 'draft' and row['base_adopted_version'] == adopted_version(db, row['year'])}


def get(db, year, version):
    row = db.execute('''SELECT b.*,s.status,s.base_adopted_version,s.adopted_at
        FROM budget_versions b JOIN budget_states s USING(year,version)
        WHERE b.year=? AND b.version=?''', (year, version)).fetchone()
    if not row:
        raise DomainError('预算版本不存在。', 'not_found', 404)
    return _dto(db, row)


def create_draft(db, body):
    allowed = {'request_id', 'expected_version', 'expected_adopted_version', 'payload'}
    if not isinstance(body, dict) or set(body) - allowed:
        raise DomainError('预算草稿请求包含不支持的字段。')
    fingerprint, cached = operation(db, body.get('request_id'), 'budget_save', body)
    if cached is not None:
        return cached
    p = body.get('payload')
    if not isinstance(p, dict) or p.get('usage'):
        raise DomainError('预算计划须为对象；实际占用只从正式交易派生。', 'actuals_read_only')
    plan = compute_budget(p)
    year = p['year']; latest = latest_version(db, year); adopted = adopted_version(db, year)
    if type(body.get('expected_version')) is not int or body['expected_version'] != latest:
        raise DomainError('预算版本已变化，请重新读取后保存草稿。', 'version_conflict', 409)
    if 'expected_adopted_version' in body and (type(body['expected_adopted_version']) is not int or body['expected_adopted_version'] != adopted):
        raise DomainError('采用预算已变化，请重新读取后保存草稿。', 'version_conflict', 409)
    from .storage import revision
    db.execute('INSERT INTO budget_versions VALUES(?,?,?,?,?)', (year, latest + 1, encode({**p, 'usage': []}), now(), revision(db)))
    db.execute('INSERT INTO budget_states VALUES(?,?,?,?,?,NULL)', (year, latest + 1, 'draft', adopted, now()))
    db.execute('INSERT INTO budget_events(year,version,event,previous_adopted_version,created_at) VALUES(?,?,?,?,?)', (year, latest + 1, 'draft_saved', adopted, now()))
    return finish_operation(db, body['request_id'], 'budget_save', fingerprint,
                            {'version': latest + 1, 'status': 'draft', 'adopted_version': adopted, 'draft': get(db, year, latest + 1), 'budget': plan})


def adopt(db, year, version, body):
    if not isinstance(body, dict) or set(body) - {'request_id', 'expected_adopted_version'}:
        raise DomainError('采用请求包含不支持的字段。')
    action = f'budget_adopt:{year}:{version}'
    fingerprint, cached = operation(db, body.get('request_id'), action, body)
    if cached is not None:
        return cached
    draft = get(db, year, version); current = adopted_version(db, year)
    if (type(body.get('expected_adopted_version')) is not int or body['expected_adopted_version'] != current
        or draft['base_adopted_version'] != current):
        raise DomainError('采用版已变化；请基于当前采用版另存草稿，核对差异后再采用。', 'version_conflict', 409)
    if draft['status'] != 'draft':
        raise DomainError('只能采用尚未采用的草稿。', 'budget_state_conflict', 409)
    old = adopted_row(db, year)
    old_lines = {l['id']: l for l in json.loads(old['payload'])['lines']} if old else {}
    new_lines = {l['id']: l for l in draft['payload']['lines']}
    bound = {t.get('budget_line_id') for t in transactions(db).values() if any((t.get(k) or '').startswith(f'{year:04d}-') for k in ('date', 'cash_date'))}
    for line_id in bound & old_lines.keys() & new_lines.keys():
        if any(old_lines[line_id][k] != new_lines[line_id][k] for k in ('actual_basis', 'execution_basis')):
            raise DomainError('已有实际关联的项目不能直接切换计量口径；请建立新项目并核对关联。', 'budget_basis_in_use', 409)
    db.execute("UPDATE budget_states SET status='superseded' WHERE year=? AND status='adopted'", (year,))
    db.execute("UPDATE budget_states SET status='adopted',adopted_at=? WHERE year=? AND version=?", (now(), year, version))
    db.execute('INSERT INTO budget_events(year,version,event,previous_adopted_version,created_at) VALUES(?,?,?,?,?)', (year, version, 'adopted', current, now()))
    advance(db)
    return finish_operation(db, body['request_id'], action, fingerprint,
                            {'version': version, 'status': 'adopted', 'previous_adopted_version': current})


def register_routes(app, store):
    @app.get('/api/v1/budget-versions')
    def versions(year: int):
        if not 1 <= year <= 9999:
            raise DomainError('年份须在 1–9999。')
        with store.read() as db:
            rows = db.execute('''SELECT b.*,s.status,s.base_adopted_version,s.adopted_at
                FROM budget_versions b JOIN budget_states s USING(year,version)
                WHERE b.year=? ORDER BY b.version DESC''', (year,)).fetchall()
            return {'items': [_dto(db, r) for r in rows], 'adopted_version': adopted_version(db, year), 'latest_version': latest_version(db, year)}

    @app.post('/api/v1/budget-versions/{year}/{version}/adopt')
    async def adopt_version(year: int, version: int, request: Request):
        body = await request.json()
        with store.write() as db:
            return adopt(db, year, version, body)
