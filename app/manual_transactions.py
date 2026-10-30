"""Human-sourced entries share the ledger command and immutable version history."""
from __future__ import annotations

from fastapi import Request

from . import historical
from .domain import DomainError, movements, review_reasons, validate_transaction
from .reports import common, effects, transaction_dto
from .storage import accounts, advance, finish_operation, operation, revision, save_transaction, transactions, uid


def _body(value, allowed):
    if not isinstance(value, dict) or set(value) - allowed:
        raise DomainError('手工补录请求包含不支持的字段。')
    return value


def preview(db, payload, reason):
    if not isinstance(payload, dict) or 'source_id' in payload:
        raise DomainError('手工补录须填写业务字段，不能自造银行来源编号。')
    if not isinstance(reason, str) or not reason.strip() or len(reason) > 2000:
        raise DomainError('请填写手工补录依据，说明交易的来源。')
    acc = accounts(db); txs = transactions(db)
    p = validate_transaction(payload, acc, txs)
    from .main import validate_budget_line
    validate_budget_line(db, p)
    # Identical amount/date is a candidate only; it never grants merge authority.
    candidates = []
    for tid, t in txs.items():
        if (t['account_id'] == p['account_id'] and t.get('date') == p['date']
            and movements(t).get(p['account_id']) == int(p['amount_minor'])):
            candidates.append({'id': tid, 'version': t['version'], 'date': t.get('date'),
                               'description': t.get('description', ''), 'kind': t['kind'],
                               'account_id': t['account_id']})
    return {'payload': p, 'reason': reason.strip(), 'possible_duplicates': candidates[:100],
            'possible_duplicate_count': len(candidates), 'effects': effects(p, acc),
            'review_reasons': review_reasons(p), 'expected_data_revision': revision(db),
            'source_basis': '人工补录；填写依据与初始业务值保存在第 1 版历史，没有银行原件。'}


def register_routes(app, store):
    @app.post('/api/v1/transactions/manual/preview')
    async def preview_manual(request: Request):
        body = _body(await request.json(), {'payload', 'reason'})
        with store.read() as db:
            return {**preview(db, body.get('payload'), body.get('reason')), **common(db)}

    @app.post('/api/v1/transactions')
    async def create_manual(request: Request):
        body = _body(await request.json(), {'request_id', 'payload', 'reason', 'expected_data_revision', 'allow_possible_duplicate'})
        with store.write() as db:
            fp, cached = operation(db, body.get('request_id'), 'transaction_manual_create', body)
            if cached is not None: return cached
            if type(body.get('expected_data_revision')) is not int or body['expected_data_revision'] != revision(db):
                raise DomainError('账本已变化，请重新检查手工补录的统计影响。', 'preview_stale', 409)
            if type(body.get('allow_possible_duplicate', False)) is not bool:
                raise DomainError('重复候选确认须为布尔值。')
            checked = preview(db, body.get('payload'), body.get('reason'))
            if checked['possible_duplicate_count'] and not body.get('allow_possible_duplicate', False):
                raise DomainError('发现同账户同日同额记录，请核对后明确确认这是另一笔交易。', 'duplicate_confirmation_required', 409)
            tid = uid()
            save_transaction(db, tid, checked['payload'], 'manual_create', checked['reason'], actor='human')
            advance(db)
            result = {'transaction': transaction_dto(db, transactions(db)[tid]), **common(db)}
            return finish_operation(db, body['request_id'], 'transaction_manual_create', fp, result)
