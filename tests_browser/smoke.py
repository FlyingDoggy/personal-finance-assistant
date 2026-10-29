"""Production browser acceptance with synthetic data and an isolated temporary DB.

Run after `npm --prefix web run build` with `.venv/bin/python tests_browser/smoke.py`.
Screenshots go to work/acceptance; no real ledger is opened or modified.
"""
from pathlib import Path
import csv
import io
import json
import re
import socket
import subprocess
import sys
import tempfile
import time
import urllib.request
from uuid import uuid4

from playwright.sync_api import sync_playwright, expect

ROOT = Path(__file__).resolve().parents[1]
OUTPUT = ROOT / "work" / "acceptance"


def csv_file(rows):
    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow(["source_id", "date", "description", "amount", "kind",
                     "category_code", "counterparty_account_id", "budget_line_id"])
    writer.writerows(rows)
    return buf.getvalue().encode("utf-8")


def run(url):
    OUTPUT.mkdir(parents=True, exist_ok=True)
    with sync_playwright() as pw:
        browser = pw.chromium.launch()
        context = browser.new_context(viewport={"width": 1440, "height": 1000})
        context.set_default_timeout(8000)
        page = context.new_page()
        errors = []
        remote_requests = []
        page.on("pageerror", lambda error: errors.append(str(error)))
        page.on("console", lambda message: errors.append(message.text) if message.type == "error" else None)
        page.on("request", lambda req: remote_requests.append(req.url)
                if req.url.startswith("http") and not req.url.startswith(url + "/") else None)
        page.goto(url, wait_until="networkidle")
        expect(page.get_by_role("heading", name="还没有账户")).to_be_visible()
        page.get_by_label("选择月份").fill("2027-01")

        def api(path):
            response = context.request.get(url + "/api/v1" + path)
            assert response.ok, response.text()
            return response.json()

        def navigate(name):
            page.get_by_role("link", name=name, exact=True).click()
            heading = {"本月": "本月", "交易": "交易", "预算": "生活预算", "报表": "管理报表", "更多": "账户与资料"}[name]
            expect(page.get_by_role("heading", name=heading, exact=True)).to_be_visible()
            expect(page.locator("main").first).to_have_css("opacity", "1")

        def capture(target, name, full_page=True):
            expect(target.locator("main").first).to_have_css("opacity", "1")
            target.screenshot(path=str(OUTPUT / name), full_page=full_page, animations="disabled")

        def add_account(name, kind, opening):
            if name == "验收工资卡":
                page.get_by_role("button", name="添加账户", exact=True).click()
            else:
                navigate("更多")
                page.get_by_role("button", name="添加账户", exact=False).first.click()
            dialog = page.get_by_role("dialog")
            dialog.get_by_label("账户名称", exact=True).fill(name)
            dialog.get_by_label("账户类型", exact=True).select_option(kind)
            dialog.get_by_label("起始负债（元，可留空）" if kind == "credit" else "起始余额（元，可留空）", exact=True).fill(opening)
            dialog.get_by_label("余额对应日期", exact=True).fill("2027-01-01")
            dialog.get_by_label("余额来源依据", exact=True).fill("合成验收期初")
            dialog.get_by_role("button", name="添加账户", exact=True).click()
            expect(dialog).not_to_be_visible()

        add_account("验收工资卡", "cash", "10000")
        add_account("验收信用卡", "credit", "0")
        print("PASS: account forms", flush=True)
        accounts = {a["name"]: a["id"] for a in api("/accounts")["items"]}
        cash, credit = accounts["验收工资卡"], accounts["验收信用卡"]

        navigate("预算")
        page.get_by_label("选择月份").fill("2027-01")
        page.get_by_role("button", name="创建年度预算", exact=True).click()
        dialog = page.get_by_role("dialog")
        dialog.get_by_label("年度留存目标（元）", exact=True).fill("60000")
        dialog.get_by_label("每月资金来源快捷填写（元）", exact=False).fill("20000")
        dialog.get_by_role("button", name="应用全年", exact=True).click()
        dialog.get_by_role("button", name="添加项目", exact=True).click()
        dialog.get_by_label("项目名称", exact=True).fill("验收餐饮")
        dialog.get_by_label("生活用途", exact=True).select_option("餐饮")
        dialog.get_by_label("首月金额（元）", exact=False).fill("5500")
        dialog.get_by_role("button", name="应用全年", exact=True).last.click()
        dialog.get_by_role("button", name="检查并采用", exact=True).click()
        dialog.get_by_role("button", name="保存并采用预算", exact=True).click()
        expect(dialog).not_to_be_visible()
        budget = api("/budgets?year=2027")
        line = budget["budget"]["lines"][0]["id"]
        assert budget["budget"]["annual"]["planned_minor"] == "6600000"
        assert not budget["actuals_connected"]
        print("PASS: budget adoption", flush=True)

        def upload(account, name, rows):
            before_count = api('/transactions')['total']
            page.get_by_role("button", name=re.compile(r"^(导入流水|添加账单)$")).first.click()
            dialog = page.get_by_role("dialog")
            dialog.get_by_label("账单所属账户", exact=True).select_option(account)
            dialog.locator("input[type=file]").set_input_files({"name": name, "mimeType": "text/csv", "buffer": csv_file(rows)})
            dialog.get_by_role("button", name="上传并查看预览", exact=True).click()
            expect(dialog.get_by_role('heading', name=name, exact=True)).to_be_visible()
            assert api('/transactions')['total'] == before_count
            confirm = dialog.get_by_role('button', name='确认导入并整理', exact=True)
            if confirm.is_visible():
                confirm.click()
            expect(dialog.get_by_text('本阶段已结束', exact=False).first).to_be_visible()
            dialog.get_by_role("button", name="关闭，保留进度", exact=True).click()
            expect(dialog).not_to_be_visible()

        cash_rows = [
            ["salary", "2027-01-24", "验收工资", "30000.00", "income", "4001", "", ""],
            ["meal", "2027-01-12", "验收餐饮现金", "-120.00", "expense", "5001", "", line],
            ["unknown", "2027-01-13", "验收待确认", "-45.00", "unknown", "", "", ""],
            ["repay", "2027-01-21", "验收信用还款", "-600.00", "repayment", "", credit, ""],
            ["reimbursement", "2027-01-26", "验收报销", "1000.00", "reimbursement", "4005", "", ""],
        ]
        upload(credit, "synthetic-credit.csv", [
            ["credit-meal", "2027-01-10", "验收餐饮信用", "-600.00", "expense", "5001", "", line],
            ["card-local-repayment", "2027-01-21", "验收信用卡还款入账", "600.00", "repayment", "", cash, ""],
        ])
        upload(cash, "synthetic-cash.csv", cash_rows)
        upload(cash, "synthetic-cash.csv", cash_rows)
        assert api("/transactions?month=2027-01")["total"] == 7
        print("PASS: import and dedup", flush=True)
        assert not api("/budgets?year=2027")["actuals_connected"]

        navigate("交易")
        page.get_by_role("button", name="待确认", exact=True).click()
        page.get_by_role("button", name="验收待确认", exact=False).click()
        page.get_by_role("button", name="修改这笔", exact=True).click()
        page.get_by_label("业务性质", exact=True).select_option("expense")
        page.get_by_label("统计科目", exact=True).select_option("5001")
        page.get_by_label("预算项目", exact=True).select_option(line)
        page.get_by_label("修改原因", exact=True).fill("验收确认餐饮")
        page.get_by_role("button", name="保存修改", exact=True).click()
        expect(page.get_by_role("button", name="修改这笔", exact=True)).to_be_visible()
        tx = next(t for t in api("/transactions?month=2027-01")["items"] if t["description"] == "验收待确认")
        assert tx["kind"] == "expense"
        page.get_by_role("button", name="撤销上次修改", exact=True).click()
        page.get_by_role("button", name="确认撤销", exact=True).click()
        expect(page.get_by_role("button", name="修改这笔", exact=True)).to_be_visible()
        assert api("/transactions/" + tx["id"])["transaction"]["kind"] == "unknown"
        page.get_by_role("button", name="修改这笔", exact=True).click()
        page.get_by_label("业务性质", exact=True).select_option("expense")
        page.get_by_label("统计科目", exact=True).select_option("5001")
        page.get_by_label("预算项目", exact=True).select_option(line)
        page.get_by_label("修改原因", exact=True).fill("再次确认餐饮")
        page.get_by_role("button", name="保存修改", exact=True).click()
        expect(page.get_by_role("button", name="修改这笔", exact=True)).to_be_visible()
        detail = api("/transactions/" + tx["id"])
        assert detail["sources"][0]["raw"]["kind"] == "unknown"
        assert len(detail["history"]) == 4

        budget = api("/budgets?year=2027")
        assert budget["actuals_connected"] is False
        assert budget["budget"]["months"][0]["daily_remaining_minor"] is None
        assert budget["budget"]["months"][0]["known_daily_remaining_minor"] == "473500"
        # The complete synthetic month can be declared for consumption. The
        # independent repayment relationship stays unresolved for payment.
        before_revision = api('/status')['data_revision']
        for aid in (cash, credit):
            response = context.request.post(url + '/api/v1/coverage', data={
                'request_id': str(uuid4()), 'account_id': aid, 'month': '2027-01', 'basis': 'consumption',
                'status': 'complete', 'evidence': '合成验收完整一月发生资料', 'confirmed': True, 'expected_version': 0,
            })
            assert response.ok, response.text()
        budget = api('/budgets?year=2027')
        assert api('/status')['data_revision'] == before_revision
        assert budget['actuals_connected'] is False
        assert budget['budget']['months'][0]['daily_remaining_minor'] == '473500'
        assert budget['budget']['months'][1]['daily_remaining_minor'] is None
        assert budget['month_quality']['2027-01']['payment']['exact'] is False
        page.reload(wait_until='networkidle')
        reports = api("/reports?month=2027-01")
        assert reports["income_expense"]["income_minor"] == "3100000"
        assert reports["income_expense"]["expense_minor"] == "76500"
        assert reports["cash_flow"]["outflow_minor"] == "76500"
        assert reports["balance_sheet"]["assets_minor"] is None
        assert reports["balance_sheet"]["liabilities_minor"] is None
        observed = {item['id']: item['observed_balance_minor'] for item in reports['balance_sheet']['accounts']}
        assert observed == {cash: '4023500', credit: '0'}
        (OUTPUT / "synthetic-report.json").write_text(json.dumps(reports, ensure_ascii=False, indent=2))
        navigate("报表")
        capture(page, "desktop-reports.png")
        page.get_by_role("tab", name="现金流", exact=False).click()
        expect(page.get_by_role("heading", name="现金收付构成")).to_be_visible()
        expect(page.get_by_text("40,235.00", exact=False)).to_be_visible()
        page.get_by_role("tab", name="资产与负债", exact=False).click()
        expect(page.get_by_text("纳入账户资产", exact=True)).to_be_visible()
        capture(page, "desktop-balances.png")
        navigate("预算")
        page.get_by_role("button", name="全年", exact=True).click()
        expect(page.get_by_role("table")).to_be_visible()
        capture(page, "desktop-budget.png")
        navigate("本月")
        expect(page.get_by_text("4,735.00", exact=False)).to_be_visible()
        capture(page, "desktop-home.png")

        mobile = context.new_page()
        mobile.set_viewport_size({"width": 390, "height": 844})
        mobile.on("pageerror", lambda error: errors.append(str(error)))
        mobile.goto(url, wait_until="networkidle")
        mobile.get_by_label("选择月份").fill("2027-01")
        expect(mobile.get_by_text("4,735.00", exact=False)).to_be_visible()
        for name in ["本月", "交易", "预算", "报表", "更多"]:
            mobile.get_by_role("link", name=name, exact=True).click()
            heading = {"本月": "本月", "交易": "交易", "预算": "生活预算", "报表": "管理报表", "更多": "账户与资料"}[name]
            expect(mobile.get_by_role("heading", name=heading, exact=True)).to_be_visible()
            expect(mobile.locator("main").first).to_have_css("opacity", "1")
            mobile.wait_for_load_state("networkidle")
            assert mobile.evaluate("document.documentElement.scrollWidth <= window.innerWidth"), name
            capture(mobile, f"mobile-{name}.png", full_page=False)
        mobile.get_by_role("button", name="添加账户", exact=False).first.click()
        expect(mobile.get_by_role("dialog")).to_be_visible()
        mobile.keyboard.press("Escape")
        expect(mobile.get_by_role("dialog")).not_to_be_visible()
        mobile.get_by_role("link", name="交易", exact=True).click()
        mobile.get_by_role("button", name="验收餐饮信用", exact=False).click()
        expect(mobile.get_by_role("heading", name="金额影响", exact=True)).to_be_visible()
        capture(mobile, "mobile-detail.png", full_page=False)
        mobile.get_by_role("button", name="修改这笔", exact=True).click()
        expect(mobile.get_by_label("业务性质", exact=True)).to_be_visible()
        mobile.get_by_role("button", name="取消修改", exact=True).click()
        assert not errors, errors
        assert not remote_requests, remote_requests
        browser.close()
        print("PASS: accounts, budget adoption, CSV import/dedup, classify/edit/undo, immutable source, reports, budget projection, desktop and 390px layouts, upstream Sheet and transaction detail. No JS/console errors or external requests.")


if __name__ == "__main__":
    with tempfile.TemporaryDirectory(prefix="finance-browser-") as temp:
        with socket.socket() as sock:
            sock.bind(("127.0.0.1", 0))
            port = sock.getsockname()[1]
        url = f"http://127.0.0.1:{port}"
        log = open(Path(temp) / "server.log", "w+")
        proc = subprocess.Popen([sys.executable, str(ROOT / "scripts/run_app.py"), "--port", str(port), "--data-dir", str(Path(temp) / "data")], cwd=ROOT, stdout=log, stderr=log)
        try:
            for _ in range(100):
                try:
                    with urllib.request.urlopen(url + "/api/v1/session", timeout=.5):
                        break
                except (OSError, urllib.error.URLError):
                    if proc.poll() is not None:
                        log.seek(0)
                        raise RuntimeError(log.read())
                    time.sleep(.1)
            else:
                raise RuntimeError("Isolated application server did not start")
            run(url)
        finally:
            proc.terminate()
            try:
                proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait()
            log.close()
