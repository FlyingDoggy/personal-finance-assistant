"""Read only the user-input columns, keyed by the original transaction ID.

Uses the saved XLSX XML without changing the workbook or its cached formulas.
"""
import argparse
import json
import posixpath
import re
import xml.etree.ElementTree as ET
from pathlib import Path
from zipfile import ZipFile

NS={'s':'http://schemas.openxmlformats.org/spreadsheetml/2006/main'}
REL='http://schemas.openxmlformats.org/officeDocument/2006/relationships'

def read_confirmation(path):
    with ZipFile(path) as z:
        strings=[]
        if 'xl/sharedStrings.xml' in z.namelist():
            strings=[''.join(t.itertext()) for t in ET.fromstring(z.read('xl/sharedStrings.xml')).findall('s:si',NS)]
        rels={r.attrib['Id']:r.attrib['Target'] for r in ET.fromstring(z.read('xl/_rels/workbook.xml.rels'))}
        sheets=ET.fromstring(z.read('xl/workbook.xml')).findall('s:sheets/s:sheet',NS)
        target=rels[next(s.attrib['{'+REL+'}id'] for s in sheets if s.attrib['name']=='收入确认')]
        target=target.lstrip('/') if target.startswith('/') else posixpath.normpath('xl/'+target)
        result=[]
        for row in ET.fromstring(z.read(target)).findall('s:sheetData/s:row',NS):
            if int(row.attrib['r'])<5:continue
            values={}
            for cell in row.findall('s:c',NS):
                column=re.sub(r'\d','',cell.attrib['r'])
                value=cell.find('s:v',NS)
                if cell.attrib.get('t')=='s':value=strings[int(value.text)] if value is not None else ''
                elif cell.attrib.get('t')=='inlineStr':value=''.join(t.text or '' for t in cell.findall('.//s:t',NS))
                else:value=value.text if value is not None else ''
                values[column]=value
            if any(values.get(c,'').strip() for c in ['G','H','I']):
                result.append({'row':int(row.attrib['r']),'id':values.get('J',''),'type':values.get('G','').strip(),'related':values.get('H','').strip(),'note':values.get('I','').strip(),'original_type':values.get('F',''),'question':values.get('A',''),'amount':values.get('C','')})
        ids=[r['id'] for r in result]
        if any(not x for x in ids) or len(ids)!=len(set(ids)):raise ValueError('Missing or duplicate transaction IDs in confirmation inputs')
        return result

if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('path');parser.add_argument('--out');args=parser.parse_args()
    rows=read_confirmation(args.path)
    if args.out:Path(args.out).write_text(json.dumps(rows,ensure_ascii=False,indent=2))
    print(json.dumps(rows,ensure_ascii=False,indent=2))
