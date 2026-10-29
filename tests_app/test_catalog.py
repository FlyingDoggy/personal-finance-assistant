"""Master data/migration acceptance uses only disposable synthetic ledgers."""
import hashlib
import json
from pathlib import Path
import sqlite3

import pytest

from app import catalog
from app.domain import DomainError
from app.storage import Store, SCHEMA_VERSION, advance, encode, save_transaction


def legacy_database(directory):
    directory.mkdir()
    path = directory / 'finance.sqlite3'
    db = sqlite3.connect(path)
    db.executescript('''
        CREATE TABLE metadata(key TEXT PRIMARY KEY,value INTEGER NOT NULL);
        INSERT INTO metadata VALUES('schema_version',1),('data_revision',7);
        CREATE TABLE accounts(id TEXT PRIMARY KEY,name TEXT NOT NULL,kind TEXT NOT NULL,currency TEXT NOT NULL,opening_balance INTEGER,opening_date TEXT,evidence TEXT,version INTEGER NOT NULL,created_at TEXT NOT NULL);
        CREATE TABLE transactions(id TEXT PRIMARY KEY,version INTEGER NOT NULL,payload TEXT NOT NULL,created_at TEXT NOT NULL);
        CREATE TABLE budget_versions(year INTEGER,version INTEGER,payload TEXT,created_at TEXT,data_revision INTEGER,PRIMARY KEY(year,version));
    ''')
    db.execute('INSERT INTO accounts VALUES(?,?,?,?,?,?,?,?,?)', ('cash', '合成银行', 'cash', 'CNY', None, None, '', 1, '2026-01-01'))
    p = {'date': '2026-01-12', 'cash_date': '2026-01-12', 'description': '不能由摘要猜商户',
         'account_id': 'cash', 'amount_minor': '-1000', 'currency': 'CNY', 'kind': 'historical', 'category_code': '5001',
         'historical': {'event': '消费', 'income_minor': '0', 'expense_minor': '1000', 'cash_minor': '-1000',
                        'debt_delta_minor': None, 'family': 'cmb_debit', 'in_scope': True, 'cash_included': True,
                        'review': '人工确认', 'behavior': '日常消费', 'reimb': '未确认', 'cash_type': '消费现金收付',
                        'ledger_lines': [{'code': '5001', 'name': '餐饮', 'debit_minor': '1000', 'credit_minor': '0'},
                                         {'code': '1001', 'name': '现金', 'debit_minor': '0', 'credit_minor': '1000'}]}}
    db.execute('INSERT INTO transactions VALUES(?,?,?,?)', ('purchase', 1, encode(p), '2026-01-12'))
    db.commit()
    db.close()
    return path, p


def budget_line(name='合成餐饮'):
    return {'id': 'dining', 'name': name, 'group': '餐饮', 'allocation': 'daily',
            'execution_basis': 'consumption', 'actual_basis': 'consumption', 'monthly_minor': ['10000'] * 12}


def test_schema_one_migration_is_backed_up_idempotent_and_preserves_facts(tmp_path):
    path, original = legacy_database(tmp_path / 'app')
    store = Store(path.parent)
    with store.read() as db:
        assert db.execute("SELECT value FROM metadata WHERE key='schema_version'").fetchone()[0] == SCHEMA_VERSION
        assert db.execute("SELECT value FROM metadata WHERE key='data_revision'").fetchone()[0] == 7
        assert json.loads(db.execute('SELECT payload FROM transactions').fetchone()[0]) == original
        assert len(catalog.chart_accounts(db)) == 37
        assert catalog.dimension_version(db) == 1
        summary = catalog.summary(db)
        assert summary['counts']['merchants'] == 0  # no merchant field, no guess
        assert summary['counts']['tags'] == 1
        assert summary['tags'][0]['name'] == '日常消费'
        assert summary['accounts'][0]['history_count'] == 1
    backup_files = list((path.parent / 'schema-backups').glob('*.sqlite3'))
    assert len(backup_files) == 1
    with sqlite3.connect(backup_files[0]) as db:
        assert db.execute("SELECT value FROM metadata WHERE key='schema_version'").fetchone()[0] == 1
        assert json.loads(db.execute('SELECT payload FROM transactions').fetchone()[0]) == original
    reopened = Store(path.parent)
    with reopened.read() as db:
        assert catalog.summary(db) == summary
    assert list((path.parent / 'schema-backups').glob('*.sqlite3')) == backup_files


def test_failed_migration_rolls_back_all_new_tables_and_can_retry(tmp_path, monkeypatch):
    path, original = legacy_database(tmp_path / 'app')
    real_sync = catalog.sync
    def fail(db):
        raise RuntimeError('synthetic migration failure')
    monkeypatch.setattr(catalog, 'sync', fail)
    with pytest.raises(RuntimeError):
        Store(path.parent)
    with sqlite3.connect(path) as db:
        assert db.execute("SELECT value FROM metadata WHERE key='schema_version'").fetchone()[0] == 1
        assert not db.execute("SELECT 1 FROM sqlite_master WHERE name='chart_accounts'").fetchone()
        assert json.loads(db.execute('SELECT payload FROM transactions').fetchone()[0]) == original
    monkeypatch.setattr(catalog, 'sync', real_sync)
    assert Store(path.parent)


def test_unknown_schema_is_rejected_before_any_write_or_backup(tmp_path):
    path, _ = legacy_database(tmp_path / 'app')
    with sqlite3.connect(path) as db:
        db.execute("UPDATE metadata SET value=99 WHERE key='schema_version'")
    before = hashlib.sha256(path.read_bytes()).hexdigest()
    with pytest.raises(DomainError, match='结构版本'):
        Store(path.parent)
    assert hashlib.sha256(path.read_bytes()).hexdigest() == before
    assert not (path.parent / 'schema-backups').exists()


def test_structured_merchant_evidence_and_tags_follow_transaction_versions(tmp_path):
    path, original = legacy_database(tmp_path / 'app')
    store = Store(path.parent)
    with store.write() as db:
        db.execute('INSERT INTO source_rows VALUES(?,?,?,?,?,?,?)', ('source-1', 'synthetic-import', 1, 'purchase',
                   encode({'description': 'do not guess', 'fields': {'merchant_name': '合成商家'}}), 'purchase', None))
        advance(db)
    with store.read() as db:
        first = catalog.summary(db)
        assert first['merchants'][0]['aliases'] == ['合成商家']
        assert first['merchants'][0]['transaction_count'] == 1
        merchant_id = first['merchants'][0]['id']
    revised = json.loads(encode(original))
    revised['historical']['behavior'] = '冲动消费'
    with store.write() as db:
        save_transaction(db, 'purchase', revised, 'edit', '合成明确标注')
        advance(db)
    with store.read() as db:
        assert db.execute('SELECT transaction_version FROM transaction_merchants').fetchone()[0] == 2
        assert db.execute('SELECT transaction_version FROM transaction_tags').fetchone()[0] == 2
        second = catalog.summary(db)
        assert second['merchants'][0]['id'] == merchant_id
        assert {t['name']: t['transaction_count'] for t in second['tags']} == {'冲动消费': 1, '日常消费': 0}
        assert json.loads(db.execute('SELECT raw FROM source_rows').fetchone()[0])['fields']['merchant_name'] == '合成商家'


def test_budget_projector_sees_rows_inserted_after_advance_and_keeps_versions(tmp_path):
    store = Store(tmp_path / 'app')
    with store.write() as db:
        revision = advance(db)
        db.execute('INSERT INTO budget_versions VALUES(?,?,?,?,?)', (2027, 1, encode({'lines': [budget_line()]}), '2026-01-01', revision))
    with store.write() as db:
        revision = advance(db)
        db.execute('INSERT INTO budget_versions VALUES(?,?,?,?,?)', (2027, 2, encode({'lines': [budget_line('合成餐饮修订')]}), '2026-01-02', revision))
    with store.read() as db:
        lines = catalog.list_budget_lines(db)
        assert [line['budget_version'] for line in lines] == [2, 1]
        assert {line['id'] for line in lines} == {'dining'}
        assert all(line['annual_minor'] == '120000' for line in lines)
        assert lines[1]['name'] == '合成餐饮'


def test_catalog_consistency_failure_rolls_back_whole_write(tmp_path):
    path, _ = legacy_database(tmp_path / 'app')
    store = Store(path.parent)
    with pytest.raises(DomainError, match='版本未递增'):
        with store.write() as db:
            db.execute("UPDATE accounts SET name='未版本化改名' WHERE id='cash'")
            advance(db)
    with store.read() as db:
        assert db.execute('SELECT name FROM accounts').fetchone()[0] == '合成银行'
        assert db.execute("SELECT value FROM metadata WHERE key='data_revision'").fetchone()[0] == 7


def test_exact_names_remain_distinct_and_nonpurchase_counterparty_is_not_a_merchant(tmp_path):
    path, original = legacy_database(tmp_path / 'app')
    store = Store(path.parent)
    with store.write() as db:
        for index, merchant in enumerate(('Shop', 'SHOP')):
            db.execute('INSERT INTO source_rows VALUES(?,?,?,?,?,?,?)', (str(index), 'synthetic', index, 'purchase',
                       encode({'fields': {'merchant_name': merchant}}), 'purchase', None))
        income = {**original, 'kind': 'income', 'amount_minor': '1000', 'category_code': '4001'}
        income.pop('historical')
        save_transaction(db, 'income', income, 'import')
        db.execute('INSERT INTO source_rows VALUES(?,?,?,?,?,?,?)', ('company', 'synthetic', 3, 'income',
                   encode({'fields': {'对方名称': '合成公司'}}), 'income', None))
        advance(db)
    with store.read() as db:
        assert {m['name'] for m in catalog.list_merchants(db)} == {'Shop', 'SHOP'}
