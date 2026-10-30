"""Independent historical-adapter acceptance. Every record here is synthetic."""
import csv
import io
import json
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from app.main import create_app
from app.storage import Store, advance, encode, save_transaction


@pytest.fixture
def book(tmp_path):
    client = TestClient(create_app(data_dir=tmp_path), base_url='http://127.0.0.1')
    assert client.get('/api/v1/session').status_code == 200
    yield client, Store(tmp_path)
    client.close()


def account(client, name='合成历史现金', kind='cash', opening=None):
    body = {'request_id': str(uuid4()), 'name': name, 'kind': kind, 'currency': 'CNY'}
    if opening is not None:
        body.update(opening_balance_minor=opening, opening_date='2026-01-01', evidence='合成期初')
    response = client.post('/api/v1/accounts', json=body)
    assert response.status_code == 200, response.text
    return response.json()['account']['id']


def historical(store, aid, tid, *, event='消费', raw='10000', income='0', expense='0', cash='0',
               debt=None, category=None, day='2026-01-12', scoped=True, included=False,
               family='citic_credit', review='自动通过', cash_type='非现金来源', lines=None):
    payload = {
        'date': day, 'cash_date': day, 'description': '合成' + event, 'account_id': aid,
        'amount_minor': raw, 'currency': 'CNY', 'kind': 'historical', 'category_code': category,
        'historical': {
            'event': event, 'raw_amount_minor': raw, 'income_minor': income,
            'expense_minor': expense, 'cash_minor': cash, 'debt_delta_minor': debt,
            'in_scope': scoped, 'cash_included': included, 'family': family,
            'review': review, 'cash_type': cash_type, 'ledger_lines': lines or [],
            'source_account_label': '合成旧账户标签',
        },
    }
    with store.write() as db:
        save_transaction(db, tid, payload, 'migration', '合成迁移证据', actor='migration')
        advance(db)
    return payload


def read(client, path):
    response = client.get('/api/v1' + path)
    assert response.status_code == 200, response.text
    return response.json()


def upload(client, aid, rows):
    stream = io.StringIO()
    writer = csv.writer(stream)
    writer.writerow(['source_id', 'date', 'description', 'amount', 'kind', 'category_code', 'counterparty_account_id'])
    writer.writerows([row + [''] * (7-len(row)) for row in rows])
    response = client.post('/api/v1/imports', data={'request_id': str(uuid4()), 'account_id': aid},
                           files={'file': ('synthetic.csv', stream.getvalue().encode(), 'text/csv')})
    assert response.status_code == 200, response.text
    return response.json()


def test_confirmed_negative_expenses_are_retained_without_inventing_refund_links(book):
    client, store = book
    card = account(client, '合成历史信用', 'credit')
    historical(store, card, 'purchase', expense='10000', debt='10000', category='5001')
    historical(store, card, 'refund', event='消费退款或返利', raw='-2500', expense='-2500', debt='-2500', category='5001')
    historical(store, card, 'interest', event='利息或贴息', raw='100', expense='100', debt='100', category='5012')
    historical(store, card, 'subsidy', event='利息或贴息', raw='-50', expense='-50', debt='-50', category='5012')
    report = read(client, '/reports?month=2026-01')
    assert report['income_expense']['expense_minor'] == '7550'
    assert report['income_expense']['income_minor'] == '0'
    assert report['cash_flow']['inflow_minor'] == report['cash_flow']['outflow_minor'] == '0'
    details = read(client, '/transactions/refund')
    assert details['read_only'] is False
    assert details['transaction']['amount_minor'] == '-2500'
    assert details['effects']['expense_minor'] == '-2500'
    assert details['effects']['income_minor'] == '0'
    assert 'refund_of' not in details['transaction']
    assert read(client, '/transactions/subsidy')['effects']['expense_minor'] == '-50'


def test_installment_movement_and_paired_repayment_do_not_create_expense_or_duplicate_cash(book):
    client, store = book
    bank = account(client)
    card = account(client, '合成信用', 'credit')
    historical(store, card, 'purchase', expense='10000', debt='10000', category='5009')
    historical(store, card, 'installment', event='分期本金出账', raw='5000', category='2001')
    historical(store, card, 'conversion', event='转分期', raw='-10000', category='2002')
    historical(store, card, 'return-adjustment', event='分期退款结算调整', raw='-5000', category='2001')
    historical(store, bank, 'bank-pay', event='信用还款', raw='-5000', cash='-5000',
               family='cmb_debit', included=True, cash_type='信用支付还款', category='2001')
    historical(store, card, 'card-pay', event='信用还款', raw='-5000', debt='-5000', category='2001')
    report = read(client, '/reports?month=2026-01')
    assert report['transaction_count'] == 6
    assert report['income_expense']['expense_minor'] == '10000'
    assert report['income_expense']['income_minor'] == '0'
    assert report['cash_flow']['outflow_minor'] == '5000'
    assert report['cash_flow']['net_minor'] == '-5000'
    assert read(client, '/transactions/installment')['effects']['expense_minor'] == '0'
    assert read(client, '/transactions/card-pay')['effects']['cash_outflow_minor'] == '0'
    card_view = next(a for a in read(client, '/accounts')['items'] if a['id'] == card)
    assert card_view['known_change_minor'] == '5000'


def test_internal_cash_pair_is_retained_per_account_but_excluded_from_combined_cash(book):
    client, store = book
    first = account(client, '合成甲')
    second = account(client, '合成乙')
    historical(store, first, 'out', event='本人账户划转', raw='-12345', cash='-12345',
               family='cmb_debit', cash_type='范围内转账（抵消）', category='1001')
    historical(store, second, 'in', event='本人账户划转', raw='12345', cash='12345',
               family='bob_debit', cash_type='范围内转账（抵消）', category='1001')
    report = read(client, '/reports?month=2026-01')
    assert report['cash_flow']['inflow_minor'] == report['cash_flow']['outflow_minor'] == '0'
    assert report['income_expense']['expense_minor'] == '0'
    assert sorted(a['known_change_minor'] for a in read(client, '/accounts')['items']) == ['-12345', '12345']


def test_outside_scope_and_undated_records_remain_retrievable_without_becoming_2026_actuals(book):
    client, store = book
    card = account(client, '合成旧信用', 'credit')
    historical(store, card, 'old', day='2025-12-20', scoped=False, expense='30000', category='5001', debt='30000')
    historical(store, card, 'undated', day=None, scoped=False, event='日期或退款时点待确认',
               raw='-10000', family='jd_baitiao', review='待确认')
    historical(store, card, 'zero', raw='0', event='关闭订单')
    all_rows = read(client, '/transactions')
    assert all_rows['total'] == 3
    assert next(t for t in all_rows['items'] if t['id'] == 'undated')['date'] is None
    assert read(client, '/transactions/undated')['read_only'] is False
    assert read(client, '/transactions/undated')['effects']['expense_minor'] == '0'
    assert read(client, '/transactions/old')['transaction']['status'] == 'excluded'
    report = read(client, '/reports?month=2026-01')
    assert report['transaction_count'] == 1
    assert report['income_expense']['expense_minor'] == '0'
    assert read(client, '/status')['available_months'] == ['2026-01']


def test_external_snapshots_do_not_create_opening_balances_or_a_scope_net_worth(book):
    client, store = book
    aid = account(client)
    historical(store, aid, 'mortgage', event='房贷还款待拆分', raw='-100000', cash='-100000',
               family='bob_debit', included=True, cash_type='房贷付款（本息未拆）', review='待确认', category='2003')
    observation = {'id': 'synthetic-observation', 'kind': '资产', 'account': '合成历史现金',
                   'date': '2026-01-25', 'amount_minor': '123456', 'ledger_balance': '未知',
                   'basis': '外部最后交易后余额', 'limitation': '不是统一截止日账面余额'}
    with store.write() as db:
        db.execute('INSERT INTO historical_observations(id,payload) VALUES(?,?)', (1, encode(observation)))
    report = read(client, '/reports?month=2026-01')
    assert report['balance_sheet']['observations'] == [observation]
    assert report['balance_sheet']['scope_net_minor'] is None
    assert report['balance_sheet']['assets_minor'] is None
    assert report['balance_sheet']['accounts'][0]['opening_balance_minor'] is None
    assert report['balance_sheet']['accounts'][0]['balance_minor'] is None
    assert report['cash_flow']['closing_minor'] is None
    assert report['cash_flow']['outflow_minor'] == '100000'
    assert report['income_expense']['expense_minor'] == '0'


def test_historical_direct_edit_and_undo_preserve_migrated_baseline(book):
    client, store = book
    aid = account(client)
    historical(store, aid, 'protected', family='cmb_debit', raw='-10000', cash='-10000',
               expense='10000', included=True, category='5001', cash_type='消费现金收付')
    before = read(client, '/transactions/protected')
    no_edit_undo = client.post('/api/v1/transactions/protected/undo', json={
        'request_id': str(uuid4()), 'expected_version': 1, 'reason': '合成撤销'})
    assert no_edit_undo.status_code == 409
    assert no_edit_undo.json()['error']['code'] == 'nothing_to_undo'
    response = client.post('/api/v1/transactions/protected/edit', json={
        'request_id': str(uuid4()), 'expected_version': 1,
        'payload': {'category_code': '5004'}, 'reason': ''})
    assert response.status_code == 200, response.text
    after = read(client, '/transactions/protected')
    assert after['transaction']['category_code'] == '5004'
    assert after['transaction']['historical']['raw_amount_minor'] == '-10000'
    assert after['history'][0] == before['history'][0]
    assert after['effects']['expense_minor'] == '10000'
    assert after['effects']['cash_outflow_minor'] == '10000'
    response = client.post('/api/v1/transactions/protected/undo', json={
        'request_id': str(uuid4()), 'expected_version': 2, 'reason': '合成撤销'})
    assert response.status_code == 200, response.text
    restored = read(client, '/transactions/protected')
    assert restored['transaction']['category_code'] == '5001'
    assert restored['history'][0] == before['history'][0]
    assert restored['transaction']['version'] == 3
    assert restored['data_revision'] > before['data_revision']


def test_public_import_and_edit_cannot_inject_historical_effects(book):
    client, store = book
    aid = account(client)
    result = upload(client, aid, [['fake-migration', '2026-01-02', '伪造历史', '-10.00', 'historical', '5001']])
    assert result['import']['rejected_count'] == 1
    assert read(client, '/transactions')['total'] == 0
    upload(client, aid, [['native', '2026-01-03', '正常消费', '-10.00', 'expense', '5001']])
    tx = read(client, '/transactions')['items'][0]
    for forged in [{'kind': 'historical'}, {'historical': {'expense_minor': '-999999'}}]:
        response = client.post('/api/v1/transactions/' + tx['id'] + '/edit', json={
            'request_id': str(uuid4()), 'expected_version': 1, 'reason': '合成伪造', 'payload': forged})
        assert response.status_code == 422, response.text
    assert read(client, '/reports?month=2026-01')['income_expense']['expense_minor'] == '1000'


def test_native_credit_transactions_and_budgets_work_alongside_null_date_history(book):
    client, store = book
    card = account(client, '合成信用', 'credit', '0')
    historical(store, card, 'undated', day=None, scoped=False, family='jd_baitiao', raw='-20000')
    historical(store, card, 'legacy', day='2026-01-01', raw='1000', debt='1000', expense='1000', category='5001')
    upload(client, card, [['native-credit', '2026-01-02', '新信用消费', '-20.00', 'expense', '5001']])
    all_rows = read(client, '/transactions')['items']
    native = next(t for t in all_rows if t['kind'] == 'expense')
    detail = read(client, '/transactions/' + native['id'])
    assert detail['effects']['expense_minor'] == '2000'
    assert sum(int(l['debit_minor']) - int(l['credit_minor']) for l in detail['ledger_lines']) == 0
    assert read(client, '/reports?month=2026-01')['income_expense']['expense_minor'] == '3000'
    assert read(client, '/accounts')['items'][0]['balance_minor'] == '3000'
    payload = {'year': 2026, 'currency': 'CNY', 'funding_minor': ['100000'] * 12,
               'target_minor': '100000', 'lines': [], 'usage': []}
    response = client.post('/api/v1/budgets', json={'request_id': str(uuid4()), 'expected_version': 0, 'payload': payload})
    assert response.status_code == 200, response.text
    adopted = client.post(f"/api/v1/budget-versions/{payload['year']}/{response.json()['version']}/adopt", json={"request_id":str(uuid4()), "expected_adopted_version":0})
    assert adopted.status_code == 200, adopted.text
    budget = read(client, '/budgets?year=2026')
    assert budget['actuals_connected'] is False
    assert budget['unallocated_usage_minor'] == '3000'


def test_human_confirmed_history_is_not_misreported_as_awaiting_classification(book):
    client, store = book
    card = account(client, '合成信用', 'credit')
    for index, review in enumerate(['人工确认', '人工修订', '自动通过']):
        historical(store, card, str(index), expense='1000', category='5001', review=review)
    assert read(client, '/transactions?review_only=true')['total'] == 0
    historical(store, card, 'tentative', expense='1000', category='5001', review='分类暂定')
    assert read(client, '/transactions?review_only=true')['total'] == 1
    assert read(client, '/reports?month=2026-01')['income_expense']['expense_minor'] == '4000'
