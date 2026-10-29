"""Synthetic end-to-end acceptance of settings, master data, journals and frozen reports."""
from pathlib import Path
import sys, tempfile, subprocess, socket, time, urllib.request, importlib.util
from playwright.sync_api import sync_playwright, expect
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
spec=importlib.util.spec_from_file_location('history_browser',ROOT/'tests_browser/history_edit.py');module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
OUT=ROOT/'work/settings-acceptance';OUT.mkdir(parents=True,exist_ok=True)
with tempfile.TemporaryDirectory(prefix='finance-settings-browser-') as tmp:
 d=Path(tmp)/'data';module.seed(d)
 with socket.socket() as s:s.bind(('127.0.0.1',0));port=s.getsockname()[1]
 url=f'http://127.0.0.1:{port}'
 log=open(Path(tmp)/'server.log','w+')
 p=subprocess.Popen([sys.executable,str(ROOT/'scripts/run_app.py'),'--port',str(port),'--data-dir',str(d)],cwd=ROOT,stdout=log,stderr=log)
 try:
  for _ in range(100):
   try: urllib.request.urlopen(url+'/api/v1/session',timeout=.5);break
   except OSError: time.sleep(.1)
  with sync_playwright() as pw:
   browser=pw.chromium.launch();ctx=browser.new_context(viewport={'width':1440,'height':1000});page=ctx.new_page();errors=[]
   page.on('pageerror',lambda e:errors.append(str(e)))
   page.goto(url+'/#settings',wait_until='networkidle')
   expect(page.get_by_role('heading',name='设置',exact=True)).to_be_visible()
   expect(page.get_by_role('heading',name='账户',exact=True)).to_be_visible()
   page.get_by_role('link',name='偏好设置',exact=True).click()
   expect(page.get_by_role('heading',name='按钮配色',exact=True)).to_be_visible()
   expect(page.get_by_role('button',name='已使用墨黑',exact=True)).to_be_visible()
   page.screenshot(path=str(OUT/'desktop-settings-preferences.png'),full_page=True,animations='disabled')
   page.locator('.appearance-grid').screenshot(path=str(OUT/'button-options.png'),animations='disabled')
   page.get_by_role('button',name='使用深绿',exact=True).click()
   expect(page.get_by_role('button',name='已使用深绿',exact=True)).to_be_visible()
   page.reload(wait_until='networkidle')
   expect(page.get_by_role('button',name='已使用深绿',exact=True)).to_be_visible()
   page.get_by_role('button',name='使用墨黑',exact=True).click()
   page.get_by_text('深色',exact=True).click()
   assert page.evaluate('document.documentElement.style.colorScheme') == 'dark'
   assert page.evaluate('document.documentElement.scrollWidth <= innerWidth')
   page.get_by_text('浅色',exact=True).click()
   page.get_by_role('link',name='科目',exact=True).click()
   expect(page.get_by_role('heading',name='科目',exact=True)).to_be_visible()
   expect(page.get_by_text('5001 · 餐饮',exact=True)).to_be_visible()
   page.get_by_role('link',name='数据',exact=True).click()
   expect(page.get_by_role('link',name='导出账本 JSON',exact=True)).to_be_visible()
   response=ctx.request.get(url+'/api/v1/data/export');assert response.ok,response.text();assert 'attachment' in response.headers['content-disposition']
   page.goto(url+'/#vouchers',wait_until='networkidle')
   expect(page.get_by_role('heading',name='凭证明细',exact=True)).to_be_visible()
   expect(page.locator('.voucher-row')).to_have_count(50)
   page.get_by_label('搜索凭证').fill('历史明细验收商户')
   expect(page.locator('.voucher-row')).to_have_count(1)
   page.locator('.voucher-row').click();expect(page.get_by_role('button',name='修改这笔',exact=True)).to_be_visible()
   page.keyboard.press('Escape')
   page.goto(url+'/#reports',wait_until='networkidle')
   expect(page.get_by_role('heading',name='管理报表',exact=True)).to_be_visible()
   page.get_by_role('button',name='保存当前报表快照',exact=True).click()
   expect(page.locator('.snapshot-list button')).to_have_count(1)
   page.locator('.snapshot-list button').click()
   expect(page.get_by_role('heading',name='报表快照',exact=True)).to_be_visible()
   expect(page.get_by_role('button',name='查看本月明细',exact=False)).to_have_count(0)
   page.get_by_role('button',name='返回当前报表',exact=True).click()
   snapshots=ctx.request.get(url+'/api/v1/report-snapshots').json()['items']
   sid=snapshots[0]['id']
   before=ctx.request.get(url+'/api/v1/report-snapshots/'+sid).json()['snapshot']
   assert before['stale'] is False
   page.goto(url+'/#vouchers',wait_until='networkidle')
   page.get_by_label('搜索凭证').fill('历史明细验收商户')
   expect(page.locator('.voucher-row')).to_have_count(1)
   page.locator('.voucher-row').click()
   page.get_by_role('button',name='修改这笔',exact=True).click()
   page.get_by_label('业务金额（元）',exact=True).fill('450.50')
   page.get_by_label('修改原因',exact=True).fill('合成验收：验证凭证刷新和旧报告冻结')
   page.get_by_role('button',name='保存修改',exact=True).click()
   expect(page.get_by_text('已保存修改，相关报表已更新。',exact=True)).to_be_visible()
   page.keyboard.press('Escape')
   expect(page.locator('.voucher-row')).to_contain_text('450.50')
   after=ctx.request.get(url+'/api/v1/report-snapshots/'+sid).json()['snapshot']
   assert after['stale'] is True
   assert after['report']==before['report']
   assert ctx.request.get(url+'/api/v1/reports?month=2027-01').json()['income_expense']['expense_minor']=='51050'
   page.goto(url+'/#reports',wait_until='networkidle')
   page.get_by_role('button',name='已保存快照 · 1',exact=True).click()
   page.locator('.snapshot-list button').click()
   expect(page.get_by_text('账本或规则已有更新，此处仍保留保存时的数值。',exact=False)).to_be_visible()
   page.set_viewport_size({'width':390,'height':844});page.goto(url+'/#settings',wait_until='networkidle')
   expect(page.get_by_role('heading',name='设置',exact=True)).to_be_visible()
   assert page.evaluate('document.documentElement.scrollWidth <= innerWidth')
   page.screenshot(path=str(OUT/'mobile-settings-menu.png'),full_page=True,animations='disabled')
   page.get_by_role('link',name='偏好设置',exact=True).click()
   expect(page.get_by_role('heading',name='偏好设置',exact=True)).to_be_visible()
   assert page.evaluate('document.documentElement.scrollWidth <= innerWidth')
   page.screenshot(path=str(OUT/'mobile-settings-preferences.png'),full_page=True,animations='disabled')
   page.get_by_role('link',name='设置',exact=True).click()
   expect(page.get_by_role('heading',name='设置',exact=True)).to_be_visible()
   assert not errors,errors
   browser.close()
   print('PASS: upstream settings desktop/mobile, theme selection, catalog, JSON export, vouchers, saved report snapshots; no JS errors')
 finally:
  p.terminate();p.wait(timeout=5);log.close()
