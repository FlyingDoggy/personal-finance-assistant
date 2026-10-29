"""Durable preview/confirm/import commands with immutable source observations.

Every funding row is one complete business group. Parsing runs before the first
write transaction; each group commits through Store.write independently. A
bounded apply can be resumed after process death without repeating completed
rows. The legacy imports/source_rows remain the evidence drill-down boundary.
"""
from __future__ import annotations

import hashlib
import json
import re
import threading
from contextlib import contextmanager
from pathlib import Path

from fastapi import File, Form, Request, UploadFile

from . import import_parsers
from .domain import DomainError, major_to_minor, money, review_reasons, validate_transaction
from .reports import common
from .storage import accounts, advance, digest, encode, finish_operation, now, operation, revision, save_transaction, transactions, uid


_RUN_GUARD = threading.Lock()
_REQUEST_LOCKS = {}
_ACTIVE_BATCHES = {}


@contextmanager
def _command_owner(store,batch_id,body,action):
    """Serialize duplicate commands, not unrelated batches or cancellation.

    This process owns the sole application writer. Persisted runs remain the
    recovery boundary after process exit; these bounded locks are only live
    request coordination and hold no database transaction.
    """
    request_id=body['request_id']
    request_key=(str(store.path),request_id)
    batch_key=(str(store.path),batch_id)
    with _RUN_GUARD:
        slot=_REQUEST_LOCKS.setdefault(request_key,{'lock':threading.Lock(),'users':0})
        slot['users']+=1
    slot['lock'].acquire()
    registered=False
    try:
        with store.read() as db:
            _,cached=operation(db,request_id,'import_batch_'+action+':'+batch_id,body)
        if cached is not None:
            yield cached
            return
        with _RUN_GUARD:
            if batch_key in _ACTIVE_BATCHES:
                raise DomainError('本批仍在处理，请刷新进度；当前处理结束后可以继续。','batch_running',409)
            _ACTIVE_BATCHES[batch_key]=request_id
            registered=True
        yield None
    finally:
        with _RUN_GUARD:
            if registered:
                _ACTIVE_BATCHES.pop(batch_key,None)
        slot['lock'].release()
        with _RUN_GUARD:
            slot['users']-=1
            if not slot['users']:
                _REQUEST_LOCKS.pop(request_key,None)


def initialize(db):
    statements = [
        '''CREATE TABLE IF NOT EXISTS import_batches(id TEXT PRIMARY KEY REFERENCES imports(id),status TEXT NOT NULL,file_status TEXT NOT NULL,version INTEGER NOT NULL,preview_meta TEXT NOT NULL,error TEXT,revision_before INTEGER NOT NULL,revision_after INTEGER NOT NULL,created_at TEXT NOT NULL,updated_at TEXT NOT NULL)''',
        '''CREATE TABLE IF NOT EXISTS import_observations(id TEXT PRIMARY KEY,batch_id TEXT NOT NULL REFERENCES import_batches(id),row_number INTEGER NOT NULL,source_namespace TEXT NOT NULL,source_id TEXT,fingerprint TEXT NOT NULL,observation TEXT NOT NULL,UNIQUE(batch_id,row_number))''',
        '''CREATE TABLE IF NOT EXISTS import_row_results(observation_id TEXT PRIMARY KEY REFERENCES import_observations(id),status TEXT NOT NULL,transaction_id TEXT,error_code TEXT,message TEXT,created_business INTEGER NOT NULL DEFAULT 0,attempts INTEGER NOT NULL DEFAULT 0,updated_at TEXT NOT NULL)''',
        '''CREATE TABLE IF NOT EXISTS source_identities(source_namespace TEXT NOT NULL,account_id TEXT NOT NULL,source_id TEXT NOT NULL,fingerprint TEXT NOT NULL,transaction_id TEXT,observation_id TEXT,PRIMARY KEY(source_namespace,account_id,source_id))''',
        '''CREATE TABLE IF NOT EXISTS import_batch_runs(request_id TEXT PRIMARY KEY,batch_id TEXT NOT NULL REFERENCES import_batches(id),action TEXT NOT NULL,fingerprint TEXT NOT NULL,started_at TEXT NOT NULL)''',
        'CREATE INDEX IF NOT EXISTS import_observations_batch ON import_observations(batch_id,row_number)',
        'CREATE INDEX IF NOT EXISTS import_results_transaction ON import_row_results(transaction_id)',
        '''CREATE TRIGGER IF NOT EXISTS import_observations_no_update BEFORE UPDATE ON import_observations BEGIN SELECT RAISE(ABORT,'import observations are immutable'); END''',
        '''CREATE TRIGGER IF NOT EXISTS import_observations_no_delete BEFORE DELETE ON import_observations BEGIN SELECT RAISE(ABORT,'import observations are immutable'); END''',
    ]
    for statement in statements:
        db.execute(statement)
    # Keep legacy identity unchanged. Namespacing is additive and preserves the
    # original fingerprint, including pre-normalization standard CSV values.
    db.execute('''INSERT OR IGNORE INTO source_identities
        SELECT CASE WHEN EXISTS(SELECT 1 FROM source_rows s JOIN imports i ON i.id=s.import_id
          WHERE s.transaction_id=x.transaction_id AND s.source_id=x.source_id AND i.account_id=x.account_id AND i.parser_version='standard_csv_v1')
          THEN 'standard_csv_v1' ELSE 'legacy' END,x.account_id,x.source_id,x.fingerprint,x.transaction_id,NULL FROM source_identity x''')


def _get(db, batch_id):
    found = db.execute('SELECT b.*,i.sha256,i.filename,i.account_id,i.parser_version FROM import_batches b JOIN imports i ON i.id=b.id WHERE b.id=?', (batch_id,)).fetchone()
    if not found:
        raise DomainError('导入批次不存在。', 'not_found', 404)
    return dict(found)


def _rows(db, batch_id):
    return [dict(r) for r in db.execute('''SELECT o.*,r.status,r.transaction_id,r.error_code,r.message,r.created_business,r.attempts
        FROM import_observations o JOIN import_row_results r ON r.observation_id=o.id WHERE o.batch_id=? ORDER BY o.row_number''', (batch_id,))]


def _error(exc):
    return {'code':exc.code, 'message':exc.message}


def _fingerprint(item):
    if item.get('payload'):
        return digest(item['payload'])
    return digest({'source_id':item.get('source_id'), 'evidence':item.get('evidence'), 'raw':{k:v for k,v in item['raw'].items() if k != '序号'}})


def _normalize_text(value):
    return ' '.join(str(value or '').split())


def _legacy_candidates(db, batch, item):
    """Same original + original fields are strong evidence, never edited facts."""
    rows = list(db.execute('''SELECT s.transaction_id,s.raw FROM source_rows s JOIN imports i ON i.id=s.import_id
        WHERE i.sha256=? AND i.account_id=? AND i.parser_version='reviewed_legacy_v1' AND s.transaction_id IS NOT NULL''', (batch['sha256'],batch['account_id'])))
    if not rows:
        return None
    candidates = set()
    new = item['raw']
    for row in rows:
        old = json.loads(row['raw'])
        if _normalize_text(old.get('raw_text')) == _normalize_text(new.get('raw_text')) and new.get('raw_text'):
            candidates.add(row['transaction_id'])
            continue
        if all(_normalize_text(old.get(k)) == _normalize_text(new.get(k)) for k in ('description_raw','posting_date_raw','transaction_date_raw')):
            try:
                if major_to_minor(str(old.get('amount_raw','')).replace(',','')) == major_to_minor(new.get('amount_raw','').replace(',','')) and str(old.get('account_identifier_raw','')).endswith(new.get('card_last4','!')):
                    candidates.add(row['transaction_id'])
            except DomainError:
                pass
    return candidates


def _evidence_ids(value):
    if isinstance(value,dict):
        for key,item in value.items():
            if key in {'支付宝交易号','交易订单号'} and isinstance(item,str):
                yield item.strip()
            if isinstance(item,(list,dict)):
                yield from _evidence_ids(item)
    elif isinstance(value,list):
        for item in value:
            yield from _evidence_ids(item)


def _resolve(db, batch, item):
    """Read-only result prediction, repeated inside each apply transaction."""
    if item.get('error'):
        return {'status':'invalid', **item['error']}
    identity = db.execute('SELECT * FROM source_identities WHERE source_namespace=? AND account_id=? AND source_id=?', (batch['source_namespace'], batch['account_id'],item['source_id'])).fetchone()
    # Legacy standard import remains available after schema initialization.
    if not identity and batch['source_namespace']=='standard_csv_v1':
        identity = db.execute('SELECT * FROM source_identity WHERE account_id=? AND source_id=?', (batch['account_id'],item['source_id'])).fetchone()
        if identity and not db.execute("SELECT 1 FROM source_rows s JOIN imports i ON i.id=s.import_id WHERE s.transaction_id=? AND i.parser_version='standard_csv_v1'", (identity['transaction_id'],)).fetchone():
            identity = None
    if identity and identity['fingerprint'] != _fingerprint(item):
        return {'status':'unresolved','code':'source_conflict','message':'同一来源标识出现解析差异；原始观察和人工判断均保留，请核对后人工更正。','transaction_id':identity['transaction_id']}
    if identity and identity['transaction_id']:
        return {'status':'duplicate','transaction_id':identity['transaction_id'],'message':'已关联既有交易；本次原件作为新增来源位置保留。'}
    if item['role']=='enrich':
        candidates = {r['transaction_id'] for r in db.execute('SELECT transaction_id,raw FROM source_rows WHERE transaction_id IS NOT NULL') if item['source_id'] in set(_evidence_ids(json.loads(r['raw'])))}
        if len(candidates)==1:
            return {'status':'evidence_only','transaction_id':next(iter(candidates)), 'message':'按同一支付宝订单号补充商品证据，金额和分类未改变。'}
        return {'status':'evidence_only','code':'evidence_unmatched','message':'仅保存商品参考；未找到唯一强标识关联，未新增消费或退款。'}
    if item['identity_quality']=='observation':
        legacy = _legacy_candidates(db,batch,item)
        if legacy is not None:
            if len(legacy)==1:
                return {'status':'duplicate','transaction_id':next(iter(legacy)), 'message':'同一已迁移原件及原始字段匹配，保留既有账务解释。'}
            return {'status':'unresolved','code':'legacy_observation_conflict','message':'该原件已有历史来源；新版解析未唯一对应原记录，未重建业务。'}
        p = item['payload']
        # Similarity is only a review candidate, never a stable ID or a merge.
        candidates=[]
        same_file={r[0] for r in db.execute('SELECT s.transaction_id FROM source_rows s JOIN imports i ON i.id=s.import_id WHERE i.sha256=? AND s.transaction_id IS NOT NULL',(batch['sha256'],))}
        for tid,t in transactions(db).items():
            if t['account_id'] != p['account_id'] or tid in same_file:
                continue
            h=t.get('historical',{})
            amount=-int(h['raw_amount_minor']) if h.get('family')=='cmb_credit' and h.get('raw_amount_minor') is not None else int(t['amount_minor'])
            if (t.get('cash_date') or t.get('date'))==p['cash_date'] and amount==int(p['amount_minor']):
                candidates.append(tid)
        if candidates:
            return {'status':'unresolved','code':'duplicate_candidate','message':'缺少稳定交易号，发现同账户同记账日同金额候选；未自动合并或新增。','candidate_transaction_ids':candidates}
    return {'status':'mapped','message':'确认后新增一笔业务。'}


def _summary(parsed):
    payloads = [r['payload'] for r in parsed['rows'] if r.get('payload') and not r.get('error')]
    days = sorted(p['date'] for p in payloads)
    return {'period_start':days[0] if days else None,'period_end':days[-1] if days else None,'currency':'CNY','inflow_minor':money(sum(max(int(p['amount_minor']),0) for p in payloads)),'outflow_minor':money(sum(max(-int(p['amount_minor']),0) for p in payloads)), 'amount_basis':'所选账户净变动；信用卡不代表银行现金流。' if parsed.get('account_kind')=='credit' else '标准模板有符号账户变动；商品证据不计资金。'}


def create_preview(store, content, filename, account_id, request_id):
    """Parse bytes without a write transaction; archive and persist observations."""
    if len(content)>import_parsers.MAX_UPLOAD:
        raise DomainError('文件超过 10 MiB 上限。','upload_too_large',413)
    filename=Path(filename or 'upload').name[:200]
    sha=hashlib.sha256(content).hexdigest()
    request_payload={'sha256':sha,'account_id':account_id}
    with store.read() as db:
        _,cached=operation(db,request_id,'import_preview',request_payload)
        if cached is not None:
            return cached
        acc=accounts(db)
        if account_id not in acc:
            raise DomainError('请先选择已有账户。')
    failure=None
    try:
        parsed=import_parsers.parse(content,filename,account_id)
        if parsed.get('account_kind') and acc[account_id]['kind']!=parsed['account_kind']:
            raise DomainError('招商信用卡账单须映射到信用账户。','account_mapping_required')
        meta={k:v for k,v in parsed.items() if k!='rows'}
        meta['summary']=_summary(parsed)
    except DomainError as exc:
        failure=_error(exc)
        parsed={'parser_version':'unrecognized_v1','source_namespace':'unrecognized','rows':[]}
        meta={**parsed,'summary':{'period_start':None,'period_end':None,'currency':'CNY','inflow_minor':'0','outflow_minor':'0'},'warnings':[exc.message]}
        meta.pop('rows')
    # Archive only bytes; parsers cannot write or evaluate scripts/formulas.
    store.archive(content,filename)
    with store.write() as db:
        fingerprint,cached=operation(db,request_id,'import_preview',request_payload)
        if cached is not None:
            return cached
        for previous_batch in db.execute('SELECT b.id,b.preview_meta FROM import_batches b JOIN imports i ON i.id=b.id WHERE i.sha256=? AND i.account_id=?',(sha,account_id)):
            if json.loads(previous_batch['preview_meta'])['parser_version']==parsed['parser_version']:
                return finish_operation(db,request_id,'import_preview',fingerprint,{'batch':batch_dto(db,previous_batch['id']),'reused':True,'effects_this_request':{'new_business':0},**common(db)})
        previous=db.execute('SELECT id FROM imports WHERE sha256=? AND account_id=? AND parser_version=?',(sha,account_id,parsed['parser_version'])).fetchone()
        iid=uid()
        # A standard file already handled by v1 still gets a fresh preview
        # attempt; the observation namespace stays standard_csv_v1.
        stored_parser=parsed['parser_version'] if not previous else parsed['parser_version']+'+preview'
        stamp=now(); rev=revision(db)
        db.execute('INSERT OR IGNORE INTO artifacts VALUES(?,?,?,?)',(sha,filename,len(content),stamp))
        db.execute('INSERT INTO imports VALUES(?,?,?,?,?,?,?,?,?,?,?,?)',(iid,sha,filename,account_id,stored_parser,'preview_ready' if not failure else 'failed',len(parsed['rows']),0,0,0,'[]',stamp))
        db.execute('INSERT INTO import_batches VALUES(?,?,?,?,?,?,?,?,?,?)',(iid,'awaiting_confirmation','preview_ready' if not failure else 'unsupported' if failure['code']=='unsupported_format' else 'failed',1,encode(meta),encode(failure) if failure else None,rev,rev,stamp,stamp))
        batch={**meta,'sha256':sha,'account_id':account_id}
        for item in parsed['rows']:
            sid=uid()
            resolution=_resolve(db,batch,item)
            # Validation is also previewed; row-local business errors do not
            # discard other readable rows in the file.
            if resolution['status']=='mapped':
                try:
                    validate_transaction(item['payload'],accounts(db),transactions(db))
                except DomainError as exc:
                    item={**item,'error':_error(exc)}
                    resolution={'status':'invalid',**item['error']}
            item={**item,'preview':resolution}
            db.execute('INSERT INTO import_observations VALUES(?,?,?,?,?,?,?)',(sid,iid,item['row_number'],parsed['source_namespace'],item.get('source_id'),_fingerprint(item),encode(item)))
            db.execute('INSERT INTO import_row_results VALUES(?,?,?,?,?,?,?,?)',(sid,'invalid' if item.get('error') else 'pending',None,item.get('error',{}).get('code'),item.get('error',{}).get('message'),0,0,stamp))
            if item.get('error'):
                db.execute('INSERT INTO source_rows VALUES(?,?,?,?,?,?,?)',(sid,iid,item['row_number'],item.get('source_id'),encode(item['raw']),None,item['error']['message']))
        response={'batch':batch_dto(db,iid),'reused':False,'effects_this_request':{'new_business':0},**common(db)}
        return finish_operation(db,request_id,'import_preview',fingerprint,response)


def batch_dto(db,batch_id):
    batch=_get(db,batch_id); meta=json.loads(batch['preview_meta']); rows=_rows(db,batch_id)
    counts={'total':len(rows),'processed':0,**{x:0 for x in ('pending','mapped','duplicate','evidence_only','unresolved','invalid','failed')}}
    for row in rows:
        counts[row['status']]+=1
        if row['status']!='pending':
            counts['processed']+=1
    txs=transactions(db)
    tids={r['transaction_id'] for r in rows if r['transaction_id']}
    unclassified=sum(txs[tid]['kind']=='expense' and txs[tid].get('category_code')=='5016' for tid in tids if tid in txs)
    review=sum(r['status'] in {'invalid','unresolved','failed'} or r['error_code']=='evidence_unmatched' for r in rows)
    review+=sum(any(x!='消费类别待补' for x in review_reasons(txs[tid])) for tid in tids if tid in txs)
    business={'new':sum(r['created_business'] for r in rows),'evidence_added':sum(r['status']=='evidence_only' and bool(r['transaction_id']) for r in rows),'duplicate_skipped':counts['duplicate'],'linked_transactions':len(tids)}
    tasks={'unclassified':unclassified,'waiting_for_codex':0,'running':0,'needs_review':review,'applied':0}
    # Queue preparation is explicit. Unclassified does not pretend that an AI
    # task was prepared or that Codex is already running.
    if tids and db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='work_items'").fetchone():
        for row in db.execute(f'''SELECT w.status,COUNT(*) AS n FROM work_items w WHERE w.target_id IN ({",".join("?" for _ in tids)})
            AND w.generation=(SELECT MAX(w2.generation) FROM work_items w2 WHERE w2.issue_key=w.issue_key) GROUP BY w.status''',tuple(tids)):
            key={'queued':'waiting_for_codex','leased':'running','applied':'applied','needs_review':'needs_review'}.get(row['status'])
            if key: tasks[key]+=row['n']
    return {k:batch[k] for k in ('id','filename','sha256','account_id','status','file_status','version','created_at','updated_at','revision_before','revision_after')} | {'import_id':batch_id,'parser_version':meta['parser_version'],'source_namespace':meta['source_namespace'],'role':meta.get('role'),'encoding':meta.get('encoding'),'field_mapping':meta.get('field_mapping',{}),'summary':meta['summary'],'source_account_options':meta.get('source_account_options',[]),'warnings':meta.get('warnings',[]),'error':json.loads(batch['error']) if batch['error'] else None,'counts':{'source_rows':counts,'business':business,'tasks':tasks},'can_apply':batch['file_status']=='preview_ready' and batch['status']=='awaiting_confirmation','can_resume':batch['status'] in {'partial','processing','cancelled'} and bool(counts['pending']+counts['failed']),'can_cancel':batch['status'] in {'awaiting_confirmation','processing','partial'},'transaction_ids':sorted(tids)}


def get_batch(store,batch_id,limit=100,offset=0):
    if not 1<=limit<=200 or offset<0:
        raise DomainError('分页 limit 须为 1–200，offset 不可为负。')
    with store.read() as db:
        batch=batch_dto(db,batch_id); rows=_rows(db,batch_id)
        items=[]
        for row in rows[offset:offset+limit]:
            observed=json.loads(row['observation'])
            items.append({'id':row['id'],'row_number':row['row_number'],'source_id':row['source_id'],'source_namespace':row['source_namespace'],'identity_quality':observed['identity_quality'],'role':observed['role'],'raw':observed['raw'],'parsed':observed.get('payload') or observed.get('evidence'),'preview':observed.get('preview'),'status':row['status'],'transaction_id':row['transaction_id'],'attempts':row['attempts'],'error':{'code':row['error_code'],'message':row['message']} if row['error_code'] else None,'message':row['message'],'warning':observed.get('warning')})
        return {'batch':batch,'rows':items,'total':len(rows),'limit':limit,'offset':offset,**common(db)}


def _validate_command(body):
    if not isinstance(body,dict) or set(body)-{'request_id','expected_version','limit'}:
        raise DomainError('批次请求只接受 request_id、expected_version、limit。')
    if type(body.get('expected_version')) is not int:
        raise DomainError('expected_version 须为整数。')
    limit=body.get('limit',100)
    if type(limit) is not int or not 1<=limit<=500:
        raise DomainError('每次处理 limit 须为 1–500。')
    return limit


def _persist_result(db,batch,row,item,result,created=False):
    tid=result.get('transaction_id')
    db.execute('''UPDATE import_row_results SET status=?,transaction_id=?,error_code=?,message=?,created_business=?,attempts=attempts+1,updated_at=? WHERE observation_id=?''',(result['status'],tid,result.get('code'),result.get('message'),int(created),now(),row['id']))
    if result['status']!='failed':
        # Raw source rows are insert-only. Their related processing state lives
        # separately; corrected parses are new immutable observations.
        db.execute('INSERT OR IGNORE INTO source_rows VALUES(?,?,?,?,?,?,?)',(row['id'],batch['id'],row['row_number'],row['source_id'],encode(item['raw']),tid,result.get('message') if result.get('code') else None))
    if item.get('source_id') and result['status'] in {'mapped','duplicate','evidence_only'}:
        db.execute('''INSERT INTO source_identities VALUES(?,?,?,?,?,?) ON CONFLICT(source_namespace,account_id,source_id)
            DO UPDATE SET transaction_id=COALESCE(source_identities.transaction_id,excluded.transaction_id)''',(row['source_namespace'],batch['account_id'],item['source_id'],row['fingerprint'],tid,row['id']))


def _apply_one(store,batch_id):
    with store.write() as db:
        batch=_get(db,batch_id)
        if batch['status']=='cancelled':
            return False
        row=db.execute('''SELECT o.* FROM import_observations o JOIN import_row_results r ON r.observation_id=o.id
            WHERE o.batch_id=? AND r.status='pending' ORDER BY o.row_number LIMIT 1''',(batch_id,)).fetchone()
        if not row:
            return False
        row=dict(row); item=json.loads(row['observation']); meta=json.loads(batch['preview_meta']); batch.update(source_namespace=meta['source_namespace'])
        db.execute('SAVEPOINT import_business')
        try:
            result=_resolve(db,batch,item)
            created=False
            if result['status']=='mapped':
                # Budget checks use the same command boundary as manual edits.
                from .main import validate_budget_line
                p=validate_transaction(item['payload'],accounts(db),transactions(db))
                validate_budget_line(db,p)
                # source_id is local to its source namespace and account.
                # An equal ID on a peer account is not a shared transfer ID;
                # every source preserves only its proven endpoint movement.
                tid=uid()
                save_transaction(db,tid,p,'import',f"{meta['parser_version']} 原始记录；批次 {batch_id}",actor='importer')
                result={'status':'mapped','transaction_id':tid,'message':'已新增业务；未分类消费已计入费用。' if p['kind']=='expense' and p.get('category_code')=='5016' else '已新增业务。'}
                created=True
                advance(db)
                if meta['source_namespace']=='standard_csv_v1':
                    db.execute('INSERT OR IGNORE INTO source_identity VALUES(?,?,?,?)',(batch['account_id'],item['source_id'],row['fingerprint'],tid))
            _persist_result(db,batch,row,item,result,created)
            db.execute('RELEASE import_business')
        except DomainError as exc:
            db.execute('ROLLBACK TO import_business')
            db.execute('RELEASE import_business')
            _persist_result(db,batch,row,item,{'status':'failed',**_error(exc)})
        db.execute('UPDATE import_batches SET revision_after=?,updated_at=? WHERE id=?',(revision(db),now(),batch_id))
        return True


def process_batch(store,batch_id,body,action='apply'):
    _validate_command(body)
    # Validation/cache lookup precedes lock allocation and also permits reading
    # an already completed receipt while a later command is processing.
    with store.read() as db:
        _,cached=operation(db,body.get('request_id'),'import_batch_'+action+':'+batch_id,body)
        if cached is not None:
            return cached
    with _command_owner(store,batch_id,body,action) as cached:
        if cached is not None:
            return cached
        return _process_batch(store,batch_id,body,action)


def _process_batch(store,batch_id,body,action='apply'):
    limit=_validate_command(body)
    command='import_batch_'+action+':'+batch_id
    with store.write() as db:
        fingerprint,cached=operation(db,body.get('request_id'),command,body)
        if cached is not None:
            return cached
        batch=_get(db,batch_id)
        run=db.execute('SELECT * FROM import_batch_runs WHERE request_id=?',(body['request_id'],)).fetchone()
        if run and (run['fingerprint']!=fingerprint or run['action']!=command or run['batch_id']!=batch_id):
            raise DomainError('同一 request_id 已用于不同批次命令。','idempotency_conflict',409)
        if not run:
            if batch['version']!=body['expected_version']:
                raise DomainError('批次版本已变化，请刷新后重试。','version_conflict',409)
            if batch['file_status']!='preview_ready':
                raise DomainError('文件尚未成功解析，不能确认导入。','parse_not_ready',409)
            allowed={'awaiting_confirmation'} if action=='apply' else {'partial','processing','cancelled'}
            if batch['status'] not in allowed:
                raise DomainError('当前批次状态不接受该操作。','batch_state_conflict',409)
            db.execute('INSERT INTO import_batch_runs VALUES(?,?,?,?,?)',(body['request_id'],batch_id,command,fingerprint,now()))
            if action=='resume':
                db.execute("UPDATE import_row_results SET status='pending',error_code=NULL,message=NULL WHERE observation_id IN (SELECT id FROM import_observations WHERE batch_id=?) AND status='failed'",(batch_id,))
            db.execute("UPDATE import_batches SET status='processing',version=version+1,updated_at=? WHERE id=?",(now(),batch_id))
    # No shared DB connection or write lock spans more than one business row.
    for _ in range(limit):
        if not _apply_one(store,batch_id):
            break
        # Unexpected failures intentionally propagate: committed rows and the
        # persisted run remain resumable; no fabricated success receipt exists.
    with store.write() as db:
        # A simultaneous retry can finish this same durable run while our last
        # row commits. Reuse its receipt before changing version or inserting a
        # second operations row.
        _,cached=operation(db,body['request_id'],command,body)
        if cached is not None:
            return cached
        batch=_get(db,batch_id); rows=_rows(db,batch_id)
        pending=any(r['status'] in {'pending','failed'} for r in rows)
        status='cancelled' if batch['status']=='cancelled' else 'partial' if pending else 'completed'
        db.execute('UPDATE import_batches SET status=?,version=version+1,revision_after=?,updated_at=? WHERE id=?',(status,revision(db),now(),batch_id))
        dto=batch_dto(db,batch_id); counts=dto['counts']['source_rows']
        db.execute('UPDATE imports SET status=?,imported_count=?,duplicate_count=?,rejected_count=?,errors=? WHERE id=?',(status,dto['counts']['business']['new'],counts['duplicate'],counts['invalid']+counts['unresolved']+counts['failed'],encode([{'row':r['row_number'],'message':r['message']} for r in rows if r['error_code']]),batch_id))
        return finish_operation(db,body['request_id'],command,fingerprint,{'batch':dto,**common(db)})


def cancel_batch(store,batch_id,body):
    _validate_command(body)
    action='import_batch_cancel:'+batch_id
    with store.write() as db:
        fingerprint,cached=operation(db,body.get('request_id'),action,body)
        if cached is not None: return cached
        batch=_get(db,batch_id)
        if batch['version']!=body['expected_version']:
            raise DomainError('批次版本已变化，请刷新后重试。','version_conflict',409)
        if batch['status'] not in {'awaiting_confirmation','processing','partial'}:
            raise DomainError('当前批次不能取消。','batch_state_conflict',409)
        db.execute("UPDATE import_batches SET status='cancelled',version=version+1,updated_at=? WHERE id=?",(now(),batch_id))
        return finish_operation(db,body['request_id'],action,fingerprint,{'batch':batch_dto(db,batch_id),**common(db)})


def register_routes(app,store):
    @app.get('/api/v1/import-batches/formats')
    def formats():
        return {'items':import_parsers.FORMATS,'max_upload_bytes':import_parsers.MAX_UPLOAD,'max_rows':import_parsers.MAX_ROWS}

    @app.get('/api/v1/import-batches')
    def batches(limit:int=50,offset:int=0):
        if not 1<=limit<=100 or offset<0:
            raise DomainError('分页 limit 须为 1–100，offset 不可为负。')
        with store.read() as db:
            ids=[r['id'] for r in db.execute('SELECT id FROM import_batches ORDER BY created_at DESC,id LIMIT ? OFFSET ?',(limit,offset))]
            return {'items':[batch_dto(db,i) for i in ids],'total':db.execute('SELECT COUNT(*) FROM import_batches').fetchone()[0],'limit':limit,'offset':offset,**common(db)}

    @app.post('/api/v1/import-batches')
    async def preview(file:UploadFile=File(...),account_id:str=Form(...),request_id:str=Form(...)):
        content=await file.read(import_parsers.MAX_UPLOAD+1)
        from starlette.concurrency import run_in_threadpool
        return await run_in_threadpool(create_preview,store,content,file.filename,account_id,request_id)

    @app.get('/api/v1/import-batches/{batch_id}')
    def detail(batch_id:str,limit:int=100,offset:int=0):
        return get_batch(store,batch_id,limit,offset)

    @app.post('/api/v1/import-batches/{batch_id}/apply')
    async def apply(batch_id:str,request:Request):
        body=await request.json()
        from starlette.concurrency import run_in_threadpool
        return await run_in_threadpool(process_batch,store,batch_id,body,'apply')

    @app.post('/api/v1/import-batches/{batch_id}/resume')
    async def resume(batch_id:str,request:Request):
        body=await request.json()
        from starlette.concurrency import run_in_threadpool
        return await run_in_threadpool(process_batch,store,batch_id,body,'resume')

    @app.post('/api/v1/import-batches/{batch_id}/cancel')
    async def cancel(batch_id:str,request:Request):
        return cancel_batch(store,batch_id,await request.json())
