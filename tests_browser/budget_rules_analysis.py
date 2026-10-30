"""Synthetic UI acceptance for explicit adoption, rule governance and reimbursements."""
from pathlib import Path
import sys, tempfile, subprocess, socket, time, urllib.request, json, re
from uuid import uuid4
from playwright.sync_api import sync_playwright, expect

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / 'tests_app')]
from test_backend import client_at, add_account, upload, budget_payload, save_budget

OUT = ROOT / 'work/r03-analysis-acceptance'; OUT.mkdir(exist_ok=True)
with tempfile.TemporaryDirectory(prefix='finance-budget-analysis-') as tmp:
    directory = Path(tmp) / 'data'
    c = client_at(directory); aid = add_account(c, name='合成验收账户')
    p = budget_payload(); p['year'] = 2026; assert save_budget(c, p).status_code == 200
    upload(c, aid, [['salary', '2026-01-24', '合成工资', '1000', 'income', '4001'],
                    ['hotel', '2026-01-03', '合成酒店', '-600', 'expense', '5003'],
                    ['lunch', '2026-01-04', '合成午餐', '-30', 'expense', '5016'],
                    ['reim', '2026-02-01', '合成报销', '100', 'reimbursement', '4005']])
    with socket.socket() as sock: sock.bind(('127.0.0.1', 0)); port = sock.getsockname()[1]
    url = f'http://127.0.0.1:{port}'
    log = open(Path(tmp) / 'server.log', 'w+')
    process = subprocess.Popen([sys.executable, str(ROOT / 'scripts/run_app.py'), '--port', str(port), '--data-dir', str(directory)], cwd=ROOT, stdout=log, stderr=log)
    try:
        for _ in range(100):
            try: urllib.request.urlopen(url + '/api/v1/session', timeout=.5); break
            except OSError: time.sleep(.1)
        with sync_playwright() as pw:
            browser = pw.chromium.launch(); ctx = browser.new_context(viewport={'width':1440, 'height':1000}, locale='zh-CN')
            page = ctx.new_page(); page.set_default_timeout(10000); errors = []
            page.on('pageerror', lambda error: errors.append(str(error)))
            def read(path):
                r = ctx.request.get(url + '/api/v1' + path); assert r.ok, r.text(); return r.json()
            page.goto(url + '/#budget', wait_until='networkidle')
            expect(page.get_by_role('heading', name='生活预算', exact=True)).to_be_visible()
            page.get_by_role('button', name='修订预算', exact=True).click()
            dialog = page.get_by_role('dialog')
            dialog.get_by_label('年度留存目标（元）', exact=True).fill('2000')
            dialog.get_by_role('button', name='检查草稿', exact=True).click()
            dialog.get_by_role('button', name='保存预算草稿', exact=True).click()
            expect(dialog).not_to_be_visible()
            expect(page.get_by_text('待采用草稿 · 1 版', exact=True)).to_be_visible()
            assert read('/budgets?year=2026')['version'] == 1
            assert read('/budgets?year=2026')['budget']['target_minor'] == '120000'
            page.screenshot(path=str(OUT / 'desktop-budget-draft.png'), full_page=True, animations='disabled')
            page.get_by_role('button', name='核对并采用', exact=True).click()
            page.get_by_role('button', name='确认采用此预算', exact=True).click()
            expect(page.get_by_role('dialog')).not_to_be_visible()
            assert read('/budgets?year=2026')['version'] == 2
            assert read('/budgets?year=2026')['budget']['target_minor'] == '200000'

            page.goto(url + '/#rules', wait_until='networkidle')
            page.get_by_label('规则名称', exact=True).fill('合成午餐默认')
            page.get_by_label('关键词', exact=True).fill('午餐')
            page.get_by_role('button', name='保存规则草稿', exact=True).click()
            expect(page.get_by_text('已保存规则草稿。请预览影响后启用。', exact=True)).to_be_visible()
            page.get_by_role('button', name='预览影响', exact=True).click()
            expect(page.get_by_text('影响预览 · 命中 1 笔', exact=True)).to_be_visible()
            page.get_by_role('button', name='确认启用此规则', exact=True).click()
            expect(page.get_by_text('规则已启用，将用于之后导入的适用记录。已有记录需重新预览后另行应用。', exact=True)).to_be_visible()
            assert next(t for t in read('/transactions')['items'] if t['description'] == '合成午餐')['category_code'] == '5016'
            page.get_by_role('button', name='预览影响', exact=True).click()
            page.get_by_role('button', name='确认应用以上 1 笔', exact=True).click()
            expect(page.get_by_text('已分类 1 笔。', exact=True)).to_be_visible()
            assert next(t for t in read('/transactions')['items'] if t['description'] == '合成午餐')['category_code'] == '5001'
            page.screenshot(path=str(OUT / 'desktop-rules.png'), full_page=True, animations='disabled')

            page.goto(url + '/#analysis', wait_until='networkidle')
            expect(page.get_by_role('heading', name='消费与报销分析', exact=True)).to_be_visible()
            page.get_by_label('报销调整方式', exact=True).select_option('budget_comparison')
            page.get_by_role('button', name='更新分析', exact=True).click()
            expect(page.get_by_text('所选模式：累计报销平滑月均。平滑不会改变收入减费用的结余。', exact=True)).to_be_visible()
            result = read('/analysis?start_month=2026-01&end_month=2026-02&mode=budget_comparison')
            assert result['totals']['adjusted_expense_minor'] == '53000'
            assert [m['adjustment_minor'] for m in result['months']] == ['5000', '5000']
            page.get_by_role('button', name='保存分析快照', exact=True).click()
            expect(page.get_by_text('已保存的分析快照', exact=True)).to_be_visible()
            page.screenshot(path=str(OUT / 'desktop-analysis.png'), full_page=True, animations='disabled')

            page.goto(url + '/#transactions', wait_until='networkidle')
            page.get_by_label('选择月份', exact=True).fill('2026-01')
            page.get_by_role('checkbox', name='选择消费 合成午餐', exact=True).check()
            page.get_by_role('button', name='批量分类（1）', exact=True).click()
            dialog = page.get_by_role('dialog')
            dialog.get_by_label('批量归入科目', exact=True).select_option('5004')
            dialog.get_by_label('批量修改依据', exact=True).fill('合成数据复核')
            dialog.get_by_role('button', name='确认修改选中的消费', exact=True).click()
            expect(dialog).not_to_be_visible()
            assert next(t for t in read('/transactions')['items'] if t['description'] == '合成午餐')['category_code'] == '5004'
            page.goto(url + '/#analysis', wait_until='networkidle')
            page.get_by_text('已保存的分析快照', exact=True).click()
            page.get_by_role('button', name=re.compile('依据已变化')).click()
            expect(page.get_by_text('数据或计算依据已变化。下方保留保存时结果，交易详情打开当前版本。', exact=False)).to_be_visible()
            page.get_by_role('button', name='读取当前账本', exact=True).click()
            expect(page.get_by_label('开始月份', exact=True)).to_have_value('2026-01')
            expect(page.get_by_label('结束月份', exact=True)).to_have_value('2026-02')
            expect(page.get_by_role('button', name='保存分析快照', exact=True)).to_be_enabled()

            page.set_viewport_size({'width':390, 'height':844})
            for route, title in [('analysis', '消费与报销分析'), ('rules', '分类规则'), ('budget', '生活预算')]:
                page.goto(url + '/#' + route, wait_until='networkidle')
                expect(page.get_by_role('heading', name=title, exact=True)).to_be_visible()
                assert page.evaluate('document.documentElement.scrollWidth <= innerWidth'), route
                page.screenshot(path=str(OUT / f'mobile-{route}.png'), full_page=True, animations='disabled')
                page.screenshot(path=str(OUT / f'viewport-{route}.png'), full_page=False, animations='disabled')
            assert not errors, errors
            browser.close()
            print('PASS: budget draft/adopt, rule preview/enable/apply, batch classification, same-window reimbursement analysis, frozen/stale snapshots, desktop/mobile; no JS errors')
    finally:
        process.terminate(); process.wait(timeout=10); log.close()
