"""Portable recovery acceptance, isolated synthetic ledgers only."""
import hashlib
import io
import json
import sqlite3
import zipfile
from pathlib import Path

import pytest

from app.domain import DomainError
from app.maintenance import create_backup, restore_backup, list_backups
from app.storage import Store, advance, encode, now, save_transaction


def populated(directory):
    store = Store(directory)
    content = b'synthetic original receipt\n'
    sha = store.archive(content, 'synthetic.csv')
    with store.write() as db:
        db.execute('INSERT INTO accounts VALUES(?,?,?,?,?,?,?,?,?)', ('cash', '合成银行', 'cash', 'CNY', None, None, '', 1, now()))
        db.execute('INSERT INTO artifacts VALUES(?,?,?,?)', (sha, 'synthetic.csv', len(content), now()))
        save_transaction(db, 'buy', {'date': '2027-01-01', 'cash_date': '2027-01-01', 'description': '合成午餐',
                                    'account_id': 'cash', 'amount_minor': '-10000', 'currency': 'CNY', 'kind': 'expense',
                                    'category_code': '5016', 'counterparty_account_id': None, 'refund_of': None,
                                    'budget_line_id': None, 'counterparty_confirmed': False}, 'import', actor='importer')
        db.execute('INSERT INTO operations VALUES(?,?,?,?,?)', ('committed', 'synthetic', 'fingerprint', '{"ok":true}', now()))
        advance(db)
    (store.directory / 'settings.json').write_text('{"currency":"CNY"}')
    (store.directory / 'machine-secret').write_text('must-not-back-up')
    return store, sha


def package_path(store, result):
    return store.directory / 'backups' / f"{result['id']}.zip"


def test_full_backup_restore_matches_facts_originals_history_and_operations(tmp_path):
    store, sha = populated(tmp_path / 'source')
    result = create_backup(store)
    assert list_backups(store)[0]['id'] == result['id']
    package = package_path(store, result)
    with zipfile.ZipFile(package) as z:
        assert z.namelist() == ['finance.sqlite3', f'originals/{sha}', 'config/settings.json', 'manifest.json']
    restored = restore_backup(package, tmp_path / 'restored')
    assert restored['current_ledger_unchanged'] is True
    assert restored['counts']['transactions'] == 1
    assert (tmp_path / 'restored' / 'originals' / sha).read_bytes() == (store.evidence / sha).read_bytes()
    assert not (tmp_path / 'restored' / 'machine-secret').exists()
    assert (tmp_path / 'restored' / 'settings.json').read_text() == '{"currency":"CNY"}'
    recovered = Store(tmp_path / 'restored')
    with store.read() as source, recovered.read() as dest:
        for table in ('transactions', 'transaction_versions', 'operations', 'accounts', 'artifacts'):
            assert [tuple(r) for r in source.execute('SELECT * FROM ' + table)] == [tuple(r) for r in dest.execute('SELECT * FROM ' + table)]
        assert dest.execute("SELECT value FROM metadata WHERE key='data_revision'").fetchone()[0] == 1
    with pytest.raises(DomainError, match='新目录'):
        restore_backup(package, store.directory)


def test_corrupt_archive_or_traversal_rejected_without_changing_live_ledger(tmp_path):
    store, sha = populated(tmp_path / 'source')
    backup = create_backup(store)
    good = package_path(store, backup)
    for kind in ('corrupt', 'traversal', 'duplicate'):
        bad = tmp_path / f'{kind}.zip'
        with zipfile.ZipFile(good) as src, zipfile.ZipFile(bad, 'w') as out:
            for name in src.namelist():
                data = b'tampered' if kind == 'corrupt' and name == f'originals/{sha}' else src.read(name)
                out.writestr(name, data)
            if kind == 'traversal':
                out.writestr('../escape', 'not permitted')
            if kind == 'duplicate':
                out.writestr('manifest.json', src.read('manifest.json'))
        with pytest.raises(DomainError):
            restore_backup(bad, tmp_path / f'restore-{kind}')
        assert not (tmp_path / f'restore-{kind}').exists()
    assert not (tmp_path / 'escape').exists()
    with store.read() as db:
        assert db.execute('SELECT COUNT(*) FROM transactions').fetchone()[0] == 1


def test_missing_referenced_original_prevents_incomplete_backup(tmp_path):
    store, sha = populated(tmp_path / 'source')
    (store.evidence / sha).unlink()
    with pytest.raises(DomainError, match='原件缺失'):
        create_backup(store)
    assert list_backups(store) == []


def test_snapshot_remains_consistent_when_live_ledger_changes_during_copy(tmp_path, monkeypatch):
    from app import maintenance
    store, sha = populated(tmp_path / 'source')
    original_hash = maintenance._hash
    changed = False
    def concurrent_write(path):
        nonlocal changed
        if path == store.evidence / sha and not changed:
            changed = True
            with store.write() as db:
                db.execute('UPDATE transactions SET payload=? WHERE id=?', (encode({'date': '2027-02-01', 'cash_date': '2027-02-01', 'description': '合成待核', 'account_id': 'cash', 'amount_minor': '-30000', 'currency': 'CNY', 'kind': 'unknown', 'category_code': None}), 'buy'))
                advance(db)
        return original_hash(path)
    monkeypatch.setattr(maintenance, '_hash', concurrent_write)
    backup = create_backup(store)
    restored = restore_backup(package_path(store, backup), tmp_path / 'restored')
    assert restored['data_revision'] == 1
    with sqlite3.connect(tmp_path / 'restored' / 'finance.sqlite3') as db:
        assert json.loads(db.execute('SELECT payload FROM transactions').fetchone()[0])['amount_minor'] == '-10000'
    with store.read() as db:
        assert db.execute("SELECT value FROM metadata WHERE key='data_revision'").fetchone()[0] == 2


def test_restore_invalidates_execution_lease_but_keeps_committed_receipt(tmp_path):
    from app.ai_work import prepare_work, claim_work
    store, _ = populated(tmp_path / 'source')
    with store.write() as db:
        scope = prepare_work(db, {'request_id': 'prepare', 'transaction_ids': ['buy']})
        claim_work(db, {'request_id': 'claim', 'scope_id': scope['scope_id'], 'limit': 1})
        epoch = db.execute("SELECT value FROM ai_runtime WHERE key='epoch'").fetchone()[0]
    backup = create_backup(store)
    restore_backup(package_path(store, backup), tmp_path / 'restored')
    with sqlite3.connect(tmp_path / 'restored' / 'finance.sqlite3') as db:
        assert db.execute("SELECT value FROM ai_runtime WHERE key='epoch'").fetchone()[0] != epoch
        assert db.execute('SELECT status FROM work_items').fetchone()[0] == 'queued'
        assert db.execute("SELECT response FROM operations WHERE request_id='committed'").fetchone()[0] == '{"ok":true}'
        assert db.execute("SELECT response FROM operations WHERE request_id='claim'").fetchone()
