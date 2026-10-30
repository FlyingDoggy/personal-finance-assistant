"""External closing observations and ledger comparisons; never balancing entries."""
from __future__ import annotations

import json

from fastapi import Request

from . import coverage, historical
from .domain import DomainError, explainable, minor, money, movements, review_reasons, valid_date
from .reports import account_balance, account_effective_day
from .storage import accounts, advance, encode, finish_operation, now, operation, revision, transactions, uid


def initialize(db):
    db.execute('''CREATE TABLE IF NOT EXISTS balance_observations(
        id TEXT PRIMARY KEY,account_id TEXT NOT NULL REFERENCES accounts(id),
        observed_date TEXT NOT NULL,version INTEGER NOT NULL,balance_minor INTEGER NOT NULL,
        definition TEXT NOT NULL,evidence TEXT NOT NULL,created_at TEXT NOT NULL,
        UNIQUE(account_id,observed_date,version))''')
    db.execute('''CREATE TABLE IF NOT EXISTS account_opening_events(
        id TEXT PRIMARY KEY,account_id TEXT NOT NULL REFERENCES accounts(id),
        previous_version INTEGER NOT NULL,version INTEGER NOT NULL,
        before_payload TEXT NOT NULL,after_payload TEXT NOT NULL,reason TEXT NOT NULL,created_at TEXT NOT NULL,
        UNIQUE(account_id,version))''')
    db.execute("INSERT OR IGNORE INTO metadata VALUES('observation_revision',0)")


def _body(value, allowed):
    if not isinstance(value, dict) or set(value) - allowed:
        raise DomainError('余额核对请求包含不支持的字段。')
    return value


def _account(db, aid):
    if not isinstance(aid, str) or aid not in (acc := accounts(db)):
        raise DomainError('账户不存在。', 'not_found', 404)
    return acc[aid]


def _text(value, label):
    if not isinstance(value, str) or not value.strip() or len(value) > 2000:
        raise DomainError(label + '须为 1–2000 字的说明。')
    return value.strip()


def observation_revision(db):
    return db.execute("SELECT value FROM metadata WHERE key='observation_revision'").fetchone()[0]


def _observation(row):
    return {**dict(row), 'balance_minor': money(row['balance_minor']), 'currency': 'CNY'}


def list_observations(db, aid, observed_date=None, limit=100, offset=0):
    _account(db, aid)
    if not 1 <= limit <= 200 or offset < 0:
        raise DomainError('分页 limit 须为 1–200，offset 不可为负。')
    where = 'account_id=?'; args = [aid]
    if observed_date is not None:
        valid_date(observed_date); where += ' AND observed_date=?'; args.append(observed_date)
    total = db.execute('SELECT COUNT(*) FROM balance_observations WHERE ' + where, args).fetchone()[0]
    rows = db.execute('SELECT * FROM balance_observations WHERE ' + where + ' ORDER BY observed_date DESC,version DESC LIMIT ? OFFSET ?', (*args, limit, offset)).fetchall()
    return {'items': [_observation(row) for row in rows], 'total': total, 'limit': limit, 'offset': offset,
            'data_revision': revision(db), 'observation_revision': observation_revision(db)}


def add_observation(db, body):
    aid = body.get('account_id'); account = _account(db, aid)
    day = valid_date(body.get('observed_date')); value = minor(body.get('balance_minor'), '银行日终余额')
    evidence = _text(body.get('evidence'), '余额依据')
    expected = body.get('expected_version')
    current = db.execute('SELECT MAX(version) FROM balance_observations WHERE account_id=? AND observed_date=?', (aid, day)).fetchone()[0] or 0
    if type(expected) is not int or expected != current:
        raise DomainError('该日余额观察已有新版本，请重新读取后再更正。', 'version_conflict', 409)
    oid = uid()
    db.execute('INSERT INTO balance_observations VALUES(?,?,?,?,?,?,?,?)',
               (oid, aid, day, current + 1, value, 'cash_closing' if account['kind'] == 'cash' else 'net_credit_debt', evidence, now()))
    db.execute("UPDATE metadata SET value=value+1 WHERE key='observation_revision'")
    return _observation(db.execute('SELECT * FROM balance_observations WHERE id=?', (oid,)).fetchone())


def set_opening(db, aid, body):
    account = _account(db, aid)
    if type(body.get('expected_version')) is not int or body['expected_version'] != account['version']:
        raise DomainError('账户版本已变化，请重新读取起点。', 'version_conflict', 409)
    reason = _text(body.get('reason'), '起点修改理由')
    value = body.get('opening_balance_minor'); day = body.get('opening_date'); evidence = body.get('evidence', '')
    if value is None:
        if (day is not None and day != '') or evidence != '':
            raise DomainError('起点未知时请同时清空起点日期和余额依据。')
        day = None
    else:
        value = minor(value, '日初余额'); day = valid_date(day); evidence = _text(evidence, '起点余额依据')
    after = {**account, 'opening_balance': value, 'opening_date': day, 'evidence': evidence, 'version': account['version'] + 1}
    db.execute('UPDATE accounts SET opening_balance=?,opening_date=?,evidence=?,version=? WHERE id=?',
               (value, day, evidence, after['version'], aid))
    db.execute('INSERT INTO account_opening_events VALUES(?,?,?,?,?,?,?,?)',
               (uid(), aid, account['version'], after['version'], encode(account), encode(after), reason, now()))
    advance(db)
    return after


def _quality(db, aid, kind, start, end, txs):
    bases = ['payment'] if kind == 'cash' else ['consumption', 'payment']
    if start is None or start > end:
        return {'complete': False, 'declarations': [], 'reasons': ['余额起点未知，不能确认完整余额核对窗口。']}
    y, m = map(int, start[:7].split('-')); ey, em = map(int, end[:7].split('-'))
    count = (ey - y) * 12 + em - m + 1
    if count > 1200:
        return {'complete': False, 'declarations': [], 'reasons': ['核对窗口超过 100 年，请检查起点日期。']}
    rows = []
    for index in range(count):
        year, zero_month = divmod(y * 12 + m - 1 + index, 12)
        month = f'{year:04d}-{zero_month + 1:02d}'
        rows.extend(coverage.get_declaration(db, aid, month, basis, txs=txs) for basis in bases)
    return {'complete': bool(rows) and all(r['complete'] for r in rows), 'declarations': rows,
            'reasons': [] if rows and all(r['complete'] for r in rows) else ['核对窗口内的账户/月/口径覆盖尚未全部确认。']}


def workspace(db, as_of, account_id=None):
    valid_date(as_of)
    acc = accounts(db); txs = transactions(db)
    if account_id is not None: _account(db, account_id)
    items = []
    for aid, account in acc.items():
        if account_id and aid != account_id: continue
        latest = db.execute('''SELECT * FROM balance_observations WHERE account_id=? AND observed_date<=?
            ORDER BY observed_date DESC,version DESC LIMIT 1''', (aid, as_of)).fetchone()
        obs = _observation(latest) if latest else None
        end = obs['observed_date'] if obs else as_of
        book = account_balance(account, txs, end)
        start = account['opening_date'] if account['opening_balance'] is not None and account['opening_date'] <= end else None
        relevant = []; undated = []
        for tid, t in txs.items():
            delta = movements(t).get(aid, 0)
            if not delta: continue
            day = account_effective_day(account, t)
            if not day:
                undated.append(tid)
            elif day <= end and (start is None or day >= start):
                relevant.append({'id': tid, 'date': day, 'delta': delta, 'explainable': explainable(t)})
        unresolved = [r['id'] for r in relevant if not r['explainable']]
        quality = _quality(db, aid, account['kind'], start, end, txs)
        projection = book['observed_balance_minor']
        difference = money(int(obs['balance_minor']) - int(projection)) if obs and projection is not None else None
        reasons = list(quality['reasons'])
        if not obs: reasons.insert(0, '尚未录入不晚于截止日的银行日终余额。')
        if unresolved: reasons.append(f'{len(unresolved)} 笔账户变动的业务关系尚未解释。')
        if undated: reasons.append(f'{len(undated)} 笔账户变动未定位日期，无法判断是否属于窗口。')
        if difference is not None and int(difference): reasons.append('银行余额与按已知流水推演的余额有差异；请核对遗漏、重复、日期和余额定义。')
        if not obs: status = 'no_observation'
        elif start is None: status = 'opening_unknown'
        elif unresolved or undated: status = 'needs_review'
        elif int(difference): status = 'difference'
        elif quality['complete']: status = 'reconciled'
        else: status = 'amounts_agree_partial'
        interval = None
        if obs:
            prior = db.execute('''SELECT * FROM balance_observations WHERE account_id=? AND observed_date<?
                ORDER BY observed_date DESC,version DESC LIMIT 1''', (aid, end)).fetchone()
            if prior:
                # Both observations are closing balances: the prior day is excluded.
                rows = []
                for tid, t in txs.items():
                    delta = movements(t).get(aid, 0); day = account_effective_day(account, t)
                    if delta and day and prior['observed_date'] < day <= end: rows.append((tid, t, delta))
                direction = 1 if account['kind'] == 'cash' else -1
                observed_change = int(obs['balance_minor']) - prior['balance_minor']
                known_change = direction * sum(r[2] for r in rows)
                interval = {'start_date': prior['observed_date'], 'end_date': end,
                            'observed_change_minor': money(observed_change), 'known_change_minor': money(known_change),
                            'difference_minor': money(observed_change - known_change),
                            'transaction_ids': [r[0] for r in rows], 'unexplained_ids': [r[0] for r in rows if not explainable(r[1])],
                            'undated_ids': undated, 'absolute_balance_reconciled': False,
                            'basis': '前后两次银行日终观察的变动，仅核对该区间，不能替代有证据的账本期初。'}
        items.append({'account': book, 'observation': obs, 'comparison_date': end,
                      'book_balance_minor': book['balance_minor'] if not undated else None,
                      'source_projection_minor': projection, 'difference_minor': difference,
                      'status': status, 'balance_reconciled': status == 'reconciled', 'quality': quality,
                      'transaction_ids': [r['id'] for r in relevant], 'unexplained_ids': unresolved,
                      'undated_ids': undated, 'reasons': reasons, 'interval': interval})
    return {'as_of': as_of, 'items': items, 'data_revision': revision(db),
            'observation_revision': observation_revision(db), 'coverage_version': coverage.version(db)}


def register_routes(app, store):
    @app.get('/api/v1/reconciliation')
    def get_workspace(as_of: str, account_id: str | None = None):
        with store.read() as db: return workspace(db, as_of, account_id)

    @app.get('/api/v1/balance-observations')
    def get_observations(account_id: str, observed_date: str | None = None, limit: int = 100, offset: int = 0):
        with store.read() as db: return list_observations(db, account_id, observed_date, limit, offset)

    @app.post('/api/v1/balance-observations')
    async def post_observation(request: Request):
        body = _body(await request.json(), {'request_id', 'account_id', 'observed_date', 'balance_minor', 'evidence', 'expected_version'})
        with store.write() as db:
            fp, cached = operation(db, body.get('request_id'), 'balance_observation', body)
            if cached is not None: return cached
            obs = add_observation(db, body)
            return finish_operation(db, body['request_id'], 'balance_observation', fp,
                                    {'observation': obs, 'data_revision': revision(db), 'observation_revision': observation_revision(db)})

    @app.post('/api/v1/accounts/{aid}/opening')
    async def post_opening(aid: str, request: Request):
        body = _body(await request.json(), {'request_id', 'expected_version', 'opening_balance_minor', 'opening_date', 'evidence', 'reason'})
        with store.write() as db:
            action = 'account_opening:' + aid
            fp, cached = operation(db, body.get('request_id'), action, body)
            if cached is not None: return cached
            after = set_opening(db, aid, body)
            return finish_operation(db, body['request_id'], action, fp,
                                    {'account': account_balance(after, transactions(db)), 'data_revision': revision(db)})

    @app.get('/api/v1/accounts/{aid}/opening-history')
    def get_opening_history(aid: str):
        with store.read() as db:
            _account(db, aid)
            rows = db.execute('SELECT * FROM account_opening_events WHERE account_id=? ORDER BY version DESC', (aid,))
            def snapshot(payload):
                value=json.loads(payload)
                if value.get('opening_balance') is not None: value['opening_balance']=money(value['opening_balance'])
                return value
            return {'items': [{**dict(r), 'before_payload': snapshot(r['before_payload']), 'after_payload': snapshot(r['after_payload'])} for r in rows],
                    'data_revision': revision(db)}
