"""Same-revision read projections; no SQL SUM or floating point arithmetic."""
from __future__ import annotations

import json

from .domain import NAMES, KIND_NAMES, money, month_bounds, movements, explainable, review_reasons, recognition_date
from .storage import accounts, transactions, revision
from . import historical, coverage as period_coverage


def coverage(db, txs=None):
    txs=transactions(db) if txs is None else txs
    reasons=['来源期间与账户范围尚未完成完整性核对；金额是当前已知记录的汇总。']
    acc=accounts(db)
    unknown=sum(a['opening_balance'] is None for a in acc.values())
    if unknown: reasons.append(f'{unknown} 个账户期初未知，绝对余额和纳入范围净额不能确定。')
    review=sum(bool(review_reasons(t)) for t in txs.values())
    if review: reasons.append(f'{review} 笔交易仍有待核事项。')
    return {'status':'partial' if txs else 'empty','reasons':reasons}


def common(db, txs=None): return {'data_revision':revision(db),'coverage':coverage(db,txs)}


def account_dtos(db, cutoff=None):
    acc=accounts(db); txs=transactions(db); result=[]
    for aid,a in acc.items():
        def effective_day(t):
            return t.get('date') if a['kind']=='credit' and (t.get('kind') in {'expense','refund'} or (historical.is_historical(t) and historical.expense_value(t))) else t.get('cash_date')
        relevant=[(t,delta) for t in txs.values() if effective_day(t) and (not cutoff or effective_day(t)<=cutoff) for mid,delta in movements(t).items() if mid==aid]
        change=sum(delta for _,delta in relevant)
        unexplained=sum(delta for t,delta in relevant if not explainable(t))
        has_unexplained=any(not explainable(t) for t,_ in relevant)
        opening_known=a['opening_balance'] is not None and (not cutoff or a['opening_date']<=cutoff)
        after_opening=sum(delta for t,delta in relevant if opening_known and effective_day(t)>=a['opening_date'])
        balance=(a['opening_balance']+(after_opening if a['kind']=='cash' else -after_opening)) if opening_known else None
        direction=1 if a['kind']=='cash' else -1
        result.append({'id':aid,'name':a['name'],'kind':a['kind'],'currency':a['currency'],'opening_balance_minor':money(a['opening_balance']) if a['opening_balance'] is not None else None,'opening_date':a['opening_date'],'balance_minor':money(balance) if balance is not None and not has_unexplained else None,'observed_balance_minor':money(balance) if balance is not None else None,'known_change_minor':money(direction*change),'book_change_minor':money(direction*(change-unexplained)),'unexplained_change_minor':money(direction*unexplained),'overpayment_minor':money(max(-balance,0)) if a['kind']=='credit' and balance is not None and not has_unexplained else None,'version':a['version']})
    return result


def transaction_dto(db,p,acc=None,txs=None):
    acc=accounts(db) if acc is None else acc
    txs=transactions(db) if txs is None else txs
    out=dict(p)
    if p['kind']=='refund' and p.get('refund_of') in txs:
        out['category_code']=txs[p['refund_of']]['category_code']
    out.update(account_name=acc[p['account_id']]['name'],category_name=NAMES.get(out.get('category_code')),review_reasons=review_reasons(p),status='posted' if explainable(p) else 'needs_review',source_count=db.execute('SELECT COUNT(*) FROM source_rows WHERE transaction_id=?',(p['id'],)).fetchone()[0])
    if historical.is_historical(p):
        out['read_only']=False
        out['business_amount_minor']=str(historical.business_amount(p))
        out['edit_options']=historical.edit_options(p,txs)
        out['status']='excluded' if not historical.scoped(p) else ('needs_review' if historical.reasons(p) else 'posted')
    return out


def effects(p,acc):
    if historical.is_historical(p): return historical.effect(p)
    income=expense=None
    if explainable(p):
        income=int(p['amount_minor']) if p['kind'] in {'income','reimbursement','family_support'} else 0
        expense=-int(p['amount_minor']) if p['kind'] in {'expense','refund'} else 0
    cash=[] if p['kind']=='transfer' and explainable(p) else [delta for aid,delta in movements(p).items() if acc[aid]['kind']=='cash']
    return {'income_minor':money(income) if income is not None else None,'expense_minor':money(expense) if expense is not None else None,'cash_inflow_minor':money(sum(max(v,0) for v in cash)),'cash_outflow_minor':money(sum(max(-v,0) for v in cash)),'budget_usage_minor':None}


def report(db,month):
    start,end=month_bounds(month)
    acc=accounts(db); txs=transactions(db)
    selected=[p for p in txs.values() if recognition_date(p) and start<=recognition_date(p)<=end and historical.scoped(p)]
    income={c:0 for c in ('4001','4002','4004','4005')}
    expense={c:0 for c in NAMES if c.startswith('5')}
    reimbursement=family_support=0
    for p in selected:
        if historical.is_historical(p):
            h=p['historical']; code=p.get('category_code')
            inc_value=int(h.get('income_minor') or '0'); exp_value=int(h.get('expense_minor') or '0')
            if inc_value: income[code]+=inc_value
            if exp_value: expense[code]+=exp_value
            if h['event']=='报销回款': reimbursement+=inc_value
            if h['event']=='家庭赞助': family_support+=inc_value
            continue
        if not explainable(p): continue
        value=int(p['amount_minor'])
        if p['kind'] in {'income','reimbursement','family_support'}:
            income[p['category_code']]+=value
            if p['kind']=='reimbursement': reimbursement+=value
            if p['kind']=='family_support': family_support+=value
        elif p['kind'] in {'expense','refund'}:
            category=txs[p['refund_of']]['category_code'] if p['kind']=='refund' else p['category_code']
            expense[category]+=-value
    cash_lines={}; inflow=outflow=unexplained_inflow=unexplained_outflow=0
    for p in txs.values():
        if not p.get('cash_date') or not start<=p['cash_date']<=end or not historical.scoped(p): continue
        if historical.is_historical(p):
            h=p['historical']
            if not h['cash_included']: continue
            value=int(h.get('cash_minor') or '0')
            inflow+=max(value,0); outflow+=max(-value,0)
            kind=h['cash_type']
            if '待确认' in kind or '待溯源' in kind:
                unexplained_inflow+=max(value,0); unexplained_outflow+=max(-value,0)
            row=cash_lines.setdefault(kind,{'kind':kind,'name':kind,'amount':0,'transaction_ids':[]})
            row['amount']+=value
            row['transaction_ids'].append(p['id'])
            continue
        if p['kind']=='transfer' and explainable(p): continue
        for aid,value in movements(p).items():
            if acc[aid]['kind']!='cash': continue
            inflow+=max(value,0); outflow+=max(-value,0)
            kind=p['kind'] if explainable(p) else 'unknown'
            if kind=='unknown': unexplained_inflow+=max(value,0); unexplained_outflow+=max(-value,0)
            row=cash_lines.setdefault(kind,{'kind':kind,'name':KIND_NAMES[kind],'amount':0,'transaction_ids':[]})
            row['amount']+=value
            if p['id'] not in row['transaction_ids']: row['transaction_ids'].append(p['id'])
    balance_accounts=account_dtos(db,end)
    known_assets=sum(max(int(a['balance_minor']),0) if a['kind']=='cash' else max(-int(a['balance_minor']),0) for a in balance_accounts if a['balance_minor'] is not None)
    known_liabilities=sum(max(int(a['balance_minor']),0) if a['kind']=='credit' else max(-int(a['balance_minor']),0) for a in balance_accounts if a['balance_minor'] is not None)
    all_known=bool(balance_accounts) and all(a['balance_minor'] is not None for a in balance_accounts)
    cash_known=bool([a for a in balance_accounts if a['kind']=='cash']) and all(a['observed_balance_minor'] is not None and a['opening_date']<=start for a in balance_accounts if a['kind']=='cash')
    closing=sum(int(a['observed_balance_minor']) for a in balance_accounts if a['kind']=='cash') if cash_known else None
    inc=sum(income.values()); exp=sum(expense.values())
    review=[p for p in selected if review_reasons(p)]
    return {'month':month,'basis':'current_known_transactions','month_quality':{basis:period_coverage.month_quality(db,month,basis,txs=txs) for basis in ('consumption','payment')},'reimbursement_date_adjustments':[{'transaction_id':t['id'],'source_date':t.get('date'),'recognized_date':recognition_date(t)} for t in txs.values() if historical.scoped(t) and recognition_date(t)!=t.get('date') and any(day and day.startswith(month) for day in (t.get('date'),recognition_date(t)))],'income_expense':{'income_minor':money(inc),'expense_minor':money(exp),'surplus_minor':money(inc-exp),'income_lines':[{'code':c,'name':NAMES[c],'amount_minor':money(v)} for c,v in income.items()],'expense_lines':[{'code':c,'name':NAMES[c],'amount_minor':money(v)} for c,v in expense.items()],'reimbursement_minor':money(reimbursement),'family_support_minor':money(family_support),'unclassified_expense_minor':money(expense['5016']),'unrealized_minor':None},'cash_flow':{'inflow_minor':money(inflow),'outflow_minor':money(outflow),'net_minor':money(inflow-outflow),'unexplained_inflow_minor':money(unexplained_inflow),'unexplained_outflow_minor':money(unexplained_outflow),'lines':[{**{k:v for k,v in r.items() if k!='amount'},'amount_minor':money(r['amount'])} for r in cash_lines.values()],'opening_minor':money(closing-(inflow-outflow)) if closing is not None else None,'closing_minor':money(closing) if closing is not None else None,'future_arrangements':None},'balance_sheet':{'name':'账户资产与负债表','as_of':end,'assets_minor':money(known_assets) if all_known else None,'liabilities_minor':money(known_liabilities) if all_known else None,'scope_net_minor':money(known_assets-known_liabilities) if all_known else None,'known_assets_minor':money(known_assets),'known_liabilities_minor':money(known_liabilities),'accounts':balance_accounts,'observations':historical.observations(db)},'review':{'count':len(review),'unclassified_count':sum(p.get('category_code')=='5016' for p in selected),'unknown_amount_minor':money(sum(abs(int(p['amount_minor'])) for p in selected if not explainable(p)))},'transaction_count':len(selected),**common(db,txs)}


def budget_projection(db,payload):
    """Known facts count once; coverage independently controls exact remainders."""
    from .budget import compute_budget
    p=json.loads(json.dumps(payload))
    txs=transactions(db); acc=accounts(db)
    lines={line['id']:line for line in p['lines']}
    usage=[]; unallocated_entries=[]; candidates=[]; rejected=[]
    unallocated=unknown=0; unknown_months={}; legacy_payment=[]; unresolved_months=set()
    year=f"{p['year']:04d}-"
    selected=[t for t in txs.values() if any(t.get(k) and t[k].startswith(year) for k in ('date','cash_date')) and historical.scoped(t)]

    def add_unknown(t,day,bases):
        nonlocal unknown
        amount=historical.business_amount(t) if historical.is_historical(t) else abs(int(t['amount_minor']))
        unknown+=amount
        for basis in bases:
            key=(day[:7],basis)
            unknown_months[key]=unknown_months.get(key,0)+amount

    def reject(t,value,day,basis='consumption'):
        nonlocal unallocated
        unallocated+=abs(value)
        if t['id'] not in rejected: rejected.append(t['id'])
        if day: unresolved_months.add((day[:7],basis))

    for t in selected:
        original=txs.get(historical.refund_parent(t))
        line_id=t.get('budget_line_id') or (original.get('budget_line_id') if original else None)
        line=lines.get(line_id)
        expense=historical.expense_value(t)
        if line and line.get('actual_basis')=='payment':
            day=t.get('cash_date')
            if not day or not day.startswith(year): continue
            if historical.is_historical(t):
                h=t['historical']; cash=int(h.get('cash_minor') or '0') if h['cash_included'] and acc[t['account_id']]['kind']=='cash' else 0
                eligible=expense!=0 or h['event'] in {'信用还款','房贷还款待拆分'}
                if h['event'] in {'信用还款','房贷还款待拆分'}:
                    legacy_payment.append(t['id'])
            else:
                cash=sum(v for aid,v in movements(t).items() if acc[aid]['kind']=='cash')
                eligible=explainable(t) and t['kind'] in {'expense','refund','repayment'}
                if t['kind']=='repayment':
                    # Existing bindings keep their interpretation for review.
                    # New bindings are blocked by validate_budget_binding.
                    legacy_payment.append(t['id'])
            value=-cash if eligible else 0; basis='payment'
            if not value:
                if expense: reject(t,expense,day,'payment')
                if not explainable(t):
                    add_unknown(t,day,{'payment'})
                continue
        else:
            day=t.get('date')
            if not day or not day.startswith(year): continue
            value=expense; basis='consumption'
            if not historical.is_historical(t) and not explainable(t):
                # A known one-sided repayment is not consumption; its uncertain
                # relationship still prevents declaring exact total execution.
                add_unknown(t,day,{'payment'} if t['kind'] in {'repayment','transfer'} else {'consumption','payment'})
                continue
            if not value:
                if historical.is_historical(t) and historical.reasons(t):
                    add_unknown(t,day,{'consumption','payment'})
                continue
            if not line or line.get('actual_basis')!='consumption':
                # Confirmed ordinary spending is real usage even before the
                # user has assigned a purpose or category. Never add a fake
                # budget line to the user's adopted plan.
                line_id=None
        entry={'id':t['id'],'line_id':line_id,'date':day,'amount_minor':money(value),'kind':basis}
        if value<0:
            if not original:
                reject(t,value,day,basis); continue
            entry['refund_of']=original['id']
        candidates.append(entry)

    positives={u['id']:u for u in candidates if int(u['amount_minor'])>0}; refunded={}
    for entry in sorted(candidates,key=lambda u:(int(u['amount_minor'])<0,u['date'],u['id'])):
        value=int(entry['amount_minor'])
        if value<0:
            parent=positives.get(entry['refund_of']); cumulative=refunded.get(entry['refund_of'],0)-value
            if not parent or parent['line_id']!=entry['line_id'] or parent['kind']!=entry['kind'] or parent['date']>entry['date'] or cumulative>int(parent['amount_minor']):
                reject(entry,value,entry['date'],entry['kind']); continue
            refunded[entry['refund_of']]=cumulative
        if entry['line_id'] is None:
            unallocated_entries.append(entry)
            unallocated+=abs(value)
            if entry['id'] not in rejected: rejected.append(entry['id'])
        else:
            usage.append(entry)
    p['usage']=usage
    computed=compute_budget(p)
    computed['unallocated_usage']=unallocated_entries
    month_quality={}
    active_bases={'consumption'} | {line['actual_basis'] for line in p['lines']}
    legacy_months={t['cash_date'][:7] for t in selected if t['id'] in legacy_payment and t.get('cash_date')}
    for row in computed['months']:
        month=row['month']
        unassigned=sum(int(u['amount_minor']) for u in unallocated_entries if u['date'][:7]==month)
        row['usage_minor']=money(int(row['usage_minor'])+unassigned)
        row['daily_usage_minor']=money(int(row['daily_usage_minor'])+unassigned)
        row['daily_remaining_minor']=money(int(row['daily_remaining_minor'])-unassigned)
        row['schedule_variance_minor']=money(int(row['schedule_variance_minor'])-unassigned)
        row['unallocated_usage_minor']=money(unassigned)
        quality={basis:period_coverage.month_quality(db,month,basis,txs=txs) for basis in ('consumption','payment')}
        for basis in quality:
            uncertain=(month,basis) in unknown_months or (month,basis) in unresolved_months
            quality[basis]['known_usage_minor']=money(sum(int(u['amount_minor']) for u in usage+unallocated_entries if u['date'][:7]==month and u['kind']==basis))
            quality[basis]['unknown_usage_minor']=money(unknown_months.get((month,basis),0))
            quality[basis]['exact']=quality[basis]['complete'] and not uncertain and not (basis=='payment' and month in legacy_months)
            if uncertain: quality[basis]['reasons'].append('存在性质或退款占用尚待核对的记录。')
            if basis=='payment' and month in legacy_months:
                quality[basis]['reasons'].append('保留既有还款预算解释，尚未证明与消费占用互斥。')
        exact=all(quality[basis]['exact'] for basis in active_bases)
        row['known_daily_remaining_minor']=row['daily_remaining_minor']
        row['known_schedule_variance_minor']=row['schedule_variance_minor']
        row['known_fixed_outstanding_minor']=row['fixed_outstanding_minor']
        if not quality['consumption']['exact']: row['daily_remaining_minor']=None
        if not exact:
            row['schedule_variance_minor']=None
            row['fixed_outstanding_minor']=None
        row['actuals_connected']=exact
        row['quality']=quality
        month_quality[month]=quality
    for result in computed['line_results']:
        basis=lines[result['id']]['actual_basis']
        result['known_annual_remaining_minor']=result['annual_remaining_minor']
        result['actuals_complete']=all(q[basis]['exact'] for q in month_quality.values())
        if not result['actuals_complete']: result['annual_remaining_minor']=None
    return computed,{'actuals_connected':all(row['actuals_connected'] for row in computed['months']),
        'month_quality':month_quality,'coverage_version':period_coverage.version(db),
        'unallocated_usage_minor':money(unallocated),'known_unallocated_usage_minor':money(sum(int(u['amount_minor']) for u in unallocated_entries)),
        'unknown_usage_minor':money(unknown),'unallocated_transaction_ids':rejected,
        'unsupported_payment_actuals':bool(legacy_payment),'legacy_payment_transaction_ids':legacy_payment,
        'actuals_basis':'current_known_consumption_and_confirmed_payment',
        'actuals_note':'已知普通消费含未分配部分，按发生日占用；付款按实际现金收付日。完整剩余须逐月逐口径确认覆盖。未匹配与跨年退款不释放预算；既有还款预算解释保留并单列待核。'}
