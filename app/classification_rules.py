"""Explicit rule drafts, impact previews and narrowly scoped classification writes."""
import json
from typing import Literal

from fastapi import Request
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from . import historical
from .domain import CHART, DomainError, validate_transaction
from .storage import accounts, advance, digest, encode, finish_operation, now, operation, raw_payload, revision, save_transaction, transactions, uid


class Definition(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)
    name: str = Field(min_length=1, max_length=100)
    match_field: Literal['description', 'merchant', 'product']
    terms: list[str] = Field(min_length=1, max_length=5)
    match_mode: Literal['any', 'all'] = 'any'
    priority: int = Field(default=0, ge=0, le=1000)
    category_code: str
    budget_line_id: str | None = Field(default=None, max_length=160)


def initialize(db):
    db.execute('''CREATE TABLE IF NOT EXISTS classification_rules(
        id TEXT NOT NULL,version INTEGER NOT NULL,status TEXT NOT NULL,
        definition TEXT NOT NULL,created_at TEXT NOT NULL,PRIMARY KEY(id,version))''')
    db.execute('''CREATE TABLE IF NOT EXISTS rule_previews(
        id TEXT PRIMARY KEY,rule_id TEXT NOT NULL,rule_version INTEGER NOT NULL,
        dependencies TEXT NOT NULL,payload TEXT NOT NULL,created_at TEXT NOT NULL)''')
    db.execute("INSERT OR IGNORE INTO metadata VALUES('rule_revision',0)")


def version(db):
    return db.execute("SELECT value FROM metadata WHERE key='rule_revision'").fetchone()[0]


def _definition(value):
    try:
        d = Definition.model_validate(value).model_dump()
    except ValidationError as exc:
        raise DomainError('规则须填写名称、匹配字段、关键词与费用科目；不接受自由表达式。') from exc
    allowed = {c['code'] for c in CHART if c['type'] == 'expense' and c['code'] != '5016' and c['enabled'] and c['selectable']}
    if d['category_code'] not in allowed or any(not t.strip() or len(t) > 200 for t in d['terms']):
        raise DomainError('费用科目或关键词无效。')
    if not d['name'].strip():
        raise DomainError('规则名称不能是空白。')
    d['name'] = d['name'].strip()
    d['terms'] = [t.strip() for t in d['terms']]
    return d


def _get(db, rid, v):
    row = db.execute('SELECT * FROM classification_rules WHERE id=? AND version=?', (rid, v)).fetchone()
    if not row:
        raise DomainError('规则版本不存在。', 'not_found', 404)
    return {**dict(row), 'definition': json.loads(row['definition'])}


def _human_fields(db, tid):
    fields = set()
    for row in db.execute("SELECT before_payload,after_payload FROM transaction_versions WHERE transaction_id=? AND actor='human'", (tid,)):
        before = json.loads(row['before_payload']) if row['before_payload'] else {}
        after = json.loads(row['after_payload'])
        fields.update(k for k in ('category_code', 'budget_line_id') if before.get(k) != after.get(k))
    return fields


def _text(db, t, field):
    if field == 'description':
        return t.get('description', '').casefold()
    keys = {'merchant': ('merchant', 'merchant_name', '商户名称', '商家名称', '交易对方'),
            'product': ('product_description', '商品名称', '商品说明', '商品', 'product')}[field]
    values = []
    for row in db.execute('SELECT raw FROM source_rows WHERE transaction_id=? ORDER BY id', (t['id'],)):
        raw = json.loads(row['raw'])
        for obj in [raw, *[v for v in raw.values() if isinstance(v, dict)]]:
            values.extend(str(obj[k]) for k in keys if isinstance(obj.get(k), (str, int)))
    return '\n'.join(values).casefold()


def proposal(db, t, candidate=None):
    if historical.is_historical(t) or t['kind'] != 'expense':
        return None, '仅处理新流水的已确认消费；历史记录由人工修改。'
    if 'category_code' in _human_fields(db, t['id']):
        return None, '保留人工分类。'
    if t.get('category_code') != '5016':
        return None, '已有分类。'
    rows = [{**dict(r), 'definition': json.loads(r['definition'])} for r in db.execute("SELECT * FROM classification_rules WHERE status='enabled'")]
    if candidate:
        rows = [r for r in rows if r['id'] != candidate['id']] + [candidate]
    matches = []
    for r in rows:
        d = r['definition']; text = _text(db, t, d['match_field'])
        hits = [term.casefold() in text for term in d['terms']]
        if (all(hits) if d['match_mode'] == 'all' else any(hits)):
            matches.append((({'product': 3, 'merchant': 2, 'description': 1}[d['match_field']], d['priority']), r))
    if not matches:
        return None, '未命中。'
    score = max(rank for rank, _ in matches)
    winners = [r for rank, r in matches if rank == score]
    if len({encode({k: r['definition'][k] for k in ('category_code', 'budget_line_id')}) for r in winners}) > 1:
        return None, '同优先级规则判断冲突，保留待核。'
    chosen = sorted(winners, key=lambda r: r['id'])[0]
    if candidate and not any(r['id'] == candidate['id'] for r in winners):
        return None, '已有更具体或更高优先级规则。'
    p = {**raw_payload(t), 'category_code': chosen['definition']['category_code']}
    line = chosen['definition']['budget_line_id']
    if line and not p.get('budget_line_id') and 'budget_line_id' not in _human_fields(db, t['id']):
        p['budget_line_id'] = line
    from .main import validate_budget_line
    try:
        validate_transaction(p, accounts(db), transactions(db), t['id'])
        validate_budget_line(db, p, t)
    except DomainError as exc:
        return None, '规则的预算关联当前不适用：' + exc.message
    return {'payload': p, 'rule_id': chosen['id'], 'rule_version': chosen['version']}, '命中规则。'


def apply_imported(db, tid):
    if not db.execute("SELECT 1 FROM classification_rules WHERE status='enabled' LIMIT 1").fetchone():
        return
    t = transactions(db)[tid]
    result, _ = proposal(db, t)
    if result:
        save_transaction(db, tid, result['payload'], 'rule_classification', f"规则 {result['rule_id']} 第 {result['rule_version']} 版", actor='rule')


def has_conflict(db, target):
    return proposal(db, target)[1].startswith('同优先级规则判断冲突')


def _dependencies(db):
    from .budget_plans import adopted_version
    years = [r[0] for r in db.execute('SELECT DISTINCT year FROM budget_versions')]
    evidence = digest([tuple(r) for r in db.execute('SELECT id,transaction_id,raw FROM source_rows ORDER BY id')])
    return {'data_revision': revision(db), 'rule_revision': version(db), 'evidence_version': evidence,
            'budgets': {str(y): adopted_version(db, y) for y in years if adopted_version(db, y)}}


def preview(db, rid, v):
    candidate = _get(db, rid, v); changes = []; skipped = {}; matching = 0
    for t in transactions(db).values():
        if historical.is_historical(t) or t['kind'] != 'expense' or t.get('category_code') != '5016':
            continue
        result, reason = proposal(db, t, candidate)
        if result:
            matching += 1
            if len(changes) < 200:
                changes.append({'id': t['id'], 'version': t['version'], 'description': t['description'],
                                'before_category': t.get('category_code'), 'category_code': result['payload']['category_code'],
                                'budget_line_id': result['payload'].get('budget_line_id')})
        else:
            skipped[reason] = skipped.get(reason, 0) + 1
    pid = uid(); deps = _dependencies(db)
    payload = {'id': pid, 'rule_id': rid, 'rule_version': v, 'changes': changes, 'matching_count': matching,
               'limited_to': 200, 'skipped': skipped, 'dependencies': deps,
               'note': '启用只影响之后导入的适用记录。应用本次预览需另行确认，最多处理所列 200 笔。'}
    db.execute('INSERT INTO rule_previews VALUES(?,?,?,?,?,?)', (pid, rid, v, encode(deps), encode(payload), now()))
    return payload


def _preview(db, pid):
    row = db.execute('SELECT * FROM rule_previews WHERE id=?', (pid,)).fetchone()
    if not row:
        raise DomainError('预览不存在。', 'not_found', 404)
    if json.loads(row['dependencies']) != _dependencies(db):
        raise DomainError('数据、规则或采用预算已变化，请重新预览。', 'preview_stale', 409)
    return row, json.loads(row['payload'])


def register_routes(app, store):
    @app.post('/api/v1/transactions/batch-classify')
    async def batch_classify(request: Request):
        body = await request.json()
        if not isinstance(body, dict) or set(body) - {'request_id', 'expected_versions', 'patches', 'reason'}:
            raise DomainError('批量分类请求无效。')
        versions = body.get('expected_versions'); patches = body.get('patches'); reason = body.get('reason')
        if (not isinstance(versions, dict) or not 1 <= len(versions) <= 200
            or any(not isinstance(k, str) or type(v) is not int for k, v in versions.items())
            or not isinstance(patches, dict) or not patches or set(patches) - {'category_code', 'budget_line_id'}
            or not isinstance(reason, str) or not reason.strip() or len(reason) > 2000):
            raise DomainError('选择 1–200 笔消费、科目或预算项目，并填写批量修改依据。')
        with store.write() as db:
            fp, cached = operation(db, body.get('request_id'), 'batch_classify', body)
            if cached is not None: return cached
            txs = transactions(db); acc = accounts(db); planned = {}
            from .main import validate_budget_line
            for tid, v in versions.items():
                t = txs.get(tid)
                if not t or t['version'] != v:
                    raise DomainError('选中的交易版本变化，整批未应用，请重新读取。', 'version_conflict', 409)
                if (historical.is_historical(t) and historical.expense_value(t) <= 0) or (not historical.is_historical(t) and t['kind'] != 'expense'):
                    raise DomainError('批量入口只修改已确认的正费用消费；退款与未知性质请进入单笔核对。')
                if historical.is_historical(t):
                    group = historical.plan_edit(t, patches, txs, acc)
                    for changed_id, p in group.items():
                        if historical.is_historical(p): p['historical']['edit_group'] = {'id': body['request_id'], 'members': sorted(group)}
                else:
                    group = {tid: validate_transaction({**raw_payload(t), **patches}, acc, txs, tid)}
                for changed_id, p in group.items():
                    if not historical.is_historical(p):
                        p = validate_transaction(p, acc, {**txs, **group}, changed_id)
                    validate_budget_line(db, p, txs.get(changed_id))
                    if changed_id in planned and planned[changed_id] != p:
                        raise DomainError('关联组存在冲突，整批未应用。', 'relation_conflict', 409)
                    planned[changed_id] = p
            for tid, p in planned.items():
                save_transaction(db, tid, p, 'edit', reason, actor='human')
            advance(db)
            return finish_operation(db, body['request_id'], 'batch_classify', fp,
                                    {'updated_count': len(planned), 'transaction_ids': sorted(planned), 'data_revision': revision(db)})

    @app.get('/api/v1/classification-rules')
    def list_rules():
        with store.read() as db:
            return {'items': [{**dict(r), 'definition': json.loads(r['definition'])} for r in db.execute('SELECT * FROM classification_rules ORDER BY created_at DESC')], 'rule_revision': version(db)}

    @app.post('/api/v1/classification-rules')
    async def save_rule(request: Request):
        body = await request.json()
        if not isinstance(body, dict) or set(body) - {'request_id', 'rule_id', 'expected_version', 'definition'}:
            raise DomainError('规则请求包含不支持字段。')
        with store.write() as db:
            fp, cached = operation(db, body.get('request_id'), 'rule_save', body)
            if cached is not None: return cached
            d = _definition(body.get('definition')); rid = body.get('rule_id') or uid()
            if not isinstance(rid, str) or len(rid) > 160:
                raise DomainError('规则编号无效。')
            current = db.execute('SELECT COALESCE(MAX(version),0) FROM classification_rules WHERE id=?', (rid,)).fetchone()[0]
            if type(body.get('expected_version')) is not int or body['expected_version'] != current:
                raise DomainError('规则版本变化，请重新读取。', 'version_conflict', 409)
            db.execute('INSERT INTO classification_rules VALUES(?,?,?,?,?)', (rid, current + 1, 'draft', encode(d), now()))
            return finish_operation(db, body['request_id'], 'rule_save', fp, {'rule': _get(db, rid, current + 1)})

    @app.post('/api/v1/classification-rules/{rid}/{v}/preview')
    def preview_rule(rid: str, v: int):
        with store.write() as db:
            return {'preview': preview(db, rid, v)}

    @app.post('/api/v1/classification-rules/{rid}/{v}/{command}')
    async def change_rule(rid: str, v: int, command: str, request: Request):
        if command not in {'enable', 'disable'}:
            raise DomainError('规则操作不存在。', 'not_found', 404)
        body = await request.json()
        if not isinstance(body, dict) or set(body) - {'request_id', 'preview_id', 'expected_rule_revision'}:
            raise DomainError('规则操作包含不支持字段。')
        with store.write() as db:
            action = f'rule_{command}:{rid}:{v}'; fp, cached = operation(db, body.get('request_id'), action, body)
            if cached is not None: return cached
            r = _get(db, rid, v)
            if type(body.get('expected_rule_revision')) is not int or body['expected_rule_revision'] != version(db):
                raise DomainError('规则状态变化，请重新读取。', 'version_conflict', 409)
            if command == 'enable':
                row, _ = _preview(db, body.get('preview_id'))
                if row['rule_id'] != rid or row['rule_version'] != v or r['status'] != 'draft':
                    raise DomainError('需要该草稿版本的当前预览。', 'rule_state_conflict', 409)
                db.execute("UPDATE classification_rules SET status='disabled' WHERE id=? AND status='enabled'", (rid,))
                db.execute("UPDATE classification_rules SET status='enabled' WHERE id=? AND version=?", (rid, v))
            else:
                if r['status'] != 'enabled':
                    raise DomainError('只能停用当前启用版本。', 'rule_state_conflict', 409)
                db.execute("UPDATE classification_rules SET status='disabled' WHERE id=? AND version=?", (rid, v))
            db.execute("UPDATE metadata SET value=value+1 WHERE key='rule_revision'")
            return finish_operation(db, body['request_id'], action, fp, {'rule': _get(db, rid, v), 'rule_revision': version(db)})

    @app.post('/api/v1/rule-previews/{pid}/apply')
    async def apply_preview(pid: str, request: Request):
        body = await request.json()
        if not isinstance(body, dict) or set(body) != {'request_id'}:
            raise DomainError('应用预览只接受 request_id。')
        with store.write() as db:
            action = 'rule_apply:' + pid; fp, cached = operation(db, body.get('request_id'), action, body)
            if cached is not None: return cached
            row, p = _preview(db, pid); candidate = _get(db, row['rule_id'], row['rule_version'])
            if candidate['status'] != 'enabled':
                raise DomainError('先启用规则，再重新预览后应用。', 'rule_not_enabled', 409)
            for item in p['changes']:
                apply_imported(db, item['id'])
            if p['changes']: advance(db)
            return finish_operation(db, body['request_id'], action, fp, {'applied_count': len(p['changes']), 'transaction_ids': [x['id'] for x in p['changes']], 'data_revision': revision(db)})
