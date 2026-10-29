"""Export the reviewed legacy ledger without reclassifying or changing it.

This is a read-only source adapter. It writes JSON evidence to a work directory;
it never opens the application's database or writes to the source SQLite file.
All monetary values are integer-fen strings. Historical amount_minor preserves
the original source sign and must NOT use the native account-movement semantics.
"""
from __future__ import annotations

import argparse
from collections import defaultdict
from datetime import date, timedelta
from decimal import Decimal
import hashlib
import json
from pathlib import Path
import sqlite3

ROOT = Path(__file__).resolve().parents[1]


def sha256(path: Path) -> str:
    value = hashlib.sha256()
    with path.open('rb') as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b''):
            value.update(chunk)
    return value.hexdigest()


def money_fields(row: dict) -> dict:
    """Keep scalars/unknowns intact while making all money fields JSON-safe."""
    return {key: str(value) if key.endswith('_minor') and value is not None else value
            for key, value in row.items()}


def stable_id(prefix: str, value: str) -> str:
    return prefix + hashlib.sha256(value.encode('utf-8')).hexdigest()[:24]


def account_name(row: dict) -> str:
    # Old Huabei labels encoded funding-card tails and installment counts.
    # They are evidence labels, not six different Huabei credit accounts.
    return '花呗' if row['family'] == 'alipay' else row['account']


def build_bundle(source: Path) -> dict:
    source = source.resolve(strict=True)
    before_hash = sha256(source)
    with sqlite3.connect(source.as_uri() + '?mode=ro', uri=True) as db:
        db.row_factory = sqlite3.Row
        if db.execute('PRAGMA integrity_check').fetchone()[0] != 'ok':
            raise ValueError('Legacy database integrity check failed')
        table_names = [r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name")]

        def rows(table):
            if table not in table_names:
                raise ValueError(f'Missing source table: {table}')
            return [dict(r) for r in db.execute('SELECT * FROM "' + table + '"')]

        processed = rows('processed_transactions')
        raw = {r['transaction_row_id']: r for r in rows('raw_transactions')}
        if len(raw) != len(processed) or set(raw) != {r['id'] for r in processed}:
            raise ValueError('Processed/source identity mismatch')
        account_map = {}
        for row in processed:
            name = account_name(row)
            item = account_map.setdefault(name, {
                'id': stable_id('legacy_account_', name), 'name': name,
                'kind': 'cash' if row['family'].endswith('_debit') else 'credit',
                'currency': 'CNY', 'opening_balance_minor': None, 'opening_date': None,
                'evidence': '旧账未确认起始余额；各日期外部观察值独立保存，不倒推期初。',
                'legacy_labels': set(),
            })
            item['legacy_labels'].add(row['account'])
        accounts = [dict(item, legacy_labels=sorted(item['legacy_labels']))
                    for _, item in sorted(account_map.items())]

        ledger_by_id = defaultdict(list)
        for row in rows('ledger_lines'):
            ledger_by_id[row['event_id']].append({
                'code': str(row['account_code']), 'name': row['account_label'],
                'account_label': row['account_label'], 'sequence': row['sequence'],
                'debit_minor': str(row['debit_minor']), 'credit_minor': str(row['credit_minor']),
                'basis': row['basis'],
            })
        transactions = []
        for row in processed:
            historical = money_fields(row)
            historical['in_scope'] = bool(row['in_scope'])
            historical['cash_included'] = bool(row['cash_included'])
            historical['source_account_label'] = row['account']
            transactions.append({
                'id': row['id'], 'date': row['date'], 'cash_date': row['date'],
                'description': row['description'],
                'account_id': account_map[account_name(row)]['id'],
                'amount_minor': str(row['raw_amount_minor']), 'currency': 'CNY',
                'kind': 'historical',
                'category_code': str(row['code']) if row['code'] is not None else None,
                'historical': historical,
                'ledger_lines': sorted(ledger_by_id[row['id']], key=lambda item: item['sequence']),
            })

        sources = []
        for row in raw.values():
            record = money_fields(row)
            record['amount'] = row['amount_raw']
            record['currency'] = row['currency_raw']
            record['fields'] = json.loads(row['raw_fields_json'])
            sources.append({
                'id': stable_id('legacy_source_', 'primary:' + row['transaction_row_id']),
                'transaction_id': row['transaction_row_id'], 'file_id': row['file_id'],
                'source_id': row['source_row_id'],
                'row_number': row['source_line_no'] or row['page_no'],
                'page_no': row['page_no'], 'source_locator': row['source_locator'],
                'raw': record, 'raw_text': row['raw_text'], 'evidence_kind': 'primary',
            })
        for row in rows('transaction_source_evidence'):
            if row['transaction_row_id'] not in raw:
                raise ValueError('Supplemental evidence has no canonical source transaction')
            record = money_fields(row)
            record['fields'] = json.loads(row['raw_fields_json'])
            # Alias fields explicitly refer to the linked canonical source;
            # the supplemental observation remains unchanged in fields/raw_text.
            record['amount'] = raw[row['transaction_row_id']]['amount_raw']
            record['currency'] = raw[row['transaction_row_id']]['currency_raw']
            record['amount_alias_basis'] = '关联主来源的原始金额；补充来源原文与字段另行完整保留'
            sources.append({
                'id': stable_id('legacy_source_', 'supplemental:' + row['evidence_id']),
                'transaction_id': row['transaction_row_id'], 'file_id': row['file_id'],
                'source_id': row['source_row_id'],
                'row_number': row['source_line_no'] or row['page_no'],
                'page_no': row['page_no'],
                'source_locator': f"page:{row['page_no']}:line:{row['source_line_no']}",
                'raw': record, 'raw_text': row['raw_text'], 'evidence_kind': row['evidence_kind'],
            })

        files = []
        for row in rows('source_files'):
            path = Path(row['archived_path'])
            if not path.is_file() or sha256(path) != row['sha256']:
                raise ValueError(f"Original file absent or changed: {row['file_id']}")
            if path.stat().st_size != row['byte_count']:
                raise ValueError(f"Original file size mismatch: {row['file_id']}")
            files.append({'file_id': row['file_id'], 'path': str(path), 'sha256': row['sha256'],
                          'filename': row['filename'], 'size': row['byte_count'],
                          'evidence_kind': row['evidence_kind'], 'source_family': row['source_family']})

        observations = []
        for index, row in enumerate(rows('report_balance_observations')):
            amount = Decimal(str(row['observed_amount'])) * 100 if row['observed_amount'] is not None else None
            if amount is not None and amount != amount.to_integral_value():
                raise ValueError('Balance observation has a fractional fen')
            observations.append({**row, 'id': f'legacy_observation_{index + 1}',
                'date': (date(1899, 12, 30) + timedelta(days=row['excel_date'])).isoformat() if row['excel_date'] is not None else None,
                'amount_minor': str(int(amount)) if amount is not None else None,
            })

        top_level_tables = {'processed_transactions', 'raw_transactions', 'transaction_source_evidence',
                            'source_files', 'ledger_lines', 'report_balance_observations'}
        additional = {table: [money_fields(row) for row in rows(table)]
                      for table in table_names if table not in top_level_tables}
        counts = {table: db.execute('SELECT COUNT(*) FROM "' + table + '"').fetchone()[0] for table in table_names}
    if before_hash != sha256(source):
        raise ValueError('Source changed while exporting; no bundle is written')
    return {'schema_version': 1,
            'source': {'path': str(source), 'sha256': before_hash, 'table_counts': counts},
            'accounts': accounts, 'transactions': transactions, 'sources': sources, 'files': files,
            'observations': observations, 'additional_evidence': additional,
            'policy': {
                'historical_read_only': True, 'amount_basis': 'original_source_sign_not_normalized_account_movement',
                'income_expense_basis': 'reviewed_processed_integer_fen',
                'cash_basis': 'bank_cash_minor_with_cash_included_flag',
                'scope_basis': 'preserve_in_scope_2026_and_undated_source_background',
            }}


def main():
    parser = argparse.ArgumentParser(description='只读导出已整理账本迁移包')
    parser.add_argument('--source', type=Path, default=ROOT / 'data/finance_processed.sqlite3')
    parser.add_argument('--output', type=Path, default=ROOT / 'work/migration-20261029/ledger_bundle.json')
    args = parser.parse_args()
    output = args.output.resolve()
    if output == args.source.resolve() or output.suffix.lower() != '.json':
        parser.error('Output must be a separate JSON evidence file')
    bundle = build_bundle(args.source)
    output.parent.mkdir(parents=True, exist_ok=True)
    temporary = output.with_suffix('.tmp.json')
    temporary.write_text(json.dumps(bundle, ensure_ascii=False, separators=(',', ':')))
    temporary.chmod(0o600)
    temporary.replace(output)
    print(json.dumps({'output': str(output), 'accounts': len(bundle['accounts']),
                      'transactions': len(bundle['transactions']), 'sources': len(bundle['sources']),
                      'files': len(bundle['files']), 'observations': len(bundle['observations'])}, ensure_ascii=False))


if __name__ == '__main__':
    main()
