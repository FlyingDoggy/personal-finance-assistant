"""Monthly actual totals and exact drill-through, using synthetic data only."""
from pathlib import Path
import json, os, socket, subprocess, sys, tempfile, time, urllib.request
from playwright.sync_api import sync_playwright, expect

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / 'tests_app')]
from test_backend import client_at, add_account, upload, list_transactions
from test_historical import historical
from app.storage import Store

OUT = ROOT / 'work/home-overview-acceptance'
OUT.mkdir(exist_ok=True)
with tempfile.TemporaryDirectory(prefix='finance-home-overview-') as tmp:
    directory = Path(tmp) / 'data'
    c = client_at(directory)
    bank = add_account(c, name='合成现金账户'); card = add_account(c, kind='credit', name='合成信用账户')
    upload(c, card, [['buy', '2026-08-10', '合成信用消费', '-200', 'expense', '5001']])
    upload(c, bank, [['salary', '2026-08-24', '合成工资', '1000', 'income', '4001'],
                     ['reim', '2026-08-20', '合成报销', '50', 'reimbursement', '4005'],
                     ['gift', '2026-08-21', '合成家庭赞助', '20', 'family_support', '4005'],
                     ['buy', '2026-08-15', '合成现金消费', '-30', 'expense', '5004'],
                     ['repay', '2026-08-25', '合成还款', '-100', 'repayment', '', card],
                     ['unknown', '2026-08-26', '合成待核变动', '-15', 'unknown'],
                     ['next', '2026-09-24', '合成次月工资', '1', 'income', '4001']])
    purchase = next(t for t in list_transactions(c) if t['description'] == '合成现金消费')
    upload(c, bank, [['refund', '2026-08-27', '合成消费退款', '20', 'refund', '', '', purchase['id']]])
    # Recognition is receipt-based, even when the legacy description date is earlier.
    historical(Store(directory), bank, 'late-reimbursement', event='报销回款',
               raw='500', income='500', cash='500', category='4005', day='2026-08-31',
               family='cmb_debit', included=True)
    with Store(directory).write() as db:
        from app.storage import save_transaction, transactions, raw_payload, advance
        t = raw_payload(transactions(db)['late-reimbursement']); t['cash_date'] = '2026-09-01'
        save_transaction(db, 'late-reimbursement', t, 'migration', actor='migration'); advance(db)
    c.close()
    with socket.socket() as sock: sock.bind(('127.0.0.1', 0)); port = sock.getsockname()[1]
    url = f'http://127.0.0.1:{port}'
    log = open(Path(tmp) / 'server.log', 'w+')
    process = subprocess.Popen([sys.executable, str(ROOT / 'scripts/run_app.py'), '--port', str(port), '--data-dir', str(directory)], cwd=ROOT, stdout=log, stderr=log)
    try:
        for _ in range(100):
            try: urllib.request.urlopen(url + '/api/v1/session', timeout=.5); break
            except OSError: time.sleep(.1)
        with sync_playwright() as pw:
            browser = pw.chromium.launch(); ctx = browser.new_context(viewport={'width':1440,'height':1000}, locale='zh-CN')
            page = ctx.new_page(); errors = []
            page.on('pageerror', lambda e: errors.append(str(e)))
            page.goto(url + '/#home', wait_until='networkidle')
            page.get_by_label('选择月份', exact=True).fill('2026-08')
            overview = page.get_by_role('region', name='当月实际收支概况', exact=True)
            for label, value in [('本月收入','¥1,070.00'),('本月消费支出','¥210.00'),('本月收支结余','¥+860.00'),('本月现金净流入','¥+945.00')]:
                expect(overview.get_by_role('region', name=label, exact=True)).to_contain_text(value)
            expect(page.get_by_text('本期未设预算', exact=True)).to_be_visible()
            # The one-sided repayment also awaits a verified relationship.
            expect(overview).to_contain_text('未解释现金流入 ¥0.00、流出 ¥115.00')
            page.screenshot(path=str(OUT / 'desktop-home.png'), full_page=True, animations='disabled')
            # Expense drill-through contains both purchases and the refund, never repayment.
            overview.get_by_role('button', name='查看本月消费支出明细', exact=True).click()
            expect(page.get_by_role('heading', name='交易', exact=True)).to_be_visible()
            expect(page.get_by_text('3 笔交易', exact=False)).to_be_visible()
            expect(page.get_by_role('button', name='合成还款', exact=False)).not_to_be_visible()
            page.goto(url + '/#home', wait_until='networkidle')
            page.get_by_label('选择月份', exact=True).fill('2026-09')
            expect(overview.get_by_role('region', name='本月收入', exact=True)).to_contain_text('¥6.00')
            overview.get_by_role('button', name='查看本月收入明细', exact=True).click()
            expect(page.get_by_text('2 笔交易', exact=False)).to_be_visible()
            expect(page.get_by_role('button', name='合成报销回款', exact=False)).to_be_visible()
            page.goto(url + '/#home', wait_until='networkidle')
            page.get_by_label('选择月份', exact=True).fill('2026-07')
            expect(overview.get_by_role('region', name='本月收入', exact=True)).to_contain_text('¥0.00')
            expect(overview.get_by_text('零值不代表资料已齐', exact=False)).to_be_visible()
            page.set_viewport_size({'width':390,'height':844})
            page.get_by_label('选择月份', exact=True).fill('2026-08')
            expect(overview.get_by_role('region', name='本月消费支出', exact=True)).to_contain_text('¥210.00')
            assert page.evaluate('document.documentElement.scrollWidth <= innerWidth')
            page.screenshot(path=str(OUT / 'mobile-home.png'), full_page=True, animations='disabled')
            assert not errors, errors
            browser.close()
        print('PASS: monthly actuals without budget, refund/repayment separation, unknown cash, receipt-date drill-through, empty month, desktop/mobile')
    finally:
        process.terminate(); process.wait(timeout=10); log.close()
