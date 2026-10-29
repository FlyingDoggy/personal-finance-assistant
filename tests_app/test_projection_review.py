"""Independent projection/snapshot failure-path checks; synthetic ledgers only."""
import json
from uuid import uuid4

from app import catalog, vouchers
from app.storage import Store, encode, now
from test_backend import client_at, add_account, upload, list_transactions, edit, get_report


def get(client, path):
    response = client.get('/api/v1' + path)
    assert response.status_code == 200, response.text
    return response.json()


def test_projection_validation_failure_rolls_back_fact_history_revision_and_operation(tmp_path, monkeypatch):
    client = client_at(tmp_path)
    aid = add_account(client)
    upload(client, aid, [['synthetic', '2027-01-03', '合成失败回滚', '-10.00', 'expense', '5001']])
    transaction = list_transactions(client)[0]
    before = get(client, '/transactions/' + transaction['id'])
    voucher_before = get(client, '/vouchers/' + transaction['id'])
    request_id = str(uuid4())
    payload = {'request_id': request_id, 'expected_version': transaction['version'],
               'payload': {'amount_minor': '-1200'}, 'reason': '合成修改'}
    real_ledger = vouchers.ledger
    def broken_ledger(*args, **kwargs):
        lines = real_ledger(*args, **kwargs)
        if lines:
            lines[0]['debit_minor'] = '1'
            lines[0]['credit_minor'] = '0'
        return lines
    monkeypatch.setattr(vouchers, 'ledger', broken_ledger)
    response = client.post('/api/v1/transactions/' + transaction['id'] + '/edit', json=payload)
    assert response.status_code == 409, response.text
    assert response.json()['error']['code'] == 'ledger_unbalanced'
    assert get(client, '/transactions/' + transaction['id']) == before
    assert get(client, '/vouchers/' + transaction['id']) == voucher_before
    assert client.get('/api/v1/operations/' + request_id).status_code == 404
    monkeypatch.setattr(vouchers, 'ledger', real_ledger)
    assert client.post('/api/v1/transactions/' + transaction['id'] + '/edit', json=payload).status_code == 200
    assert get_report(client)['income_expense']['expense_minor'] == '1200'


def test_dimension_version_marks_snapshot_stale_without_rewriting_saved_result(tmp_path):
    client = client_at(tmp_path)
    aid = add_account(client)
    upload(client, aid, [['synthetic', '2027-01-03', '合成版本快照', '-10.00', 'expense', '5001']])
    snapshot = client.post('/api/v1/report-snapshots', json={'request_id': str(uuid4()), 'month': '2027-01'}).json()['snapshot']
    revision_before = get(client, '/status')['data_revision']
    store = Store(tmp_path)
    with store.write() as db:
        old = db.execute('SELECT payload,sha256 FROM classification_versions WHERE version=1').fetchone()
        db.execute('INSERT INTO classification_versions VALUES(?,?,?,?,?)', (2, old['payload'], old['sha256'], now(), '合成分类版本变化'))
    after = get(client, '/report-snapshots/' + snapshot['id'])['snapshot']
    assert after['stale'] is True
    assert after['report'] == snapshot['report']
    assert after['dimension_version'] == 1
    assert get(client, '/status')['data_revision'] == revision_before


def test_restarting_with_new_rules_rebuilds_projection_without_fabricating_fact_revision(tmp_path, monkeypatch):
    client = client_at(tmp_path)
    aid = add_account(client)
    upload(client, aid, [['synthetic', '2027-01-03', '合成规则版本', '-10.00', 'expense', '5001']])
    before = get(client, '/vouchers')
    snapshot = client.post('/api/v1/report-snapshots', json={'request_id': str(uuid4()), 'month': '2027-01'}).json()['snapshot']
    monkeypatch.setattr(vouchers, 'RULE_VERSION', 'synthetic-finance-rules-v2')
    client.close()
    reopened = client_at(tmp_path)
    after = get(reopened, '/vouchers')
    assert after['data_revision'] == before['data_revision']
    assert after['items'][0]['rule_version'] == 'synthetic-finance-rules-v2'
    assert after['items'][0]['transaction_version'] == before['items'][0]['transaction_version']
    assert after['summary'] == before['summary']
    assert get(reopened, '/report-snapshots/' + snapshot['id'])['snapshot']['stale'] is True


def test_snapshot_retry_after_later_edit_keeps_one_immutable_capture(tmp_path):
    client = client_at(tmp_path)
    aid = add_account(client)
    upload(client, aid, [['synthetic', '2027-01-03', '合成幂等快照', '-10.00', 'expense', '5001']])
    transaction = list_transactions(client)[0]
    payload = {'request_id': str(uuid4()), 'month': '2027-01'}
    first = client.post('/api/v1/report-snapshots', json=payload).json()
    assert edit(client, transaction, {'amount_minor': '-1500'}).status_code == 200
    revision_after_edit = get(client, '/status')['data_revision']
    replay = client.post('/api/v1/report-snapshots', json=payload).json()
    assert replay == first  # idempotent operation result is the original receipt
    assert get(client, '/report-snapshots')['total'] == 1
    current = get(client, '/report-snapshots/' + first['snapshot']['id'])['snapshot']
    assert current['stale'] is True
    assert current['report']['income_expense']['expense_minor'] == '1000'
    assert get_report(client)['income_expense']['expense_minor'] == '1500'
    assert get(client, '/status')['data_revision'] == revision_after_edit


def test_local_id_collision_keeps_two_vouchers_and_edit_only_changes_its_endpoint(tmp_path):
    client = client_at(tmp_path)
    one, two = add_account(client), add_account(client)
    upload(client, one, [['synthetic-pair', '2027-01-03', '合成转出', '-10.00', 'transfer', '', two]])
    upload(client, two, [['synthetic-pair', '2027-01-03', '合成转入', '10.00', 'transfer', '', one]])
    transaction = next(t for t in list_transactions(client) if t['account_id'] == one)
    before = {t['transaction_id']: t for t in get(client, '/vouchers')['items']}
    assert edit(client, transaction, {'amount_minor': '-1200'}).status_code == 200
    after = get(client, '/vouchers')
    assert after['total'] == 2
    assert all(t['source_count'] == 1 for t in after['items'])
    assert all(t['group_id'] == before[t['transaction_id']]['group_id'] for t in after['items'])
    assert after['summary']['debit_minor'] == after['summary']['credit_minor'] == '0'
    assert get_report(client)['cash_flow']['inflow_minor'] == '1000'
    assert get_report(client)['cash_flow']['outflow_minor'] == '1200'
    assert next(t for t in list_transactions(client) if t['account_id'] == two)['amount_minor'] == '1000'


def test_refund_relation_is_not_counted_as_duplicate_paired_source(tmp_path):
    client = client_at(tmp_path)
    aid = add_account(client)
    upload(client, aid, [['purchase', '2027-01-03', '合成原消费', '-10.00', 'expense', '5001'],
                        ['refund', '2027-01-04', '合成真实退款', '5.00', 'refund']])
    rows = list_transactions(client)
    parent = next(t for t in rows if t['kind'] == 'expense')
    child = next(t for t in rows if t['kind'] == 'refund')
    assert edit(client, child, {'refund_of': parent['id']}).status_code == 200
    response = get(client, '/vouchers')
    assert response['total'] == 2
    assert response['summary']['paired_source_count'] == 0
    assert len({t['group_id'] for t in response['items']}) == 2
    assert all(t['status'] == 'posted' for t in response['items'])
