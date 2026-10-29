"""Independent negative cases for concurrent imports and bounded AI evidence."""
from concurrent.futures import ThreadPoolExecutor, TimeoutError
import threading

import pytest

from app import ai_work, imports_v2
from app.domain import DomainError
from app.storage import encode, transactions, uid
from test_ai_work import synthetic_store, add_expense, prepare, claim_context, proposal, submit
from test_imports_v2 import setup, preview, command


def test_same_import_request_concurrently_returns_one_receipt_without_extra_rows(tmp_path, monkeypatch):
    store = setup(tmp_path)
    batch = preview(store, [[str(index), '2027-01-02', '合成消费', '-1.00', 'expense'] for index in range(3)])
    body = command(batch, limit=1)
    entered, release = threading.Event(), threading.Event()
    count_lock = threading.Lock()
    call_count = 0
    original = imports_v2._apply_one

    def same_time(*args):
        nonlocal call_count
        with count_lock:
            call_count += 1
            first_call = call_count == 1
        if first_call:
            entered.set()
            assert release.wait(timeout=5)
        return original(*args)

    monkeypatch.setattr(imports_v2, '_apply_one', same_time)
    with ThreadPoolExecutor(max_workers=2) as workers:
        first = workers.submit(imports_v2.process_batch, store, batch['id'], body)
        assert entered.wait(timeout=5)
        second = workers.submit(imports_v2.process_batch, store, batch['id'], body)
        # A correct request lock holds the second call until the first returns;
        # an unguarded retry gets a chance to commit its row while first waits.
        try:
            second.result(timeout=.25)
        except TimeoutError:
            pass
        finally:
            release.set()
        futures = [first, second]
        outcomes = []
        for future in futures:
            try:
                outcomes.append(future.result(timeout=10))
            except Exception as error:
                outcomes.append(error)
    with store.read() as db:
        fact_count = len(transactions(db))
    assert all(isinstance(result, dict) for result in outcomes), [type(result).__name__ for result in outcomes]
    assert outcomes[0] == outcomes[1]
    assert fact_count == 1


def test_new_source_after_context_excerpt_limit_invalidates_old_ai_submission(tmp_path):
    store = synthetic_store(tmp_path)
    tid = add_expense(store)
    # Exactly fifty existing sources fit the evidence excerpt. The newly added
    # source sorts after them, so it must still invalidate the dependency token.
    with store.write() as db:
        for index in range(2, 51):
            db.execute('INSERT INTO source_rows VALUES(?,?,?,?,?,?,?)',
                       (f'a-source-{index:03d}', 'synthetic-import', index, uid(),
                        encode({'description': '合成午餐记录'}), tid, None))
    prepare(store)
    claim, context = claim_context(store)
    assert len([e for e in context['evidence'] if e['id'].startswith('source:')]) == 50
    with store.write() as db:
        db.execute('INSERT INTO source_rows VALUES(?,?,?,?,?,?,?)',
                   ('z-new-contradicting-source', 'synthetic-import', 51, uid(),
                    encode({'商品名称': '合成商品证据：卫生纸，并非餐饮'}), tid, None))
    with pytest.raises(DomainError) as caught:
        submit(store, proposal(claim, context))
    assert caught.value.code in {'lease_invalid', 'dependency_conflict'}
    with store.read() as db:
        assert transactions(db)[tid]['category_code'] == '5016'
