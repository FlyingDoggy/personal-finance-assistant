"""Queryable master data derived from explicit facts, with a frozen chart baseline.

No merchant is guessed from a description. Source names are only matched exactly;
normalizing aliases to one commercial entity requires a later explicit decision.
These are current master data and version snapshots, not a full SCD2 warehouse.
"""
from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone


DDL = (
    '''CREATE TABLE IF NOT EXISTS classification_versions(
       version INTEGER PRIMARY KEY,payload TEXT NOT NULL,sha256 TEXT NOT NULL,
       created_at TEXT NOT NULL,basis TEXT NOT NULL)''',
    '''CREATE TABLE IF NOT EXISTS chart_accounts(
       code TEXT PRIMARY KEY,name TEXT NOT NULL,type TEXT NOT NULL,
       selectable INTEGER NOT NULL,enabled INTEGER NOT NULL,supported INTEGER NOT NULL,
       classification_version INTEGER NOT NULL REFERENCES classification_versions(version))''',
    '''CREATE TABLE IF NOT EXISTS account_versions(
       account_id TEXT NOT NULL REFERENCES accounts(id),version INTEGER NOT NULL,
       payload TEXT NOT NULL,recorded_at TEXT NOT NULL,basis TEXT NOT NULL,
       PRIMARY KEY(account_id,version))''',
    '''CREATE TABLE IF NOT EXISTS merchants(
       id TEXT PRIMARY KEY,name TEXT NOT NULL UNIQUE,created_at TEXT NOT NULL,
       basis TEXT NOT NULL)''',
    '''CREATE TABLE IF NOT EXISTS merchant_aliases(
       merchant_id TEXT NOT NULL REFERENCES merchants(id),alias TEXT NOT NULL,
       basis TEXT NOT NULL,PRIMARY KEY(merchant_id,alias))''',
    '''CREATE TABLE IF NOT EXISTS transaction_merchants(
       transaction_id TEXT NOT NULL REFERENCES transactions(id),transaction_version INTEGER NOT NULL,
       merchant_id TEXT NOT NULL REFERENCES merchants(id),source_id TEXT NOT NULL,basis TEXT NOT NULL,
       PRIMARY KEY(transaction_id,merchant_id,source_id))''',
    '''CREATE TABLE IF NOT EXISTS tags(
       id TEXT PRIMARY KEY,namespace TEXT NOT NULL,name TEXT NOT NULL,created_at TEXT NOT NULL,
       UNIQUE(namespace,name))''',
    '''CREATE TABLE IF NOT EXISTS transaction_tags(
       transaction_id TEXT NOT NULL REFERENCES transactions(id),transaction_version INTEGER NOT NULL,
       tag_id TEXT NOT NULL REFERENCES tags(id),basis TEXT NOT NULL,
       PRIMARY KEY(transaction_id,tag_id))''',
    '''CREATE TABLE IF NOT EXISTS budget_lines(
       year INTEGER NOT NULL,budget_version INTEGER NOT NULL,id TEXT NOT NULL,position INTEGER NOT NULL,
       name TEXT NOT NULL,group_name TEXT NOT NULL,allocation TEXT NOT NULL,
       execution_basis TEXT NOT NULL,actual_basis TEXT NOT NULL,monthly_minor TEXT NOT NULL,
       annual_minor TEXT NOT NULL,payload TEXT NOT NULL,
       PRIMARY KEY(year,budget_version,id),
       FOREIGN KEY(year,budget_version) REFERENCES budget_versions(year,version))''',
    'CREATE INDEX IF NOT EXISTS merchant_transaction_lookup ON transaction_merchants(merchant_id,transaction_id)',
    'CREATE INDEX IF NOT EXISTS tag_transaction_lookup ON transaction_tags(tag_id,transaction_id)',
)

MERCHANT_FIELDS = ('merchant_name', 'merchant', '商户名称', '商家名称', '交易对方', '对方名称')
UNCERTAIN_TAG_PARTS = ('待确认', '未确认', '待区分', '候选', '待补')


def _encode(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'))


def _now():
    return datetime.now(timezone.utc).isoformat()


def _id(namespace, value):
    return namespace + '_' + hashlib.sha256(value.encode()).hexdigest()[:32]


def initialize(db):
    """Called inside the Store migration transaction; does not commit."""
    from .domain import CHART
    for statement in DDL:
        db.execute(statement)
    payload = _encode(CHART)
    db.execute('INSERT OR IGNORE INTO classification_versions VALUES(?,?,?,?,?)',
               (1, payload, hashlib.sha256(payload.encode()).hexdigest(), _now(),
                '应用 v0.4 的 37 科目基线；从本次建表起记录，未伪造此前生效区间。'))
    baseline = json.loads(db.execute('SELECT payload FROM classification_versions WHERE version=1').fetchone()[0])
    for item in baseline:
        db.execute('INSERT OR IGNORE INTO chart_accounts VALUES(?,?,?,?,?,?,?)',
                   (item['code'], item['name'], item['type'], int(item['selectable']),
                    int(item['enabled']), int(item['supported']), 1))


def _merchant_names(raw):
    """Only named structured fields; no recursive search in arbitrary blobs."""
    if not isinstance(raw, dict):
        return []
    result = set()
    for fields in (raw, raw.get('fields')):
        if not isinstance(fields, dict):
            continue
        for key in MERCHANT_FIELDS:
            name = fields.get(key)
            if isinstance(name, str) and name.strip() and len(name.strip()) <= 500:
                result.add(name.strip())
    return sorted(result)


def _is_purchase(t):
    if t.get('kind') in {'expense', 'refund'}:
        return True
    h = t.get('historical')
    return isinstance(h, dict) and h.get('event') in {'消费', '车辆租赁付款', '消费退款', '消费退款或返利'}


def sync(db):
    """Rebuild version-linked relationships; source evidence and facts never change."""
    from .domain import DomainError
    stamp = _now()
    for row in db.execute('SELECT * FROM accounts'):
        item = dict(row)
        payload = _encode(item)
        previous = db.execute('SELECT payload FROM account_versions WHERE account_id=? AND version=?',
                              (item['id'], item['version'])).fetchone()
        if previous and previous['payload'] != payload:
            raise DomainError('账户内容改变但版本未递增，不能覆盖原账户快照。', 'account_version_conflict', 409)
        db.execute('INSERT OR IGNORE INTO account_versions VALUES(?,?,?,?,?)',
                   (item['id'], item['version'], payload, stamp, '首次观察到的当前账户版本；不反推旧版本。'))

    txs = {row['id']: {**json.loads(row['payload']), 'id': row['id'], 'version': row['version']}
           for row in db.execute('SELECT id,version,payload FROM transactions')}
    db.execute('DELETE FROM transaction_merchants')
    db.execute('DELETE FROM transaction_tags')
    for source in db.execute('SELECT id,transaction_id,raw FROM source_rows WHERE transaction_id IS NOT NULL'):
        t = txs.get(source['transaction_id'])
        if not t or not _is_purchase(t):
            continue
        for name in _merchant_names(json.loads(source['raw'])):
            mid = _id('merchant', name)
            db.execute('INSERT OR IGNORE INTO merchants VALUES(?,?,?,?)',
                       (mid, name, stamp, '消费来源的明确交易对方／商户字段；仅按完整名称精确匹配。'))
            db.execute('INSERT OR IGNORE INTO merchant_aliases VALUES(?,?,?)',
                       (mid, name, '来源原名；未推断为其他商户的别名。'))
            db.execute('INSERT OR IGNORE INTO transaction_merchants VALUES(?,?,?,?,?)',
                       (t['id'], t['version'], mid, source['id'], '原始结构化商户字段'))
    for t in txs.values():
        h = t.get('historical', {})
        for key, namespace in (('behavior', 'behavior'), ('reimb', 'reimbursement')):
            name = h.get(key)
            if not isinstance(name, str) or not name.strip() or len(name) > 500:
                continue
            name = name.strip()
            if any(part in name for part in UNCERTAIN_TAG_PARTS):
                continue
            tag_id = _id('tag', namespace + ':' + name)
            db.execute('INSERT OR IGNORE INTO tags VALUES(?,?,?,?)', (tag_id, namespace, name, stamp))
            db.execute('INSERT INTO transaction_tags VALUES(?,?,?,?)',
                       (t['id'], t['version'], tag_id, '当前历史解释的明确 ' + key + ' 字段'))

    for version in db.execute('SELECT year,version,payload FROM budget_versions ORDER BY year,version'):
        payload = json.loads(version['payload'])
        for position, line in enumerate(payload['lines']):
            # The original line ID is retained across adopted budget versions.
            values = (version['year'], version['version'], line['id'], position,
                      line['name'], line['group'], line['allocation'], line['execution_basis'],
                      line['actual_basis'], _encode(line['monthly_minor']),
                      str(sum(int(value) for value in line['monthly_minor'])), _encode(line))
            previous = db.execute('SELECT payload FROM budget_lines WHERE year=? AND budget_version=? AND id=?',
                                  values[:3]).fetchone()
            if previous and previous['payload'] != values[-1]:
                raise DomainError('已采用预算项目在同版本下发生变化，不能覆盖旧版本。', 'budget_version_conflict', 409)
            db.execute('INSERT OR IGNORE INTO budget_lines VALUES(?,?,?,?,?,?,?,?,?,?,?,?)', values)


def dimension_version(db):
    return db.execute('SELECT MAX(version) FROM classification_versions').fetchone()[0]


def chart_accounts(db):
    return [{**dict(row), **{key: bool(row[key]) for key in ('selectable', 'enabled', 'supported')}}
            for row in db.execute('SELECT * FROM chart_accounts ORDER BY code')]


def list_merchants(db):
    return [{**dict(row), 'aliases': [a['alias'] for a in db.execute(
        'SELECT alias FROM merchant_aliases WHERE merchant_id=? ORDER BY alias', (row['id'],))]}
        for row in db.execute('''SELECT m.id,m.name,m.basis,COUNT(DISTINCT tm.transaction_id) AS transaction_count
                              FROM merchants m LEFT JOIN transaction_merchants tm ON tm.merchant_id=m.id
                              GROUP BY m.id ORDER BY m.name,m.id''')]


def list_tags(db):
    return [dict(row) for row in db.execute('''SELECT t.id,t.namespace,t.name,COUNT(tt.transaction_id) AS transaction_count
                         FROM tags t LEFT JOIN transaction_tags tt ON tt.tag_id=t.id
                         GROUP BY t.id ORDER BY t.namespace,t.name,t.id''')]


def list_budget_lines(db):
    result = []
    for row in db.execute('SELECT * FROM budget_lines ORDER BY year DESC,budget_version DESC,position'):
        item = dict(row)
        item['group'] = item.pop('group_name')
        item['monthly_minor'] = json.loads(item['monthly_minor'])
        item.pop('payload')
        result.append(item)
    return result


def summary(db):
    version = dimension_version(db)
    classification = db.execute('SELECT version,created_at,basis FROM classification_versions WHERE version=?', (version,)).fetchone()
    account_rows = [dict(row) for row in db.execute('''SELECT a.id,a.name,a.kind,a.currency,a.version,
                          (SELECT COUNT(*) FROM account_versions v WHERE v.account_id=a.id) AS history_count
                          FROM accounts a ORDER BY a.name,a.id''')]
    groups = {'chart_accounts': chart_accounts(db), 'accounts': account_rows,
              'merchants': list_merchants(db), 'tags': list_tags(db), 'budget_lines': list_budget_lines(db)}
    return {'dimension_version': version,
            'classification': {**dict(classification), 'kind': 'baseline', 'notes': classification['basis']},
            'counts': {name: len(rows) for name, rows in groups.items()}, **groups,
            'limitations': ['当前为可查询主数据与分类基线版本，尚未启用按生效期管理的 SCD2。',
                            '科目保持当前业务模板的 37 项；分类结构变更与目录维护界面尚未开放。',
                            '商户仅取消费来源明确字段并按完整原名匹配；不同名称不会自动认作同一商户。',
                            '标签来自现有明确标注，待确认／候选标记不冒充已确认用途。']}
