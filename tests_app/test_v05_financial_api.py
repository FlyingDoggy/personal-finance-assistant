"""Public entry-point checks for v0.5; isolated synthetic accounts only."""
from uuid import uuid4

from test_backend import client_at, add_account, upload, list_transactions, edit, save_budget
from test_v05_financial_core import budget


def test_coverage_api_retry_versions_and_fact_revision_are_independent(tmp_path):
    client = client_at(tmp_path)
    aid = add_account(client)
    before = client.get('/api/v1/status').json()['data_revision']
    body = {'request_id': str(uuid4()), 'account_id': aid, 'month': '2027-03',
            'basis': 'consumption', 'status': 'complete', 'evidence': '合成全月零交易账单',
            'confirmed': True, 'expected_version': 0}
    first = client.post('/api/v1/coverage', json=body)
    assert first.status_code == 200, first.text
    assert first.json()['data_revision'] == before
    assert first.json()['coverage_version'] == 1
    assert client.post('/api/v1/coverage', json=body).json() == first.json()
    current = client.get('/api/v1/coverage?month=2027-03&basis=consumption').json()
    assert current['complete'] is True
    assert current['items'][0]['version'] == 1
    conflict = client.post('/api/v1/coverage', json={**body, 'request_id': str(uuid4())})
    assert conflict.status_code == 409
    upload(client, aid, [['new', '2027-03-02', '合成补录', '-10.00', 'expense', '5001']])
    current = client.get('/api/v1/coverage?month=2027-03&basis=consumption').json()
    assert current['complete'] is False and current['items'][0]['stale'] is True
    # Saved declaration remains auditable; the new row does not increment it.
    assert current['coverage_version'] == 1 and current['items'][0]['version'] == 1


def test_public_repayment_binding_and_single_field_confirmation_are_rejected(tmp_path):
    client = client_at(tmp_path)
    bank, card = add_account(client), add_account(client, 'credit')
    assert save_budget(client, budget(True)).status_code == 200
    upload(client, card, [['buy', '2027-01-01', '合成信用消费', '-100.00', 'expense', '5001', '', '', 'daily']])
    upload(client, bank, [['repay', '2027-01-05', '合成还款', '-100.00', 'repayment', '', card]])
    repayment = next(t for t in list_transactions(client) if t['kind'] == 'repayment')
    rejected = edit(client, repayment, {'budget_line_id': 'payment'})
    assert rejected.status_code == 422
    assert rejected.json()['error']['code'] == 'repayment_budget_unsupported'
    confirmed = edit(client, repayment, {'counterparty_confirmed': True})
    assert confirmed.status_code == 422
    assert confirmed.json()['error']['code'] == 'relation_confirmation_unsupported'
    result = client.get('/api/v1/budgets?year=2027').json()
    assert result['budget']['months'][0]['usage_minor'] == '10000'
    assert len(result['budget']['usage']) == 1
    assert next(t for t in list_transactions(client) if t['id'] == repayment['id'])['version'] == 1


def test_public_reimbursement_date_edit_cannot_advance_receipt_income(tmp_path):
    client = client_at(tmp_path)
    aid = add_account(client)
    upload(client, aid, [['reimb', '2027-02-01', '合成到账报销', '100.00', 'reimbursement']])
    transaction = list_transactions(client)[0]
    rejected = edit(client, transaction, {'date': '2027-01-31'})
    assert rejected.status_code == 422
    assert rejected.json()['error']['code'] == 'reimbursement_date_conflict'
    assert client.get('/api/v1/reports?month=2027-01').json()['income_expense']['income_minor'] == '0'
    feb = client.get('/api/v1/reports?month=2027-02').json()['income_expense']
    assert feb['income_minor'] == feb['reimbursement_minor'] == '10000'


def test_public_missing_month_does_not_borrow_another_months_coverage(tmp_path):
    client = client_at(tmp_path)
    aid = add_account(client)
    assert save_budget(client, budget()).status_code == 200
    upload(client, aid, [['buy', '2027-01-05', '合成待分类消费', '-80.00', 'expense', '5016']])
    for month in ['2027-01', '2027-03']:
        response = client.post('/api/v1/coverage', json={'request_id': str(uuid4()), 'account_id': aid,
            'month': month, 'basis': 'consumption', 'status': 'complete', 'evidence': '合成完整月证明',
            'confirmed': True, 'expected_version': 0})
        assert response.status_code == 200, response.text
    result = client.get('/api/v1/budgets?year=2027').json()
    jan, feb, mar = result['budget']['months'][:3]
    assert jan['usage_minor'] == '8000' and jan['daily_remaining_minor'] == '92000'
    assert feb['usage_minor'] == '0' and feb['daily_remaining_minor'] is None
    assert mar['usage_minor'] == '0' and mar['daily_remaining_minor'] == '100000'
    assert result['actuals_connected'] is False
