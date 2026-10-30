"""Evidence-driven transaction rules. All amounts are signed integer fen."""
from __future__ import annotations

import calendar
import re
from datetime import date
from . import historical

MAX_MONEY = 9223372036854775807
KINDS = {'expense', 'income', 'reimbursement', 'family_support', 'repayment', 'transfer', 'refund', 'unknown'}
KIND_NAMES = dict(expense='消费', income='收入', reimbursement='报销回款', family_support='家庭赞助', repayment='信用还款', transfer='本人互转', refund='退款', unknown='未解释现金变动')
_NAMES = [
('1001','现金及银行存款'),('1002','内部转账在途'),('1003','信用支付溢缴款'),('1004','待收报销款'),('1005','其他应收款'),('1006','可退押金'),('1007','公积金'),('1008','投资成本'),('1009','投资估值调整'),
('2001','信用支付结算应付款'),('2002','融资分期借款'),('2003','房贷本金'),('2004','其他借款本金'),('2005','其他应付款'),
('3001','纳入范围期初净额'),('3002','纳入范围变更'),
('4001','劳动收入'),('4002','已实现投资损益'),('4003','未实现投资估值损益'),('4004','分红及利息收入'),('4005','其他业务收入'),
('5001','餐饮'),('5002','日用居家'),('5003','住房'),('5004','交通'),('5005','服饰鞋包'),('5006','个人护理'),('5007','健康与宠物'),('5008','文娱学习与运动'),('5009','数码与家电'),('5010','数字服务'),('5011','人情赠与'),('5012','利息'),('5013','手续费'),('5014','税费与个人社保'),('5015','其他已确认支出'),('5016','未分类支出')]
NAMES = dict(_NAMES)
CHART = [{'code':c,'name':n,'type':{'1':'asset','2':'liability','3':'scope','4':'income','5':'expense'}[c[0]],'selectable':c.startswith('5') or c in {'1001','2001','4001','4004','4005'},'enabled':c!='1004','supported': c.startswith('5') or c in {'1001','2001','4001','4004','4005'}} for c,n in _NAMES]

class DomainError(ValueError):
    def __init__(self, message, code='validation_error', status=422):
        self.message, self.code, self.status = message, code, status
        super().__init__(message)


def minor(value, field='金额'):
    if not isinstance(value, str) or len(value)>20 or not re.fullmatch(r'0|-?[1-9][0-9]*', value):
        raise DomainError(f'{field}须为规范整数十进制字符串（最小单位分），不能用浮点数。')
    number = int(value)
    if abs(number) > MAX_MONEY:
        raise DomainError(f'{field}超出支持范围。', 'money_overflow')
    return number


def money(value):
    if abs(value) > MAX_MONEY:
        raise DomainError('汇总金额超出支持范围。', 'money_overflow')
    return str(value)


def major_to_minor(value):
    if not isinstance(value, str) or len(value)>23 or not re.fullmatch(r'[+-]?(?:0|[1-9][0-9]*)(?:\.[0-9]{1,2})?', value):
        raise DomainError('CSV amount 须为人民币元的十进制文本，最多两位小数，不自动舍入。')
    sign = -1 if value.startswith('-') else 1
    whole, _, decimal = value.lstrip('+-').partition('.')
    return money(sign * (int(whole)*100 + int((decimal+'00')[:2])))


def valid_date(value):
    if not isinstance(value, str) or not re.fullmatch(r'\d{4}-\d{2}-\d{2}', value):
        raise DomainError('日期须为 YYYY-MM-DD。')
    try:
        date.fromisoformat(value)
    except ValueError:
        raise DomainError('日期不存在。')
    return value


def month_bounds(value):
    if not isinstance(value,str) or not re.fullmatch(r'\d{4}-\d{2}', value):
        raise DomainError('月份须为 YYYY-MM。')
    valid_date(value+'-01')
    y,m = map(int, value.split('-'))
    return value+'-01', f'{value}-{calendar.monthrange(y,m)[1]:02d}'


def validate_transaction(payload, accounts, transactions, current_id=None):
    p = dict(payload)
    allowed={'date','cash_date','description','account_id','amount_minor','currency','kind','category_code','counterparty_account_id','refund_of','budget_line_id','counterparty_confirmed','source_id'}
    if set(p)-allowed:
        raise DomainError('不支持的交易字段：'+', '.join(sorted(set(p)-allowed)))
    valid_date(p.get('date'))
    p['cash_date'] = valid_date(p.get('cash_date',p['date']))
    if p.get('currency','CNY') != 'CNY':
        raise DomainError('首版仅支持 CNY，不能隐式换汇。', 'unsupported')
    p['currency']='CNY'
    if not isinstance(p.get('account_id'),str) or p['account_id'] not in accounts:
        raise DomainError('账户不存在。')
    account=accounts[p['account_id']]
    value=minor(p.get('amount_minor'))
    if value == 0:
        raise DomainError('交易金额不能为零。')
    if not isinstance(p.get('kind'),str) or p['kind'] not in KINDS:
        raise DomainError('此业务性质尚未支持；请标为 unknown 待核。', 'unsupported')
    if not isinstance(p.get('description',''),str) or len(p.get('description','')) > 2000:
        raise DomainError('描述须为不超过 2000 字的文本。')
    p['description']=p.get('description','')
    for key in ('category_code','counterparty_account_id','refund_of','budget_line_id'):
        optional_value=p.get(key)
        p[key]=None if optional_value is None or optional_value=='' else optional_value
        if p[key] is not None and (not isinstance(p[key],str) or len(p[key])>160):
            raise DomainError(f'{key} 无效。')
    p['counterparty_confirmed']=p.get('counterparty_confirmed',False)
    if type(p['counterparty_confirmed']) is not bool:
        raise DomainError('counterparty_confirmed 须为布尔值。')
    peer=p['counterparty_account_id']
    if peer and (peer not in accounts or peer == p['account_id']):
        raise DomainError('对端必须为已存在的另一个本人账户。')
    kind=p['kind']
    if kind in {'repayment','transfer'} and p['counterparty_confirmed']:
        previous=transactions.get(current_id) if current_id else None
        if not previous or not previous.get('counterparty_confirmed') or any(
            previous.get(key)!=p.get(key) for key in ('kind','account_id','counterparty_account_id','amount_minor','date','cash_date','currency')
        ):
            raise DomainError('新的两端关系须通过可替换两端事实的原子关联确认；本版本只保留各端已证实流水，不能用单个确认字段生成对端。', 'relation_confirmation_unsupported', 422)
    validate_reimbursement_dates(p, transactions.get(current_id) if current_id else None)
    if kind == 'expense':
        if value >= 0:
            raise DomainError('消费的归一化账户净变动必须为负；信用负债增加也用负数。')
        p['category_code']=p['category_code'] or '5016'
        if p['category_code'] not in NAMES or not p['category_code'].startswith('5'):
            raise DomainError('消费只能选择现有 16 个费用科目，不能写系统或资产科目。')
        if p['category_code'] == '5015' and not p['description'].strip():
            raise DomainError('其他已确认支出需要用途说明。')
    elif kind in {'income','reimbursement','family_support'}:
        if value<=0 or account['kind']!='cash':
            raise DomainError('本入口收入需为现金账户已确认到账；信用卡正向变动不能直接记收入。')
        if kind in {'reimbursement','family_support'}:
            if p['category_code'] not in {None,'4005'}:
                raise DomainError('报销回款和家庭赞助必须使用 4005 并保留独立子类型。')
            p['category_code']='4005'
        else:
            p['category_code']=p['category_code'] or '4001'
            if p['category_code'] not in {'4001','4004','4005'}:
                raise DomainError('投资损益和系统科目不能通过普通收入入口写入。')
    elif kind=='repayment':
        p['category_code']=None
        if peer and {account['kind'],accounts[peer]['kind']}!={'cash','credit'}:
            raise DomainError('首版还款仅支持现金账户与信用支付账户；融资本息拆分业务请保留待核。','unsupported')
        if (account['kind']=='cash' and value>0) or (account['kind']=='credit' and value<0):
            raise DomainError('还款须为现金减少或信用负债减少。')
    elif kind=='transfer':
        p['category_code']=None
        if account['kind']!='cash' or (peer and accounts[peer]['kind']!='cash'):
            raise DomainError('本人互转仅支持两个现金账户；信用付款请使用还款。')
    elif kind=='refund':
        if value<=0:
            raise DomainError('退款必须增加账户净额。')
        if p['refund_of']:
            original=transactions.get(p['refund_of'])
            if not original or historical.expense_value(original)<=0 or (not historical.is_historical(original) and original['kind']!='expense'):
                raise DomainError('退款必须关联已存在的原消费。')
            if original['account_id']!=p['account_id']:
                raise DomainError('跨账户退款分配尚未支持，请保留待核。','unsupported')
            if not original.get('date') or p['date']<original['date']:
                raise DomainError('退款日期不能早于原消费。')
            if original.get('currency')!=p['currency']:
                raise DomainError('退款和原消费币种必须一致。')
            background_origin=historical.is_historical(original) and not original['historical'].get('scope_override') and original['date'][:4]<p['date'][:4]
            if not historical.scoped(original) and not background_origin:
                raise DomainError('原消费排除时不能单独纳入退款。','linked_amount_conflict',409)
            refunded=sum(-historical.expense_value(t) for tid,t in transactions.items() if tid!=current_id and historical.refund_parent(t)==p['refund_of'])
            if refunded+value>historical.expense_value(original):
                raise DomainError('退款合计超过原消费金额，请先处理关联业务。','linked_amount_conflict',409)
            p['category_code']=original['category_code']
        else:
            p['category_code']=None
    else:
        p['category_code']=None
    if kind not in {'repayment','transfer'} and peer:
        raise DomainError('只有本人互转和信用还款可设置对端账户。')
    if kind!='refund' and p['refund_of']:
        raise DomainError('只有退款可以关联原消费。')
    refunds=[] if current_id is None else [t for tid,t in transactions.items() if tid!=current_id and historical.refund_parent(t)==current_id]
    if refunds and (kind!='expense' or sum(-historical.expense_value(t) for t in refunds)>abs(value) or any(t['account_id']!=p['account_id'] or not t.get('date') or t['date']<p['date'] for t in refunds)):
        raise DomainError('变更与后续退款关联冲突，请先处理关联业务。','linked_amount_conflict',409)
    return p


def review_reasons(p):
    if historical.is_historical(p): return historical.reasons(p)
    reasons=[]
    if p['kind']=='unknown': reasons.append('业务性质待确认')
    if p['kind']=='expense' and p.get('category_code')=='5016': reasons.append('消费类别待补')
    if p['kind']=='repayment' and not (p.get('counterparty_account_id') and p.get('counterparty_confirmed')): reasons.append('还款账户债务关系待确认')
    if p['kind']=='transfer' and not (p.get('counterparty_account_id') and p.get('counterparty_confirmed')): reasons.append('对端到账证据待确认')
    if p['kind']=='refund' and not p.get('refund_of'): reasons.append('退款原消费待关联')
    return reasons


def explainable(p):
    return not [r for r in review_reasons(p) if r!='消费类别待补']


def recognition_date(p):
    """Receipt evidence controls reimbursement recognition, including legacy rows."""
    reimbursement = p.get('kind') == 'reimbursement' or (
        historical.is_historical(p) and p['historical'].get('event') == '报销回款')
    return p.get('cash_date') if reimbursement else p.get('date')


def validate_reimbursement_dates(p, previous=None):
    """Reject a new inconsistent recognition date without rewriting old evidence.

    Existing inconsistent source/receipt dates remain readable and may receive
    unrelated edits. A change to either date must explicitly resolve the pair.
    Reports always use the receipt date, even for those legacy payloads.
    """
    reimbursement = p.get('kind') == 'reimbursement' or (
        historical.is_historical(p) and p['historical'].get('event') == '报销回款')
    if not reimbursement or p.get('date') == p.get('cash_date'):
        return
    if previous and (not historical.is_historical(p) or previous.get('historical', {}).get('event') == '报销回款') and recognition_date(previous) == p.get('cash_date') and all(
        p.get(key) == previous.get(key) for key in ('kind', 'date', 'cash_date')
    ):
        return
    raise DomainError('报销收入按证实到账日确认；修改归期须使 date 与 cash_date 一致，原始描述日期保留在来源凭据中。', 'reimbursement_date_conflict')


def validate_budget_binding(p, line, previous=None):
    """Shared write guard; ordinary repayments cannot create a second usage.

    Historical interpretations and an unchanged existing native binding can
    still be edited. No public field enables the future financing exception.
    """
    if historical.is_historical(p) or p.get('kind') != 'repayment' or line.get('actual_basis') != 'payment':
        return
    if previous and not historical.is_historical(previous) and previous.get('kind') == 'repayment' and all(
        previous.get(key) == p.get(key) for key in ('budget_line_id','account_id','cash_date','amount_minor')
    ):
        return
    raise DomainError('普通信用还款不再占用付款预算；有证据的融资事项分配尚未开放，请清空预算关联。', 'repayment_budget_unsupported', 422)


def movements(p):
    """Known signed account changes, including unexplained source movements."""
    if historical.is_historical(p): return historical.movement(p)
    result={p['account_id']:int(p['amount_minor'])}
    if p['kind'] in {'repayment','transfer'} and p.get('counterparty_account_id') and explainable(p):
        result[p['counterparty_account_id']]=-int(p['amount_minor'])
    return result


def ledger(p, accounts, transactions):
    if historical.is_historical(p):
        return p['historical'].get('ledger_lines', []) if historical.scoped(p) else []
    if not explainable(p): return []
    result=[]
    def add(code, value, account_id=None):
        result.append({'code':code,'name':NAMES[code],'account_id':account_id,'debit_minor':money(max(value,0)),'credit_minor':money(max(-value,0))})
    value=int(p['amount_minor'])
    ordered=sorted((t for t in transactions.values() if t.get('cash_date')),key=lambda t:t['cash_date'])
    for aid,delta in movements(p).items():
        account=accounts[aid]
        if account['kind']=='cash':
            add('1001',delta,aid)
            continue
        # Only an evidenced absolute starting balance can establish an overpayment.
        known=account.get('opening_balance') is not None and account.get('opening_date')<=p['cash_date']
        before=account.get('opening_balance') or 0
        if known:
            for previous in ordered:
                if previous['id']==p.get('id'): break
                if previous['cash_date']<account['opening_date'] or previous['cash_date']>p['cash_date']: continue
                change=movements(previous).get(aid,0)
                if change and not explainable(previous): known=False; break
                before-=change
        if known:
            after=before-delta
            debt_change=max(before,0)-max(after,0)
            overpayment_change=max(-after,0)-max(-before,0)
            if debt_change: add('2001',debt_change,aid)
            if overpayment_change: add('1003',overpayment_change,aid)
        else:
            add('2001',delta,aid)
    if p['kind'] in {'expense','income','reimbursement','family_support','refund'}:
        category=p['category_code']
        if p['kind']=='refund': category=transactions[p['refund_of']]['category_code']
        add(category,-value)
    assert sum(int(r['debit_minor'])-int(r['credit_minor']) for r in result)==0
    return result
