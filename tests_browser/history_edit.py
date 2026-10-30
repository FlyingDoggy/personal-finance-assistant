"""Historical edit UI acceptance with synthetic data in a disposable database."""
from pathlib import Path
import json
import socket
import subprocess
import sys
import tempfile
import time
import urllib.request
from uuid import uuid4

from fastapi.testclient import TestClient
from playwright.sync_api import sync_playwright, expect

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from app.main import create_app
from app.storage import encode, now, save_transaction, advance


def seed(directory):
    app = create_app(directory)
    with TestClient(app, base_url='http://127.0.0.1') as client:
        client.get('/api/v1/session')
        aid = client.post('/api/v1/accounts', json={'request_id': str(uuid4()), 'name': '合成历史信用卡', 'kind': 'credit'}).json()['account']['id']
        plan = {'year': 2027, 'currency': 'CNY', 'funding_minor': ['1000000'] * 12, 'target_minor': '1000000',
                'lines': [{'id': line, 'name': name, 'group': name, 'allocation': 'daily', 'execution_basis': 'consumption',
                           'actual_basis': 'consumption', 'monthly_minor': ['550000'] * 12}
                          for line, name in [('dining', '合成餐饮'), ('traffic', '合成交通')]], 'usage': []}
        response = client.post('/api/v1/budgets', json={'request_id': str(uuid4()), 'expected_version': 0, 'payload': plan})
        assert response.status_code == 200, response.text
        adopted=client.post("/api/v1/budget-versions/2027/1/adopt",json={"request_id":str(uuid4()),"expected_adopted_version":0})
        assert adopted.status_code==200,adopted.text
    store = app.state.store
    evidence = b'SYNTHETIC ORIGINAL 300.00'
    sha = store.archive(evidence, 'synthetic-evidence.csv')
    with store.write() as db:
        for i in range(61):
            tid = 'synthetic-target' if i == 0 else f'filler-{i}'
            amount = '30000' if i == 0 else '100'
            day = '2027-01-01' if i == 0 else '2027-01-20'
            p = {'date': day, 'cash_date': day, 'description': '历史明细验收商户' if i == 0 else f'合成普通商户{i}',
                 'account_id': aid, 'amount_minor': amount, 'currency': 'CNY', 'kind': 'historical',
                 'category_code': '5001', 'budget_line_id': 'dining',
                 'historical': {'event': '消费', 'amount_minor': amount, 'raw_amount_minor': amount,
                                'income_minor': '0', 'expense_minor': amount, 'cash_minor': '0',
                                'debt_delta_minor': amount, 'in_scope': True, 'cash_included': False,
                                'family': 'citic_credit', 'review': '人工确认', 'cash_type': '非现金来源',
                                'source_account_label': '合成信用来源', 'ledger_lines': [
                                    {'code': '5001', 'name': '餐饮', 'debit_minor': amount, 'credit_minor': '0'},
                                    {'code': '2001', 'name': '信用支付结算应付款', 'debit_minor': '0', 'credit_minor': amount},
                                ]}}
            save_transaction(db, tid, p, 'migrate', '合成迁移', 'migration')
        db.execute('INSERT INTO artifacts VALUES(?,?,?,?)', (sha, 'synthetic-evidence.csv', len(evidence), now()))
        db.execute('INSERT INTO imports VALUES(?,?,?,?,?,?,?,?,?,?,?,?)', ('source-import', sha, 'synthetic-evidence.csv', aid, 'synthetic', 'completed', 1, 1, 0, 0, '[]', now()))
        db.execute('INSERT INTO source_rows VALUES(?,?,?,?,?,?,?)', ('source-row', 'source-import', 1, 'original-id',
                   encode({'description': '仅原始证据含银杏鞋', 'amount': '300.00', 'source_locator': '原件第1行'}), 'synthetic-target', None))
        advance(db)


def run(url):
    output = ROOT / 'work' / 'acceptance-history-edit'
    output.mkdir(parents=True, exist_ok=True)
    with sync_playwright() as pw:
        browser = pw.chromium.launch()
        context = browser.new_context(viewport={'width': 1440, 'height': 1000})
        page = context.new_page()
        page.set_default_timeout(10000)
        errors = []
        page.on('pageerror', lambda error: errors.append(str(error)))
        page.goto(url, wait_until='networkidle')

        def api(path):
            response = context.request.get(url + '/api/v1' + path)
            assert response.ok, response.text()
            return response.json()

        page.get_by_text('全部交易与原始明细', exact=True).click()
        expect(page.get_by_role('heading', name='交易', exact=True)).to_be_visible()
        expect(page.get_by_label('交易期间')).to_have_value('all')
        page.get_by_label('搜索全部交易').fill('银杏鞋')
        expect(page.locator('.plain-transaction-row')).to_have_count(1)
        page.locator('.plain-transaction-row').click()
        expect(page.get_by_role('button', name='修改这笔', exact=True)).to_be_visible()
        before = api('/transactions/synthetic-target')
        plan_before = api('/budgets?year=2027')['budget']['lines']
        page.get_by_role('button', name='修改这笔', exact=True).click()
        page.get_by_label('业务金额（元）', exact=True).fill('450.50')
        page.get_by_label('统计科目', exact=True).select_option('5004')
        page.get_by_label('预算项目', exact=True).select_option('traffic')
        page.get_by_label('修改原因', exact=True).fill('合成验收：订单核实为交通费用')
        page.get_by_role('button', name='保存修改', exact=True).click()
        expect(page.get_by_text('已保存修改，相关报表已更新。', exact=True)).to_be_visible()
        after = api('/transactions/synthetic-target')
        assert after['transaction']['version'] == 2
        assert after['sources'] == before['sources']
        assert after['effects']['expense_minor'] == '45050'
        report = api('/reports?month=2027-01')
        assert report['income_expense']['expense_minor'] == '51050'
        assert next(line for line in report['income_expense']['expense_lines'] if line['code'] == '5004')['amount_minor'] == '45050'
        budget = api('/budgets?year=2027')['budget']
        assert budget['lines'] == plan_before
        assert next(u for u in budget['usage'] if u['id'] == 'synthetic-target')['line_id'] == 'traffic'
        page.screenshot(path=str(output / 'edited-detail.png'), animations='disabled')
        page.get_by_role('link', name='报表', exact=True).click()
        expect(page.get_by_role('heading', name='管理报表', exact=True)).to_be_visible()
        category_row = page.get_by_role('button', name='交通', exact=False)
        expect(category_row).to_contain_text('450.50')
        category_row.click()
        expect(page.get_by_role('heading', name='交易', exact=True)).to_be_visible()
        expect(page.locator('.plain-transaction-row')).to_have_count(1)
        page.locator('.plain-transaction-row').click()
        page.get_by_role('button', name='撤销上次修改', exact=True).click()
        page.get_by_role('button', name='确认撤销', exact=True).click()
        expect(page.get_by_text('已撤销上次修改，历史记录已保留。', exact=True)).to_be_visible()
        undone = api('/transactions/synthetic-target')
        assert undone['transaction']['version'] == 3
        assert undone['effects']['expense_minor'] == '30000'
        assert undone['sources'] == before['sources']
        assert [h['action'] for h in undone['history']] == ['migrate', 'edit', 'undo']
        assert api('/reports?month=2027-01')['income_expense']['expense_minor'] == '36000'
        assert next(u for u in api('/budgets?year=2027')['budget']['usage'] if u['id'] == 'synthetic-target')['line_id'] == 'dining'
        page.reload(wait_until='networkidle')
        page.get_by_label('搜索全部交易').fill('银杏鞋')
        expect(page.locator('.plain-transaction-row')).to_have_count(1)
        page.set_viewport_size({'width': 390, 'height': 844})
        page.locator('.plain-transaction-row').click()
        expect(page.get_by_role('dialog')).to_be_visible()
        page.get_by_role('button', name='修改这笔', exact=True).click()
        expect(page.get_by_label('业务金额（元）')).to_be_visible()
        assert page.evaluate('document.documentElement.scrollWidth <= innerWidth')
        page.screenshot(path=str(output / 'mobile-history-edit.png'), animations='disabled')
        assert not errors, errors
        browser.close()
    print('PASS: full-ledger raw evidence search, historical edit, immutable evidence, history/undo, report and budget recalculation, persisted versions, mobile editor')


if __name__ == '__main__':
    with tempfile.TemporaryDirectory(prefix='finance-history-ui-') as temp:
        directory = Path(temp) / 'data'
        seed(directory)
        with socket.socket() as sock:
            sock.bind(('127.0.0.1', 0))
            port = sock.getsockname()[1]
        url = f'http://127.0.0.1:{port}'
        log = open(Path(temp) / 'server.log', 'w+')
        proc = subprocess.Popen([sys.executable, str(ROOT / 'scripts/run_app.py'), '--port', str(port), '--data-dir', str(directory)], cwd=ROOT, stdout=log, stderr=log)
        try:
            for _ in range(100):
                try:
                    with urllib.request.urlopen(url + '/api/v1/session', timeout=.5):
                        break
                except OSError:
                    if proc.poll() is not None:
                        log.seek(0)
                        raise RuntimeError(log.read())
                    time.sleep(.1)
            else:
                raise RuntimeError('Isolated server did not start')
            run(url)
        finally:
            proc.terminate()
            try:
                proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait()
            log.close()
