"""A report's definition and coverage can change without a new money fact."""
from uuid import uuid4

from app import report_snapshots
from test_backend import client_at, add_account, upload


def capture(client):
    response = client.post('/api/v1/report-snapshots', json={'request_id': str(uuid4()), 'month': '2027-01'})
    assert response.status_code == 200, response.text
    return response.json()['snapshot']


def test_coverage_only_change_stales_snapshot_without_rewriting_report_or_fact_revision(tmp_path):
    client = client_at(tmp_path)
    aid = add_account(client)
    upload(client, aid, [['purchase', '2027-01-02', '合成支出', '-10.00', 'expense', '5001']])
    original = capture(client)
    assert original['stale'] is False
    before_revision = client.get('/api/v1/status').json()['data_revision']
    response = client.post('/api/v1/coverage', json={
        'request_id': str(uuid4()), 'account_id': aid, 'month': '2027-01', 'basis': 'consumption',
        'status': 'complete', 'evidence': '合成完整月账单', 'confirmed': True, 'expected_version': 0,
    })
    assert response.status_code == 200, response.text
    assert client.get('/api/v1/status').json()['data_revision'] == before_revision
    current = client.get('/api/v1/report-snapshots/' + original['id']).json()['snapshot']
    assert current['stale'] is True
    assert current['report'] == original['report']
    assert current['coverage_version'] == original['coverage_version'] == 0
    assert client.get('/api/v1/report-snapshots').json()['items'][0]['stale'] is True
    after = capture(client)
    assert after['stale'] is False and after['coverage_version'] == 1


def test_calculator_definition_change_stales_list_and_detail_with_same_data(tmp_path, monkeypatch):
    client = client_at(tmp_path)
    add_account(client)
    original = capture(client)
    before_revision = client.get('/api/v1/status').json()['data_revision']
    monkeypatch.setattr(report_snapshots, 'CALCULATOR_VERSION', 'synthetic-next-definition')
    current = client.get('/api/v1/report-snapshots/' + original['id']).json()['snapshot']
    assert current['stale'] is True
    assert current['report'] == original['report']
    assert current['calculator_version'] == original['calculator_version']
    assert client.get('/api/v1/status').json()['data_revision'] == before_revision
    assert client.get('/api/v1/report-snapshots').json()['items'][0]['stale'] is True
    refreshed = capture(client)
    assert refreshed['stale'] is False
    assert refreshed['calculator_version'] == 'synthetic-next-definition'
