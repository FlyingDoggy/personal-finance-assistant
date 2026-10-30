"""Deterministic, same-window consumption analysis and immutable input snapshots."""
import json
from datetime import date
from decimal import Decimal, ROUND_HALF_UP
from zoneinfo import ZoneInfo
from datetime import datetime

from fastapi import Request

from . import historical, coverage
from .budget import normalize_reimbursements
from .budget_plans import adopted_row, adopted_version
from .domain import DomainError, NAMES, month_bounds, money, recognition_date, review_reasons, explainable
from .reports import effects, budget_projection
from .storage import accounts, digest, encode, finish_operation, now, operation, revision, transactions, uid

DEFINITION_VERSION = 'consumption-analysis-v1'
POLICY_VERSION = 'same-window-received-reimbursements-v1'


def initialize(db):
    db.execute('''CREATE TABLE IF NOT EXISTS analysis_snapshots(
        id TEXT PRIMARY KEY,parameters TEXT NOT NULL,dependencies TEXT NOT NULL,
        payload TEXT NOT NULL,created_at TEXT NOT NULL)''')


def _months(start, end):
    month_bounds(start); month_bounds(end)
    first = int(start[:4]) * 12 + int(start[5:]) - 1
    last = int(end[:4]) * 12 + int(end[5:]) - 1
    if not 0 <= last - first < 12:
        raise DomainError('请选择按自然月计的连续 1–12 个月。')
    return [f'{n//12:04d}-{n%12+1:02d}' for n in range(first, last + 1)]


def _rounded(value):
    return str(int(Decimal(value).quantize(Decimal('1'), rounding=ROUND_HALF_UP)))


def dependencies(db, params):
    from .catalog import dimension_version
    from .classification_rules import version as rule_version
    months = _months(params['start_month'], params['end_month'])
    evidence = digest([tuple(r) for r in db.execute('SELECT id,transaction_id,raw FROM source_rows ORDER BY id')])
    return {'data_revision': revision(db), 'evidence_version': evidence, 'coverage_version': coverage.version(db),
            'rule_version': rule_version(db), 'dimension_version': dimension_version(db),
            'budget_versions': {str(y): adopted_version(db, y) for y in sorted({int(m[:4]) for m in months})},
            'definition_version': DEFINITION_VERSION, 'policy_version': POLICY_VERSION}


def calculate(db, start_month, end_month, mode='period', estimate=False, minimum_minor='200000', large_basis='gross', living_scope='undeclared'):
    if not isinstance(mode, str) or mode not in {'period', 'budget_comparison'} or type(estimate) is not bool:
        raise DomainError('报销模式须为 period 或 budget_comparison。')
    if not isinstance(minimum_minor, str) or not minimum_minor.isdigit() or len(minimum_minor) > 15 or int(minimum_minor) < 1:
        raise DomainError('大额门槛须为正整数分字符串。')
    if living_scope not in ('undeclared', 'observed_income', 'net_salary_plan'):
        raise DomainError('生活预算来源范围无效。')
    if large_basis not in ('gross', 'net'):
        raise DomainError('大额筛选口径须为 gross 或 net。')
    months = _months(start_month, end_month); m = len(months)
    params = {'start_month': start_month, 'end_month': end_month, 'mode': mode, 'estimate': estimate, 'minimum_minor': minimum_minor, 'large_basis': large_basis, 'living_scope': living_scope}
    txs = transactions(db); acc = accounts(db)
    undated = [t['id'] for t in txs.values() if historical.scoped(t) and not recognition_date(t)]
    rows = []; categories = {}; merchants = {}; purposes = {}; unknown = []; review = []; family = 0
    buckets = {month: {'month': month, 'income': 0, 'expense': 0, 'reimbursement': 0} for month in months}
    for t in txs.values():
        day = recognition_date(t)
        if not historical.scoped(t) or not day or day[:7] not in buckets:
            continue
        effect = effects(t, acc); b = buckets[day[:7]]
        inc = int(effect['income_minor'] or '0'); exp = int(effect['expense_minor'] or '0')
        b['income'] += inc; b['expense'] += exp
        is_reimbursement = t['historical']['event'] == '报销回款' if historical.is_historical(t) else t['kind'] == 'reimbursement'
        is_family = t['historical']['event'] == '家庭赞助' if historical.is_historical(t) else t['kind'] == 'family_support'
        if is_reimbursement: b['reimbursement'] += inc
        if is_family: family += inc
        if effect['expense_minor'] is None or effect['income_minor'] is None or (historical.is_historical(t) and not explainable(t)):
            unknown.append(t['id'])
        if review_reasons(t): review.append(t['id'])
        if exp:
            parent_id = historical.refund_parent(t) if historical.is_historical(t) else t.get('refund_of')
            parent = txs.get(parent_id)
            code = parent.get('category_code') if exp < 0 and parent else t.get('category_code')
            category = categories.setdefault(code, {'code': code, 'name': NAMES.get(code, '用途待确认'), 'amount': 0, 'transaction_ids': []})
            category['amount'] += exp; category['transaction_ids'].append(t['id'])
            names = list(db.execute('SELECT DISTINCT m.id,m.name FROM transaction_merchants tm JOIN merchants m ON m.id=tm.merchant_id WHERE tm.transaction_id=?', (t['id'],)))
            merchant_key = names[0]['id'] if len(names) == 1 else 'description:' + t['description']
            merchant = merchants.setdefault(merchant_key, {'name': names[0]['name'] if len(names) == 1 else t['description'],
                'basis': 'merchant' if len(names) == 1 else 'description', 'amount': 0, 'transaction_ids': []})
            merchant['amount'] += exp; merchant['transaction_ids'].append(t['id'])
            rows.append({'id': t['id'], 'version': t['version'], 'date': day, 'description': t['description'],
                         'category_code': code, 'account_id': t['account_id'], 'expense_minor': money(exp), 'refund_of': parent_id,
                         'source_amount_minor': t['amount_minor']})
    income = sum(b['income'] for b in buckets.values()); expense = sum(b['expense'] for b in buckets.values()); received = sum(b['reimbursement'] for b in buckets.values())
    normalized = normalize_reimbursements(money(income), money(expense), money(received), m)
    smoothed = normalized['monthly_reimbursement_minor']
    monthly = []; complete = not bool(undated); budget_cache = {}; budget_rows = []
    today = datetime.now(ZoneInfo('Asia/Shanghai')).date()
    for index, month in enumerate(months):
        b = buckets[month]
        q = {basis: coverage.month_quality(db, month, basis, txs=txs) for basis in ('consumption', 'payment')}
        # A declaration cannot make an unfinished/future month a closed observation window.
        ended = date.fromisoformat(month_bounds(month)[1]) < today
        month_complete = q['consumption']['complete'] and ended and not any((recognition_date(txs[tid]) or '').startswith(month) for tid in unknown)
        complete = complete and month_complete
        correction = b['reimbursement'] if mode == 'period' else int(smoothed[index])
        monthly.append({'month': month, 'income_minor': money(b['income']), 'expense_minor': money(b['expense']),
                        'received_reimbursement_minor': money(b['reimbursement']), 'adjustment_minor': money(correction),
                        'adjusted_income_minor': money(b['income'] - correction), 'adjusted_expense_minor': money(b['expense'] - correction),
                        'surplus_minor': money(b['income'] - b['expense']), 'quality': q, 'complete': month_complete, 'month_closed': ended})
        year = int(month[:4])
        if year not in budget_cache:
            row = adopted_row(db, year)
            if row:
                computed, meta = budget_projection(db, json.loads(row['payload']))
                computed['unsupported_payment_actuals'] = meta['unsupported_payment_actuals']
                budget_cache[year] = computed
            else:
                budget_cache[year] = None
        plan = budget_cache[year]
        if plan:
            item = plan['months'][int(month[5:]) - 1]
            for line in plan['lines']:
                key = (year, line['id'])
                purpose = purposes.setdefault(key, {'year': year, 'line_id': line['id'], 'name': line['name'],
                    'basis': line['actual_basis'], 'planned': 0, 'used': 0, 'transaction_ids': []})
                purpose['planned'] += int(line['monthly_minor'][int(month[5:]) - 1])
                uses = [u for u in plan['usage'] if u['line_id'] == line['id'] and u['date'][:7] == month]
                purpose['used'] += sum(int(u['amount_minor']) for u in uses)
                purpose['transaction_ids'].extend(u['id'] for u in uses)
            budget_rows.append({'month': month, 'budget_version': adopted_version(db, year),
                               'planned_minor': item['planned_minor'], 'known_usage_minor': item['usage_minor'],
                               'daily_planned_minor': item['daily_minor'], 'known_daily_usage_minor': item['daily_usage_minor'],
                               'daily_remaining_minor': item['daily_remaining_minor'], 'known_daily_remaining_minor': item['known_daily_remaining_minor'],
                               'quality': item['quality'], 'unsupported_payment_actuals': plan['unsupported_payment_actuals'], 'unallocated_usage_minor': item['unallocated_usage_minor']})
    refunds = [r for r in rows if int(r['expense_minor']) < 0]
    refund_totals = {}
    for r in refunds:
        if r['refund_of']: refund_totals[r['refund_of']] = refund_totals.get(r['refund_of'], 0) - int(r['expense_minor'])
    large = [{**r, 'recorded_refund_minor': money(refund_totals.get(r['id'], 0)),
              'net_in_window_minor': money(int(r['expense_minor']) - refund_totals.get(r['id'], 0))}
             for r in rows if int(r['expense_minor']) > 0]
    large = [r for r in large if int(r['expense_minor'] if large_basis == 'gross' else r['net_in_window_minor']) >= int(minimum_minor)]
    large.sort(key=lambda r: (-int(r['expense_minor']), r['id']))
    category_rows = [{'code': v['code'], 'name': v['name'], 'amount_minor': money(v['amount']),
                      'transaction_ids': v['transaction_ids'], 'count': len(v['transaction_ids'])} for v in categories.values()]
    category_rows.sort(key=lambda c: (-int(c['amount_minor']), c['code'] or ''))
    if not complete and not estimate:
        normalized['annualized_reimbursement_minor'] = None
    warnings = list(normalized['warnings'])
    if not complete:
        warnings.append({'code': 'partial_window', 'message': '存在缺资料、未解释记录或未结束月份；月均为已知记录月均。年化需明确选择估计模式。'})
    alerts = [{'code': 'known_daily_over_budget', 'month': r['month'],
               'amount_minor': money(int(r['known_daily_usage_minor']) - int(r['daily_planned_minor'])),
               'message': '已知日常占用已超过本月计划；资料补齐后仍需重新核对。'} for r in budget_rows if int(r['known_daily_usage_minor']) > int(r['daily_planned_minor'])]
    living = {'status': 'unavailable', 'scope': living_scope, 'reason': '生活资金来源与付款占用的报销范围尚未确认；消费标准化可独立使用。'}
    if living_scope == 'net_salary_plan':
        living['reason'] = '所选来源为已排除报销的计划工资，不能再次扣减报销；消费标准化另列，采用计划不变。'
    if living_scope == 'observed_income':
        if len(budget_rows) != m:
            living['reason'] = '所选来源为包含报销的实际到账收入，但部分月份没有采用预算，不能把未知生活占用按零调整。'
        elif any(r['unsupported_payment_actuals'] for r in budget_rows):
            living['reason'] = '既有付款预算尚未证实与消费占用互斥；先核对事项关联，消费标准化仍可使用。'
        else:
            usage = sum(int(r['known_usage_minor']) for r in budget_rows)
            adjusted = normalize_reimbursements(money(income), money(usage), money(received), m)
            living.update(status='complete' if complete and all(r['quality']['consumption']['exact'] and r['quality']['payment']['exact'] for r in budget_rows) else 'partial',
                          gross_source_minor=money(income), gross_usage_minor=money(usage), applicable_reimbursement_minor=money(received),
                          adjusted_source_minor=adjusted['adjusted_income_minor'], adjusted_usage_minor=adjusted['adjusted_expense_minor'],
                          surplus_minor=money(income-usage), warnings=adjusted['warnings'],
                          reason='来源明确选择了包含报销的已知实际收入；同期报销只在来源和生活占用汇总各抵减一次，不按类别摊分，不改变分项额度。')
    return {'parameters': params, 'dependencies': dependencies(db, params), 'months': monthly,
            'totals': {'income_minor': money(income), 'expense_minor': money(expense), 'reimbursement_minor': money(received),
                       'family_support_minor': money(family), 'surplus_minor': money(income - expense), **normalized},
            'averages': {k: _rounded(Decimal(v) / m) for k, v in {
                'income_minor': income, 'expense_minor': expense, 'adjusted_income_minor': income - received,
                'adjusted_expense_minor': expense - received, 'reimbursement_minor': received}.items()},
            'annualized_adjusted_expense_minor': _rounded(Decimal(expense - received) * 12 / m) if (complete or estimate) and expense >= received else None,
            'quality': {'status': 'complete' if complete else 'partial', 'complete': complete, 'estimated': not complete and estimate,
                        'months': m, 'transaction_count': sum(1 for t in txs.values() if historical.scoped(t) and (recognition_date(t) or '')[:7] in buckets),
                        'unknown_transaction_ids': unknown, 'undated_transaction_ids': undated, 'review_transaction_ids': review,
                        'unclassified_minor': money(sum(c['amount'] for c in categories.values() if c['code'] == '5016'))},
            'merchants': sorted([{'name': v['name'], 'basis': v['basis'], 'amount_minor': money(v['amount']), 'transaction_ids': v['transaction_ids']} for v in merchants.values()], key=lambda v: -int(v['amount_minor'])),
            'purposes': [{'year': v['year'], 'line_id': v['line_id'], 'name': v['name'], 'basis': v['basis'], 'planned_minor': money(v['planned']), 'known_usage_minor': money(v['used']), 'transaction_ids': v['transaction_ids']} for v in purposes.values()],
            'categories': category_rows, 'large_expenses': large, 'refunds': refunds, 'expense_rows': rows,
            'budget_actuals': budget_rows, 'alerts': alerts, 'warnings': warnings,
            'living_normalization': living,
            'notes': ['主账和三表保留实际总额；本页调整不修改交易或采用预算。',
                      '家庭赞助保留在收入中，不计作报销，也不由此推断未来经常性收入。',
                      '大额按发生期本币费用毛额筛选，退款另列；净额只减本窗口已记录的对应退款。',
                      '月均分母为同窗自然月数，平滑报销不是每月到账承诺。']}


def register_routes(app, store):
    @app.get('/api/v1/analysis')
    def get_analysis(start_month: str, end_month: str, mode: str = 'period', estimate: bool = False, minimum_minor: str = '200000', large_basis: str = 'gross', living_scope: str = 'undeclared'):
        with store.read() as db:
            return calculate(db, start_month, end_month, mode, estimate, minimum_minor, large_basis, living_scope)

    @app.post('/api/v1/analysis-snapshots')
    async def save_snapshot(request: Request):
        body = await request.json()
        if not isinstance(body, dict) or set(body) - {'request_id', 'parameters', 'expected_dependencies'} or not isinstance(body.get('parameters'), dict):
            raise DomainError('分析快照请求无效。')
        if set(body['parameters']) - {'start_month', 'end_month', 'mode', 'estimate', 'minimum_minor', 'large_basis', 'living_scope'} or not {'start_month', 'end_month'} <= set(body['parameters']):
            raise DomainError('分析参数无效。')
        with store.write() as db:
            fp, cached = operation(db, body.get('request_id'), 'analysis_snapshot', body)
            if cached is not None: return cached
            result = calculate(db, **body['parameters'])
            if 'expected_dependencies' in body and body['expected_dependencies'] != result['dependencies']:
                raise DomainError('分析依据已变化，请更新分析后再保存。', 'analysis_stale', 409)
            sid = uid()
            db.execute('INSERT INTO analysis_snapshots VALUES(?,?,?,?,?)', (sid, encode(result['parameters']), encode(result['dependencies']), encode(result), now()))
            return finish_operation(db, body['request_id'], 'analysis_snapshot', fp, {'id': sid, 'created_at': now(), 'stale': False, 'analysis': result})

    def dto(db, row, include=False):
        p = json.loads(row['parameters'])
        result = {'id': row['id'], 'created_at': row['created_at'], 'parameters': p,
                  'stale': json.loads(row['dependencies']) != dependencies(db, p)}
        if include: result['analysis'] = json.loads(row['payload'])
        return result

    @app.get('/api/v1/analysis-snapshots')
    def list_snapshots():
        with store.read() as db:
            return {'items': [dto(db, r) for r in db.execute('SELECT * FROM analysis_snapshots ORDER BY created_at DESC LIMIT 50')]}

    @app.get('/api/v1/analysis-snapshots/{sid}')
    def get_snapshot(sid: str):
        with store.read() as db:
            row = db.execute('SELECT * FROM analysis_snapshots WHERE id=?', (sid,)).fetchone()
            if not row: raise DomainError('分析快照不存在。', 'not_found', 404)
            return dto(db, row, True)
