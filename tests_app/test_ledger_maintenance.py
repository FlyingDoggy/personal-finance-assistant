"""Ledger maintenance acceptance with independently calculable synthetic money."""
import hashlib
import json
from pathlib import Path
from uuid import uuid4

import pytest

from app.storage import Store, accounts, revision
from test_backend import client_at, add_account, upload, list_transactions, get_report, edit, budget_payload, save_budget
from test_historical import historical


def read(c, path):
    r = c.get('/api/v1' + path); assert r.status_code == 200, r.text
    return r.json()


def payload(aid, value='-10000', day='2027-01-12', kind='expense', **other):
    return {'account_id': aid, 'amount_minor': value, 'date': day, 'cash_date': day,
            'description': '合成手工记录', 'currency': 'CNY', 'kind': kind, 'category_code': '5001' if kind == 'expense' else None, **other}


def manual(c, p, **other):
    body = {'request_id': str(uuid4()), 'payload': p, 'reason': '合成纸质收据补录',
            'expected_data_revision': read(c, '/status')['data_revision'], **other}
    return c.post('/api/v1/transactions', json=body)


def observe(c, aid, value, day='2027-01-31', version=0, **other):
    return c.post('/api/v1/balance-observations', json={'request_id': str(uuid4()), 'account_id': aid,
                  'observed_date': day, 'balance_minor': value, 'evidence': '合成银行日终净余额截图', 'expected_version': version, **other})


def opening(c, aid, value, day='2027-01-01', **other):
    a = next(a for a in read(c, '/accounts')['items'] if a['id'] == aid)
    return c.post(f'/api/v1/accounts/{aid}/opening', json={'request_id': str(uuid4()), 'expected_version': a['version'],
                  'opening_balance_minor': value, 'opening_date': day, 'evidence': '合成日初余额依据', 'reason': '合成起点复核', **other})


def recon(c, aid, day='2027-01-31'):
    return read(c, f'/reconciliation?as_of={day}&account_id={aid}')['items'][0]


def declare(c, aid, basis='payment', month='2027-01'):
    old = read(c, f'/coverage?month={month}&account_id={aid}&basis={basis}')['items'][0]
    r = c.post('/api/v1/coverage', json={'request_id': str(uuid4()), 'account_id': aid, 'month': month,
               'basis': basis, 'status': 'complete', 'evidence': '合成资料完整（含无交易日）', 'confirmed': True, 'expected_version': old['version']})
    assert r.status_code == 200, r.text


def test_manual_preview_apply_retry_and_human_provenance(tmp_path):
    c = client_at(tmp_path); aid = add_account(c, opening='100000'); assert save_budget(c, budget_payload()).status_code == 200
    p = payload(aid, budget_line_id='food')
    before = read(c, '/status')['data_revision']
    preview = c.post('/api/v1/transactions/manual/preview', json={'payload': p, 'reason': '合成现金小票'})
    assert preview.status_code == 200, preview.text
    checked = preview.json()
    assert checked['effects']['expense_minor'] == '10000' and checked['effects']['cash_outflow_minor'] == '10000'
    assert read(c, '/transactions')['total'] == 0 and read(c, '/status')['data_revision'] == before
    body = {'request_id': str(uuid4()), 'payload': p, 'reason': '合成现金小票', 'expected_data_revision': checked['expected_data_revision']}
    r = c.post('/api/v1/transactions', json=body); assert r.status_code == 200, r.text
    assert c.post('/api/v1/transactions', json=body).json() == r.json()
    tid = r.json()['transaction']['id']; detail = read(c, '/transactions/' + tid)
    assert detail['sources'] == [] and len(detail['history']) == 1
    assert detail['history'][0]['action'] == 'manual_create' and detail['history'][0]['actor'] == 'human'
    report = get_report(c)
    assert report['income_expense']['expense_minor'] == '10000' and report['cash_flow']['net_minor'] == '-10000'
    assert next(u for u in read(c, '/budgets?year=2027')['budget']['usage'] if u['id'] == tid)['amount_minor'] == '10000'
    assert report['balance_sheet']['accounts'][0]['balance_minor'] == '90000'


def test_manual_duplicates_are_candidates_and_stale_preview_is_rejected(tmp_path):
    c = client_at(tmp_path); aid = add_account(c)
    p = payload(aid); assert manual(c, p).status_code == 200
    preview = c.post('/api/v1/transactions/manual/preview', json={'payload': p, 'reason': '合成另一份收据'}).json()
    assert preview['possible_duplicate_count'] == 1
    assert manual(c, p).json()['error']['code'] == 'duplicate_confirmation_required'
    assert manual(c, p, allow_possible_duplicate=True).status_code == 200
    stale = manual(c, payload(aid, '-20000'), expected_data_revision=preview['expected_data_revision'])
    assert stale.status_code == 409 and stale.json()['error']['code'] == 'preview_stale'
    assert read(c, '/transactions')['total'] == 2
    assert get_report(c)['income_expense']['expense_minor'] == '20000'


@pytest.mark.parametrize('patch', [{'kind': 'historical'}, {'amount_minor': 1.25}, {'amount_minor': '-0'}, {'source_id': 'pretend-bank'}, {'historical': {}}, {'amount_minor': '-100', 'income_minor': '999'}])
def test_manual_rejects_non_native_or_derived_fields(tmp_path, patch):
    c = client_at(tmp_path); aid = add_account(c)
    r = manual(c, {**payload(aid), **patch})
    assert r.status_code == 422, r.text
    assert read(c, '/transactions')['total'] == 0


def test_original_download_checks_auth_registration_size_hash_and_symlink(tmp_path):
    c = client_at(tmp_path); aid = add_account(c)
    batch = upload(c, aid, [['a','2027-01-01','合成原件','-12.34','expense','5001']], filename='合成账单.csv')['import']
    detail = read(c, '/transactions/' + list_transactions(c)[0]['id'])
    url = detail['sources'][0]['download_url']; response = c.get(url)
    assert response.status_code == 200
    assert hashlib.sha256(response.content).hexdigest() == batch['sha256']
    assert 'attachment;' in response.headers['content-disposition'] and "filename*=UTF-8''" in response.headers['content-disposition']
    assert response.headers['x-content-type-options'] == 'nosniff'
    c.cookies.clear(); assert c.get(url).status_code == 401
    c.get('/api/v1/session')
    assert c.get('/api/v1/artifacts/' + 'a' * 64 + '/download').status_code == 404
    file = tmp_path / 'originals' / batch['sha256']; original = file.read_bytes()
    file.chmod(0o600); file.write_bytes(b'X' * len(original))
    assert c.get(url).json()['error']['code'] == 'evidence_corrupt'
    file.unlink(); assert c.get(url).json()['error']['code'] == 'evidence_missing'
    target = tmp_path / 'outside'; target.write_bytes(original); file.symlink_to(target)
    assert c.get(url).json()['error']['code'] == 'evidence_corrupt'


def test_observations_are_immutable_versioned_and_do_not_write_financial_facts(tmp_path):
    c = client_at(tmp_path); aid = add_account(c, opening='100000')
    upload(c, aid, [['buy','2027-01-02','合成消费','-100','expense','5001']])
    before = read(c, '/status')['data_revision']; first = observe(c, aid, '90000'); assert first.status_code == 200
    receipt = first.json(); assert receipt['data_revision'] == before
    assert observe(c, aid, '80000').status_code == 409
    assert observe(c, aid, '80000', version=1).status_code == 200
    history = read(c, f'/balance-observations?account_id={aid}&observed_date=2027-01-31')['items']
    assert [o['balance_minor'] for o in history] == ['80000', '90000']
    assert read(c, '/status')['data_revision'] == before and read(c, '/transactions')['total'] == 1
    assert recon(c, aid)['difference_minor'] == '-10000'
    assert get_report(c)['income_expense']['expense_minor'] == '10000'


def test_zero_difference_requires_opening_coverage_and_explained_movements(tmp_path):
    c = client_at(tmp_path); aid = add_account(c, opening='100000')
    assert manual(c, payload(aid)).status_code == 200
    assert observe(c, aid, '90000').status_code == 200
    assert recon(c, aid)['status'] == 'amounts_agree_partial'
    declare(c, aid); assert recon(c, aid)['balance_reconciled']
    unknown = manual(c, payload(aid, '5000', kind='unknown')).json()['transaction']
    assert observe(c, aid, '95000', version=1).status_code == 200
    row = recon(c, aid)
    assert row['difference_minor'] == '0' and row['status'] == 'needs_review' and row['book_balance_minor'] is None
    assert not row['quality']['complete'] and row['quality']['declarations'][0]['stale']
    assert edit(c, unknown, {'kind': 'income', 'category_code': '4005'}).status_code == 200
    declare(c, aid)
    assert recon(c, aid)['status'] == 'reconciled'


def test_unknown_opening_has_interval_comparison_without_absolute_claim(tmp_path):
    c = client_at(tmp_path); aid = add_account(c)
    observe(c, aid, '100000', day='2027-01-10')
    assert manual(c, payload(aid, day='2027-01-11')).status_code == 200
    observe(c, aid, '90000', day='2027-01-20')
    row = recon(c, aid)
    assert row['status'] == 'opening_unknown' and row['difference_minor'] is None
    assert row['book_balance_minor'] is None and row['interval']['difference_minor'] == '0'
    assert not row['interval']['absolute_balance_reconciled']
    assert read(c, '/accounts')['items'][0]['opening_balance_minor'] is None


def test_latest_observation_compares_its_own_day_and_credit_net_debt_can_be_negative(tmp_path):
    c = client_at(tmp_path); aid = add_account(c, kind='credit', opening='0')
    buy = manual(c, payload(aid)).json()['transaction']
    refund = manual(c, payload(aid, '10000', day='2027-01-13', kind='refund', refund_of=buy['id']))
    assert refund.status_code == 200, refund.text
    upload(c, aid, [['repay','2027-01-14','合成单端还款','50','repayment']])
    observe(c, aid, '-5000', day='2027-01-14')
    manual(c, payload(aid, '-2000', day='2027-01-20'))
    row = recon(c, aid)
    assert row['comparison_date'] == '2027-01-14' and row['source_projection_minor'] == '-5000'
    assert row['difference_minor'] == '0' and row['observation']['definition'] == 'net_credit_debt'
    assert row['status'] == 'needs_review'


def test_opening_revision_has_history_and_changes_balances_not_income_or_spending(tmp_path):
    c = client_at(tmp_path); aid = add_account(c)
    manual(c, payload(aid)); before = get_report(c)['income_expense']; rev = read(c, '/status')['data_revision']
    r = opening(c, aid, '100000'); assert r.status_code == 200, r.text
    assert r.json()['account']['balance_minor'] == '90000' and r.json()['data_revision'] == rev + 1
    assert get_report(c)['income_expense'] == before
    assert opening(c, aid, '200000', expected_version=1).status_code == 409
    history = read(c, f'/accounts/{aid}/opening-history')['items']
    assert history[0]['before_payload']['opening_balance'] is None and history[0]['after_payload']['opening_balance'] == '100000'
    # Prior unexplained flows do not invalidate an evidenced later opening.
    manual(c, payload(aid, '5000', day='2026-12-31', kind='unknown'))
    assert read(c, '/accounts')['items'][0]['balance_minor'] == '90000'
    assert opening(c, aid, None, opening_date=None, evidence='').status_code == 200
    assert read(c, '/accounts')['items'][0]['balance_minor'] is None


def test_cross_model_refund_capacity_edit_group_and_restarted_undo(tmp_path):
    c = client_at(tmp_path); card = add_account(c, kind='credit', opening='0')
    assert save_budget(c, budget_payload()).status_code == 200
    store = Store(tmp_path)
    historical(store, card, 'legacy-buy', day='2027-01-02', raw='10000', expense='10000', debt='10000', category='5001')
    parent = read(c, '/transactions/legacy-buy')['transaction']
    assert edit(c, parent, {'budget_line_id': 'food'}).status_code == 200
    refund = manual(c, payload(card, '3000', day='2027-01-15', kind='refund', refund_of='legacy-buy')).json()['transaction']
    assert get_report(c)['income_expense']['expense_minor'] == '7000'
    assert sorted(u['amount_minor'] for u in read(c, '/budgets?year=2027')['budget']['usage']) == ['-3000','10000']
    assert manual(c, payload(card, '8000', day='2027-01-16', kind='refund', refund_of='legacy-buy')).status_code == 409
    parent = read(c, '/transactions/legacy-buy')['transaction']
    changed = edit(c, parent, {'category_code': '5004'}); assert changed.status_code == 200, changed.text
    assert len(changed.json()['affected_transaction_ids']) == 2
    # New observations, opening audit and mixed edit groups survive a full restore.
    assert observe(c,card,'7000').status_code==200
    assert opening(c,card,'0').status_code==200
    from app.maintenance import create_backup, restore_backup
    backup=create_backup(Store(tmp_path)); restored=tmp_path.parent/(tmp_path.name+'-restored')
    restore_backup(tmp_path/'backups'/(backup['id']+'.zip'),restored)
    c.close(); c = client_at(restored)
    assert read(c,f'/balance-observations?account_id={card}')['items'][0]['balance_minor']=='7000'
    assert read(c,f'/accounts/{card}/opening-history')['items'][0]['after_payload']['opening_balance']=='0'
    latest = read(c, '/transactions/' + refund['id'])['transaction']
    undone = c.post(f"/api/v1/transactions/{refund['id']}/undo", json={'request_id': str(uuid4()), 'expected_version': latest['version']})
    assert undone.status_code == 200, undone.text
    assert read(c, '/transactions/legacy-buy')['transaction']['category_code'] == '5001'
    assert read(c, '/transactions/' + refund['id'])['transaction']['category_code'] == '5001'
    assert get_report(c)['income_expense']['expense_minor'] == '7000'
    parent = read(c, '/transactions/legacy-buy')['transaction']
    assert edit(c, parent, {'business_amount_minor': '2000'}).status_code == 409
    assert edit(c, parent, {'in_scope': False}).status_code == 409


def test_cross_model_refund_rejects_dates_accounts_scope_and_subsequent_group_changes(tmp_path):
    c = client_at(tmp_path); card = add_account(c, kind='credit'); other = add_account(c, kind='credit')
    historical(Store(tmp_path), card, 'old', day='2027-01-02', raw='10000', expense='10000', debt='10000', category='5001')
    assert manual(c, payload(card,'3000',day='2027-01-01',kind='refund',refund_of='old')).status_code == 422
    assert manual(c, payload(other,'3000',kind='refund',refund_of='old')).status_code == 422
    child = manual(c,payload(card,'3000',kind='refund',refund_of='old')).json()['transaction']
    parent = read(c,'/transactions/old')['transaction']; changed = edit(c,parent,{'category_code':'5004'}).json()['transaction']
    child = read(c,'/transactions/'+child['id'])['transaction']
    assert edit(c,child,{'description':'合成后续人工说明'}).status_code == 200
    undo = c.post('/api/v1/transactions/old/undo',json={'request_id':str(uuid4()),'expected_version':changed['version']})
    assert undo.status_code == 409 and undo.json()['error']['code'] == 'linked_version_conflict'
    assert read(c,'/transactions/old')['transaction']['category_code'] == '5004'


def test_schema_four_backup_can_be_restored_and_upgraded_without_fact_changes(tmp_path):
    from app.maintenance import create_backup, restore_backup
    directory=tmp_path/'book'; c=client_at(directory); aid=add_account(c,opening='100000')
    manual(c,payload(aid)); store=Store(directory)
    with store.write() as db:
        db.execute("UPDATE metadata SET value=4 WHERE key='schema_version'")
        original=[tuple(r) for r in db.execute('SELECT * FROM transactions')]
    # Simulate the preceding schema with the newly added service tables absent.
    with store.connect() as db:
        for name in ('balance_observations','account_opening_events','transaction_edit_members'):
            db.execute('DROP TABLE '+name)
    backup=create_backup(store)
    path=directory/'backups'/(backup['id']+'.zip')
    dest=tmp_path/'restored'; restore_backup(path,dest)
    recovered=Store(dest)
    with recovered.read() as db:
        assert db.execute("SELECT value FROM metadata WHERE key='schema_version'").fetchone()[0]==5
        assert [tuple(r) for r in db.execute('SELECT * FROM transactions')]==original
        assert db.execute('SELECT COUNT(*) FROM balance_observations').fetchone()[0]==0
