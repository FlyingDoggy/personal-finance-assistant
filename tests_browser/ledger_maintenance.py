"""Manual supplement, original downloads, mixed refunds and balance workflow."""
from pathlib import Path
import hashlib, json, socket, subprocess, sys, tempfile, time, urllib.request
from playwright.sync_api import sync_playwright, expect

ROOT=Path(__file__).resolve().parents[1]
sys.path[:0]=[str(ROOT),str(ROOT/'tests_app')]
from test_backend import client_at, add_account, upload, budget_payload, save_budget, edit
from test_historical import historical
from app.storage import Store

OUT=ROOT/'work/r04-maintenance-acceptance';OUT.mkdir(exist_ok=True)
with tempfile.TemporaryDirectory(prefix='finance-maintenance-') as tmp:
    directory=Path(tmp)/'book'; c=client_at(directory)
    bank=add_account(c,opening='100000',name='合成工资卡');card=add_account(c,kind='credit',opening='0',name='合成信用卡')
    assert save_budget(c,budget_payload()).status_code==200
    batch=upload(c,bank,[['imported','2027-01-02','合成已导入午餐','-100','expense','5001','','','food']],filename='合成原始流水.csv')['import']
    historical(Store(directory),card,'legacy-buy',day='2027-01-02',raw='10000',expense='10000',debt='10000',category='5001')
    parent=c.get('/api/v1/transactions/legacy-buy').json()['transaction'];assert edit(c,parent,{'budget_line_id':'food'}).status_code==200
    c.close()
    with socket.socket() as sock:sock.bind(('127.0.0.1',0));port=sock.getsockname()[1]
    url=f'http://127.0.0.1:{port}';log=open(Path(tmp)/'server.log','w+')
    process=subprocess.Popen([sys.executable,str(ROOT/'scripts/run_app.py'),'--port',str(port),'--data-dir',str(directory)],cwd=ROOT,stdout=log,stderr=log)
    try:
        for _ in range(100):
            try:urllib.request.urlopen(url+'/api/v1/session',timeout=.5);break
            except OSError:time.sleep(.1)
        with sync_playwright() as pw:
            browser=pw.chromium.launch();ctx=browser.new_context(viewport={'width':1440,'height':1080},locale='zh-CN',accept_downloads=True)
            page=ctx.new_page();page.set_default_timeout(12000);errors=[];page.on('pageerror',lambda e:errors.append(str(e)))
            def read(path):
                r=ctx.request.get(url+'/api/v1'+path);assert r.ok,r.text();return r.json()
            page.goto(url+'/#reconciliation',wait_until='networkidle')
            expect(page.get_by_role('heading',name='余额核对',exact=True)).to_be_visible()
            page.get_by_label('选择月份',exact=True).fill('2027-01')
            bank_region=page.get_by_role('region',name='合成工资卡余额核对',exact=True)
            bank_region.get_by_role('button',name='录入银行余额',exact=True).click()
            dialog=page.get_by_role('dialog')
            dialog.get_by_label('银行日终余额（元）',exact=True).fill('850')
            dialog.get_by_label('银行余额依据',exact=True).fill('合成银行App日终余额')
            dialog.get_by_role('button',name='保存银行余额观察',exact=True).click();expect(dialog).not_to_be_visible()
            expect(bank_region.get_by_text('余额有差异',exact=True)).to_be_visible()
            expect(bank_region).to_contain_text('¥-50.00')
            bank_region.get_by_role('button',name='补录遗漏交易',exact=True).click()
            dialog=page.get_by_role('dialog')
            expect(dialog.get_by_label('补录账户',exact=True)).to_have_value(bank)
            dialog.get_by_label('确认日期',exact=True).fill('2027-01-03')
            dialog.get_by_label('补录金额（元）',exact=True).fill('50')
            dialog.get_by_label('补录科目',exact=True).select_option('5001')
            dialog.get_by_label('补录预算项目',exact=True).select_option('food')
            dialog.get_by_label('交易描述',exact=True).fill('合成遗漏早餐')
            dialog.get_by_label('补录依据',exact=True).fill('合成现金小票，银行流水遗漏')
            dialog.get_by_role('button',name='检查补录',exact=True).click()
            expect(dialog.get_by_role('region',name='手工补录检查',exact=True)).to_contain_text('¥50.00')
            dialog.get_by_role('button',name='确认补录',exact=True).click();expect(dialog).not_to_be_visible()
            expect(bank_region.get_by_text('金额一致 · 资料待核',exact=True)).to_be_visible()
            page.get_by_text('核对资料覆盖',exact=True).click()
            page.get_by_label('核对账户',exact=True).select_option(bank)
            page.get_by_label('资料口径',exact=True).select_option('payment')
            page.get_by_label('本次覆盖状态',exact=True).select_option('complete')
            page.get_by_label('核对证据与缺口说明',exact=True).fill('合成完整资料：已包含所有日期及零交易日')
            page.get_by_role('checkbox',name='我已核对该账户、月份和口径的完整资料；没有交易时也有依据。',exact=True).check()
            page.get_by_role('button',name='保存覆盖声明',exact=True).click()
            expect(bank_region.get_by_text('该日余额核对一致',exact=True)).to_be_visible()
            bank_region.get_by_role('button',name='设置账户起点',exact=True).click()
            dialog=page.get_by_role('dialog');dialog.get_by_label('日初现金余额（元）',exact=True).fill('1100')
            dialog.get_by_label('日初余额依据',exact=True).fill('合成起点更正凭据')
            dialog.get_by_label('起点修改理由',exact=True).fill('合成起点复核')
            dialog.get_by_role('button',name='保存账户起点',exact=True).click();expect(dialog).not_to_be_visible()
            expect(bank_region.get_by_text('余额有差异',exact=True)).to_be_visible()
            assert read('/reports?month=2027-01')['income_expense']['expense_minor']=='25000'
            page.screenshot(path=str(OUT/'desktop-reconciliation.png'),full_page=True,animations='disabled')

            page.goto(url+'/#transactions',wait_until='networkidle')
            page.get_by_role('button',name='合成已导入午餐',exact=False).click()
            page.locator('details.source-detail > summary').filter(has_text='合成原始流水.csv').click()
            with page.expect_download() as download:
                page.get_by_role('link',name='下载原件 · 合成原始流水.csv',exact=True).click()
            downloaded=download.value
            assert downloaded.suggested_filename=='合成原始流水.csv'
            assert hashlib.sha256(Path(downloaded.path()).read_bytes()).hexdigest()==batch['sha256']
            # A new native refund may reference the migrated purchase, once only.
            page.get_by_role('button',name='手工补录',exact=True).click();dialog=page.get_by_role('dialog')
            dialog.get_by_label('补录账户',exact=True).select_option(card)
            dialog.get_by_label('补录业务性质',exact=True).select_option('refund')
            dialog.get_by_label('确认日期',exact=True).fill('2027-01-15')
            dialog.get_by_label('补录金额（元）',exact=True).fill('30')
            dialog.get_by_label('退款原消费编号',exact=True).fill('legacy-buy')
            dialog.get_by_label('交易描述',exact=True).fill('合成新退款')
            dialog.get_by_label('补录依据',exact=True).fill('合成退款回单，关联旧消费')
            dialog.get_by_role('button',name='检查补录',exact=True).click()
            expect(dialog.get_by_role('region',name='手工补录检查',exact=True)).to_contain_text('¥-30.00')
            dialog.get_by_role('button',name='确认补录',exact=True).click();expect(dialog).not_to_be_visible()
            expect(page.get_by_text('人工补录：初始业务值',exact=False)).to_be_visible()
            report=read('/reports?month=2027-01');assert report['income_expense']['expense_minor']=='22000'
            assert report['cash_flow']['outflow_minor']=='15000'
            usage=read('/budgets?year=2027')['budget']['usage'];assert sum(int(u['amount_minor']) for u in usage)==22000

            # Candidate is visible and requires explicit acknowledgement; do not add a duplicate.
            page.get_by_role('button',name='手工补录',exact=True).click();dialog=page.get_by_role('dialog')
            dialog.get_by_label('补录账户',exact=True).select_option(bank)
            dialog.get_by_label('确认日期',exact=True).fill('2027-01-02')
            dialog.get_by_label('补录金额（元）',exact=True).fill('100')
            dialog.get_by_label('交易描述',exact=True).fill('合成同额候选')
            dialog.get_by_label('补录依据',exact=True).fill('合成重复识别验收')
            dialog.get_by_role('button',name='检查补录',exact=True).click()
            expect(dialog.get_by_text('发现 1 笔同账户、同日、同额的候选',exact=True)).to_be_visible()
            expect(dialog.get_by_role('button',name='确认补录',exact=True)).to_be_disabled()
            dialog.get_by_role('checkbox',name='已核对，这是另一笔真实交易，仍要补录',exact=True).check()
            expect(dialog.get_by_role('button',name='确认补录',exact=True)).to_be_enabled()
            page.screenshot(path=str(OUT/'desktop-manual-preview.png'),full_page=True,animations='disabled')
            dialog.get_by_role('button',name='关闭',exact=True).click()
            assert read('/status')['transaction_count']==4
            page.set_viewport_size({'width':390,'height':844})
            page.goto(url+'/#reconciliation',wait_until='networkidle')
            expect(page.get_by_role('heading',name='余额核对',exact=True)).to_be_visible()
            assert page.evaluate('document.documentElement.scrollWidth <= innerWidth')
            page.screenshot(path=str(OUT/'mobile-reconciliation.png'),full_page=True,animations='disabled')
            assert all(h<30 for h in bank_region.locator('.reconciliation-values dd').evaluate_all('(nodes)=>nodes.map(n=>n.getBoundingClientRect().height)')), 'Amounts must not wrap decimals on mobile'
            bank_region.get_by_role('button',name='补录遗漏交易',exact=True).click();dialog=page.get_by_role('dialog')
            expect(dialog.get_by_label('补录账户',exact=True)).to_be_visible()
            assert page.evaluate('document.documentElement.scrollWidth <= innerWidth')
            page.screenshot(path=str(OUT/'mobile-manual.png'),full_page=True,animations='disabled')
            dialog.get_by_label('确认日期',exact=True).fill('2027-01-31')
            dialog.get_by_label('补录金额（元）',exact=True).fill('9')
            dialog.get_by_label('交易描述',exact=True).fill('合成手机补录')
            dialog.get_by_label('补录依据',exact=True).fill('合成手机端小票')
            dialog.get_by_role('button',name='检查补录',exact=True).click()
            dialog.get_by_role('button',name='确认补录',exact=True).click();expect(dialog).not_to_be_visible()
            assert read('/status')['transaction_count']==5
            assert not errors,errors
            browser.close()
        print('PASS: balance observation/difference, manual supplement, coverage, explicit opening/history, original hash download, mixed refund and budget, duplicate confirmation, desktop/mobile')
    finally:
        process.terminate();process.wait(timeout=10);log.close()
