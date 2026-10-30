"""Independent edit acceptance: synthetic ledger, never the user's database."""
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from app.main import create_app
from app.storage import Store, advance, encode, save_transaction


@pytest.fixture
def book(tmp_path):
    client = TestClient(create_app(tmp_path), base_url='http://127.0.0.1')
    assert client.get('/api/v1/session').status_code == 200
    yield client, Store(tmp_path)
    client.close()


def get(client, path):
    response = client.get('/api/v1' + path)
    assert response.status_code == 200, response.text
    return response.json()


def account(client, kind='cash'):
    response = client.post('/api/v1/accounts', json={
        'request_id': str(uuid4()), 'name': '独立合成' + kind,
        'kind': kind, 'currency': 'CNY',
    })
    assert response.status_code == 200, response.text
    return response.json()['account']['id']


def seed(store, aid, tid, *, event='消费', value='10000', income='0', expense='10000',
         cash='-10000', debt=None, category='5001', day='2027-01-12', family='cmb_debit',
         cash_included=True, related=None, link_type=None, in_scope=True, ledger=None):
    if ledger is None:
        ledger = [] if not int(expense) else [
            {'code': category, 'name': '旧费用科目', 'sequence': 1,
             'debit_minor': str(max(int(expense), 0)), 'credit_minor': str(max(-int(expense), 0)),
             'account_id': None, 'basis': '合成原解释'},
            {'code': '1001' if family.endswith('_debit') else '2001', 'name': '旧资金科目', 'sequence': 2,
             'debit_minor': str(max(-int(expense), 0)), 'credit_minor': str(max(int(expense), 0)),
             'account_id': None, 'basis': '合成原解释'},
        ]
    p = {'date': day, 'cash_date': day, 'description': '合成旧描述 ' + tid,
         'account_id': aid, 'amount_minor': value, 'currency': 'CNY', 'kind': 'historical',
         'category_code': category, 'budget_line_id': None, 'refund_of': None,
         'historical': {'event': event, 'amount_minor': value, 'raw_amount_minor': value,
                        'income_minor': income, 'expense_minor': expense, 'cash_minor': cash,
                        'debt_delta_minor': debt, 'date': day, 'family': family, 'code': int(category),
                        'cash_included': cash_included, 'in_scope': in_scope, 'review': '人工确认',
                        'reason': '合成历史已核对', 'cash_type': '消费现金收付',
                        'related_id': related, 'link_type': link_type, 'ledger_lines': ledger,
                        'source_account_label': '合成来源账户'}}
    filename = 'synthetic-' + tid + '.json'
    evidence = encode({'id': tid, 'amount': value, 'date': day, 'description': p['description']})
    sha = store.archive(evidence.encode(), filename)
    with store.write() as db:
        db.execute('INSERT INTO artifacts VALUES(?,?,?,?)', (sha, filename, len(evidence.encode()), '2027-01-01'))
        db.execute('INSERT INTO imports VALUES(?,?,?,?,?,?,?,?,?,?,?,?)',
                   ('import-' + tid, sha, filename, aid, 'synthetic-history-1', 'imported', 1, 1, 0, 0, '[]', '2027-01-01'))
        db.execute('INSERT INTO source_rows VALUES(?,?,?,?,?,?,?)',
                   ('source-' + tid, 'import-' + tid, 1, tid, evidence, tid, None))
        save_transaction(db, tid, p, 'migrate', '合成来源', actor='migration')
        advance(db)
    return p


def edit(client, tid, changes, *, version=None, request_id=None, reason='合成证据更正'):
    if version is None:
        version = get(client, '/transactions/' + tid)['transaction']['version']
    return client.post('/api/v1/transactions/' + tid + '/edit', json={
        'request_id': request_id or str(uuid4()), 'expected_version': version,
        'payload': changes, 'reason': reason,
    })


def ok_edit(client, tid, changes, **kwargs):
    response = edit(client, tid, changes, **kwargs)
    assert response.status_code == 200, response.text
    return response.json()


def undo(client, tid, version=None):
    if version is None:
        version = get(client, '/transactions/' + tid)['transaction']['version']
    return client.post('/api/v1/transactions/' + tid + '/undo', json={
        'request_id': str(uuid4()), 'expected_version': version, 'reason': '合成撤销',
    })


def budget(client, year=2027):
    payload = {'year': year, 'currency': 'CNY', 'funding_minor': ['100000'] * 12,
               'target_minor': '100000', 'usage': [], 'lines': [
                   {'id': 'food', 'name': '合成餐饮', 'group': '日常', 'allocation': 'daily',
                    'execution_basis': 'consumption', 'actual_basis': 'consumption',
                    'monthly_minor': ['20000'] * 12},
                   {'id': 'transit', 'name': '合成交通', 'group': '日常', 'allocation': 'daily',
                    'execution_basis': 'consumption', 'actual_basis': 'consumption',
                    'monthly_minor': ['10000'] * 12},
               ]}
    response = client.post('/api/v1/budgets', json={
        'request_id': str(uuid4()), 'expected_version': 0, 'payload': payload,
    })
    assert response.status_code == 200, response.text
    adopted = client.post(f"/api/v1/budget-versions/{payload['year']}/{response.json()['version']}/adopt", json={"request_id":str(uuid4()), "expected_adopted_version":0})
    assert adopted.status_code == 200, adopted.text
    return payload


def report(client, month='2027-01'):
    return get(client, '/reports?month=' + month)


def assert_balanced(lines):
    assert sum(int(x['debit_minor']) - int(x['credit_minor']) for x in lines) == 0


def test_classification_updates_journal_budget_and_history_without_overwriting_source(book):
    client, store = book
    aid = account(client)
    original = seed(store, aid, 'purchase')
    budget(client)
    before = get(client, '/transactions/purchase')
    assert before['read_only'] is False
    with store.read() as db:
        budget_snapshot = db.execute('SELECT payload FROM budget_versions').fetchone()[0]
    ok_edit(client, 'purchase', {'category_code': '5004', 'budget_line_id': 'transit',
                                'description': '合成修订描述'}, reason='')
    after = get(client, '/transactions/purchase')
    assert after['transaction']['category_code'] == '5004'
    assert after['transaction']['amount_minor'] == original['amount_minor']
    assert after['transaction']['historical']['raw_amount_minor'] == original['amount_minor']
    assert after['history'][0]['after'] == original
    assert before['sources'] and after['sources'] == before['sources']
    assert after['history'][1]['before'] == original
    assert any(x['code'] == '5004' and x['debit_minor'] == '10000' for x in after['ledger_lines'])
    assert not any(x['code'] == '5001' for x in after['ledger_lines'])
    assert_balanced(after['ledger_lines'])
    assert next(x for x in report(client)['income_expense']['expense_lines'] if x['code'] == '5004')['amount_minor'] == '10000'
    current_budget = get(client, '/budgets?year=2027')
    assert current_budget['version'] == 1
    assert current_budget['budget']['usage'] == [{'id': 'purchase', 'line_id': 'transit',
        'date': '2027-01-12', 'amount_minor': '10000', 'kind': 'consumption'}]
    with store.read() as db:
        assert db.execute('SELECT payload FROM budget_versions').fetchone()[0] == budget_snapshot
    response = undo(client, 'purchase')
    assert response.status_code == 200, response.text
    restored = get(client, '/transactions/purchase')
    assert restored['transaction']['category_code'] == '5001'
    assert [x['action'] for x in restored['history']] == ['migrate', 'edit', 'undo']
    assert get(client, '/budgets?year=2027')['budget']['usage'] == []


def test_consumption_date_does_not_move_cash_date_and_amount_updates_both_effects(book):
    client, store = book
    aid = account(client)
    seed(store, aid, 'purchase', value='-10000')
    budget(client)
    ok_edit(client, 'purchase', {'date': '2027-02-03', 'business_amount_minor': '12500',
                                'budget_line_id': 'food'})
    detail = get(client, '/transactions/purchase')
    assert detail['transaction']['cash_date'] == '2027-01-12'
    assert detail['transaction']['amount_minor'] == '-10000'
    assert detail['effects']['expense_minor'] == '12500'
    assert detail['effects']['cash_outflow_minor'] == '12500'
    assert report(client)['income_expense']['expense_minor'] == '0'
    assert report(client)['cash_flow']['outflow_minor'] == '12500'
    assert report(client, '2027-02')['income_expense']['expense_minor'] == '12500'
    assert report(client, '2027-02')['cash_flow']['outflow_minor'] == '0'
    assert get(client, '/budgets?year=2027')['budget']['usage'][0]['date'] == '2027-02-03'
    ok_edit(client, 'purchase', {'cash_date': '2027-03-01'})
    assert report(client)['cash_flow']['outflow_minor'] == '0'
    assert report(client, '2027-02')['income_expense']['expense_minor'] == '12500'
    assert report(client, '2027-03')['cash_flow']['outflow_minor'] == '12500'


def test_linked_refund_inherits_category_and_blocks_invalid_parent_revisions(book):
    client, store = book
    aid = account(client, 'credit')
    seed(store, aid, 'purchase', value='20000', expense='20000', cash='0', debt='20000', family='citic_credit', cash_included=False)
    seed(store, aid, 'refund', event='消费退款或返利', value='-5000', expense='-5000', cash='0', debt='-5000',
         day='2027-01-18', family='citic_credit', cash_included=False,
         related='purchase', link_type='退款对应原消费')
    budget(client)
    ok_edit(client, 'purchase', {'category_code': '5004', 'budget_line_id': 'transit'})
    parent = get(client, '/transactions/purchase')
    child = get(client, '/transactions/refund')
    assert child['transaction']['category_code'] == '5004'
    assert any(x['code'] == '5004' and x['credit_minor'] == '5000' for x in child['ledger_lines'])
    assert report(client)['income_expense']['expense_minor'] == '15000'
    usage = get(client, '/budgets?year=2027')['budget']['usage']
    assert sum(int(x['amount_minor']) for x in usage) == 15000
    for changes in ({'business_amount_minor': '4000'}, {'date': '2027-01-20'}):
        response = edit(client, 'purchase', changes)
        assert response.status_code in (409, 422), response.text
        assert get(client, '/transactions/purchase')['transaction'] == parent['transaction']
        assert get(client, '/transactions/refund')['transaction'] == child['transaction']


@pytest.mark.parametrize(('event', 'category'), [('消费退款或返利', '5001'), ('利息或贴息', '5012')])
def test_unlinked_negative_expense_keeps_its_confirmed_sign_when_edited(book, event, category):
    client, store = book
    aid = account(client, 'credit')
    seed(store, aid, 'negative', event=event, value='-200', expense='-200', cash='0', debt='-200',
         family='citic_credit', category=category, cash_included=False)
    ok_edit(client, 'negative', {'business_amount_minor': '300', 'description': '合成确认退费'})
    detail = get(client, '/transactions/negative')
    assert detail['effects']['income_minor'] == '0'
    assert detail['effects']['expense_minor'] == '-300'
    assert detail['effects']['cash_inflow_minor'] == '0'
    assert detail['transaction']['amount_minor'] == '-200'
    assert report(client)['income_expense']['expense_minor'] == '-300'
    assert_balanced(detail['ledger_lines'])


@pytest.mark.parametrize('event', ['分期本金出账', '转分期', '分期退款结算调整'])
def test_installment_revisions_never_recount_original_purchase_as_expense(book, event):
    client, store = book
    aid = account(client, 'credit')
    seed(store, aid, 'installment', event=event, value='5000', expense='0', cash='0', debt=None,
         family='citic_credit', category='2001', cash_included=False, ledger=[])
    ok_edit(client, 'installment', {'business_amount_minor': '6000', 'description': '合成金额校正'})
    detail = get(client, '/transactions/installment')
    assert detail['effects']['expense_minor'] == detail['effects']['income_minor'] == '0'
    assert detail['effects']['cash_inflow_minor'] == detail['effects']['cash_outflow_minor'] == '0'
    assert report(client)['income_expense']['expense_minor'] == '0'


def test_paired_transfer_edits_both_balances_and_undo_refuses_later_peer_edit(book):
    client, store = book
    one, two = account(client), account(client)
    seed(store, one, 'out', event='本人账户划转', value='-5000', expense='0', cash='-5000', category='1001',
         cash_included=False, related='in', link_type='范围内转账双边抵消', ledger=[])
    seed(store, two, 'in', event='本人账户划转', value='5000', expense='0', cash='5000', category='1001',
         cash_included=False, related='out', link_type='范围内转账双边抵消', ledger=[])
    ok_edit(client, 'out', {'business_amount_minor': '7500'})
    out, incoming = get(client, '/transactions/out'), get(client, '/transactions/in')
    assert out['transaction']['version'] == incoming['transaction']['version'] == 2
    assert sorted(x['known_change_minor'] for x in get(client, '/accounts')['items']) == ['-7500', '7500']
    assert report(client)['cash_flow']['net_minor'] == '0'
    assert report(client)['cash_flow']['inflow_minor'] == report(client)['cash_flow']['outflow_minor'] == '0'
    assert incoming['transaction']['amount_minor'] == '5000'
    ok_edit(client, 'in', {'description': '对端后续独立修订'}, reason='')
    response = undo(client, 'out', version=2)
    assert response.status_code == 409, response.text
    assert get(client, '/transactions/out')['transaction'] == out['transaction']
    assert get(client, '/transactions/in')['transaction']['description'] == '对端后续独立修订'


def test_paired_repayment_has_one_cash_outflow_and_group_undo_restores_both(book):
    client, store = book
    bank, card = account(client), account(client, 'credit')
    seed(store, bank, 'cash-pay', event='信用还款', value='-5000', expense='0', cash='-5000', category='2001',
         related='card-pay', link_type='还款双源', ledger=[])
    seed(store, card, 'card-pay', event='信用还款', value='-5000', expense='0', cash='0', debt='-5000', category='2001',
         cash_included=False, family='citic_credit', related='cash-pay', link_type='还款双源', ledger=[])
    ok_edit(client, 'cash-pay', {'business_amount_minor': '7000'})
    assert report(client)['cash_flow']['outflow_minor'] == '7000'
    assert report(client)['income_expense']['expense_minor'] == '0'
    assert get(client, '/transactions/card-pay')['transaction']['historical']['debt_delta_minor'] == '-7000'
    response = undo(client, 'cash-pay')
    assert response.status_code == 200, response.text
    assert report(client)['cash_flow']['outflow_minor'] == '5000'
    assert get(client, '/transactions/card-pay')['transaction']['historical']['debt_delta_minor'] == '-5000'
    assert get(client, '/transactions/cash-pay')['transaction']['version'] == 3
    assert get(client, '/transactions/card-pay')['transaction']['version'] == 3


def test_retry_is_idempotent_and_stale_write_or_forged_effects_are_rejected(book):
    client, store = book
    aid = account(client)
    seed(store, aid, 'purchase')
    request_id = str(uuid4())
    first = ok_edit(client, 'purchase', {'description': '合成修订'}, request_id=request_id, version=1)
    second = ok_edit(client, 'purchase', {'description': '合成修订'}, request_id=request_id, version=1)
    assert first == second
    assert len(get(client, '/transactions/purchase')['history']) == 2
    assert edit(client, 'purchase', {'description': '过期'}, version=1).status_code == 409
    for changes in ({'historical': {'expense_minor': '99999'}}, {'amount_minor': '99999'}, {'raw_amount_minor': '99999'}):
        response = edit(client, 'purchase', changes)
        assert response.status_code == 422, response.text
    assert get(client, '/transactions/purchase')['transaction']['description'] == '合成修订'
    assert report(client)['income_expense']['expense_minor'] == '10000'


def payment_budget(client):
    payload = {'year': 2027, 'currency': 'CNY', 'funding_minor': ['100000'] * 12,
               'target_minor': '100000', 'usage': [], 'lines': [
                   {'id': 'loan', 'name': '合成分期还款', 'group': '生活', 'allocation': 'fixed',
                    'execution_basis': 'scheduled_payment', 'actual_basis': 'payment',
                    'monthly_minor': ['10000'] * 12},
               ]}
    response = client.post('/api/v1/budgets', json={
        'request_id': str(uuid4()), 'expected_version': 0, 'payload': payload,
    })
    assert response.status_code == 200, response.text
    adopted = client.post(f"/api/v1/budget-versions/{payload['year']}/{response.json()['version']}/adopt", json={"request_id":str(uuid4()), "expected_adopted_version":0})
    assert adopted.status_code == 200, adopted.text


def test_payment_budget_uses_actual_cash_endpoint_once_and_its_independent_date(book):
    client, store = book
    bank, card = account(client), account(client, 'credit')
    seed(store, bank, 'cash-pay', event='信用还款', value='-5000', expense='0', cash='-5000', category='2001',
         related='card-pay', link_type='还款双源', ledger=[])
    seed(store, card, 'card-pay', event='信用还款', value='-5000', expense='0', cash='0', debt='-5000', category='2001',
         cash_included=False, family='citic_credit', related='cash-pay', link_type='还款双源', ledger=[])
    payment_budget(client)
    ok_edit(client, 'cash-pay', {'budget_line_id': 'loan', 'cash_date': '2027-02-01'})
    counterpart_link = edit(client, 'card-pay', {'budget_line_id': 'loan'})
    # A no-cash endpoint may be rejected as a payment link, or retained as evidence;
    # either policy must never count it as another payment.
    assert counterpart_link.status_code in (200, 422), counterpart_link.text
    usage = get(client, '/budgets?year=2027')['budget']['usage']
    assert len(usage) == 1
    assert usage[0]['id'] == 'cash-pay'
    assert usage[0]['date'] == '2027-02-01'
    assert usage[0]['kind'] == 'payment'
    assert usage[0]['amount_minor'] == '5000'
    ok_edit(client, 'cash-pay', {'business_amount_minor': '7500'})
    assert get(client, '/budgets?year=2027')['budget']['usage'][0]['amount_minor'] == '7500'
    assert report(client)['cash_flow']['outflow_minor'] == '0'
    assert report(client, '2027-02')['cash_flow']['outflow_minor'] == '7500'
    assert get(client, '/budgets?year=2027')['version'] == 1


@pytest.mark.parametrize(('event', 'expense', 'debt', 'category'), [
    ('消费', '8000', '8000', '5009'),
    ('分期本金出账', '0', None, '2001'),
])
def test_unpaid_credit_fact_does_not_fulfil_a_payment_budget(book, event, expense, debt, category):
    client, store = book
    card = account(client, 'credit')
    seed(store, card, 'credit-fact', event=event, value='8000', expense=expense, cash='0', debt=debt,
         family='citic_credit', category=category, cash_included=False)
    payment_budget(client)
    response = edit(client, 'credit-fact', {'budget_line_id': 'loan'})
    assert response.status_code in (200, 422), response.text
    computed = get(client, '/budgets?year=2027')['budget']
    assert computed['usage'] == []
    assert computed['months'][0]['fixed_outstanding_minor'] is None
    assert computed['months'][0]['known_fixed_outstanding_minor'] == '10000'
    assert computed['months'][0]['quality']['payment']['exact'] is False
    assert report(client)['income_expense']['expense_minor'] == expense
    assert report(client)['cash_flow']['outflow_minor'] == '0'


def test_unknown_mortgage_split_can_still_fulfil_its_full_confirmed_payment(book):
    client, store = book
    bank = account(client)
    seed(store, bank, 'mortgage', event='房贷还款待拆分', value='-9000', expense='0', cash='-9000',
         category='2003', ledger=[])
    payment_budget(client)
    ok_edit(client, 'mortgage', {'budget_line_id': 'loan'})
    computed = get(client, '/budgets?year=2027')['budget']
    assert computed['usage'][0]['amount_minor'] == '9000'
    assert computed['usage'][0]['kind'] == 'payment'
    assert computed['months'][0]['fixed_outstanding_minor'] is None
    assert computed['months'][0]['known_fixed_outstanding_minor'] == '1000'
    assert computed['months'][0]['quality']['payment']['exact'] is False
    assert report(client)['income_expense']['expense_minor'] == '0'
    assert report(client)['cash_flow']['outflow_minor'] == '9000'


def test_credit_consumption_date_moves_debt_period_without_moving_cash_receipt(book):
    client, store = book
    card = account(client, 'credit')
    seed(store, card, 'purchase', value='10000', expense='10000', cash='0', debt='10000',
         family='citic_credit', cash_included=False)
    assert report(client)['balance_sheet']['accounts'][0]['known_change_minor'] == '10000'
    ok_edit(client, 'purchase', {'date': '2027-02-03'})
    assert report(client)['income_expense']['expense_minor'] == '0'
    assert report(client)['balance_sheet']['accounts'][0]['known_change_minor'] == '0'
    assert report(client, '2027-02')['balance_sheet']['accounts'][0]['known_change_minor'] == '10000'
    assert get(client, '/transactions/purchase')['transaction']['cash_date'] == '2027-01-12'


def test_confirming_expense_nature_updates_income_expense_without_double_cash(book):
    client, store = book
    bank = account(client)
    seed(store, bank, 'unknown', event='个人转付待确认', value='-5000', expense='0', cash='-5000',
         category='1005', ledger=[])
    assert report(client)['income_expense']['expense_minor'] == '0'
    assert report(client)['cash_flow']['outflow_minor'] == '5000'
    ok_edit(client, 'unknown', {'historical_event': '消费', 'category_code': '5001', 'review': '人工确认'})
    detail = get(client, '/transactions/unknown')
    assert detail['transaction']['historical']['event'] == '消费'
    assert detail['effects']['expense_minor'] == '5000'
    assert detail['effects']['cash_outflow_minor'] == '5000'
    assert report(client)['income_expense']['expense_minor'] == '5000'
    assert report(client)['cash_flow']['outflow_minor'] == '5000'
    assert_balanced(detail['ledger_lines'])
    response = undo(client, 'unknown')
    assert response.status_code == 200, response.text
    assert report(client)['income_expense']['expense_minor'] == '0'
    assert report(client)['cash_flow']['outflow_minor'] == '5000'


def test_undated_background_record_can_be_confirmed_without_changing_its_source(book):
    client, store = book
    card = account(client, 'credit')
    original = seed(store, card, 'undated', day=None, in_scope=False, family='jd_baitiao',
                    event='日期或退款时点待确认', value='-1000', expense='0', cash='0',
                    debt=None, category='5016', cash_included=False, ledger=[])
    assert get(client, '/transactions/undated')['transaction']['status'] == 'excluded'
    ok_edit(client, 'undated', {'date': '2027-02-01', 'historical_event': '消费退款或返利',
                              'category_code': '5009', 'review': '人工确认', 'in_scope': True})
    detail = get(client, '/transactions/undated')
    assert detail['history'][0]['after'] == original
    assert detail['transaction']['date'] == '2027-02-01'
    assert detail['sources'][0]['raw']['date'] is None
    assert detail['effects']['expense_minor'] == '-1000'
    assert report(client, '2027-02')['income_expense']['expense_minor'] == '-1000'


@pytest.mark.parametrize('parent_change', [
    {'business_amount_minor': '4000'},
    {'date': '2027-01-20'},
    {'in_scope': False},
])
def test_undo_refund_unlink_rechecks_its_current_parent(book, parent_change):
    client, store = book
    card = account(client, 'credit')
    seed(store, card, 'purchase', value='20000', expense='20000', cash='0', debt='20000',
         family='citic_credit', cash_included=False)
    seed(store, card, 'refund', event='消费退款或返利', value='-5000', expense='-5000', cash='0', debt='-5000',
         day='2027-01-18', family='citic_credit', cash_included=False,
         related='purchase', link_type='退款对应原消费')
    ok_edit(client, 'refund', {'refund_of': None})
    ok_edit(client, 'purchase', parent_change)
    parent_before = get(client, '/transactions/purchase')
    child_before = get(client, '/transactions/refund')
    response = undo(client, 'refund')
    assert response.status_code == 409, response.text
    assert get(client, '/transactions/purchase')['transaction'] == parent_before['transaction']
    assert get(client, '/transactions/refund')['transaction'] == child_before['transaction']


def test_paired_scope_change_is_atomic_and_cannot_enable_an_undated_peer(book):
    client, store = book
    one, two = account(client), account(client)
    seed(store, one, 'out', event='本人账户划转', value='-5000', expense='0', cash='-5000', category='1001',
         cash_included=False, related='in', link_type='范围内转账双边抵消', in_scope=False, ledger=[])
    seed(store, two, 'in', event='本人账户划转', value='5000', expense='0', cash='5000', category='1001',
         cash_included=False, related='out', link_type='范围内转账双边抵消', in_scope=False, day=None, ledger=[])
    response = edit(client, 'out', {'in_scope': True})
    assert response.status_code in (409, 422), response.text
    assert get(client, '/transactions/out')['transaction']['historical']['in_scope'] is False
    assert get(client, '/transactions/in')['transaction']['historical']['in_scope'] is False


def test_clearing_confirmed_cash_date_cannot_silently_remove_money_from_cash_report(book):
    client, store = book
    bank = account(client)
    seed(store, bank, 'purchase', value='-10000')
    response = edit(client, 'purchase', {'cash_date': None})
    assert response.status_code in (409, 422), response.text
    assert report(client)['cash_flow']['outflow_minor'] == '10000'


def test_historical_to_native_refund_link_is_not_accepted_without_shared_validation(book):
    client, store = book
    card = account(client, 'credit')
    native = {'date': '2027-01-02', 'cash_date': '2027-01-02', 'description': '合成新消费',
              'account_id': card, 'amount_minor': '-10000', 'currency': 'CNY', 'kind': 'expense',
              'category_code': '5001', 'counterparty_account_id': None, 'refund_of': None,
              'budget_line_id': None, 'counterparty_confirmed': False, 'source_id': 'native-purchase'}
    with store.write() as db:
        save_transaction(db, 'native-purchase', native, 'import', '合成来源')
        advance(db)
    seed(store, card, 'refund', event='消费退款或返利', value='-5000', expense='-5000', cash='0', debt='-5000',
         family='citic_credit', cash_included=False)
    response = edit(client, 'refund', {'refund_of': 'native-purchase'})
    assert response.status_code in (409, 422), response.text
    assert get(client, '/transactions/refund')['transaction'].get('refund_of') is None


def test_cross_year_cash_payment_uses_payment_year_in_both_budget_and_detail(book):
    client, store = book
    bank = account(client)
    seed(store, bank, 'mortgage', event='房贷还款待拆分', value='-9000', expense='0', cash='-9000',
         category='2003', day='2026-12-30', ledger=[])
    payment_budget(client)
    ok_edit(client, 'mortgage', {'budget_line_id': 'loan', 'cash_date': '2027-01-03'})
    usage = get(client, '/budgets?year=2027')['budget']['usage']
    assert len(usage) == 1 and usage[0]['date'] == '2027-01-03'
    assert usage[0]['amount_minor'] == '9000'
    assert get(client, '/transactions/mortgage')['effects']['budget_usage_minor'] == '9000'
    assert report(client, '2026-12')['cash_flow']['outflow_minor'] == '0'
    assert report(client)['cash_flow']['outflow_minor'] == '9000'


def test_confirmed_cross_year_refund_remains_editable_when_origin_is_background(book):
    client, store = book
    card = account(client, 'credit')
    seed(store, card, 'old-purchase', value='10000', expense='10000', cash='0', debt='10000',
         family='citic_credit', cash_included=False, day='2026-12-20', in_scope=False)
    seed(store, card, 'refund', event='消费退款或返利', value='-3000', expense='-3000', cash='0', debt='-3000',
         family='citic_credit', cash_included=False, related='old-purchase', link_type='退款对应原消费')
    budget(client)
    ok_edit(client, 'refund', {'description': '旧年度原单的已确认退款', 'budget_line_id': 'food'}, reason='')
    assert report(client)['income_expense']['expense_minor'] == '-3000'
    assert get(client, '/transactions/refund')['transaction']['historical']['related_id'] == 'old-purchase'
    # No invented 2027 spending is released by a refund of a 2026 purchase.
    assert get(client, '/budgets?year=2027')['budget']['usage'] == []
    assert get(client, '/budgets?year=2027')['unallocated_usage_minor'] == '3000'
