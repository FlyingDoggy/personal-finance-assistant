from test_historical import book, account, historical, read


def test_logical_export_contains_current_and_history_without_mutation(book):
    client, store = book
    aid = account(client)
    historical(store, aid, 'export-synthetic', family='cmb_debit', raw='-10000', expense='10000', cash='-10000', category='5001')
    before = read(client, '/status')['data_revision']
    response = client.get('/api/v1/data/export')
    assert response.status_code == 200
    assert 'attachment' in response.headers['content-disposition']
    assert response.headers['cache-control'] == 'no-store'
    body = response.json()
    assert body['data_revision'] == before
    assert body['includes_original_files'] is False
    assert body['tables']['transactions'][0]['id'] == 'export-synthetic'
    assert body['tables']['transaction_versions'][0]['action'] == 'migration'
    assert len(body['tables']['chart_accounts']) == 37
    assert read(client, '/status')['data_revision'] == before
