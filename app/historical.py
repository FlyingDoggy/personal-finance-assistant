"""Versioned interpretations of immutable migration evidence.

The historical workbook has already resolved cash, expense and debt effects.
Raw bank signs cannot recreate those decisions. Only the offline migration
command can originate this representation; edits validate the current
interpretation, while API imports cannot claim arbitrary effect fields.
"""
import json
from copy import deepcopy

EXPENSE_EVENTS = {'消费', '车辆租赁付款', '利息或贴息'}
REFUND_EVENTS = {'消费退款', '消费退款或返利'}
INCOME_EVENTS = {'工资实收': '4001', '存款利息': '4004', '家庭赞助': '4005', '报销回款': '4005', '一次性其他收入': '4005', '个税退税': '4005'}
OTHER_EVENTS = {'本人账户划转', '信用还款', '房贷还款待拆分', '分期本金出账', '转分期', '分期退款结算调整', '关闭订单', '取消订单退款', '个人转付待确认', '性质待确认', '现金存入待溯源', '证券资金划转', '雇主其他来款', '代管资金收付', '过桥代收代付', '信用账户来款待确认', '日期或退款时点待确认'}
EVENTS = sorted(EXPENSE_EVENTS | REFUND_EVENTS | set(INCOME_EVENTS) | OTHER_EVENTS)
PAIRED_LINKS = {'还款双源', '范围内转账双边抵消', '关闭订单退款'}
EDIT_FIELDS = {'date', 'cash_date', 'description', 'account_id', 'category_code', 'budget_line_id', 'refund_of', 'business_amount_minor', 'historical_event', 'in_scope', 'review'}


def business_amount(p):
    return abs(int(p['historical'].get('amount_minor') or p.get('amount_minor') or '0'))


def refund_parent(p):
    if not is_historical(p):
        return p.get('refund_of') if p.get('kind') == 'refund' else None
    h = p['historical']
    return p.get('refund_of') or (h.get('related_id') if h.get('link_type') == '退款对应原消费' else None)


def expense_value(p):
    if is_historical(p):
        return int(p['historical'].get('expense_minor') or '0')
    return -int(p['amount_minor']) if p.get('kind') in {'expense', 'refund'} else 0


def edit_options(p, txs):
    h = p['historical']
    related = sorted({t['id'] for t in txs.values() if t['id'] != p['id'] and is_historical(t) and
                      (t['id'] == h.get('related_id') or t['historical'].get('related_id') == p['id'])})
    category_type = 'expense' if expense_value(p) or h['event'] in EXPENSE_EVENTS | REFUND_EVENTS else ('income' if h['event'] in INCOME_EVENTS else 'none')
    settlement = h['event'] in {'分期本金出账', '转分期', '分期退款结算调整', '关闭订单', '取消订单退款'}
    allowed_events = [h['event']] if related or settlement else sorted(EXPENSE_EVENTS | REFUND_EVENTS | set(INCOME_EVENTS) | {'性质待确认', h['event']})
    return {'events': allowed_events, 'category_type': category_type, 'related_transaction_ids': related,
            'relation_note': '配对金额会一起更新；退款继承原消费科目。关联记录有后续修改时，不会强行撤销。' if related else ('此行为分期或订单结算，修改其金额与说明不会新增消费。' if settlement else None)}


def _set_category(p, code):
    from .domain import NAMES
    h = p['historical']; previous = p.get('category_code')
    p['category_code'] = code; h['code'] = int(code) if code else None
    for line in h.get('ledger_lines', []):
        if line.get('code') == previous and previous and previous[0] in {'4', '5'}:
            line.update(code=code, name=NAMES[code], account_label=NAMES[code])


def _scale(p, value):
    from .domain import DomainError, money
    old = business_amount(p); h = p['historical']
    if old == value:
        return
    if old == 0:
        raise DomainError('原记录金额为零，请同时确认业务性质后再填写金额。', 'historical_zero_amount')
    for key in ('income_minor', 'expense_minor', 'cash_minor', 'debt_delta_minor'):
        if h.get(key) is not None:
            scaled, remainder = divmod(int(h[key]) * value, old)
            if remainder:
                raise DomainError('这笔记录包含不能按比例分摊到分的多项金额，请先核对拆分。', 'historical_split_required', 409)
            h[key] = money(scaled)
    for line in h.get('ledger_lines', []):
        for key in ('debit_minor', 'credit_minor'):
            scaled, remainder = divmod(int(line[key]) * value, old)
            if remainder:
                raise DomainError('分录金额不能按比例准确到分，请先核对拆分。', 'historical_split_required', 409)
            line[key] = money(scaled)
    h['amount_minor'] = money(value)


def _interpret_event(p, event, value, accounts):
    """An explicit human nature change, with direction checked against source facts."""
    from .domain import DomainError, NAMES, money
    h = p['historical']; cash_account = accounts[p['account_id']]['kind'] == 'cash'
    cash = int(h.get('cash_minor') or '0'); debt = h.get('debt_delta_minor')
    direction = (cash > 0) - (cash < 0) if cash_account else ((int(debt) < 0) - (int(debt) > 0) if debt is not None else 0)
    original_event = h['event']
    if event not in EXPENSE_EVENTS | REFUND_EVENTS | set(INCOME_EVENTS) | {'性质待确认'}:
        raise DomainError('还款、互转、分期与代管业务需要保留原关联；可修改金额、日期和说明，不能凭性质标签新建资金关系。', 'historical_relation_required', 409)
    if original_event in {'分期本金出账', '转分期', '分期退款结算调整', '关闭订单', '取消订单退款'}:
        raise DomainError('此记录是分期或订单结算行，不能改成新的消费；请修改对应原消费。', 'historical_relation_required', 409)
    income = expense = 0; code = None
    if event in EXPENSE_EVENTS:
        if direction > 0:
            raise DomainError('原资金方向是转入或负债减少，不能直接改为消费；退款请选消费退款。')
        expense = value; direction = -1
        code = '5012' if event == '利息或贴息' else ('5004' if event == '车辆租赁付款' else (p.get('category_code') if str(p.get('category_code')).startswith('5') else '5016'))
    elif event in REFUND_EVENTS:
        if direction < 0:
            raise DomainError('原资金方向是转出或负债增加，不能直接改为退款。')
        expense = -value; direction = 1
        code = p.get('category_code') if str(p.get('category_code')).startswith('5') else '5016'
    elif event in INCOME_EVENTS:
        if not cash_account or direction < 0:
            raise DomainError('收入必须为现金账户转入，不能把信用负债减少或转出改为收入。')
        income = value; direction = 1; code = INCOME_EVENTS[event]
    h.update(event=event, amount_minor=money(value), income_minor=money(income), expense_minor=money(expense),
             cash_minor=money(direction * value) if cash_account else '0',
             debt_delta_minor=None if cash_account else money(-direction * value),
             cash_included=cash_account, review='人工确认' if event != '性质待确认' else '待确认',
             cash_type=('消费现金收付' if expense else ('收入现金收付' if income else '性质待确认')) if cash_account else '非现金来源')
    p['category_code'] = code; h['code'] = int(code) if code else None
    h['ledger_lines'] = []
    if code:
        account_code = '1001' if cash_account else '2001'
        for sequence, (line_code, signed) in enumerate(((account_code, direction * value), (code, -direction * value)), 1):
            h['ledger_lines'].append({'code': line_code, 'name': NAMES[line_code], 'account_id': p['account_id'] if line_code == account_code else None,
                'debit_minor': money(max(signed, 0)), 'credit_minor': money(max(-signed, 0)), 'sequence': sequence, 'basis': '人工修订后的当前解释'})


def plan_edit(current, changes, txs, accounts):
    from .domain import DomainError, NAMES, minor, valid_date
    from .storage import raw_payload
    if set(changes) - EDIT_FIELDS:
        raise DomainError('不支持的历史编辑字段：' + ', '.join(sorted(set(changes) - EDIT_FIELDS)))
    for key in ('account_id','historical_event','review'):
        if key in changes and not isinstance(changes[key],str):
            raise DomainError(key + ' 须为文本。')
    for key in ('category_code','budget_line_id','refund_of'):
        if key in changes and changes[key] is not None and not isinstance(changes[key],str):
            raise DomainError(key + ' 须为文本或空值。')
    p = deepcopy(raw_payload(current)); h = p['historical']; tid = current['id']
    value = minor(changes.get('business_amount_minor', str(business_amount(p))), '业务金额')
    if value < 0 or (value == 0 and business_amount(p) and h['event'] not in {'关闭订单', '取消订单退款'}):
        raise DomainError('业务金额须为正数；正负方向由业务性质保留。')
    event = changes.get('historical_event', h['event'])
    if event not in EVENTS:
        raise DomainError('未知历史业务性质。')
    if 'account_id' in changes and changes['account_id'] != p['account_id']:
        aid = changes['account_id']
        if aid not in accounts or accounts[aid]['kind'] != accounts[p['account_id']]['kind']:
            raise DomainError('历史账户更正须选同类型账户，不能把银行现金记录改成信用欠款记录。')
        p['account_id'] = aid
        for line in h.get('ledger_lines', []):
            if line.get('code') == ('1001' if accounts[aid]['kind'] == 'cash' else '2001'):
                line.update(account_id=aid, account_label=accounts[aid]['name'], name=accounts[aid]['name'])
    related = [t for t in txs.values() if is_historical(t) and t['id'] != tid and (t['id'] == h.get('related_id') or t['historical'].get('related_id') == tid)]
    if event != h['event'] and related:
        raise DomainError('此记录已有配对或退款关系，不能单独改变性质。', 'linked_amount_conflict', 409)
    if value != business_amount(p) and h.get('link_type') == '过桥资金组':
        raise DomainError('过桥组包含多笔分拆金额，不能按一对一关系改金额；请保留原金额并在说明中记录待核事项。', 'linked_amount_conflict', 409)
    if event != h['event']:
        _interpret_event(p, event, value, accounts)
    else:
        _scale(p, value)
    for key in ('date', 'cash_date'):
        if key in changes:
            p[key] = valid_date(changes[key]) if changes[key] is not None else None
    if not p.get('cash_date') and int(h.get('cash_minor') or '0'):
        raise DomainError('已有实际现金收付的记录必须保留现金收付日。')
    if 'description' in changes:
        if not isinstance(changes['description'], str) or len(changes['description']) > 2000:
            raise DomainError('描述须为不超过 2000 字的文本。')
        p['description'] = changes['description']
    if 'in_scope' in changes:
        if type(changes['in_scope']) is not bool:
            raise DomainError('in_scope 须为布尔值。')
        if changes['in_scope'] and not p.get('date'):
            raise DomainError('纳入报表前须先确认日期。')
        if h['in_scope'] != changes['in_scope']: h['scope_override'] = True
        h['in_scope'] = changes['in_scope']
    if 'review' in changes and changes['review'] != h.get('review'):
        if changes['review'] not in {'人工确认', '待确认', '分类暂定'}:
            raise DomainError('复核状态仅支持人工确认、待确认或分类暂定。')
        h['review'] = changes['review']
    for key in ('budget_line_id', 'refund_of'):
        if key in changes:
            value_text = changes[key] or None
            if value_text is not None and (not isinstance(value_text, str) or len(value_text) > 160):
                raise DomainError(key + ' 无效。')
            p[key] = value_text
    if 'refund_of' in changes:
        if expense_value(p) >= 0:
            if p.get('refund_of'): raise DomainError('只有负费用可以关联原消费。')
        elif p.get('refund_of'):
            h.update(related_id=p['refund_of'], link_type='退款对应原消费')
        elif h.get('link_type') == '退款对应原消费':
            h.update(related_id=None, link_type=None)
    if 'category_code' in changes:
        code = changes['category_code'] or None
        inc = int(h.get('income_minor') or '0'); exp = int(h.get('expense_minor') or '0')
        if exp and (code not in NAMES or not code.startswith('5')):
            raise DomainError('费用只能选择费用科目。')
        if inc and code not in {'4001', '4004', '4005'}:
            raise DomainError('收入只能选择已支持的收入科目。')
        if h['event'] in INCOME_EVENTS and code != INCOME_EVENTS[h['event']]:
            raise DomainError('该收入性质有固定科目；请同时修改业务性质。')
        if not inc and not exp and code != p.get('category_code'):
            raise DomainError('非收支行的科目由原业务关系决定，不能单独改成费用或收入。')
        _set_category(p, code)
    planned = {tid: p}
    # Two source rows describe one movement. Preserve both source signs, update
    # the interpretation atomically, and keep their independently observed dates.
    if h.get('link_type') in PAIRED_LINKS:
        peer_id = h.get('related_id'); peer = txs.get(peer_id)
        if peer and is_historical(peer):
            if h.get('link_type') in {'还款双源','范围内转账双边抵消'} and peer['account_id'] == p['account_id']:
                raise DomainError('配对两端不能归到同一账户。', 'linked_amount_conflict', 409)
            peer_p = deepcopy(raw_payload(peer))
            if value != business_amount(current):
                if business_amount(peer) != business_amount(current):
                    raise DomainError('原配对两端金额不一致，请先核对关系。', 'linked_amount_conflict', 409)
                _scale(peer_p, value)
            if 'in_scope' in changes:
                if h['in_scope'] and not peer_p.get('date'):
                    raise DomainError('配对另一端尚无日期，不能同时纳入报表。', 'linked_amount_conflict', 409)
                if peer_p['historical']['in_scope'] != h['in_scope']: peer_p['historical']['scope_override'] = True
                peer_p['historical']['in_scope'] = h['in_scope']
            if peer_p != raw_payload(peer): planned[peer_id] = peer_p
    return validate_graph(planned, txs, tid, changes)


def validate_graph(planned, txs, tid=None, changes=None):
    from .domain import DomainError
    from .storage import raw_payload
    changes=changes or {}
    combined = {**txs, **planned}
    # Parent categories flow to every confirmed refund. Validation is against
    # the whole post-edit graph, including native refund children.
    touched = set(planned)
    for child_id, child in list(combined.items()):
        parent_id = refund_parent(child)
        if not parent_id or (child_id not in touched and parent_id not in touched): continue
        parent = combined.get(parent_id)
        if not parent or is_historical(parent) != is_historical(child) or expense_value(parent) <= 0 or expense_value(child) >= 0 or parent['account_id'] != child['account_id']:
            raise DomainError('退款必须关联同账户的正额原消费。', 'linked_amount_conflict', 409)
        if not child.get('date') or not parent.get('date') or child['date'] < parent['date']:
            raise DomainError('退款日期不能早于原消费。', 'linked_amount_conflict', 409)
        background_origin = is_historical(parent) and not parent['historical'].get('scope_override') and parent['date'][:4] < child['date'][:4]
        if scoped(child) and not scoped(parent) and not background_origin:
            raise DomainError('原消费排除时，其已关联退款也需要先排除。', 'linked_amount_conflict', 409)
        siblings = [t for t in combined.values() if refund_parent(t) == parent_id]
        if sum(-expense_value(t) for t in siblings) > expense_value(parent):
            raise DomainError('累计退款不能超过原消费。', 'linked_amount_conflict', 409)
        if child.get('category_code') != parent.get('category_code'):
            if child_id == tid and 'category_code' in changes:
                raise DomainError('已关联退款的科目继承原消费，请修改原消费科目。')
            updated = deepcopy(raw_payload(child))
            if is_historical(updated): _set_category(updated, parent['category_code'])
            else: updated['category_code'] = parent['category_code']
            planned[child_id] = updated; combined[child_id] = updated
    for item in planned.values():
        if is_historical(item):
            if sum(int(l['debit_minor']) - int(l['credit_minor']) for l in item['historical'].get('ledger_lines', [])):
                raise DomainError('修改后的分录不平衡，未保存。', 'ledger_unbalanced', 409)
    return planned


def undo_plan(db, current, txs):
    from .domain import DomainError
    marker = current['historical'].get('edit_group') or {}
    members = marker.get('members') or [current['id']]
    result = {}
    for tid in members:
        item = txs.get(tid)
        if not item:
            raise DomainError('关联记录不存在，不能撤销。', 'linked_version_conflict', 409)
        last = db.execute('SELECT * FROM transaction_versions WHERE transaction_id=? AND version=?', (tid, item['version'])).fetchone()
        peer_marker = item.get('historical', {}).get('edit_group') or item.get('edit_group') or {}
        if last['action'] != 'edit' or not last['before_payload'] or (marker and peer_marker.get('id') != marker.get('id')):
            raise DomainError('相关记录已有后续修改，不能用旧版本覆盖；请查看修改历史。', 'linked_version_conflict', 409)
        result[tid] = json.loads(last['before_payload'])
    # Validate both incoming and outgoing relations against current versions.
    # Undo may not acquire new children or rewrite later unrelated decisions.
    validated=validate_graph(deepcopy(result),txs)
    if validated!=result:
        raise DomainError('撤销与当前退款科目关系冲突，请先处理后续修改。', 'linked_amount_conflict', 409)
    return result


def is_historical(p):
    return p.get('kind') == 'historical'


def scoped(p):
    return not is_historical(p) or p['historical']['in_scope']


def reasons(p):
    h = p['historical']
    if not p.get('date'):
        return ['原始日期未确认，保留来源但未纳入期间统计']
    if not h['in_scope']:
        return []
    review = h.get('review')
    return [] if review in {'自动通过', '人工确认', '人工修订', None, ''} else [review]


def movement(p):
    h = p['historical']
    if not h['in_scope']:
        return {}
    amount = int(h.get('cash_minor') or '0') if h['family'].endswith('_debit') else -int(h.get('debt_delta_minor') or '0')
    return {p['account_id']: amount}


def effect(p):
    h = p['historical']
    active = h['in_scope']
    cash = int(h.get('cash_minor') or '0') if active and h['cash_included'] else 0
    return {
        'income_minor': str(int(h.get('income_minor') or '0') if active else 0),
        'expense_minor': str(int(h.get('expense_minor') or '0') if active else 0),
        'cash_inflow_minor': str(max(cash, 0)),
        'cash_outflow_minor': str(max(-cash, 0)),
        'budget_usage_minor': None,
        'basis': '当前版本的收入、费用与合并现金影响；修改后的解释参与报表，原始金额与凭据保留，原始符号不作统一推断。',
    }


def migration_summary(db):
    row = db.execute('SELECT manifest FROM migration_runs ORDER BY created_at DESC LIMIT 1').fetchone()
    if not row: return None
    result = json.loads(row['manifest'])
    changed = sum(1 for t in db.execute('SELECT version,payload FROM transactions WHERE version>1') if is_historical(json.loads(t['payload'])))
    result.update(current_ledger_modified=bool(changed), edited_transaction_count=changed)
    return result


def observations(db):
    return [json.loads(row['payload']) for row in db.execute('SELECT payload FROM historical_observations ORDER BY id')]


def budget_provenance(db, year, version):
    row = db.execute('SELECT payload FROM budget_provenance WHERE year=? AND version=?', (year, version)).fetchone()
    return json.loads(row['payload']) if row else None
