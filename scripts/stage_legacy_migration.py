"""Build an isolated, replay-safe application DB from reviewed legacy evidence.

Never writes to the source database, Excel files, or the live application.
Activation is a separate stop/checkpoint/backup/switch operation after review.
"""
from __future__ import annotations

import argparse
from collections import defaultdict
import hashlib
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from app.budget import compute_budget
from app.domain import minor, valid_date, NAMES
from app.storage import Store, encode, digest, now, advance, save_transaction


def file_hash(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def load(path):
    return json.loads(Path(path).read_text())


def stage(work, destination):
    bundle_path=work/'ledger_bundle.json'
    bundle=load(bundle_path)
    budget=load(work/'budget_payload.json')
    mapping=load(work/'budget_mapping.json')
    actuals=load(work/'actuals_excel_baseline.json')
    compute_budget(budget)
    if budget['usage']: raise ValueError('A migrated plan must not create actual usage')
    identity=digest({'bundle':file_hash(bundle_path),'budget':digest(budget),'mapping':digest(mapping),'actuals':digest(actuals)})
    for filename, expected in {**mapping['source_hashes'],**actuals['source_hashes']}.items():
        path=Path(filename)
        if not path.is_absolute(): path=ROOT/path
        if file_hash(path)!=expected: raise ValueError('Source changed since reconciliation: '+str(path))
    if file_hash(bundle['source']['path'])!=bundle['source']['sha256']:
        raise ValueError('Legacy ledger changed since export')
    store=Store(destination)
    with store.read() as db:
        previous=db.execute('SELECT id FROM migration_runs').fetchone()
        if previous:
            if previous['id']==identity: return {'reused':True,'migration_id':identity}
            raise ValueError('Destination already has another migration; choose a new staging directory')
        if db.execute('SELECT COUNT(*) FROM transactions').fetchone()[0] or db.execute('SELECT COUNT(*) FROM accounts').fetchone()[0] or db.execute('SELECT COUNT(*) FROM budget_versions').fetchone()[0]:
            raise ValueError('Staging requires an empty application database; no existing data overwritten')

    account_ids={a['id'] for a in bundle['accounts']}
    txs={t['id']:t for t in bundle['transactions']}
    files={f['file_id']:f for f in bundle['files']}
    if len(txs)!=len(bundle['transactions']): raise ValueError('Duplicate legacy identities')
    for t in txs.values():
        if t['account_id'] not in account_ids or t['kind']!='historical': raise ValueError('Invalid historical account or kind')
        minor(t['amount_minor'])
        if t['date']: valid_date(t['date'])
        h=t['historical']
        for key in ('income_minor','expense_minor','cash_minor','debt_delta_minor'):
            if h[key] is not None: minor(h[key],key)
        if h['in_scope'] and not t['date']: raise ValueError('Undated record cannot count as a period fact')
        if int(h['income_minor'] or '0') and t['category_code'] not in {'4001','4004','4005'}: raise ValueError('Unmapped historical income')
        if int(h['expense_minor'] or '0') and (t['category_code'] not in NAMES or not t['category_code'].startswith('5')): raise ValueError('Unmapped historical expense')
        if sum(int(l['debit_minor'])-int(l['credit_minor']) for l in t['ledger_lines']): raise ValueError('Unbalanced historical journal: '+t['id'])
    grouped=defaultdict(list)
    primary=defaultdict(int)
    for s in bundle['sources']:
        if s['transaction_id'] not in txs or s['file_id'] not in files: raise ValueError('Missing source relation')
        grouped[(s['file_id'],txs[s['transaction_id']]['account_id'])].append(s)
        primary[s['transaction_id']]+=s['evidence_kind']=='primary'
    if set(primary)!=set(txs) or any(v!=1 for v in primary.values()): raise ValueError('Every legacy row must have one primary source')

    artifacts=[]
    for f in files.values():
        content=Path(f['path']).read_bytes()
        if hashlib.sha256(content).hexdigest()!=f['sha256'] or len(content)!=f['size']: raise ValueError('Original evidence changed')
        sha=store.archive(content,f['filename'])
        artifacts.append((sha,f['filename'],len(content)))
    # Keep the complete reviewed database, bridge manifest and both Excel baselines.
    for path in [Path(bundle['source']['path']),bundle_path,Path(mapping['source_workbook']),Path(actuals['source_workbook']),work/'budget_mapping.json',work/'actuals_excel_baseline.json']:
        content=path.read_bytes()
        artifacts.append((store.archive(content,path.name),path.name,len(content)))
    stamp=now()
    manifest={'migration_id':identity,'source_transaction_count':len(txs),
              'scoped_transaction_count':sum(t['historical']['in_scope'] for t in txs.values()),
              'undated_transaction_count':sum(not t['date'] for t in txs.values()),
              'source_evidence_count':len(bundle['sources']),'original_file_count':len(files),
              'account_count':len(account_ids),'budget_year':budget['year'],
              'source_workbook':Path(actuals['source_workbook']).name,
              'budget_workbook':Path(mapping['source_workbook']).name,
              'source_hashes':{'legacy_database':bundle['source']['sha256'],'ledger_bundle':file_hash(bundle_path)},
              'completed_at':stamp,'reconciliation_status':'迁移已暂存，等待应用结果核对'}
    provenance={'source_workbook':Path(mapping['source_workbook']).name,'source_version':mapping['source_version'],
                'notes':['沿用已采用 Excel 的逐月预算，未重新预测。','可用资金包含税后工资及可提取公积金假设；公积金不作为工资或已到账收入。','2027 年尚无实际交易，计划金额不表示实际为零。']+[item['name']+'：'+item['note'] for item in mapping['unmeasured']],
                'funding_components':mapping['income_sources'],'line_provenance':mapping['lines'],
                'unmeasured':mapping['unmeasured']}
    with store.write() as db:
        for sha,name,size in artifacts:
            db.execute('INSERT OR IGNORE INTO artifacts VALUES(?,?,?,?)',(sha,name,size,stamp))
        for a in bundle['accounts']:
            db.execute('INSERT INTO accounts VALUES(?,?,?,?,?,?,?,?,?)',(a['id'],a['name'],a['kind'],'CNY',None,None,a['evidence'],1,stamp))
        for tid,t in txs.items():
            p={k:v for k,v in t.items() if k not in {'id','ledger_lines'}}
            p.update(counterparty_account_id=None,refund_of=None,budget_line_id=None,counterparty_confirmed=False,source_id=tid)
            p['historical']={**p['historical'],'ledger_lines':[{**l,'account_id':None} for l in t['ledger_lines']]}
            save_transaction(db,tid,p,'migrate','沿用此前已整理并与 Excel 核对的历史解释；完整来源与修改依据已归档。',actor='migration')
            db.execute('INSERT INTO source_identity VALUES(?,?,?,?)',(t['account_id'],tid,digest(p),tid))
        for (file_id,account_id),sources in grouped.items():
            f=files[file_id];iid='legacy_import_'+digest([file_id,account_id])[:32]
            sources.sort(key=lambda s:(s['evidence_kind']!='primary',s['source_locator'],s['id']))
            imported=sum(s['evidence_kind']=='primary' for s in sources)
            db.execute('INSERT INTO imports VALUES(?,?,?,?,?,?,?,?,?,?,?,?)',(iid,f['sha256'],f['filename'],account_id,'reviewed_legacy_v1','completed',len(sources),imported,len(sources)-imported,0,'[]',stamp))
            for sequence,s in enumerate(sources,1):
                raw={**s['raw'],'source_locator':s['source_locator'],'source_page':s['page_no'],'source_line':s['row_number'],'evidence_kind':s['evidence_kind']}
                db.execute('INSERT INTO source_rows VALUES(?,?,?,?,?,?,?)',(s['id'],iid,sequence,s['source_id'],encode(raw),s['transaction_id'],None))
        for index,observation in enumerate(bundle['observations'],1):
            db.execute('INSERT INTO historical_observations VALUES(?,?)',(index,encode(observation)))
        rev=advance(db)
        db.execute('INSERT INTO budget_versions VALUES(?,?,?,?,?)',(budget['year'],1,encode(budget),stamp,rev))
        db.execute('INSERT INTO budget_provenance VALUES(?,?,?)',(budget['year'],1,encode(provenance)))
        db.execute('INSERT INTO migration_runs VALUES(?,?,?)',(identity,encode(manifest),stamp))
    with store.connect() as db:
        if db.execute('PRAGMA integrity_check').fetchone()[0]!='ok': raise ValueError('Staged database integrity failed')
        db.execute('PRAGMA wal_checkpoint(TRUNCATE)')
    return manifest


if __name__=='__main__':
    parser=argparse.ArgumentParser(description='在独立目录暂存真实账本与预算迁移；不切换在线库')
    parser.add_argument('--work',type=Path,default=ROOT/'work/migration-20261029')
    parser.add_argument('--destination',type=Path,required=True)
    args=parser.parse_args()
    if args.destination.resolve()==(ROOT/'data/app').resolve(): parser.error('Destination must be an isolated staging directory')
    print(json.dumps(stage(args.work.resolve(),args.destination.resolve()),ensure_ascii=False))
