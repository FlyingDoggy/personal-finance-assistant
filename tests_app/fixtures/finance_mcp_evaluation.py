"""Seed the fixed, synthetic MCP benchmark in a new directory only.

Gold labels stay in this developer fixture; they are never sent as MCP evidence.
No model is called and no precision/coverage score is claimed by seeding.
"""
import json
from pathlib import Path

from app.ai_work import prepare_work
from app.domain import validate_transaction
from app.storage import Store, accounts, advance, encode, now, save_transaction, transactions

FIXTURE = Path(__file__).with_name('finance_mcp_evaluation_cases.json')


def seed_synthetic_evaluation(directory):
    directory = Path(directory)
    if directory.exists() and any(directory.iterdir()):
        raise ValueError('Synthetic benchmark requires a new empty directory; never reuse a ledger.')
    store = Store(directory)
    cases = json.loads(FIXTURE.read_text())['cases']
    with store.write() as db:
        db.execute('INSERT INTO accounts VALUES(?,?,?,?,?,?,?,?,?)', ('eval-cash', '合成评测账户', 'cash', 'CNY', None, None, None, 1, now()))
        for number, case in enumerate(cases, 1):
            tid = 'eval-' + case['id']
            payload = {'date': '2027-01-02', 'cash_date': '2027-01-02', 'account_id': 'eval-cash', 'currency': 'CNY',
                       'amount_minor': case['amount_minor'], 'description': case['description'],
                       'kind': case['kind'], 'category_code': case['category_code']}
            if case['kind'] == 'refund':
                parent = validate_transaction({**payload, 'kind': 'expense', 'category_code': '5002'}, accounts(db), transactions(db))
                save_transaction(db, tid + '-parent', parent, 'import', actor='importer')
                payload.update(amount_minor=str(-int(case['amount_minor'])), refund_of=tid + '-parent', category_code=None)
            payload = validate_transaction(payload, accounts(db), transactions(db))
            save_transaction(db, tid, payload, 'import', actor='importer')
            if case.get('human_category'):
                payload = validate_transaction({**payload, 'category_code': case['human_category']}, accounts(db), transactions(db), tid)
                save_transaction(db, tid, payload, 'edit', '合成人工决定', actor='human')
            raw = {'description': case['description'], 'merchant': case['merchant'], 'product': case['product']}
            db.execute('INSERT INTO source_rows VALUES(?,?,?,?,?,?,?)', ('eval-source-' + case['id'], 'eval-import', number, case['id'], encode(raw), tid, None))
        advance(db)
        scope = prepare_work(db, {'request_id': 'eval-prepare', 'import_id': 'eval-import'})
    return store, scope
