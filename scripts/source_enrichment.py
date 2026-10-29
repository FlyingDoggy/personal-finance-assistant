"""Join archived channel descriptions to existing events without adding money."""
import csv
import io
import re
from collections import Counter, defaultdict
from decimal import Decimal
from datetime import date
from pathlib import Path
import unicodedata

BANK_NAMES={'cmb':'招商','citic':'中信','spdb':'浦发','bob':'北京银行'}

def merchant_matches(description, counterparty):
    clean=lambda s:re.sub(r'[\s\W_]+','',unicodedata.normalize('NFKC',s)).lower()
    party=clean(counterparty)
    return len(party)>=4 and party not in ['支付宝','支付宝支付','微信支付','微信转账','财付通','财付通支付','银联支付','快捷支付','平台商户','特约商户'] and party in clean(description)

def alipay_enrichments(inventory, events):
    source_files=[f for f in inventory['source_files'] if f['source_family']=='alipay' and f['filename'].lower().endswith('.csv')]
    rows=[]
    for source in source_files:
        raw=Path(source['archived_path']).read_bytes()
        try:text=raw.decode('utf-8-sig')
        except UnicodeDecodeError:text=raw.decode('gb18030')
        content=list(csv.reader(io.StringIO(text)))
        start=next(i for i,r in enumerate(content) if r and r[0].strip()=='交易时间')
        headers=[h.strip() for h in content[start]]
        for line,values in enumerate(content[start+1:],start+2):
            if not values or not re.match(r'^\d{4}-\d\d-\d\d',values[0]):continue
            row={k:v.strip() for k,v in zip(headers,values)}
            if row.get('收/支')!='支出':continue
            amount=Decimal(row['金额'].replace(',',''))*100
            if amount!=amount.to_integral_value():raise ValueError('Channel amount has sub-cent precision')
            row.update(minor=int(amount),source_csv_line=line)
            rows.append((source,row))
    index=defaultdict(list)
    for source,row in rows:index[(row['交易时间'][:10],row['minor'])].append((source,row))
    matches=[]
    for event in events:
        if not event['in_scope'] or event['expense_minor']<=0 or event['family']=='alipay':continue
        bank=BANK_NAMES.get(event['family'].split('_')[0]);tail=event['account_last4']
        if not bank or not tail:continue
        candidates=[(s,r) for s,r in index[(event['date'],event['amount_minor'])]
                    if bank in r.get('收/付款方式','') and re.search(r'[（(]'+re.escape(tail)+r'[）)]',r.get('收/付款方式',''))]
        basis='日期+金额+付款卡双向唯一匹配'
        if not candidates:
            candidates=[(s,r) for s,r in rows if r['minor']==event['amount_minor']
                        and abs((date.fromisoformat(event['date'])-date.fromisoformat(r['交易时间'][:10])).days)<=2
                        and bank in r.get('收/付款方式','') and re.search(r'[（(]'+re.escape(tail)+r'[）)]',r.get('收/付款方式',''))
                        and merchant_matches(event['description'],r.get('交易对方',''))]
            basis='商户完整名称+金额+付款卡一致，日期相差最多2天，双向唯一匹配'
        if len(candidates)!=1:continue
        source,row=candidates[0]
        matches.append({'id':event['id'],'evidence':row,'source_file':source['filename'],'source_sha256':source['sha256'],'match_basis':basis})
    counts=Counter((m['source_sha256'],m['evidence']['source_csv_line']) for m in matches)
    return [m for m in matches if counts[(m['source_sha256'],m['evidence']['source_csv_line'])]==1]
