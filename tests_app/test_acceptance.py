"""Independent integrator checks. All identities and amounts are synthetic."""
import csv
import io
from uuid import uuid4

from fastapi.testclient import TestClient

from app.main import create_app


def session(path):
    client = TestClient(create_app(data_dir=path), base_url="http://127.0.0.1")
    assert client.get("/api/v1/session").status_code == 200
    return client


def account(c, name, kind="cash", opening="1000000"):
    r = c.post("/api/v1/accounts", json={"request_id": str(uuid4()), "name": name,
        "kind": kind, "currency": "CNY", "opening_balance_minor": opening,
        "opening_date": "2027-01-01", "evidence": "合成验收起点"})
    assert r.status_code == 200, r.text
    return r.json()["account"]["id"]


def upload(c, aid, rows, filename="synthetic.csv", request_id=None):
    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow(["source_id", "date", "description", "amount", "kind", "category_code", "counterparty_account_id", "refund_of", "budget_line_id"])
    writer.writerows([list(row) + [""] * (9-len(row)) for row in rows])
    r = c.post("/api/v1/imports", data={"account_id": aid, "request_id": request_id or str(uuid4())},
               files={"file": (filename, buf.getvalue().encode(), "text/csv")})
    assert r.status_code == 200, r.text
    return r.json()


def report(c):
    r = c.get("/api/v1/reports", params={"month": "2027-01"})
    assert r.status_code == 200, r.text
    return r.json()


def test_credit_purchase_and_repayment_have_one_expense_and_one_cash_payment(tmp_path):
    c = session(tmp_path)
    bank = account(c, "合成银行卡")
    card = account(c, "合成信用卡", "credit", "0")
    upload(c, card, [["card-buy", "2027-01-10", "合成餐饮", "-600.00", "expense", "5001"]])
    upload(c, bank, [["repay", "2027-01-20", "合成还款", "-600.00", "repayment", "", card]])
    result = report(c)
    assert result["income_expense"]["expense_minor"] == "60000"
    assert result["income_expense"]["income_minor"] == "0"
    assert result["cash_flow"]["outflow_minor"] == "60000"
    assert result["balance_sheet"]["scope_net_minor"] is None
    assert next(a for a in result["balance_sheet"]["accounts"] if a["id"] == card)["balance_minor"] == "60000"
    bank_view = next(a for a in result["balance_sheet"]["accounts"] if a["id"] == bank)
    assert bank_view["observed_balance_minor"] == "940000"
    assert bank_view["unexplained_change_minor"] == "-60000"


def test_unknown_cash_does_not_become_income_and_reimports_are_idempotent(tmp_path):
    c = session(tmp_path)
    aid = account(c, "合成现金")
    rows = [["work", "2027-01-05", "合成垫付", "-400.00", "expense", "5004"],
            ["reimb", "2027-01-07", "合成报销", "200.00", "reimbursement", ""],
            ["mystery", "2027-01-08", "不能凭符号当收入", "100.00", "unknown", ""]]
    upload(c, aid, rows)
    upload(c, aid, rows, filename="renamed.csv")
    result = report(c)
    assert result["transaction_count"] == 3
    assert result["income_expense"]["income_minor"] == "20000"
    assert result["income_expense"]["expense_minor"] == "40000"
    assert result["cash_flow"]["net_minor"] == "-10000"
    assert result["cash_flow"]["unexplained_inflow_minor"] == "10000"
    assert result["coverage"]["status"] == "partial"


def test_edit_undo_persist_and_stale_versions_cannot_overwrite(tmp_path):
    c = session(tmp_path)
    aid = account(c, "合成账户")
    upload(c, aid, [["edit", "2027-01-02", "待改类别", "-100.00", "expense", "5001"]])
    tx = c.get("/api/v1/transactions").json()["items"][0]
    body = {"request_id": str(uuid4()), "expected_version": tx["version"], "reason": "合成纠错",
            "payload": {"category_code": "5004"}}
    route = f"/api/v1/transactions/{tx['id']}"
    first = c.post(route+"/edit", json=body)
    assert first.status_code == 200, first.text
    assert c.post(route+"/edit", json=body).json() == first.json()
    body["request_id"] = str(uuid4())
    assert c.post(route+"/edit", json=body).status_code == 409
    c.close()
    c = session(tmp_path)
    detail = c.get(route).json()
    assert detail["transaction"]["category_code"] == "5004"
    assert len(detail["history"]) == 2
    assert detail["sources"][0]["raw"]["category_code"] == "5001"
    undone = c.post(route+"/undo", json={"request_id": str(uuid4()),
        "expected_version": detail["transaction"]["version"], "reason": "撤销合成纠错"})
    assert undone.status_code == 200, undone.text
    assert undone.json()["transaction"]["category_code"] == "5001"
    assert report(c)["income_expense"]["expense_minor"] == "10000"


def test_partial_refund_does_not_rewrite_original_and_cannot_exceed_it(tmp_path):
    c = session(tmp_path)
    aid = account(c, "合成账户")
    upload(c, aid, [["buy", "2027-01-02", "合成消费", "-100.00", "expense", "5001"]])
    tx = c.get("/api/v1/transactions").json()["items"][0]
    upload(c, aid, [["refund", "2027-01-03", "部分退款", "20.00", "refund", "", "", tx["id"]]])
    assert report(c)["income_expense"]["expense_minor"] == "8000"
    outcome = upload(c, aid, [["over-refund", "2027-01-04", "超额退款", "90.00", "refund", "", "", tx["id"]]])
    assert outcome["import"]["rejected_count"] == 1
    assert report(c)["income_expense"]["expense_minor"] == "8000"
    assert c.get(f"/api/v1/transactions/{tx['id']}").json()["transaction"]["amount_minor"] == "-10000"
