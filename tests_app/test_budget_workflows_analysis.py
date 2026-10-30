"""Independent hand-calculable cases for draft/adopt, rules and same-window analysis."""
from copy import deepcopy
from uuid import uuid4

from test_backend import client_at, add_account, upload, budget_payload, save_budget, list_transactions


def post(c, path, body):
    r = c.post('/api/v1' + path, json={'request_id': str(uuid4()), **body})
    assert r.status_code == 200, r.text
    return r.json()


def draft(c, p, expected, base):
    return post(c, '/budgets', {'expected_version': expected, 'expected_adopted_version': base, 'payload': p})


def adopt(c, p, v, base):
    return post(c, f"/budget-versions/{p['year']}/{v}/adopt", {'expected_adopted_version': base})


def test_draft_never_changes_current_plan_links_catalog_or_snapshot(tmp_path):
    c = client_at(tmp_path); aid = add_account(c)
    p = budget_payload(); assert save_budget(c, p).status_code == 200
    upload(c, aid, [['meal', '2027-01-01', '合成午餐', '-30', 'expense', '5001', '', '', 'food']])
    original = c.get('/api/v1/budgets?year=2027').json()
    snapshot = post(c, '/report-snapshots', {'month': '2027-01'})['snapshot']
    categories = c.get('/api/v1/catalog').json()['budget_lines']
    p['lines'][0]['id'] = 'draft-only'; p['lines'][0]['monthly_minor'] = ['100'] * 12
    saved = draft(c, p, 1, 1)
    assert saved['status'] == 'draft' and saved['adopted_version'] == 1
    assert saved['draft']['impact']['orphaned_link_count'] == 1
    assert c.get('/api/v1/budgets?year=2027').json()['budget'] == original['budget']
    assert c.get('/api/v1/status').json()['data_revision'] == original['data_revision']
    assert c.get('/api/v1/catalog').json()['budget_lines'] == categories
    assert c.get('/api/v1/report-snapshots/' + snapshot['id']).json()['snapshot']['stale'] is False
    adopt(c, p, 2, 1)
    current = c.get('/api/v1/budgets?year=2027').json()
    assert current['version'] == 2 and current['budget']['lines'][0]['id'] == 'draft-only'
    assert current['budget']['months'][0]['usage_minor'] == '3000'
    assert list_transactions(c)[0]['budget_line_id'] == 'food'


def test_stale_adoption_rejected_and_completed_request_replays(tmp_path):
    c = client_at(tmp_path); p = budget_payload(); save_budget(c, p)
    draft(c, p, 1, 1); draft(c, p, 2, 1)
    body = {'request_id': str(uuid4()), 'expected_adopted_version': 1}
    r = c.post('/api/v1/budget-versions/2027/2/adopt', json=body)
    assert r.status_code == 200
    assert c.post('/api/v1/budget-versions/2027/2/adopt', json=body).json() == r.json()
    stale = c.post('/api/v1/budget-versions/2027/3/adopt', json={'request_id': str(uuid4()), 'expected_adopted_version': 2})
    assert stale.status_code == 409
    assert c.get('/api/v1/budgets?year=2027').json()['version'] == 2


def rule(c, code='5001', **kwargs):
    return post(c, '/classification-rules', {'expected_version': 0, 'definition': {
        'name': '合成午餐规则', 'match_field': 'description', 'terms': ['午餐'], 'category_code': code, **kwargs}})['rule']


def preview(c, r):
    response = c.post(f"/api/v1/classification-rules/{r['id']}/{r['version']}/preview")
    assert response.status_code == 200, response.text
    return response.json()['preview']


def enable(c, r):
    p = preview(c, r)
    return post(c, f"/classification-rules/{r['id']}/{r['version']}/enable", {
        'preview_id': p['id'], 'expected_rule_revision': p['dependencies']['rule_revision']})


def test_rule_draft_preview_enable_apply_and_new_import_are_distinct(tmp_path):
    c = client_at(tmp_path); aid = add_account(c)
    upload(c, aid, [['meal', '2027-01-01', '合成午餐', '-30', 'expense', '5016']])
    r = rule(c); p = preview(c, r)
    assert p['matching_count'] == 1 and list_transactions(c)[0]['category_code'] == '5016'
    bad = c.post('/api/v1/rule-previews/' + p['id'] + '/apply', json={'request_id': str(uuid4())})
    assert bad.status_code == 409
    enable(c, r)
    assert list_transactions(c)[0]['category_code'] == '5016'
    p = preview(c, r); body = {'request_id': str(uuid4())}
    response = c.post('/api/v1/rule-previews/' + p['id'] + '/apply', json=body)
    assert response.status_code == 200 and response.json()['applied_count'] == 1
    assert c.post('/api/v1/rule-previews/' + p['id'] + '/apply', json=body).json() == response.json()
    upload(c, aid, [['meal2', '2027-01-02', '合成午餐二', '-40', 'expense', '5016']])
    rows = list_transactions(c)
    assert {t['category_code'] for t in rows} == {'5001'}
    assert sum(int(t['amount_minor']) for t in rows) == -7000
    assert c.get('/api/v1/transactions/' + rows[0]['id']).json()['history'][-1]['actor'] == 'rule'


def test_conflicting_rules_and_human_overrides_are_preserved(tmp_path):
    c = client_at(tmp_path); aid = add_account(c)
    a = rule(c); enable(c, a)
    b = rule(c, '5004'); enable(c, b)
    upload(c, aid, [['meal', '2027-01-01', '合成午餐', '-30', 'expense', '5016']])
    t = list_transactions(c)[0]
    assert t['category_code'] == '5016'
    p = preview(c, b)
    assert p['matching_count'] == 0 and any('冲突' in s for s in p['skipped'])
    post(c, '/transactions/' + t['id'] + '/edit', {'expected_version': t['version'], 'payload': {'category_code': '5005'}, 'reason': '人工用途'})
    assert preview(c, a)['matching_count'] == 0


def test_rule_preview_rejects_changes_and_budget_drafts_do_not_stale_it(tmp_path):
    c = client_at(tmp_path); aid = add_account(c); p = budget_payload(); save_budget(c, p)
    upload(c, aid, [['meal', '2027-01-01', '合成午餐', '-30', 'expense', '5016']])
    r = rule(c); enable(c, r); pp = preview(c, r)
    draft(c, p, 1, 1)
    assert post(c, '/rule-previews/' + pp['id'] + '/apply', {})['applied_count'] == 1
    pp = preview(c, r)
    upload(c, aid, [['meal2', '2027-01-02', '新事实', '-30', 'expense', '5016']])
    assert c.post('/api/v1/rule-previews/' + pp['id'] + '/apply', json={'request_id': str(uuid4())}).status_code == 409


def analysis(c, **kwargs):
    r = c.get('/api/v1/analysis', params={'start_month': '2026-01', 'end_month': '2026-02', **kwargs})
    assert r.status_code == 200, r.text
    return r.json()


def test_same_window_adjustment_includes_late_reimbursement_and_never_family(tmp_path):
    c = client_at(tmp_path); aid = add_account(c)
    upload(c, aid, [['salary', '2026-01-24', '合成工资', '1000', 'income', '4001'],
                    ['hotel', '2026-01-03', '合成酒店', '-600', 'expense', '5003'],
                    ['reim', '2026-02-01', '原描述为1月31日，2月到账报销', '100', 'reimbursement', '4005']])
    r = analysis(c)
    assert r['totals']['income_minor'] == '110000' and r['totals']['expense_minor'] == '60000'
    assert r['totals']['adjusted_income_minor'] == '100000' and r['totals']['adjusted_expense_minor'] == '50000'
    assert r['averages']['adjusted_income_minor'] == '50000' and r['averages']['adjusted_expense_minor'] == '25000'
    assert r['months'][0]['adjustment_minor'] == '0' and r['months'][1]['adjustment_minor'] == '10000'
    assert r['annualized_adjusted_expense_minor'] is None
    smoothed = analysis(c, mode='budget_comparison', estimate=True)
    assert [m['adjustment_minor'] for m in smoothed['months']] == ['5000', '5000']
    assert smoothed['annualized_adjusted_expense_minor'] == '300000'
    assert smoothed['totals']['surplus_minor'] == '50000'
    upload(c, aid, [['family', '2026-02-01', '合成家庭赞助', '50', 'family_support', '4005']])
    r = analysis(c)
    assert r['totals']['reimbursement_minor'] == '10000' and r['totals']['family_support_minor'] == '5000'
    assert r['totals']['adjusted_income_minor'] == '105000'


def test_negative_adjustment_is_not_clamped_or_annualized(tmp_path):
    c = client_at(tmp_path); aid = add_account(c)
    upload(c, aid, [['e', '2026-01-01', '合成消费', '-20', 'expense', '5001'], ['r', '2026-02-01', '合成报销', '100', 'reimbursement', '4005']])
    r = analysis(c, estimate=True)
    assert r['totals']['adjusted_expense_minor'] == '-8000'
    assert r['annualized_adjusted_expense_minor'] is None
    assert any(w['code'] == 'negative_adjusted_expense' for w in r['warnings'])


def test_full_aggregation_snapshots_and_draft_only_changes(tmp_path):
    c = client_at(tmp_path); aid = add_account(c); p = budget_payload(); p['year'] = 2026; save_budget(c, p)
    upload(c, aid, [[str(i), '2026-01-01', '合成消费', '-1', 'expense', '5001'] for i in range(501)])
    r = analysis(c, minimum_minor='100')
    assert r['totals']['expense_minor'] == '50100' and len(r['expense_rows']) == 501
    assert sum(int(x['amount_minor']) for x in r['categories']) == 50100
    frozen = post(c, '/analysis-snapshots', {'parameters': r['parameters']})
    draft(c, p, 1, 1)
    current = c.get('/api/v1/analysis-snapshots/' + frozen['id']).json()
    assert current['stale'] is False and current['analysis'] == r
    adopt(c, p, 2, 1)
    stale = c.get('/api/v1/analysis-snapshots/' + frozen['id']).json()
    assert stale['stale'] is True and stale['analysis'] == r


def test_batch_classification_is_atomic_versioned_and_preserves_money(tmp_path):
    c = client_at(tmp_path); aid = add_account(c)
    upload(c, aid, [['a', '2026-01-01', '合成一', '-10', 'expense', '5016'], ['b', '2026-01-02', '合成二', '-20', 'expense', '5016']])
    rows = list_transactions(c); versions = {t['id']: t['version'] for t in rows}
    invalid = {**versions, rows[0]['id']: 99}
    bad = c.post('/api/v1/transactions/batch-classify', json={'request_id': str(uuid4()), 'expected_versions': invalid, 'patches': {'category_code': '5001'}, 'reason': '合成'})
    assert bad.status_code == 409 and list_transactions(c) == rows
    body = {'request_id': str(uuid4()), 'expected_versions': versions, 'patches': {'category_code': '5001'}, 'reason': '明确批量核对'}
    r = c.post('/api/v1/transactions/batch-classify', json=body)
    assert r.status_code == 200 and r.json()['updated_count'] == 2
    assert c.post('/api/v1/transactions/batch-classify', json=body).json() == r.json()
    assert sum(int(t['amount_minor']) for t in list_transactions(c)) == -3000
    assert all(t['category_code'] == '5001' and t['version'] == 2 for t in list_transactions(c))
    assert c.get('/api/v1/transactions/' + rows[0]['id']).json()['history'][-1]['actor'] == 'human'


def test_analysis_snapshot_coverage_definition_and_screen_revision_checks(tmp_path, monkeypatch):
    from app import analysis as service
    c = client_at(tmp_path); aid = add_account(c)
    upload(c, aid, [['a', '2026-01-01', '合成消费', '-10', 'expense', '5001']])
    r = analysis(c)
    snap = post(c, '/analysis-snapshots', {'parameters': r['parameters'], 'expected_dependencies': r['dependencies']})
    before = c.get('/api/v1/status').json()['data_revision']
    post(c, '/coverage', {'account_id': aid, 'month': '2026-01', 'basis': 'consumption', 'status': 'complete', 'evidence': '合成完整账单', 'confirmed': True, 'expected_version': 0})
    assert c.get('/api/v1/status').json()['data_revision'] == before
    assert c.get('/api/v1/analysis-snapshots/' + snap['id']).json()['stale'] is True
    bad = c.post('/api/v1/analysis-snapshots', json={'request_id': str(uuid4()), 'parameters': r['parameters'], 'expected_dependencies': r['dependencies']})
    assert bad.status_code == 409
    r = analysis(c); snap = post(c, '/analysis-snapshots', {'parameters': r['parameters']})
    monkeypatch.setattr(service, 'DEFINITION_VERSION', 'synthetic-next-definition')
    frozen = c.get('/api/v1/analysis-snapshots/' + snap['id']).json()
    assert frozen['stale'] is True and frozen['analysis'] == r


def test_large_gross_net_refunds_and_complete_zero_month_denominator(tmp_path):
    c = client_at(tmp_path); aid = add_account(c)
    upload(c, aid, [['a', '2026-01-01', '合成消费', '-100', 'expense', '5001']])
    tid = list_transactions(c)[0]['id']
    upload(c, aid, [['refund', '2026-02-01', '合成退款', '80', 'refund', '', '', tid]])
    gross = analysis(c, minimum_minor='5000')
    net = analysis(c, minimum_minor='5000', large_basis='net')
    assert len(gross['large_expenses']) == 1 and gross['large_expenses'][0]['net_in_window_minor'] == '2000'
    assert net['large_expenses'] == [] and len(net['refunds']) == 1
    for month in ('2026-01', '2026-02'):
        post(c, '/coverage', {'account_id': aid, 'month': month, 'basis': 'consumption', 'status': 'complete', 'evidence': '合成月资料完整', 'confirmed': True, 'expected_version': 0})
    r = analysis(c)
    assert r['quality']['complete'] is True and r['quality']['months'] == 2
    assert r['averages']['expense_minor'] == '1000' and r['annualized_adjusted_expense_minor'] == '12000'


def test_budget_basis_change_with_existing_usage_is_rejected(tmp_path):
    c = client_at(tmp_path); aid = add_account(c); p = budget_payload(); save_budget(c, p)
    upload(c, aid, [['expense', '2027-01-01', '合成消费', '-10', 'expense', '5001', '', '', 'food']])
    p['lines'][0].update(actual_basis='payment', execution_basis='scheduled_payment')
    draft(c, p, 1, 1)
    r = c.post('/api/v1/budget-versions/2027/2/adopt', json={'request_id': str(uuid4()), 'expected_adopted_version': 1})
    assert r.status_code == 409 and r.json()['error']['code'] == 'budget_basis_in_use'
    assert c.get('/api/v1/budgets?year=2027').json()['version'] == 1


def test_rule_conflict_cannot_be_overridden_by_an_ai_classification(tmp_path):
    import pytest
    from app.domain import DomainError
    from test_ai_work import prepare, claim_context, proposal, submit
    c = client_at(tmp_path); aid = add_account(c)
    a = rule(c); enable(c, a); b = rule(c, '5004'); enable(c, b)
    upload(c, aid, [['expense', '2027-01-01', '合成午餐', '-30', 'expense', '5016']])
    store = c.app.state.store; run = prepare(store)
    claim, context = claim_context(store, scope_id=run['scope_id'])
    assert context['rule_conflict'] is True and context['allowed_fields'] == []
    with pytest.raises(DomainError) as exc:
        submit(store, proposal(claim, context))
    assert exc.value.code == 'rule_conflict'
    assert list_transactions(c)[0]['category_code'] == '5016'


def test_manual_historical_batch_propagates_refund_and_is_undoable(tmp_path):
    from test_historical_edit_review import seed
    c = client_at(tmp_path); aid = add_account(c); store = c.app.state.store
    seed(store, aid, 'parent', expense='10000')
    seed(store, aid, 'child', event='消费退款或返利', value='-2000', expense='-2000', cash='2000', related='parent', link_type='退款对应原消费')
    t = next(t for t in list_transactions(c) if t['id'] == 'parent')
    result = post(c, '/transactions/batch-classify', {'expected_versions': {t['id']: t['version']}, 'patches': {'category_code': '5004'}, 'reason': '合成关联复核'})
    assert result['updated_count'] == 2
    rows = {t['id']: t for t in list_transactions(c)}
    assert rows['parent']['category_code'] == rows['child']['category_code'] == '5004'
    assert rows['parent']['historical']['expense_minor'] == '10000' and rows['child']['historical']['expense_minor'] == '-2000'
    post(c, '/transactions/parent/undo', {'expected_version': rows['parent']['version'], 'reason': '撤销合成批量修改'})
    assert {t['category_code'] for t in list_transactions(c)} == {'5001'}


def test_living_reimbursement_requires_source_inclusion_and_budget_scope(tmp_path):
    c = client_at(tmp_path); aid = add_account(c)
    upload(c, aid, [['income', '2026-01-24', '合成工资', '1000', 'income', '4001'], ['expense', '2026-01-03', '合成消费', '-600', 'expense', '5001'], ['reim', '2026-02-01', '合成报销', '100', 'reimbursement', '4005']])
    missing = analysis(c, living_scope='observed_income')
    assert missing['living_normalization']['status'] == 'unavailable'
    # Consumption normalization remains available even without a living plan.
    assert missing['totals']['adjusted_expense_minor'] == '50000'
    p = budget_payload(); p['year'] = 2026; save_budget(c, p)
    r = analysis(c, living_scope='observed_income')['living_normalization']
    assert r['gross_source_minor'] == '110000' and r['gross_usage_minor'] == '60000'
    assert r['applicable_reimbursement_minor'] == '10000'
    assert r['adjusted_source_minor'] == '100000' and r['adjusted_usage_minor'] == '50000'
    assert r['surplus_minor'] == '50000'
    net = analysis(c, living_scope='net_salary_plan')['living_normalization']
    assert net['status'] == 'unavailable' and 'adjusted_source_minor' not in net


def test_evidence_only_change_invalidates_rule_preview_and_analysis_snapshot(tmp_path):
    from app.storage import encode
    c = client_at(tmp_path); aid = add_account(c)
    upload(c, aid, [['expense', '2026-01-01', '合成午餐', '-30', 'expense', '5016']])
    r = rule(c); enable(c, r); pp = preview(c, r)
    a = analysis(c); frozen = post(c, '/analysis-snapshots', {'parameters': a['parameters']})
    before = c.get('/api/v1/status').json()['data_revision']
    store = c.app.state.store
    with store.write() as db:
        source = db.execute('SELECT import_id,transaction_id FROM source_rows LIMIT 1').fetchone()
        db.execute('INSERT INTO source_rows VALUES(?,?,?,?,?,?,?)', (str(uuid4()), source['import_id'], 3, 'synthetic-enrichment', encode({'merchant': '合成新商户', '商品名称': '合成商品证据'}), source['transaction_id'], None))
    assert c.get('/api/v1/status').json()['data_revision'] == before
    rejected = c.post('/api/v1/rule-previews/' + pp['id'] + '/apply', json={'request_id': str(uuid4())})
    assert rejected.status_code == 409 and rejected.json()['error']['code'] == 'preview_stale'
    assert c.get('/api/v1/analysis-snapshots/' + frozen['id']).json()['stale'] is True
    assert c.get('/api/v1/analysis-snapshots/' + frozen['id']).json()['analysis'] == a
    assert analysis(c)['merchants'][0]['name'] == '合成新商户'
