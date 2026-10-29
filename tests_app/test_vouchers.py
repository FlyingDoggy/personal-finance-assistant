"""Journal projection and frozen report acceptance, using synthetic data only."""
import json
from uuid import uuid4

import pytest

from app import vouchers
from app.storage import Store, advance, encode, save_transaction
from test_backend import client_at, add_account, upload, list_transactions, edit, get_report


def get(c, path):
    response = c.get('/api/v1' + path)
    assert response.status_code == 200, response.text
    return response.json()


def test_current_journal_projects_native_lines_and_edits_atomically(tmp_path):
    c = client_at(tmp_path)
    aid = add_account(c)
    upload(c, aid, [['purchase', '2027-01-02', '合成凭证消费', '-12.34', 'expense', '5001']])
    t = list_transactions(c)[0]
    listed = get(c, '/vouchers?month=2027-01')
    assert listed['total'] == 1
    assert listed['summary']['debit_minor'] == listed['summary']['credit_minor'] == '1234'
    assert listed['summary']['basis'] == 'source_interpretations'
    header = listed['items'][0]
    assert header['transaction_id'] == t['id']
    assert header['transaction_version'] == 1
    assert header['status'] == 'posted'
    assert header['source_count'] == 1
    assert header['data_revision'] == listed['data_revision']
    source_before = get(c, '/transactions/' + t['id'])['sources']
    assert edit(c, t, {'category_code': '5004', 'amount_minor': '-2345'}).status_code == 200
    detail = get(c, '/vouchers/' + t['id'])
    assert detail['voucher']['transaction_version'] == 2
    assert {line['code'] for line in detail['lines']} == {'1001', '5004'}
    assert detail['voucher']['debit_minor'] == '2345'
    assert get(c, '/transactions/' + t['id'])['sources'] == source_before
    assert get_report(c)['income_expense']['expense_minor'] == '2345'
    assert get(c, '/vouchers?category_code=1001')['total'] == 1
    assert get(c, '/vouchers?category_code=5001')['total'] == 0


def test_unknown_and_excluded_records_are_not_given_invented_entries(tmp_path):
    c = client_at(tmp_path)
    aid = add_account(c)
    upload(c, aid, [['unknown', '2027-01-02', '合成待核', '25.00', 'unknown']])
    result = get(c, '/vouchers?status=review')
    assert result['total'] == 1
    assert result['items'][0]['line_count'] == 0
    assert result['summary']['line_count'] == 0
    assert get_report(c)['cash_flow']['inflow_minor'] == '2500'
    t = list_transactions(c)[0]
    assert get(c, '/vouchers/' + t['id'])['lines'] == []


def test_legacy_paired_evidence_does_not_generate_second_journal(tmp_path):
    c = client_at(tmp_path)
    bank = add_account(c)
    card = add_account(c, 'credit')
    store = Store(tmp_path)
    with store.write() as db:
        for tid, aid, peer, family, lines in [
            ('bank-end', bank, 'card-end', 'cmb_debit', [
                {'code': '2001', 'name': '合成应付款', 'debit_minor': '10000', 'credit_minor': '0'},
                {'code': '1001', 'name': '合成银行', 'debit_minor': '0', 'credit_minor': '10000'}]),
            ('card-end', card, 'bank-end', 'citic_credit', []),
        ]:
            p = {'date': '2027-01-05', 'cash_date': '2027-01-05', 'description': '合成双源还款',
                 'account_id': aid, 'amount_minor': '-10000', 'currency': 'CNY', 'kind': 'historical',
                 'category_code': '2001', 'historical': {'event': '信用还款', 'family': family,
                    'raw_amount_minor': '-10000', 'amount_minor': '10000', 'income_minor': '0', 'expense_minor': '0',
                    'cash_minor': '-10000' if tid == 'bank-end' else '0', 'debt_delta_minor': '-10000' if tid == 'card-end' else None,
                    'cash_included': tid == 'bank-end', 'in_scope': True, 'review': '人工确认',
                    'related_id': peer, 'link_type': '还款双源', 'cash_type': '信用支付还款', 'ledger_lines': lines}}
            save_transaction(db, tid, p, 'migrate', '合成证据', 'migration')
        advance(db)
    result = get(c, '/vouchers')
    assert result['total'] == 2
    headers = {h['transaction_id']: h for h in result['items']}
    assert headers['card-end']['status'] == 'evidence_only'
    assert headers['card-end']['line_count'] == 0
    assert headers['card-end']['group_id'] == headers['bank-end']['group_id']
    assert result['summary']['debit_minor'] == result['summary']['credit_minor'] == '10000'
    assert get_report(c)['cash_flow']['outflow_minor'] == '10000'
    assert get_report(c)['income_expense']['expense_minor'] == '0'


def test_native_reciprocal_local_ids_keep_endpoint_vouchers_pending_relation(tmp_path):
    c = client_at(tmp_path)
    first = add_account(c)
    second = add_account(c)
    upload(c, first, [['strong-id', '2027-01-03', '合成转出', '-80.00', 'transfer', '', second]])
    upload(c, second, [['strong-id', '2027-01-03', '合成转入', '80.00', 'transfer', '', first]])
    result = get(c, '/vouchers')
    assert result['total'] == 2
    assert all(item['source_count'] == 1 and item['status'] == 'review' for item in result['items'])
    assert len({item['group_id'] for item in result['items']}) == 2
    assert result['summary']['debit_minor'] == result['summary']['credit_minor'] == '0'
    assert result['summary']['paired_source_count'] == 0
    assert get_report(c)['cash_flow']['inflow_minor'] == '8000'
    assert get_report(c)['cash_flow']['outflow_minor'] == '8000'
    assert get_report(c)['cash_flow']['net_minor'] == '0'
    observed = {item['id']: item['known_change_minor'] for item in get_report(c)['balance_sheet']['accounts']}
    assert observed == {first: '-8000', second: '8000'}


def test_refund_is_a_distinct_journal_not_duplicate_source_evidence(tmp_path):
    c = client_at(tmp_path)
    aid = add_account(c)
    upload(c, aid, [['purchase', '2027-01-02', '合成原消费', '-100.00', 'expense', '5001']])
    parent = list_transactions(c)[0]
    upload(c, aid, [['refund', '2027-01-03', '合成退款', '20.00', 'refund', '', '', parent['id']]])
    result = get(c, '/vouchers')
    assert result['total'] == 2
    assert result['summary']['paired_source_count'] == 0
    assert len({h['group_id'] for h in result['items']}) == 2
    # Journal side totals measure turnover, not net spending.
    assert result['summary']['debit_minor'] == '12000'
    assert get_report(c)['income_expense']['expense_minor'] == '8000'


def test_voucher_search_filters_all_source_evidence_and_revision(tmp_path):
    c = client_at(tmp_path)
    aid = add_account(c)
    upload(c, aid, [[f'r{i}', '2027-01-02', '特殊商品' if i == 0 else '合成普通商品', '-1.00', 'expense', '5001'] for i in range(55)])
    first = get(c, '/vouchers?limit=1')
    assert get(c, '/vouchers?q=特殊商品&limit=1')['total'] == 1
    assert get(c, '/vouchers?q=synthetic.csv&limit=1')['total'] == 55
    assert get(c, '/vouchers?month=2027-02')['total'] == 0
    t = list_transactions(c)[0]
    assert edit(c, t, {'description': '合成新描述'}).status_code == 200
    assert c.get('/api/v1/vouchers', params={'offset': 1, 'data_revision': first['data_revision']}).status_code == 409
    for query in ['status=bogus', 'month=bad', 'limit=0', 'offset=-1']:
        assert c.get('/api/v1/vouchers?' + query).status_code == 422


def test_saved_report_is_immutable_and_does_not_change_ledger_revision(tmp_path):
    c = client_at(tmp_path)
    aid = add_account(c)
    upload(c, aid, [['p', '2027-01-02', '合成消费', '-30.00', 'expense', '5001']])
    t = list_transactions(c)[0]
    before = get(c, '/status')['data_revision']
    payload = {'request_id': str(uuid4()), 'month': '2027-01'}
    response = c.post('/api/v1/report-snapshots', json=payload)
    assert response.status_code == 200, response.text
    snapshot = response.json()['snapshot']
    assert snapshot['report']['income_expense']['expense_minor'] == '3000'
    assert snapshot['stale'] is False
    assert snapshot['data_revision'] == before
    assert snapshot['transaction_versions'][t['id']] == 1
    assert get(c, '/status')['data_revision'] == before
    assert c.post('/api/v1/report-snapshots', json=payload).json() == response.json()
    assert get(c, '/report-snapshots')['total'] == 1
    assert c.post('/api/v1/report-snapshots', json={**payload, 'month': '2027-02'}).status_code == 409
    assert edit(c, t, {'amount_minor': '-4500'}).status_code == 200
    after = get(c, '/report-snapshots/' + snapshot['id'])['snapshot']
    assert after['stale'] is True
    assert after['report'] == snapshot['report']
    assert get_report(c)['income_expense']['expense_minor'] == '4500'
    c.close()
    restarted = client_at(tmp_path)
    assert get(restarted, '/report-snapshots/' + snapshot['id'])['snapshot']['report'] == snapshot['report']
    assert get(restarted, '/vouchers/' + t['id'])['voucher']['debit_minor'] == '4500'


def test_snapshot_marks_changed_rule_or_dimension_version_stale(tmp_path, monkeypatch):
    c = client_at(tmp_path)
    response = c.post('/api/v1/report-snapshots', json={'request_id': str(uuid4()), 'month': '2027-01'})
    assert response.status_code == 200, response.text
    sid = response.json()['snapshot']['id']
    monkeypatch.setattr(vouchers, 'RULE_VERSION', 'synthetic-next-rules')
    assert get(c, '/report-snapshots/' + sid)['snapshot']['stale'] is True


def test_projection_rebuild_is_deterministic_and_preserves_original_history(tmp_path):
    c = client_at(tmp_path)
    aid = add_account(c)
    upload(c, aid, [['p', '2027-01-02', '合成消费', '-50.00', 'expense', '5001']])
    before = get(c, '/vouchers')
    store = Store(tmp_path)
    with store.write() as db:
        source_before = [tuple(r) for r in db.execute('SELECT * FROM source_rows')]
        history_before = [tuple(r) for r in db.execute('SELECT * FROM transaction_versions')]
        vouchers.rebuild(db, force=True)
        assert source_before == [tuple(r) for r in db.execute('SELECT * FROM source_rows')]
        assert history_before == [tuple(r) for r in db.execute('SELECT * FROM transaction_versions')]
    assert get(c, '/vouchers') == before
