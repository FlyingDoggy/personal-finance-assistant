"""Full-ledger searches include evidence off the first page; synthetic records only."""
import json

from app.storage import advance, encode, save_transaction
from test_historical import account, book, historical, read, upload


def test_search_matches_original_evidence_beyond_page_and_after_edit(book):
    client, store = book
    aid = account(client)
    rows = [[f'row-{i}', '2026-01-02', f'普通商户{i}', '-10.00', 'expense', '5001'] for i in range(65)]
    rows[0][2] = '证据独有银杏商品'
    upload(client, aid, rows)
    match = read(client, '/transactions?q=证据独有')['items'][0]
    with store.write() as db:
        payload = json.loads(db.execute('SELECT payload FROM transactions WHERE id=?', (match['id'],)).fetchone()[0])
        payload['description'] = '用户修正后摘要'
        save_transaction(db, match['id'], payload, 'edit', '合成修订')
        advance(db)
    for query in ['银杏商品', '用户修正后摘要', 'row-0']:
        result = read(client, '/transactions?q=' + query + '&limit=1')
        assert result['total'] == 1
        assert result['items'][0]['id'] == match['id']
    # Original file name is searchable across all imported records.
    assert read(client, '/transactions?q=synthetic.csv&limit=2')['total'] == 65


def test_combined_filters_and_unscoped_undated_rows(book):
    client, store = book
    bank = account(client)
    card = account(client, '合成信用卡', 'credit')
    historical(store, bank, 'dining', family='cmb_debit', raw='-100', expense='100',
               category='5001', day='2026-01-04')
    historical(store, card, 'shopping', expense='500', category='5005', day='2026-02-10')
    historical(store, card, 'unknown', category='5016', day=None, scoped=False, review='待确认')
    result = read(client, f'/transactions?start_date=2026-01-01&end_date=2026-01-31&account_id={bank}&category_code=5001&kind=消费&scope=included')
    assert [t['id'] for t in result['items']] == ['dining']
    assert result['filter_options']['kinds'] == ['消费']
    assert read(client, '/transactions?scope=excluded')['items'][0]['id'] == 'unknown'
    assert read(client, '/transactions?review_only=true')['total'] == 1
    assert read(client, '/transactions?start_date=2026-01-01&scope=excluded')['total'] == 0
    assert read(client, '/transactions?ids=dining,unknown')['total'] == 2


def test_filter_validation_and_paging_revision(book):
    client, store = book
    aid = account(client)
    historical(store, aid, 'one', category='5001')
    first = read(client, '/transactions?limit=1')
    historical(store, aid, 'two', category='5001')
    assert client.get('/api/v1/transactions', params={'data_revision': first['data_revision'], 'offset': 1}).status_code == 409
    for query in ['scope=incorrect', 'start_date=oops', 'start_date=2026-02-01&end_date=2026-01-01', 'q=' + 'a' * 201]:
        assert client.get('/api/v1/transactions?' + query).status_code == 422


def test_refund_filter_follows_current_parent_category(book):
    client, store = book
    aid = account(client)
    upload(client, aid, [['purchase', '2026-01-01', '合成原单', '-100.00', 'expense', '5001']])
    parent = read(client, '/transactions')['items'][0]
    from uuid import uuid4
    payload = {'date': '2026-01-02', 'cash_date': '2026-01-02', 'description': '合成退款', 'account_id': aid,
               'currency': 'CNY', 'kind': 'refund', 'amount_minor': '1000', 'category_code': '5001',
               'refund_of': parent['id'], 'source_id': 'refund'}
    with store.write() as db:
        save_transaction(db, 'refund', payload, 'import')
        advance(db)
    edited = client.post('/api/v1/transactions/' + parent['id'] + '/edit', json={
        'request_id': str(uuid4()), 'expected_version': 1, 'payload': {'category_code': '5005'}})
    assert edited.status_code == 200, edited.text
    rows = read(client, '/transactions?category_code=5005')['items']
    assert {t['id'] for t in rows} == {parent['id'], 'refund'}
    assert read(client, '/transactions?category_code=5001')['total'] == 0
