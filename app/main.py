"""Independent local API for the first executable v0.4 vertical slice."""
from __future__ import annotations

import csv
import io
import json
import os
from datetime import date
from pathlib import Path

from fastapi import FastAPI, File, Form, Request, UploadFile
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from starlette.exceptions import HTTPException as StarletteHTTPException

from .domain import CHART, DomainError, KINDS, minor, money, major_to_minor, valid_date, month_bounds, validate_transaction, review_reasons, ledger, explainable, movements
from .storage import Store, accounts, transactions, now, uid, encode, digest, revision, advance, operation, finish_operation, save_transaction, raw_payload
from .reports import common, coverage, account_dtos, transaction_dto, effects, report, budget_projection
from .security import install_security
from . import historical
from .transaction_search import filter_transactions, business_kind
from . import vouchers, report_snapshots
from . import catalog, budget_plans

PARSER_VERSION='standard_csv_v1'
CSV_COLUMNS={'source_id','date','description','amount','kind','category_code','counterparty_account_id'}
MAX_UPLOAD=10*1024*1024


def body_object(body, allowed):
    if not isinstance(body,dict): raise DomainError('请求正文须为 JSON 对象。')
    if set(body)-set(allowed): raise DomainError('不支持的字段：'+', '.join(sorted(set(body)-set(allowed))))
    return body


def expected(body,current):
    if type(body.get('expected_version')) is not int or body['expected_version']!=current:
        raise DomainError('数据版本已变化，请刷新后重试。','version_conflict',409)


def import_dto(row):
    r=dict(row); r['errors']=json.loads(r['errors']); return r


def create_app(data_dir=None, token=None, ai_token=None, web_dir=None):
    directory=Path(data_dir or os.environ.get('FINANCE_DATA_DIR') or Path(__file__).resolve().parent.parent/'data'/'app')
    store=Store(directory)
    app=FastAPI(title='个人财务管理 · 本地第一批',version='0.5.0-dev.2',docs_url=None,redoc_url=None,openapi_url=None)
    app.state.store=store
    app.state.ai_token = ai_token or os.environ.get("FINANCE_AI_TOKEN")
    install_security(app,token,ai_token=app.state.ai_token)

    @app.exception_handler(DomainError)
    async def domain_error(request,exc):
        return JSONResponse({'error':{'code':exc.code,'message':exc.message}},status_code=exc.status)

    @app.exception_handler(RequestValidationError)
    async def request_error(request,exc):
        return JSONResponse({'error':{'code':'validation_error','message':'请求字段、类型或文件无效。'}},status_code=422)

    @app.exception_handler(json.JSONDecodeError)
    async def json_error(request,exc):
        return JSONResponse({'error':{'code':'validation_error','message':'请求正文不是有效 JSON。'}},status_code=422)

    from .budget import BudgetError
    @app.exception_handler(BudgetError)
    async def budget_error(request,exc):
        return JSONResponse({'error':{'code':exc.code,'message':exc.message}},status_code=422)

    @app.exception_handler(StarletteHTTPException)
    async def http_error(request,exc):
        return JSONResponse({'error':{'code':'not_found' if exc.status_code==404 else 'http_error','message':'请求资源不存在。' if exc.status_code==404 else str(exc.detail)}},status_code=exc.status_code)

    @app.get('/api/v1/status')
    def get_status():
        with store.read() as db:
            txs=transactions(db)
            available=sorted({t['date'][:7] for t in txs.values() if t.get('date') and historical.scoped(t)})
            return {'service':'personal-finance-local','schema_version':db.execute("SELECT value FROM metadata WHERE key='schema_version'").fetchone()[0],'transaction_count':len(txs),'account_count':db.execute('SELECT COUNT(*) FROM accounts').fetchone()[0],'import_count':db.execute('SELECT COUNT(*) FROM imports').fetchone()[0],'capabilities':{'standard_csv':True,'native_bank_import':True,'native_cmb_credit_pdf':True,'import_preview_resume':True,'full_backup_restore':True,'coverage_declarations':True,'ai_queue':True,'mcp_classification':bool(app.state.ai_token),'investments':False,'ai':False,'remote_access':False,'report_export':False,'historical_migration':True,'budget_actuals':'explicit_consumption_and_payment_links','historical_editing':True,'voucher_browser':True,'dimension_catalog':True,'report_snapshots':True,'logical_json_export':True,'budget_drafts':True,'classification_rules':True,'batch_classification':True,'consumption_analysis':True,'reimbursement_normalization':True,'analysis_snapshots':True},'limitations':['历史账本支持直接修改与保留历史；配对金额同步、退款科目继承。原生格式目前支持招商信用卡文本 PDF；支付宝商品明细仅补证据。投资维护、融资本息重新拆分、报表导出与远程访问尚未交付。','当前报表仅为已知记录汇总；覆盖范围按账户、月份和口径确认。','AI 任务仅限新导入的未分类消费；需由已配置的 Codex MCP 主动领取，不会在上传后自动启动模型。'],'available_months':available,'budget_years':[r[0] for r in db.execute("SELECT year FROM budget_states WHERE status='adopted' ORDER BY year")],'migration':historical.migration_summary(db),**common(db,txs)}

    @app.get('/api/v1/chart-accounts')
    def get_chart():
        with store.read() as db: return {'items':catalog.chart_accounts(db),**common(db)}

    @app.get('/api/v1/catalog')
    def get_catalog():
        with store.read() as db: return {**catalog.summary(db),**common(db)}

    @app.get('/api/v1/data/export')
    def get_data_export():
        from .data_export import export_bytes
        with store.read() as db:
            content=export_bytes(db)
        return Response(content, media_type='application/json', headers={
            'Content-Disposition': 'attachment; filename="finance-data.json"',
            'Cache-Control': 'no-store',
        })

    @app.get('/api/v1/accounts')
    def get_accounts():
        with store.read() as db: return {'items':account_dtos(db),**common(db)}

    @app.post('/api/v1/accounts')
    async def post_account(request:Request):
        body=body_object(await request.json(),{'request_id','name','kind','currency','opening_balance_minor','opening_date','evidence'})
        with store.write() as db:
            fingerprint,cached=operation(db,body.get('request_id'),'account_create',body)
            if cached is not None: return cached
            name=body.get('name')
            if not isinstance(name,str) or not name.strip() or len(name)>100: raise DomainError('账户名须为 1–100 字符。')
            if not isinstance(body.get('kind'),str) or body['kind'] not in {'cash','credit'}: raise DomainError('本批账户类型仅支持 cash 和 credit。','unsupported')
            if body.get('currency','CNY')!='CNY': raise DomainError('本批仅支持 CNY。','unsupported')
            opening=body.get('opening_balance_minor'); opening_date=body.get('opening_date'); evidence=body.get('evidence','')
            if not isinstance(evidence,str) or len(evidence)>2000: raise DomainError('期初证据说明须为不超过 2000 字的文本。')
            if opening_date=='': opening_date=None
            if opening is not None:
                opening=minor(opening,'期初余额'); valid_date(opening_date)
                if not isinstance(evidence,str) or not evidence.strip(): raise DomainError('设置已知期初须填写余额证据说明。')
                if body['kind']=='credit' and opening<0: raise DomainError('信用账户期初请用非负负债金额；期初溢缴款入口尚未支持。','unsupported')
            elif opening_date is not None: raise DomainError('未知期初不能单独填写期初日。')
            aid=uid()
            db.execute('INSERT INTO accounts VALUES(?,?,?,?,?,?,?,?,?)',(aid,name.strip(),body['kind'],'CNY',opening,opening_date,evidence,1,now()))
            advance(db)
            response={'account':next(a for a in account_dtos(db) if a['id']==aid),**common(db)}
            return finish_operation(db,body['request_id'],'account_create',fingerprint,response)

    @app.get('/api/v1/transactions')
    def get_transactions(month:str|None=None,account_id:str|None=None,review_only:bool=False,limit:int=100,offset:int=0,data_revision:int|None=None,
                         q:str|None=None,category_code:str|None=None,kind:str|None=None,start_date:str|None=None,end_date:str|None=None,scope:str='all',ids:str|None=None):
        if not 1<=limit<=200 or offset<0: raise DomainError('分页 limit 须为 1–200，offset 不可为负。')
        if month: month_bounds(month)
        with store.read() as db:
            if data_revision is not None and data_revision!=revision(db): raise DomainError('查询修订已过期，请从第一页刷新。','revision_conflict',409)
            acc=accounts(db); txs=transactions(db)
            chosen=filter_transactions(db,txs,acc,month=month,account_id=account_id,review_only=review_only,q=q,category_code=category_code,kind=kind,start_date=start_date,end_date=end_date,scope=scope,ids=ids)
            return {'items':[transaction_dto(db,p,acc,txs) for p in chosen[offset:offset+limit]],'total':len(chosen),'limit':limit,'offset':offset,'filter_options':{'kinds':sorted({business_kind(t) for t in txs.values()})},**common(db,txs)}

    @app.get('/api/v1/transactions/{tid}')
    def get_transaction(tid:str):
        with store.read() as db:
            txs=transactions(db); acc=accounts(db)
            if tid not in txs: raise DomainError('交易不存在。','not_found',404)
            p=txs[tid]
            sources=[]
            for row in db.execute('SELECT s.*,i.filename,i.sha256,i.parser_version FROM source_rows s JOIN imports i ON i.id=s.import_id WHERE s.transaction_id=? ORDER BY i.created_at,s.row_number',(tid,)):
                sources.append({'id':row['id'],'filename':row['filename'],'sha256':row['sha256'],'row_number':row['row_number'],'raw':json.loads(row['raw']),'parser_version':row['parser_version']})
            history=[{'version':r['version'],'action':r['action'],'actor':r['actor'],'reason':r['reason'],'created_at':r['created_at'],'before':json.loads(r['before_payload']) if r['before_payload'] else None,'after':json.loads(r['after_payload'])} for r in db.execute('SELECT * FROM transaction_versions WHERE transaction_id=? ORDER BY version',(tid,))]
            effect=effects(p,acc)
            for year in {int(p[key][:4]) for key in ('date','cash_date') if p.get(key)}:
                budget=budget_plans.adopted_row(db,year)
                if not budget: continue
                computed,meta=budget_projection(db,json.loads(budget['payload']))
                linked=next((u for u in computed['usage'] + computed.get('unallocated_usage', []) if u['id']==tid),None)
                if linked: effect['budget_usage_minor']=linked['amount_minor']
                elif p['kind'] in {'income','reimbursement','family_support','repayment','transfer'}: effect['budget_usage_minor']='0'
            return {'transaction':transaction_dto(db,p,acc,txs),'sources':sources,'history':history,'ledger_lines':ledger(p,acc,txs),'effects':effect,'read_only':False,'effects_basis':effect.get('basis','当前已确认交易；现金按独立收付日；预算仅取已保存预算中明确关联的消费事实。'),**common(db,txs)}

    @app.post('/api/v1/transactions/{tid}/edit')
    async def edit_transaction(tid:str,request:Request):
        body=body_object(await request.json(),{'request_id','expected_version','payload','reason'})
        with store.write() as db:
            action='transaction_edit:'+tid
            fingerprint,cached=operation(db,body.get('request_id'),action,body)
            if cached is not None: return cached
            txs=transactions(db)
            if tid not in txs: raise DomainError('交易不存在。','not_found',404)
            current=txs[tid]; expected(body,current['version'])
            if historical.is_historical(current):
                changes=body_object(body.get('payload'),historical.EDIT_FIELDS)
                reason=body.get('reason','')
                if not isinstance(reason,str) or len(reason)>2000:
                    raise DomainError('修改理由格式无效。')
                before_values={**current,'business_amount_minor':str(historical.business_amount(current)),'historical_event':current['historical']['event'],'in_scope':current['historical']['in_scope']}
                if any(key in changes and changes[key]!=before_values.get(key) for key in ('business_amount_minor','historical_event','date','cash_date','in_scope','refund_of','account_id')) and not reason.strip():
                    raise DomainError('金额、性质、日期、范围或关联变更需要简短依据，原始凭据会继续保留。')
                planned=historical.plan_edit(current,changes,txs,accounts(db))
                marker={'id':body['request_id'],'members':sorted(planned)}
                for changed_id,p in planned.items():
                    validate_budget_line(db,p,txs.get(changed_id))
                    from .domain import validate_reimbursement_dates
                    validate_reimbursement_dates(p,txs.get(changed_id))
                    if historical.is_historical(p):
                        p['historical']['edit_group']=marker
                    else:
                        # A historical parent may have a native refund child.
                        # Keep the group in history without adding native fields.
                        p=validate_transaction(p,accounts(db),{**txs,**planned},changed_id)
                    save_transaction(db,changed_id,p,'edit',reason)
                advance(db)
                response={'transaction':transaction_dto(db,transactions(db)[tid]),'affected_transaction_ids':sorted(planned),**common(db)}
                return finish_operation(db,body['request_id'],action,fingerprint,response)
            changes=body_object(body.get('payload'),{'date','cash_date','description','kind','category_code','amount_minor','counterparty_account_id','refund_of','budget_line_id','counterparty_confirmed'})
            reason=body.get('reason','')
            if not isinstance(reason,str) or len(reason)>2000: raise DomainError('修改理由格式无效。')
            if any(k in changes and changes[k]!=current.get(k) for k in ('amount_minor','kind','counterparty_account_id','refund_of','counterparty_confirmed','cash_date')) and not reason.strip():
                raise DomainError('金额、性质、资金日期或关联变更需要简短依据说明。')
            p=validate_transaction({**raw_payload(current),**changes},accounts(db),txs,tid)
            validate_budget_line(db,p,current)
            save_transaction(db,tid,p,'edit',reason)
            advance(db)
            response={'transaction':transaction_dto(db,transactions(db)[tid]),**common(db)}
            return finish_operation(db,body['request_id'],action,fingerprint,response)

    @app.post('/api/v1/transactions/{tid}/undo')
    async def undo_transaction(tid:str,request:Request):
        body=body_object(await request.json(),{'request_id','expected_version','reason'})
        with store.write() as db:
            action='transaction_undo:'+tid
            fingerprint,cached=operation(db,body.get('request_id'),action,body)
            if cached is not None: return cached
            txs=transactions(db)
            if tid not in txs: raise DomainError('交易不存在。','not_found',404)
            current=txs[tid]; expected(body,current['version'])
            last=db.execute('SELECT * FROM transaction_versions WHERE transaction_id=? AND version=?',(tid,current['version'])).fetchone()
            if last['action']!='edit' or not last['before_payload']: raise DomainError('当前版本没有可直接撤销的人工修改。','nothing_to_undo',409)
            if historical.is_historical(current):
                planned=historical.undo_plan(db,current,txs)
                reason=body.get('reason','撤销上次修改')
                if not isinstance(reason,str) or len(reason)>2000: raise DomainError('修改理由格式无效。')
                for changed_id,p in planned.items():
                    validate_budget_line(db,p,txs.get(changed_id))
                    save_transaction(db,changed_id,p,'undo',reason)
                advance(db)
                response={'transaction':transaction_dto(db,transactions(db)[tid]),'affected_transaction_ids':sorted(planned),**common(db)}
                return finish_operation(db,body['request_id'],action,fingerprint,response)
            p=validate_transaction(json.loads(last['before_payload']),accounts(db),txs,tid)
            validate_budget_line(db,p)
            reason=body.get('reason','撤销上次修改')
            if not isinstance(reason,str) or len(reason)>2000: raise DomainError('修改理由格式无效。')
            save_transaction(db,tid,p,'undo',reason)
            advance(db)
            response={'transaction':transaction_dto(db,transactions(db)[tid]),**common(db)}
            return finish_operation(db,body['request_id'],action,fingerprint,response)

    @app.get('/api/v1/imports')
    def get_imports():
        with store.read() as db:
            return {'items':[import_dto(r) for r in db.execute('SELECT * FROM imports ORDER BY created_at DESC LIMIT 100')],**common(db)}

    @app.post('/api/v1/imports')
    async def post_import(file:UploadFile=File(...),account_id:str=Form(...),request_id:str=Form(...)):
        content=await file.read(MAX_UPLOAD+1)
        if len(content)>MAX_UPLOAD: raise DomainError('文件超过 10 MiB 上限。','upload_too_large',413)
        filename=Path(file.filename or 'upload.csv').name[:200]
        sha=store.archive(content,filename)
        identity={'sha256':sha,'account_id':account_id,'parser_version':PARSER_VERSION}
        with store.write() as db:
            fingerprint,cached=operation(db,request_id,'import',identity)
            if cached is not None: return cached
            acc=accounts(db)
            if account_id not in acc: raise DomainError('请先选择已有账户。')
            existing=db.execute('SELECT * FROM imports WHERE sha256=? AND account_id=? AND parser_version=?',(sha,account_id,PARSER_VERSION)).fetchone()
            if existing:
                return finish_operation(db,request_id,'import',fingerprint,{'import':import_dto(existing),'reused':True,**common(db)})
            db.execute('INSERT OR IGNORE INTO artifacts VALUES(?,?,?,?)',(sha,filename,len(content),now()))
            iid=uid(); errors=[]; rows=[]
            try:
                source=content.decode('utf-8-sig')
                reader=csv.DictReader(io.StringIO(source,newline=''))
                if reader.fieldnames is None or not CSV_COLUMNS.issubset(reader.fieldnames) or len(reader.fieldnames)!=len(set(reader.fieldnames)):
                    raise DomainError('只接受标准 CSV 模板；需要 source_id,date,description,amount,kind,category_code,counterparty_account_id 列。原生账单请先转换，原件已归档。','unsupported_format')
                if set(reader.fieldnames)-CSV_COLUMNS-{'refund_of','budget_line_id'}:
                    raise DomainError('CSV 存在未支持的列，已停止解释。','unsupported_format')
                for row in reader:
                    if len(rows)>=10000: raise DomainError('单批最多 10000 行。','batch_too_large')
                    rows.append((reader.line_num,row))
            except (UnicodeDecodeError,csv.Error,DomainError) as exc:
                errors=[{'row':0,'message':getattr(exc,'message','文件须为 UTF-8 编码的标准 CSV。')}]; rows=[]
            # The source rows and parsing failures stay immutable after this transaction.
            db.execute('INSERT INTO imports VALUES(?,?,?,?,?,?,?,?,?,?,?,?)',(iid,sha,filename,account_id,PARSER_VERSION,'received',len(rows),0,0,0,'[]',now()))
            imported=duplicates=rejected=0
            txs=transactions(db)
            for line,row in rows:
                sid=uid(); tid=None; error=None
                try:
                    if None in row or any(v is None for v in row.values()): raise DomainError('CSV 列数与表头不一致。')
                    source_id=row['source_id'].strip()
                    if not source_id or len(source_id)>160: raise DomainError('source_id 须为 1–160 字符且在此账户稳定唯一；不可用同日同额代替。')
                    p={'source_id':source_id,'date':row['date'].strip(),'description':row['description'],'account_id':account_id,'amount_minor':major_to_minor(row['amount'].strip()),'currency':'CNY','kind':row['kind'].strip() or 'unknown','category_code':row['category_code'].strip() or None,'counterparty_account_id':row['counterparty_account_id'].strip() or None,'refund_of':row.get('refund_of') or None,'budget_line_id':row.get('budget_line_id') or None}
                    fp=digest(p)
                    identity_row=db.execute('SELECT * FROM source_identity WHERE account_id=? AND source_id=?',(account_id,source_id)).fetchone()
                    if identity_row:
                        if identity_row['fingerprint']!=fp: raise DomainError('相同 source_id 内容有变化；原判断未覆盖，请在交易详情人工更正。','source_conflict',409)
                        tid=identity_row['transaction_id']; duplicates+=1
                    else:
                        p=validate_transaction(p,acc,txs)
                        validate_budget_line(db,p)
                        tid=uid(); version=save_transaction(db,tid,p,'import','标准 CSV 原始记录',actor='importer'); imported+=1
                        from .classification_rules import apply_imported
                        apply_imported(db,tid)
                        txs[tid]={**p,'id':tid,'version':version}
                        db.execute('INSERT INTO source_identity VALUES(?,?,?,?)',(account_id,source_id,fp,tid))
                except DomainError as exc:
                    rejected+=1; error=exc.message; errors.append({'row':line,'message':error}); tid=None
                db.execute('INSERT INTO source_rows VALUES(?,?,?,?,?,?,?)',(sid,iid,line,row.get('source_id'),encode(row),tid,error))
            status='failed' if errors and not imported and not duplicates else 'partially_processed' if errors else 'processed'
            db.execute('UPDATE imports SET status=?,imported_count=?,duplicate_count=?,rejected_count=?,errors=? WHERE id=?',(status,imported,duplicates,rejected,encode(errors),iid))
            advance(db)
            response={'import':import_dto(db.execute('SELECT * FROM imports WHERE id=?',(iid,)).fetchone()),**common(db)}
            return finish_operation(db,request_id,'import',fingerprint,response)

    @app.get('/api/v1/reports')
    def get_reports(month:str|None=None):
        with store.read() as db: return report(db,month or date.today().strftime('%Y-%m'))

    @app.get('/api/v1/vouchers')
    def get_vouchers(month:str|None=None,q:str|None=None,account_id:str|None=None,
                     category_code:str|None=None,status:str|None=None,limit:int=50,offset:int=0,
                     data_revision:int|None=None):
        with store.read() as db:
            return {**vouchers.query(db,month=month,q=q,account_id=account_id,category_code=category_code,
                                     status=status,limit=limit,offset=offset,data_revision=data_revision),**common(db)}

    @app.get('/api/v1/vouchers/{tid}')
    def get_voucher(tid:str):
        with store.read() as db: return {**vouchers.detail(db,tid),**common(db)}

    @app.get('/api/v1/report-snapshots')
    def get_report_snapshots(month:str|None=None,limit:int=50,offset:int=0):
        with store.read() as db: return {**report_snapshots.list_snapshots(db,month,limit,offset),**common(db)}

    @app.post('/api/v1/report-snapshots')
    async def post_report_snapshot(request:Request):
        body=body_object(await request.json(),{'request_id','month'})
        month_bounds(body.get('month'))
        with store.write() as db:
            fingerprint,cached=operation(db,body.get('request_id'),'report_snapshot',body)
            if cached is not None: return cached
            snapshot=report_snapshots.capture(db,body['month'])
            response={'snapshot':snapshot,**common(db)}
            return finish_operation(db,body['request_id'],'report_snapshot',fingerprint,response)

    @app.get('/api/v1/report-snapshots/{sid}')
    def get_report_snapshot(sid:str):
        with store.read() as db: return {'snapshot':report_snapshots.get(db,sid),**common(db)}

    @app.get('/api/v1/budgets')
    def get_budget(year:int):
        if not 1<=year<=9999: raise DomainError('预算年份须在 1–9999。')
        with store.read() as db:
            row=budget_plans.adopted_row(db,year)
            if not row: return {'version':0,'latest_version':budget_plans.latest_version(db,year),'budget':None,'actuals_connected':False,**common(db)}
            computed,meta=budget_projection(db,json.loads(row['payload']))
            return {'version':row['version'],'latest_version':budget_plans.latest_version(db,year),'status':'adopted','budget':computed,'migration':historical.budget_provenance(db,year,row['version']),**meta,**common(db)}

    @app.post('/api/v1/budgets')
    async def post_budget(request:Request):
        body=await request.json()
        with store.write() as db:
            return budget_plans.create_draft(db,body)

    @app.get('/api/v1/operations/{request_id}')
    def get_operation(request_id:str):
        with store.read() as db:
            row=db.execute('SELECT response FROM operations WHERE request_id=?',(request_id,)).fetchone()
            if not row: raise DomainError('请求记录不存在。','not_found',404)
            return json.loads(row['response'])

    from . import maintenance, coverage as coverage_service, ai_work, imports_v2, classification_rules, analysis
    analysis.register_routes(app,store)
    classification_rules.register_routes(app,store)
    budget_plans.register_routes(app,store)
    maintenance.register_routes(app,store)
    coverage_service.register_routes(app,store)
    ai_work.register_routes(app,store)
    imports_v2.register_routes(app,store)

    @app.get('/api/v1/{unsupported:path}')
    def unsupported_api(unsupported:str):
        raise DomainError('此接口尚未实现。','not_found',404)

    dist=Path(web_dir or os.environ.get('FINANCE_WEB_DIST') or Path(__file__).resolve().parent.parent/'web'/'dist')
    if dist.exists(): app.mount('/',StaticFiles(directory=dist,html=True),name='web')
    return app


def validate_budget_line(db,p,previous=None):
    if not p.get('budget_line_id'): return
    candidate_years={int(p[key][:4]) for key in ('date','cash_date') if p.get(key)}
    matches=[]
    for year in candidate_years:
        row=budget_plans.adopted_row(db,year)
        if row: matches.extend(line for line in json.loads(row['payload'])['lines'] if line['id']==p['budget_line_id'] and (p.get('cash_date' if line.get('actual_basis')=='payment' else 'date') or '').startswith(str(year)))
    if not matches:
        raise DomainError('预算行不存在于交易发生年的当前预算，请先保存预算或清空关联。')
    line=matches[0]
    from .domain import validate_budget_binding
    validate_budget_binding(p,line,previous)
    if historical.is_historical(p):
        h=p['historical']
        if line.get('actual_basis')=='payment':
            if not h['cash_included'] or not int(h.get('cash_minor') or '0') or not (historical.expense_value(p) or h['event'] in {'信用还款','房贷还款待拆分'}):
                raise DomainError('付款预算需要实际现金收付记录；信用消费、分期出账和已抵消的另一端不能代替付款。')
        elif not historical.expense_value(p):
            raise DomainError('消费预算只能关联费用或已确认负费用；还款与分期出账不重复占用。')
    elif p['kind'] not in ({'expense','refund','repayment'} if line.get('actual_basis')=='payment' else {'expense','refund'}):
        raise DomainError('该性质不能映射此预算口径。','unsupported')
    elif line.get('actual_basis')=='payment':
        acc=accounts(db)
        if not explainable(p) or not sum(value for aid,value in movements(p).items() if acc[aid]['kind']=='cash'):
            raise DomainError('付款预算需要已确认的实际现金收付；信用账单金额不能代替付款。')
