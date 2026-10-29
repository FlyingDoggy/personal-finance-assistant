"""Read-only inventory of a source SQLite database and cached XLSX cells."""
import argparse
import json
import sqlite3
import zipfile
from collections import Counter
from pathlib import Path
from xml.etree import ElementTree as ET

NS = {'m': 'http://schemas.openxmlformats.org/spreadsheetml/2006/main'}

def read_xlsx(path):
    with zipfile.ZipFile(path) as z:
        strings=[]
        if 'xl/sharedStrings.xml' in z.namelist():
            strings=[''.join(t.text or '' for t in e.findall('.//m:t',NS)) for e in ET.fromstring(z.read('xl/sharedStrings.xml'))]
        rel={e.attrib['Id']:e.attrib['Target'] for e in ET.fromstring(z.read('xl/_rels/workbook.xml.rels'))}
        result={}
        for sh in ET.fromstring(z.read('xl/workbook.xml')).findall('m:sheets/m:sheet',NS):
            rid=sh.attrib['{http://schemas.openxmlformats.org/officeDocument/2006/relationships}id']
            target=rel[rid]; target=target.lstrip('/') if target.startswith('/') else 'xl/'+target
            cells={}
            for c in ET.fromstring(z.read(target)).findall('.//m:sheetData/m:row/m:c',NS):
                value=c.find('m:v',NS); formula=c.find('m:f',NS)
                if c.attrib.get('t')=='inlineStr': v=''.join(t.text or '' for t in c.findall('.//m:t',NS))
                elif value is None: v=None
                elif c.attrib.get('t')=='s': v=strings[int(value.text)]
                elif c.attrib.get('t') in ('str','e'): v=value.text
                else:
                    try: v=float(value.text); v=int(v) if v.is_integer() else v
                    except (TypeError,ValueError): v=value.text
                if v is not None or formula is not None:
                    cells[c.attrib['r']]={'value':v,'formula':formula.text if formula is not None else None}
            result[sh.attrib['name']]=cells
        return result

def main():
    ap=argparse.ArgumentParser(); ap.add_argument('--database',required=True); ap.add_argument('--workbook',required=True); ap.add_argument('--out',required=True); args=ap.parse_args()
    out=Path(args.out); out.mkdir(parents=True,exist_ok=True)
    c=sqlite3.connect(Path(args.database).resolve().as_uri()+'?mode=ro',uri=True); c.row_factory=sqlite3.Row
    records=[dict(r) for r in c.execute('SELECT r.*, f.source_family, f.filename FROM raw_transactions r JOIN source_files f USING(file_id)')]
    tables={t:[dict(x) for x in c.execute('SELECT * FROM '+t)] for t in ['source_files','statement_summaries','account_loan_snapshots','coverage_notes']}
    (out/'source_inventory.json').write_text(json.dumps({'records':records,**tables},ensure_ascii=False),encoding='utf-8')
    wb=read_xlsx(args.workbook); (out/'reference_workbook.json').write_text(json.dumps(wb,ensure_ascii=False),encoding='utf-8')
    overview={}
    for fam in sorted({r['source_family'] for r in records}):
        rows=[r for r in records if r['source_family']==fam]
        overview[fam]={'count':len(rows),'parsers':dict(Counter(r['parser'] for r in rows)),
            'dates':sorted({r['transaction_date_iso'] or r['posting_date_iso'] or 'unknown' for r in rows})[::max(1,len(rows)//5)],
            'directions':dict(Counter(r['direction_raw'] for r in rows)),
            'types':dict(Counter(r['transaction_type_raw'] for r in rows)),
            'products':list({r['product_raw'] for r in rows}),
            'currencies':dict(Counter(r['currency_raw'] for r in rows))}
    print(json.dumps({'sources':overview,'workbook_sheets':{k:len(v) for k,v in wb.items()}},ensure_ascii=False))
    c.close()

if __name__=='__main__':main()
