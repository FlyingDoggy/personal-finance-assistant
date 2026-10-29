"""v0.5 money invariants; every ledger and source is synthetic and temporary."""
from copy import deepcopy

import pytest

from app import coverage
from app.domain import DomainError, explainable, ledger, movements, validate_transaction, validate_budget_binding, validate_reimbursement_dates
from app.reports import budget_projection, report
from app.storage import Store, accounts, advance, now, save_transaction, transactions


@pytest.fixture
def book(tmp_path):
    store = Store(tmp_path)
    with store.write() as db:
        coverage.initialize(db)
        for aid, kind in [('bank', 'cash'), ('card', 'credit')]:
            db.execute('INSERT INTO accounts VALUES(?,?,?,?,?,?,?,?,?)',
                       (aid, '合成' + aid, kind, 'CNY', None, None, '', 1, now()))
    return store


def fact(tid, amount='-10000', **changes):
    return {'id': tid, 'date': '2027-01-05', 'cash_date': '2027-01-05',
            'description': '合成记录', 'account_id': 'card', 'amount_minor': amount,
            'currency': 'CNY', 'kind': 'expense', 'category_code': '5016',
            'counterparty_account_id': None, 'counterparty_confirmed': False,
            'refund_of': None, 'budget_line_id': None, **changes}


def save(db, t):
    save_transaction(db, t['id'], {k: v for k, v in t.items() if k != 'id'}, 'import', '合成测试')
    advance(db)


def budget(payment=False):
    result = {'year': 2027, 'currency': 'CNY', 'target_minor': '0', 'funding_minor': ['100000'] * 12,
              'lines': [{'id': 'daily', 'name': '日常', 'group': '生活', 'allocation': 'daily',
                         'execution_basis': 'consumption', 'actual_basis': 'consumption', 'monthly_minor': ['100000'] * 12}], 'usage': []}
    if payment:
        result['lines'].append({'id': 'payment', 'name': '付款', 'group': '固定', 'allocation': 'fixed',
                                'execution_basis': 'scheduled_payment', 'actual_basis': 'payment', 'monthly_minor': ['10000'] * 12})
    return result


def declare(db, month, basis='consumption', aid=None):
    for account_id in [aid] if aid else accounts(db):
        old = coverage.get_declaration(db, account_id, month, basis)
        coverage.set_declaration(db, account_id, month, basis, status='complete', evidence='合成全月账单及零交易核对',
                                 confirmed=True, expected_version=old['version'])


@pytest.mark.parametrize('shared_id', [False, True])
def test_ac66_unconfirmed_both_ends_only_have_their_own_movement(book, shared_id):
    with book.write() as db:
        left = fact('bank-local', '-10000', kind='repayment', account_id='bank', counterparty_account_id='card', source_id='SAME' if shared_id else 'BANK-100')
        right = fact('card-local', '10000', kind='repayment', account_id='card', counterparty_account_id='bank', source_id='SAME' if shared_id else 'CARD-900')
        save(db, left); save(db, right)
        assert movements(left) == {'bank': -10000}
        assert movements(right) == {'card': 10000}
        assert not explainable(left) and not explainable(right)
        assert ledger(left, accounts(db), transactions(db)) == []
        result = report(db, '2027-01')
        assert result['income_expense']['expense_minor'] == '0'
        assert result['cash_flow']['outflow_minor'] == '10000'
        by_id = {a['id']: a for a in result['balance_sheet']['accounts']}
        assert by_id['bank']['known_change_minor'] == '-10000'
        assert by_id['card']['known_change_minor'] == '-10000'


@pytest.mark.parametrize('kind', ['repayment', 'transfer'])
def test_only_explicit_confirmed_relation_can_infer_counterpart(book, kind):
    with book.read() as db:
        acc = accounts(db)
        if kind == 'transfer': acc['card']['kind'] = 'cash'
        p = fact('one', '-10000', kind=kind, account_id='bank', counterparty_account_id='card')
        assert movements(p) == {'bank': -10000}
        p['counterparty_confirmed'] = True
        assert movements(p) == {'bank': -10000, 'card': 10000}
        assert sum(int(x['debit_minor'])-int(x['credit_minor']) for x in ledger(p, acc, {'one': p})) == 0


@pytest.mark.parametrize('kind', ['repayment', 'transfer'])
def test_new_confirmation_cannot_create_two_independent_double_ended_facts(book, kind):
    with book.read() as db:
        acc = accounts(db)
        if kind == 'transfer': acc['card']['kind'] = 'cash'
        p = fact('one', '-10000', kind=kind, account_id='bank', counterparty_account_id='card', counterparty_confirmed=True)
        payload = {key: value for key, value in p.items() if key != 'id'}
        with pytest.raises(DomainError) as caught:
            validate_transaction(payload, acc, {})
        assert caught.value.code == 'relation_confirmation_unsupported'
        with pytest.raises(DomainError):
            validate_transaction(payload, acc, {'one': {**p, 'counterparty_confirmed': False}}, 'one')
        # Previously confirmed native evidence remains supported.
        assert validate_transaction(payload, acc, {'one': p}, 'one')['counterparty_confirmed'] is True
        for changes in ({'amount_minor': '-12000'}, {'date': '2027-01-06'}, {'cash_date': '2027-01-06'}, {'counterparty_account_id': None}):
            with pytest.raises(DomainError) as caught:
                validate_transaction({**payload, **changes}, acc, {'one': p}, 'one')
            assert caught.value.code == 'relation_confirmation_unsupported'


def test_ac69_shared_service_rejects_new_repayment_payment_binding_but_keeps_existing(book):
    row = budget(True)['lines'][1]
    p = fact('repay', '-10000', account_id='bank', kind='repayment', counterparty_account_id='card', counterparty_confirmed=True, budget_line_id='payment')
    with pytest.raises(DomainError) as caught:
        validate_budget_binding(p, row)
    assert caught.value.code == 'repayment_budget_unsupported'
    validate_budget_binding(p, row, {**p, 'description': '旧描述'})
    with pytest.raises(DomainError):
        validate_budget_binding(p, row, {**p, 'budget_line_id': 'other'})
    validate_budget_binding({**p, 'kind': 'expense'}, row)


def test_legacy_payment_interpretation_is_preserved_and_disclosed(book):
    with book.write() as db:
        save(db, fact('purchase', budget_line_id='daily'))
        save(db, fact('legacy-payment', '-10000', account_id='bank', kind='repayment', counterparty_account_id='card', counterparty_confirmed=True, budget_line_id='payment'))
        declare(db, '2027-01'); declare(db, '2027-01', 'payment')
        result, quality = budget_projection(db, budget(True))
        assert result['months'][0]['usage_minor'] == '20000'
        assert quality['unsupported_payment_actuals'] is True
        assert quality['legacy_payment_transaction_ids'] == ['legacy-payment']
        assert quality['month_quality']['2027-01']['payment']['exact'] is False
        assert result['months'][0]['schedule_variance_minor'] is None


def test_ac67_known_unassigned_usage_and_complete_zero_are_month_specific(book):
    with book.write() as db:
        save(db, fact('purchase', budget_line_id='daily'))
        save(db, fact('unclassified', '-8000'))
        declare(db, '2027-01'); declare(db, '2027-03')
        original = budget()
        result, meta = budget_projection(db, original)
        jan, feb, mar = result['months'][:3]
        assert jan['usage_minor'] == jan['daily_usage_minor'] == '18000'
        assert jan['daily_remaining_minor'] == '82000'
        assert feb['usage_minor'] == '0' and feb['daily_remaining_minor'] is None
        assert feb['known_daily_remaining_minor'] == '100000'
        assert mar['usage_minor'] == '0' and mar['daily_remaining_minor'] == '100000'
        assert meta['actuals_connected'] is False
        assert meta['known_unallocated_usage_minor'] == '8000'
        assert original == budget()
        # Reclassification and assignment move the same amount, never add it.
        save(db, fact('unclassified', '-8000', category_code='5001', budget_line_id='daily'))
        reassigned, _ = budget_projection(db, original)
        assert reassigned['months'][0]['usage_minor'] == '18000'
        assert reassigned['months'][0]['daily_remaining_minor'] == '82000'
        assert reassigned['unallocated_usage'] == []


def test_coverage_is_evidenced_versioned_scoped_and_does_not_claim_balance_reconciliation(book):
    with book.write() as db:
        with pytest.raises(DomainError) as caught:
            coverage.set_declaration(db, 'bank', '2027-01', 'consumption', status='complete', evidence='', confirmed=True, expected_version=0)
        assert caught.value.code == 'coverage_evidence_required'
        declare(db, '2027-01'); declare(db, '2027-02'); declare(db, '2027-01', 'payment')
        assert coverage.month_quality(db, '2027-01', 'consumption')['complete']
        assert coverage.month_quality(db, '2027-01', 'consumption')['balance_reconciled'] is False
        # A January credit purchase paid in February invalidates the matching
        # occurrence declaration, not January payment or February occurrence.
        save(db, fact('late-arrival', cash_date='2027-02-01'))
        assert coverage.get_declaration(db, 'card', '2027-01', 'consumption')['stale']
        assert not coverage.get_declaration(db, 'bank', '2027-01', 'consumption')['stale']
        assert coverage.month_quality(db, '2027-02', 'consumption')['complete']
        assert coverage.month_quality(db, '2027-01', 'payment')['complete']
        declare(db, '2027-01', aid='card')
        assert coverage.month_quality(db, '2027-01', 'consumption')['complete']
        assert coverage.get_declaration(db, 'card', '2027-01', 'consumption')['version'] == 2
        assert db.execute('SELECT COUNT(*) FROM coverage_declarations WHERE account_id=? AND month=? AND basis=?', ('card', '2027-01', 'consumption')).fetchone()[0] == 2
        with pytest.raises(DomainError) as caught:
            coverage.set_declaration(db, 'card', '2027-01', 'consumption', status='partial', evidence='合成修订', confirmed=False, expected_version=1)
        assert caught.value.code == 'version_conflict'


def test_payment_coverage_cannot_unlock_consumption_remainder(book):
    with book.write() as db:
        declare(db, '2027-01', 'payment')
        result, meta = budget_projection(db, budget())
        assert meta['month_quality']['2027-01']['payment']['exact'] is True
        assert result['months'][0]['daily_remaining_minor'] is None


def test_unallocated_refund_releases_only_a_confirmed_original_and_never_twice(book):
    with book.write() as db:
        save(db, fact('purchase'))
        save(db, fact('refund', '3000', kind='refund', refund_of='purchase'))
        save(db, fact('unlinked-refund', '9000', kind='refund'))
        result, meta = budget_projection(db, budget())
        assert result['months'][0]['usage_minor'] == '7000'
        assert result['months'][0]['daily_usage_minor'] == '7000'
        assert meta['known_unallocated_usage_minor'] == '7000'
        assert meta['unknown_usage_minor'] == '9000'


def test_ac68_existing_source_date_is_preserved_but_report_recognizes_receipt(book):
    with book.write() as db:
        reimbursement = fact('reimb', '10000', kind='reimbursement', account_id='bank', category_code='4005', date='2027-01-31', cash_date='2027-02-01')
        save(db, reimbursement)
        save(db, fact('family', '2000', kind='family_support', account_id='bank', category_code='4005', date='2027-02-01', cash_date='2027-02-01'))
        before = deepcopy(transactions(db)['reimb'])
        january, february = report(db, '2027-01'), report(db, '2027-02')
        assert january['income_expense']['income_minor'] == '0'
        assert january['income_expense']['reimbursement_minor'] == '0'
        assert february['income_expense']['income_minor'] == '12000'
        assert february['income_expense']['reimbursement_minor'] == '10000'
        assert february['income_expense']['family_support_minor'] == '2000'
        assert transactions(db)['reimb'] == before
        assert february['reimbursement_date_adjustments'][0]['transaction_id'] == 'reimb'


def test_reimbursement_edit_cannot_silently_create_inconsistent_dates(book):
    with book.read() as db:
        current = fact('reimb', '10000', kind='reimbursement', account_id='bank', category_code='4005', date='2027-02-01', cash_date='2027-02-01')
        clean = {k: v for k, v in current.items() if k != 'id'}
        with pytest.raises(DomainError) as caught:
            validate_transaction({**clean, 'date': '2027-01-31'}, accounts(db), {'reimb': current}, 'reimb')
        assert caught.value.code == 'reimbursement_date_conflict'
        prior = {**current, 'date': '2027-01-31'}
        edited = validate_transaction({**clean, 'date': '2027-01-31', 'description': '补充说明'}, accounts(db), {'reimb': prior}, 'reimb')
        assert edited['date'] == '2027-01-31'
        assert edited['cash_date'] == '2027-02-01'
        repaired = validate_transaction(clean, accounts(db), {'reimb': prior}, 'reimb')
        assert repaired['date'] == repaired['cash_date']


def test_historical_reimbursement_validation_allows_existing_payload_but_not_event_conversion():
    p = {'kind': 'historical', 'date': '2027-01-31', 'cash_date': '2027-02-01', 'historical': {'event': '报销回款'}}
    validate_reimbursement_dates(p, deepcopy(p))
    with pytest.raises(DomainError):
        validate_reimbursement_dates(p, {**p, 'historical': {'event': '一次性其他收入'}})
