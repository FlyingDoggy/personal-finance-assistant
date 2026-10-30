"""Independent API counterexamples; every database and amount is synthetic."""

import csv
import io
from uuid import uuid4

from fastapi.testclient import TestClient
import pytest

from app.main import create_app


@pytest.fixture
def client(tmp_path):
    with TestClient(create_app(tmp_path), base_url="http://127.0.0.1", raise_server_exceptions=False) as result:
        assert result.get("/api/v1/session").status_code == 200
        yield result


def add_account(client, name="合成账户", kind="cash", opening="100000"):
    payload = {"request_id": str(uuid4()), "name": name, "kind": kind, "currency": "CNY", "opening_balance_minor": opening}
    if opening is not None:
        payload.update(opening_date="2027-01-01", evidence="反例测试已知期初")
    response = client.post("/api/v1/accounts", json=payload)
    assert response.status_code == 200, response.text
    return response.json()["account"]["id"]


def import_rows(client, account_id, rows):
    stream = io.StringIO()
    fields = ["source_id", "date", "description", "amount", "kind", "category_code", "counterparty_account_id", "refund_of", "budget_line_id"]
    writer = csv.DictWriter(stream, fieldnames=fields)
    writer.writeheader()
    for index, row in enumerate(rows):
        writer.writerow({"source_id": str(uuid4()), "date": "2027-01-10", "description": "合成反例", **row})
    response = client.post("/api/v1/imports", data={"account_id": account_id, "request_id": str(uuid4())}, files={"file": ("synthetic-counterexample.csv", stream.getvalue().encode(), "text/csv")})
    assert response.status_code == 200, response.text
    return response.json()


def read_report(client, month="2027-01"):
    response = client.get("/api/v1/reports", params={"month": month})
    assert response.status_code == 200, response.text
    return response.json()


def only_transaction(client):
    result = client.get("/api/v1/transactions").json()["items"]
    assert len(result) == 1
    return result[0]


def edit(client, transaction, changes):
    return client.post(f"/api/v1/transactions/{transaction['id']}/edit", json={"request_id": str(uuid4()), "expected_version": transaction["version"], "payload": changes, "reason": "合成反例验证"})


def save_budget(client, basis="consumption", target="0"):
    payload = {
        "year": 2027, "currency": "CNY", "funding_minor": ["200000"] * 12,
        "target_minor": target,
        "lines": [{"id": "daily", "name": "合成生活", "group": "生活", "allocation": "daily", "execution_basis": basis, "actual_basis": "payment" if basis == "scheduled_payment" else "consumption", "monthly_minor": ["100000"] * 12}],
        "usage": [],
    }
    response = client.post("/api/v1/budgets", json={"request_id": str(uuid4()), "expected_version": 0, "payload": payload})
    assert response.status_code == 200, response.text
    adopted = client.post(f"/api/v1/budget-versions/{payload['year']}/{response.json()['version']}/adopt", json={"request_id":str(uuid4()), "expected_adopted_version":0})
    assert adopted.status_code == 200, adopted.text
    return client.get("/api/v1/budgets?year=2027").json()


def test_internal_transfer_local_ids_preserve_evidence_without_inventing_relation(client):
    source = add_account(client, "合成来源", opening="100000")
    target = add_account(client, "合成去向", opening="50000")
    import_rows(client, source, [{"source_id": "shared-transfer", "amount": "-100.00", "kind": "transfer", "counterparty_account_id": target}])
    assert read_report(client)["cash_flow"]["unexplained_outflow_minor"] == "10000"
    import_rows(client, target, [{"source_id": "shared-transfer", "amount": "100.00", "kind": "transfer", "counterparty_account_id": source}])
    result = read_report(client)
    assert result["transaction_count"] == 2
    assert result["income_expense"]["income_minor"] == result["income_expense"]["expense_minor"] == "0"
    assert result["cash_flow"]["inflow_minor"] == result["cash_flow"]["outflow_minor"] == "10000"
    assert result["cash_flow"]["net_minor"] == "0"
    assert result["balance_sheet"]["scope_net_minor"] is None
    balances = {item["id"]: item["observed_balance_minor"] for item in result["balance_sheet"]["accounts"]}
    assert balances == {source: "90000", target: "60000"}
    for transaction in client.get('/api/v1/transactions').json()['items']:
        detail = client.get(f"/api/v1/transactions/{transaction['id']}").json()
        assert len(detail["sources"]) == 1
        assert detail["ledger_lines"] == []


@pytest.mark.parametrize(('kind', 'opening', 'amount', 'observed', 'change'), [
    ('cash', '100000', '100.00', '110000', '10000'),
    ('credit', '0', '-100.00', '10000', '10000'),
])
def test_unknown_account_change_is_observed_but_never_book_income_or_known_net_worth(client, kind, opening, amount, observed, change):
    account_id = add_account(client, kind=kind, opening=opening)
    import_rows(client, account_id, [{"amount": amount, "kind": "unknown"}])
    result = read_report(client)
    account = result["balance_sheet"]["accounts"][0]
    assert result["income_expense"]["income_minor"] == "0"
    assert result["income_expense"]["expense_minor"] == "0"
    assert account["balance_minor"] is None
    assert account["observed_balance_minor"] == observed
    assert account["known_change_minor"] == change
    assert account["book_change_minor"] == "0"
    assert account["unexplained_change_minor"] == change
    assert result["balance_sheet"]["scope_net_minor"] is None
    if kind == "cash":
        assert result["cash_flow"]["opening_minor"] == "100000"
        assert result["cash_flow"]["closing_minor"] == "110000"


def test_unknown_opening_never_becomes_zero_based_balance(client):
    account_id = add_account(client, opening=None)
    import_rows(client, account_id, [{"amount": "-80.00", "kind": "expense", "category_code": "5016"}])
    result = read_report(client)
    assert result["income_expense"]["expense_minor"] == "8000"
    assert result["cash_flow"]["net_minor"] == "-8000"
    assert result["cash_flow"]["opening_minor"] is None
    assert result["cash_flow"]["closing_minor"] is None
    assert result["balance_sheet"]["scope_net_minor"] is None


def test_refund_follows_changed_original_category_without_changing_original_or_refund_amount(client):
    account_id = add_account(client)
    import_rows(client, account_id, [{"amount": "-100.00", "kind": "expense", "category_code": "5001"}])
    original = only_transaction(client)
    import_rows(client, account_id, [{"amount": "20.00", "kind": "refund", "date": "2027-01-11", "refund_of": original["id"]}])
    changed = edit(client, original, {"category_code": "5004"})
    assert changed.status_code == 200, changed.text
    result = read_report(client)
    expense = {item["code"]: item["amount_minor"] for item in result["income_expense"]["expense_lines"]}
    assert expense["5001"] == "0"
    assert expense["5004"] == "8000"
    entries = client.get("/api/v1/transactions").json()["items"]
    refund = next(item for item in entries if item["kind"] == "refund")
    assert refund["category_code"] == "5004"
    assert refund["amount_minor"] == "2000"
    detail = client.get(f"/api/v1/transactions/{refund['id']}").json()
    assert any(item["code"] == "5004" and item["credit_minor"] == "2000" for item in detail["ledger_lines"])
    original_after = next(item for item in entries if item["id"] == original["id"])
    assert original_after["amount_minor"] == "-10000"
    assert edit(client, original_after, {"amount_minor": "-1000"}).status_code == 409


def test_refund_in_later_month_releases_that_month_only(client):
    account_id = add_account(client)
    import_rows(client, account_id, [{"amount": "-100.00", "kind": "expense", "category_code": "5001"}])
    original = only_transaction(client)
    import_rows(client, account_id, [{"amount": "20.00", "kind": "refund", "date": "2027-02-11", "refund_of": original["id"]}])
    assert read_report(client)["income_expense"]["expense_minor"] == "10000"
    february = read_report(client, "2027-02")
    assert february["income_expense"]["expense_minor"] == "-2000"
    assert february["cash_flow"]["inflow_minor"] == "2000"


@pytest.mark.parametrize("malicious", [[], {}, ["cash"], {"name": "cash"}])
def test_non_scalar_account_kind_is_a_structured_rejection(client, malicious):
    response = client.post("/api/v1/accounts", json={"request_id": str(uuid4()), "name": "合成无效输入", "kind": malicious})
    assert response.status_code == 422, response.text
    assert "error" in response.json()
    assert client.get("/api/v1/accounts").json()["items"] == []


@pytest.mark.parametrize("malicious", [[], {}, ["expense"], {"name": "expense"}])
def test_non_scalar_transaction_kind_is_a_structured_rejection(client, malicious):
    account_id = add_account(client)
    import_rows(client, account_id, [{"amount": "-1.00", "kind": "expense", "category_code": "5001"}])
    original = only_transaction(client)
    response = edit(client, original, {"kind": malicious})
    assert response.status_code == 422, response.text
    assert "error" in response.json()
    assert only_transaction(client)["version"] == original["version"]


def test_malformed_json_and_forged_actor_are_structured_rejections(client):
    malformed = client.post("/api/v1/accounts", content=b'{"request_id":', headers={"Content-Type": "application/json"})
    assert malformed.status_code == 422, malformed.text
    assert "error" in malformed.json()
    forged = client.post("/api/v1/accounts", json={"request_id": str(uuid4()), "name": "合成", "kind": "cash", "actor": "human"})
    assert forged.status_code == 422, forged.text
    assert client.get("/api/v1/accounts").json()["items"] == []


@pytest.mark.parametrize("field,malicious", [
    ("category_code", []), ("category_code", False), ("refund_of", {}),
    ("budget_line_id", 0), ("counterparty_account_id", []),
])
def test_falsy_non_text_fields_cannot_silently_clear_existing_classification_or_links(client, field, malicious):
    account_id = add_account(client)
    import_rows(client, account_id, [{"amount": "-1.00", "kind": "expense", "category_code": "5001"}])
    original = only_transaction(client)
    response = edit(client, original, {field: malicious})
    assert response.status_code == 422, response.text
    assert "error" in response.json()
    after = only_transaction(client)
    assert after["category_code"] == "5001"
    assert after["version"] == original["version"]


@pytest.mark.parametrize("value", [1.23, True, "1.0", "01", "9223372036854775808", "-9223372036854775808"])
def test_account_money_never_coerces_or_overflows(client, value):
    response = client.post("/api/v1/accounts", json={"request_id": str(uuid4()), "name": "合成", "kind": "cash", "opening_balance_minor": value, "opening_date": "2027-01-01", "evidence": "合成"})
    assert response.status_code == 422, response.text
    assert "error" in response.json()


def test_aggregate_money_overflow_is_structured_and_not_sqlite_float(client):
    add_account(client, "大额合成一", opening="9223372036854775807")
    add_account(client, "大额合成二", opening="1")
    response = client.get("/api/v1/reports", params={"month": "2027-01"})
    assert response.status_code == 422, response.text
    assert response.json()["error"]["code"] == "money_overflow"


def test_no_source_transactions_and_unsupported_payment_lines_do_not_claim_connected_actuals(client):
    saved = save_budget(client)
    assert saved["actuals_connected"] is False
    assert client.get("/api/v1/budgets", params={"year": 2027}).json()["actuals_connected"] is False


def test_payment_budget_without_payment_evidence_is_not_connected_even_with_known_income(client):
    account_id = add_account(client)
    import_rows(client, account_id, [{"amount": "1000.00", "kind": "income"}])
    saved = save_budget(client, basis="scheduled_payment")
    assert saved["actuals_connected"] is False
    assert saved["unsupported_payment_actuals"] is False


def test_unallocated_consumption_is_visible_and_not_presented_as_known_zero_actuals(client):
    save_budget(client)
    account_id = add_account(client)
    import_rows(client, account_id, [{"amount": "-80.00", "kind": "expense", "category_code": "5016"}])
    pending = client.get("/api/v1/budgets", params={"year": 2027}).json()
    assert pending["actuals_connected"] is False
    assert pending["unallocated_usage_minor"] == "8000"
    transaction = only_transaction(client)
    assert pending["unallocated_transaction_ids"] == [transaction["id"]]
    response = edit(client, transaction, {"budget_line_id": "daily"})
    assert response.status_code == 200, response.text
    linked = client.get("/api/v1/budgets", params={"year": 2027}).json()
    assert linked["actuals_connected"] is False
    assert linked["month_quality"]["2027-01"]["consumption"]["complete"] is False
    assert linked["budget"]["months"][0]["usage_minor"] == "8000"
    assert linked["budget"]["months"][0]["daily_remaining_minor"] is None
    assert linked["budget"]["months"][0]["known_daily_remaining_minor"] == "92000"
    assert pending["budget"]["months"][0]["usage_minor"] == "8000"
    assert read_report(client)["income_expense"]["expense_minor"] == "8000"


def test_budget_rejects_user_supplied_actuals_even_with_valid_unique_fact_id(client):
    payload = {"year": 2027, "currency": "CNY", "funding_minor": ["0"] * 12, "target_minor": "0", "lines": [], "usage": [{"id": "forged", "line_id": "none", "date": "2027-01-01", "kind": "consumption", "amount_minor": "100"}]}
    response = client.post("/api/v1/budgets", json={"request_id": str(uuid4()), "expected_version": 0, "payload": payload})
    assert response.status_code == 422, response.text
    assert response.json()["error"]["code"] == "actuals_read_only"
    assert client.get("/api/v1/budgets", params={"year": 2027}).json()["version"] == 0


def test_unknown_cash_plus_unallocated_consumption_preserves_two_distinct_uncertainties(client):
    save_budget(client)
    account_id = add_account(client)
    import_rows(client, account_id, [{"amount": "-80.00", "kind": "expense", "category_code": "5016"}, {"amount": "-120.00", "kind": "unknown"}])
    report = read_report(client)
    assert report["income_expense"]["expense_minor"] == "8000"
    assert report["cash_flow"]["outflow_minor"] == "20000"
    projection = client.get("/api/v1/budgets", params={"year": 2027}).json()
    assert projection["actuals_connected"] is False
    assert projection["unallocated_usage_minor"] == "8000"
    assert projection["unknown_usage_minor"] == "12000"
