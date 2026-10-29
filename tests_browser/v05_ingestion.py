"""v0.5 browser acceptance: synthetic PDF, isolated ledger, no private data."""
from pathlib import Path
import io
import json
import socket
import subprocess
import sys
import tempfile
import time
import urllib.request
from uuid import uuid4

from playwright.sync_api import sync_playwright, expect
from pypdf import PdfWriter
from pypdf.generic import DictionaryObject, NameObject, DecodedStreamObject

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
OUT = ROOT / 'work' / 'v05-ingestion-acceptance'


def synthetic_pdf():
    """A real text PDF carrying only the checked-in synthetic statement."""
    text = (ROOT/'tests_app/fixtures/cmb_credit_statement.txt').read_text()
    alphabet = sorted(set(text)-{'\n'})
    assert len(alphabet) < 255
    codes = {char:i+1 for i,char in enumerate(alphabet)}
    pairs = '\n'.join(f'<{code:02x}> <{char.encode("utf-16-be").hex()}>' for char,code in codes.items())
    cmap = DecodedStreamObject()
    cmap.set_data(('/CIDInit /ProcSet findresource begin\n12 dict begin\nbegincmap\n/CMapName /Synthetic def\n/CMapType 2 def\n1 begincodespacerange\n<00> <ff>\nendcodespacerange\n'+str(len(codes))+' beginbfchar\n'+pairs+'\nendbfchar\nendcmap\nCMapName currentdict /CMap defineresource pop\nend\nend').encode())
    writer=PdfWriter(); page=writer.add_blank_page(width=612,height=792)
    font=DictionaryObject({NameObject('/Type'):NameObject('/Font'),NameObject('/Subtype'):NameObject('/Type1'),NameObject('/BaseFont'):NameObject('/Helvetica'),NameObject('/ToUnicode'):writer._add_object(cmap)})
    page[NameObject('/Resources')]=DictionaryObject({NameObject('/Font'):DictionaryObject({NameObject('/F1'):writer._add_object(font)})})
    stream=DecodedStreamObject(); lines=['BT /F1 10 Tf 20 760 Td']
    for line in text.splitlines(): lines.extend(['<'+''.join(f'{codes[c]:02x}' for c in line)+'> Tj','0 -16 Td'])
    lines.append('ET'); stream.set_data('\n'.join(lines).encode()); page[NameObject('/Contents')]=writer._add_object(stream)
    output=io.BytesIO(); writer.write(output); raw=output.getvalue()
    from app.import_parsers import parse
    result=parse(raw,'synthetic-cmb.pdf','synthetic-account')
    assert len(result['rows'])==6
    return raw


def run(url, scratch):
    OUT.mkdir(parents=True,exist_ok=True)
    content=synthetic_pdf()
    with sync_playwright() as pw:
        browser=pw.chromium.launch()
        context=browser.new_context(viewport={'width':1440,'height':1000},accept_downloads=True)
        context.set_default_timeout(10000)
        page=context.new_page(); errors=[]
        page.on('pageerror',lambda error:errors.append(str(error)))
        page.goto(url+'/#imports',wait_until='networkidle')

        def api(path):
            response=context.request.get(url+'/api/v1'+path)
            assert response.ok,response.text()
            return response.json()

        response=context.request.post(url+'/api/v1/accounts',data={'request_id':str(uuid4()),'name':'v05合成招行信用账户','kind':'credit','currency':'CNY'})
        assert response.ok,response.text(); account=response.json()['account']['id']
        page.reload(wait_until='networkidle')
        expect(page.get_by_role('heading',name='添加账单',exact=True)).to_be_visible()
        page.get_by_label('账单所属账户',exact=True).select_option(account)
        page.get_by_label('选择账单文件',exact=True).set_input_files({'name':'synthetic-cmb.pdf','mimeType':'application/pdf','buffer':content})
        before=api('/status')
        page.get_by_role('button',name='上传并查看预览',exact=True).click()
        expect(page.get_by_role('button',name='确认导入并整理',exact=True)).to_be_visible()
        preview=api('/import-batches')['items'][0]; batch_id=preview['id']
        assert preview['counts']['source_rows']['total']==6
        assert api('/status')['transaction_count']==before['transaction_count']==0
        assert api('/status')['data_revision']==before['data_revision']
        assert page.evaluate('document.documentElement.scrollWidth <= innerWidth')
        page.screenshot(path=str(OUT/'desktop-native-preview.png'),full_page=True,animations='disabled')
        page.get_by_role('button',name='确认导入并整理',exact=True).click()
        expect(page.get_by_role('button',name='准备交给 Codex',exact=True)).to_be_enabled()
        expect(page.get_by_role('button',name='停止后续处理',exact=True)).to_have_count(0)
        result=api('/import-batches/'+batch_id)['batch']
        assert result['status']=='completed' and result['counts']['business']['new']==6
        assert result['counts']['tasks']['unclassified']==3 and result['counts']['tasks']['waiting_for_codex']==0
        assert api('/status')['transaction_count']==6
        rev=api('/status')['data_revision']

        page.get_by_role('button',name='上传并查看预览',exact=True).click()
        expect(page.get_by_text('原件已存在，正在查看原批次；本次上传新增业务为 0。下方显示原批次累计结果。',exact=True)).to_be_visible()
        assert api('/status')['transaction_count']==6 and api('/status')['data_revision']==rev
        page.get_by_role('button',name='准备交给 Codex',exact=True).click()
        expect(page.get_by_label('复制到 Codex 的指令',exact=True)).to_be_visible()
        expect(page.get_by_text('需要你在已连接财务 MCP 的 Codex 会话发送下面的指令；目前尚未开始运行。',exact=False)).to_be_visible()
        prepared=api('/import-batches/'+batch_id)['batch']
        assert prepared['counts']['tasks']['waiting_for_codex']==3 and prepared['counts']['tasks']['running']==0
        assert api('/status')['data_revision']==rev
        page.reload(wait_until='networkidle')
        expect(page.get_by_role('heading',name='synthetic-cmb.pdf',exact=True)).to_be_visible()
        expect(page.get_by_label('查看已保存的批次',exact=True)).to_have_value(batch_id)
        expect(page.get_by_text('等待 Codex 3',exact=True)).to_be_visible()
        row=page.locator('details.import-item').nth(3); row.locator('summary').first.click()
        row.get_by_role('button',name='查看已关联交易',exact=False).click()
        expect(page.get_by_role('button',name='修改这笔',exact=True)).to_be_visible()
        page.keyboard.press('Escape')
        expect(page.get_by_role('dialog')).not_to_be_visible()
        assert page.evaluate('document.documentElement.scrollWidth <= innerWidth')
        page.screenshot(path=str(OUT/'desktop-native-results.png'),full_page=True,animations='disabled')

        # The modal must close when handing its prepared scope to the AI page.
        page.get_by_role('button',name='导入流水',exact=True).first.click()
        dialog=page.get_by_role('dialog')
        expect(dialog.get_by_role('heading',name='添加账单',exact=True)).to_be_visible()
        dialog.get_by_label('选择账单文件',exact=True).set_input_files({'name':'synthetic-cmb.pdf','mimeType':'application/pdf','buffer':content})
        dialog.get_by_role('button',name='上传并查看预览',exact=True).click()
        expect(dialog.get_by_role('button',name='准备交给 Codex',exact=True)).to_be_visible()
        dialog.get_by_role('button',name='准备交给 Codex',exact=True).click()
        expect(dialog.get_by_role('link',name='查看 Codex 任务与回执',exact=True)).to_be_visible()
        dialog.get_by_role('link',name='查看 Codex 任务与回执',exact=True).click()
        expect(page.get_by_role('dialog')).not_to_be_visible()
        expect(page.get_by_role('heading',name='AI 待办',exact=True)).to_be_visible()
        page.reload(wait_until='networkidle')
        expect(page.get_by_text('等待 Codex · 第 1 次任务',exact=True)).to_have_count(3)
        assert api('/import-batches/'+batch_id)['batch']['counts']['tasks']['waiting_for_codex']==3
        page.get_by_text('继续此前准备的任务',exact=True).click()
        page.get_by_role('button',name='显示此批指令',exact=True).first.click()
        expect(page.get_by_role('button',name='复制任务指令',exact=True)).to_be_visible()
        page.goto(url+'/#imports',wait_until='networkidle')
        expect(page.get_by_role('heading',name='synthetic-cmb.pdf',exact=True)).to_be_visible()
        row=page.locator('details.import-item').nth(3); row.locator('summary').first.click()

        page.set_viewport_size({'width':390,'height':844})
        assert page.evaluate('document.documentElement.scrollWidth <= innerWidth')
        row.get_by_text('原始字段',exact=True).click()
        assert page.evaluate('document.documentElement.scrollWidth <= innerWidth')
        page.screenshot(path=str(OUT/'mobile-native-results.png'),full_page=True,animations='disabled')

        page.goto(url+'/#settings',wait_until='networkidle')
        page.get_by_role('link',name='数据',exact=True).click()
        expect(page.get_by_role('button',name='创建完整备份',exact=True)).to_be_visible()
        page.get_by_label('覆盖月份',exact=True).fill('2027-02')
        page.get_by_label('核对账户',exact=True).select_option(account)
        expect(page.get_by_label('本次覆盖状态',exact=True)).to_be_visible()
        page.get_by_label('本次覆盖状态',exact=True).select_option('complete')
        page.get_by_label('核对证据与缺口说明',exact=True).fill('合成验证：2027年2月完整零交易账单，已核对。')
        page.get_by_label('我已核对该账户、月份和口径的完整资料；没有交易时也有依据。',exact=True).check()
        page.get_by_role('button',name='保存覆盖声明',exact=True).click()
        expect(page.get_by_text('v05合成招行信用账户 · 2027-02 的覆盖声明已保存。',exact=True)).to_be_visible()
        page.get_by_label('覆盖月份',exact=True).fill('2027-03')
        expect(page.get_by_text('尚未声明',exact=True)).to_be_visible()
        page.get_by_label('覆盖月份',exact=True).fill('2027-02')
        expect(page.get_by_text('第 1 版',exact=True)).to_be_visible()
        assert api('/status')['data_revision']==rev
        assert page.evaluate('document.documentElement.scrollWidth <= innerWidth')

        page.get_by_role('button',name='创建完整备份',exact=True).click()
        expect(page.get_by_role('link',name='下载备份',exact=True)).to_have_count(1)
        with page.expect_download() as download_event:
            page.get_by_role('link',name='下载备份',exact=True).click()
        backup_path=Path(scratch)/'synthetic-backup.zip'; download_event.value.save_as(backup_path)
        assert backup_path.is_file() and backup_path.stat().st_size>0
        page.get_by_label('验证并恢复备份包',exact=True).set_input_files(backup_path)
        page.get_by_role('button',name='恢复到独立目录',exact=True).click()
        expect(page.get_by_text('恢复验证通过：6 笔交易',exact=False)).to_be_visible()
        assert api('/status')['transaction_count']==6 and api('/status')['data_revision']==rev
        assert api('/import-batches/'+batch_id)['batch']['counts']['tasks']['waiting_for_codex']==3
        assert page.evaluate('document.documentElement.scrollWidth <= innerWidth')
        page.screenshot(path=str(OUT/'mobile-coverage-backup-restore.png'),full_page=True,animations='disabled')
        page.set_viewport_size({'width':1440,'height':1000})
        assert page.evaluate('document.documentElement.scrollWidth <= innerWidth')
        assert not errors,errors
        browser.close()
        print('PASS: native PDF preview before booking, apply, reuse adds zero, AI prepared/waiting, durable reload, exact transaction drill-down, coverage save/month isolation, backup download and isolated restore, desktop/390px no overflow; no JS errors.')


if __name__=='__main__':
    with tempfile.TemporaryDirectory(prefix='finance-v05-browser-') as temp:
        with socket.socket() as sock: sock.bind(('127.0.0.1',0)); port=sock.getsockname()[1]
        url=f'http://127.0.0.1:{port}'
        log=open(Path(temp)/'server.log','w+')
        proc=subprocess.Popen([sys.executable,str(ROOT/'scripts/run_app.py'),'--port',str(port),'--data-dir',str(Path(temp)/'data')],cwd=ROOT,stdout=log,stderr=log)
        try:
            for _ in range(100):
                try: urllib.request.urlopen(url+'/api/v1/session',timeout=.5); break
                except OSError:
                    if proc.poll() is not None: log.seek(0); raise RuntimeError(log.read())
                    time.sleep(.1)
            else: raise RuntimeError('Isolated v0.5 server did not start')
            run(url,temp)
        finally:
            proc.terminate(); proc.wait(timeout=5); log.close()
