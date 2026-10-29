"""Synthetic-only import lifecycle, source safety and strict adapter tests."""
import csv
import hashlib
import io
import json
import sqlite3
from pathlib import Path
from uuid import uuid4
import xml.etree.ElementTree as ET
from concurrent.futures import ThreadPoolExecutor
import threading

import pytest
from fastapi import FastAPI
from fastapi.responses import JSONResponse
from fastapi.testclient import TestClient

from app import import_parsers, imports_v2
from app.domain import DomainError
from app.import_parsers.cmb_credit import parse_text_pages
from app.storage import Store, accounts, advance, digest, encode, now, revision, save_transaction, transactions


FIXTURE = Path(__file__).parent/'fixtures'/'cmb_credit_statement.txt'


def setup(tmp_path,kind='cash'):
    store=Store(tmp_path)
    with store.write() as db:
        imports_v2.initialize(db)
        db.execute('INSERT INTO accounts VALUES(?,?,?,?,?,?,?,?,?)',('a','合成账户',kind,'CNY',None,None,None,1,now()))
    return store


def csv_bytes(rows):
    output=io.StringIO(); writer=csv.writer(output)
    writer.writerow(['source_id','date','description','amount','kind','category_code','counterparty_account_id'])
    for row in rows:
        writer.writerow(row+['']*(7-len(row)))
    return output.getvalue().encode()


def preview(store,rows):
    return imports_v2.create_preview(store,csv_bytes(rows),'synthetic.csv','a',str(uuid4()))['batch']


def command(batch,limit=100):
    return {'request_id':str(uuid4()),'expected_version':batch['version'],'limit':limit}


def apply(store,batch,limit=100,action='apply'):
    return imports_v2.process_batch(store,batch['id'],command(batch,limit),action)['batch']


def test_preview_is_not_booking_and_sources_are_immutable(tmp_path):
    store=setup(tmp_path)
    before=0
    batch=preview(store,[['s1','2027-01-02','合成商品','-12.50','expense']])
    assert batch['status']=='awaiting_confirmation' and batch['can_apply']
    assert batch['summary']['outflow_minor']=='1250'
    with store.read() as db:
        assert revision(db)==before
        assert transactions(db)=={}
        assert db.execute('SELECT COUNT(*) FROM import_observations').fetchone()[0]==1
    with pytest.raises(sqlite3.IntegrityError,match='immutable'):
        with store.write() as db:
            db.execute("UPDATE import_observations SET observation='{}'")
    assert hashlib.sha256((store.evidence/batch['sha256']).read_bytes()).hexdigest()==batch['sha256']
    completed=apply(store,batch)
    assert completed['counts']['business']['new']==1
    assert completed['counts']['tasks']['unclassified']==1
    assert completed['counts']['tasks']['waiting_for_codex']==0
    assert completed['revision_after']==1
    with store.read() as db:
        transaction=next(iter(transactions(db).values()))
        assert transaction['category_code']=='5016'
        assert db.execute('SELECT transaction_id FROM source_rows').fetchone()[0]==transaction['id']


def test_partial_cancel_restart_resume_only_remaining(tmp_path):
    store=setup(tmp_path)
    batch=preview(store,[[str(n),'2027-01-02','合成消费','-1.00','expense'] for n in range(3)])
    batch=apply(store,batch,1)
    assert batch['status']=='partial' and batch['counts']['source_rows']['pending']==2
    batch=imports_v2.cancel_batch(store,batch['id'],command(batch))['batch']
    assert batch['status']=='cancelled' and batch['revision_after']==1
    store=Store(tmp_path)
    batch=apply(store,batch,100,'resume')
    assert batch['status']=='completed' and batch['counts']['business']['new']==3
    with store.read() as db:
        assert len(transactions(db))==3
        assert revision(db)==3


def test_reimport_links_existing_source_and_retains_human_edit(tmp_path):
    store=setup(tmp_path)
    batch=apply(store,preview(store,[['same','2027-01-02','消费','-10.00','expense','5001']]))
    tid=batch['transaction_ids'][0]
    with store.write() as db:
        p=json.loads(db.execute('SELECT payload FROM transactions WHERE id=?',(tid,)).fetchone()[0]); p['category_code']='5004'
        save_transaction(db,tid,p,'edit','合成人工分类'); advance(db)
    # Different original bytes, same stable ID and source contents.
    content=csv_bytes([['same','2027-01-02','消费','-10.00','expense','5001']])+b'\n'
    again=imports_v2.create_preview(store,content,'another.csv','a',str(uuid4()))['batch']
    again=apply(store,again)
    assert again['counts']['business']['new']==0 and again['counts']['source_rows']['duplicate']==1
    with store.read() as db:
        assert len(transactions(db))==1 and transactions(db)[tid]['category_code']=='5004'
        assert db.execute('SELECT COUNT(*) FROM source_rows WHERE transaction_id=?',(tid,)).fetchone()[0]==2
        assert revision(db)==2
    conflict=apply(store,preview(store,[['same','2027-01-02','消费','-11.00','expense','5001']]))
    assert conflict['counts']['source_rows']['unresolved']==1
    assert imports_v2.get_batch(store,conflict['id'])['rows'][0]['error']['code']=='source_conflict'


def test_parser_revision_does_not_replace_previous_fact(tmp_path,monkeypatch):
    store=setup(tmp_path); payload=[['same','2027-01-02','消费','-10.00','expense','5001']]
    original=apply(store,preview(store,payload)); fn=import_parsers.parse
    def revised(*args):
        p=fn(*args); p['parser_version']='standard_csv_v2'; p['rows'][0]['payload']['description']='新版解释'; return p
    monkeypatch.setattr(import_parsers,'parse',revised)
    newer=apply(store,preview(store,payload))
    assert newer['counts']['source_rows']['unresolved']==1
    with store.read() as db:
        assert len(transactions(db))==1
        assert transactions(db)[original['transaction_ids'][0]]['description']=='消费'


def test_failed_business_rolls_back_every_write_and_can_retry(tmp_path,monkeypatch):
    store=setup(tmp_path); batch=preview(store,[['good','2027-01-02','成功','-1.00','expense'],['bad','2027-01-02','失败','-2.00','expense']]); original=imports_v2.save_transaction
    def failing(db,tid,p,*args,**kwargs):
        original(db,tid,p,*args,**kwargs)
        if p['source_id']=='bad': raise DomainError('合成提交失败','synthetic_failure',409)
    monkeypatch.setattr(imports_v2,'save_transaction',failing)
    batch=apply(store,batch)
    assert batch['status']=='partial' and batch['counts']['source_rows']['failed']==1
    with store.read() as db:
        assert len(transactions(db))==1 and revision(db)==1
        assert db.execute('SELECT COUNT(*) FROM transaction_versions').fetchone()[0]==1
        assert db.execute('SELECT COUNT(*) FROM source_rows').fetchone()[0]==1
    monkeypatch.setattr(imports_v2,'save_transaction',original)
    batch=apply(store,batch,action='resume')
    assert batch['counts']['business']['new']==2 and batch['status']=='completed'


def test_crash_recovers_same_operation_and_returns_idempotent_receipt(tmp_path,monkeypatch):
    store=setup(tmp_path); batch=preview(store,[['1','2027-01-02','甲','-1.00','expense'],['2','2027-01-02','乙','-2.00','expense']]); request=command(batch); original=imports_v2._apply_one; counter=0
    def crash(*args):
        nonlocal counter
        counter+=1
        if counter==2: raise RuntimeError('synthetic process interruption')
        return original(*args)
    monkeypatch.setattr(imports_v2,'_apply_one',crash)
    with pytest.raises(RuntimeError): imports_v2.process_batch(store,batch['id'],request)
    assert imports_v2.get_batch(store,batch['id'])['batch']['status']=='processing'
    monkeypatch.setattr(imports_v2,'_apply_one',original)
    response=imports_v2.process_batch(Store(tmp_path),batch['id'],request)
    assert response['batch']['counts']['business']['new']==2
    assert imports_v2.process_batch(store,batch['id'],request)==response
    with pytest.raises(DomainError,match='request_id'):
        imports_v2.process_batch(store,batch['id'],{**request,'limit':1})


def test_unknown_malformed_and_oversized_file_do_not_change_financial_revision(tmp_path):
    store=setup(tmp_path)
    bad=imports_v2.create_preview(store,b'unknown binary','unknown.xls','a',str(uuid4()))['batch']
    assert bad['file_status']=='unsupported' and not bad['can_apply']
    with pytest.raises(DomainError,match='尚未成功解析'): apply(store,bad)
    with pytest.raises(DomainError,match='10 MiB'):
        imports_v2.create_preview(store,b'x'*(import_parsers.MAX_UPLOAD+1),'large.csv','a',str(uuid4()))
    partial=preview(store,[['valid','2027-01-02','正常','-1.00','expense'],['bad','2027-02-30','坏日期','-1.00','expense']])
    partial=apply(store,partial)
    assert partial['counts']['source_rows']['invalid']==1 and partial['counts']['business']['new']==1
    with store.read() as db: assert revision(db)==1


def test_native_cmb_dates_directions_and_no_invented_peer():
    result=parse_text_pages([FIXTURE.read_text()],'a','a'*64)
    assert len(result['rows'])==6 and result['source_account_options']==['1111','2222']
    rows=result['rows']
    assert [r['payload']['kind'] for r in rows]==['repayment','unknown','refund','expense','expense','expense']
    assert rows[0]['payload']['amount_minor']=='10000'
    assert rows[-1]['payload']['date']=='2026-12-30'
    assert rows[1]['payload']['date']=='2027-01-10'
    assert all(r['payload']['counterparty_account_id'] is None for r in rows)
    assert all(r['identity_quality']=='observation' for r in rows)
    assert all('a'*64 in r['source_id'] for r in rows)
    with pytest.raises(DomainError):
        parse_text_pages([FIXTURE.read_text().replace('25.00 1111','25.005 1111')],'a','a'*64)
    with pytest.raises(DomainError):
        parse_text_pages(['unrecognized bank'],'a','a'*64)


def native_parser(monkeypatch):
    original=import_parsers.parse
    def synthetic(content,filename,account):
        if content.startswith(b'%PDF-synthetic'):
            return parse_text_pages([FIXTURE.read_text()],account,hashlib.sha256(content).hexdigest())
        return original(content,filename,account)
    monkeypatch.setattr(import_parsers,'parse',synthetic)


def test_native_equal_amount_rows_remain_distinct_and_cross_file_is_review_candidate(tmp_path,monkeypatch):
    native_parser(monkeypatch); store=setup(tmp_path,'credit')
    batch=imports_v2.create_preview(store,b'%PDF-synthetic-1','synthetic.pdf','a',str(uuid4()))['batch']; batch=apply(store,batch)
    assert batch['counts']['business']['new']==6
    again=imports_v2.create_preview(store,b'%PDF-synthetic-1','renamed.pdf','a',str(uuid4()))
    assert again['reused'] and again['batch']['id']==batch['id']
    other=imports_v2.create_preview(store,b'%PDF-synthetic-2','second-export.pdf','a',str(uuid4()))['batch']; other=apply(store,other)
    assert other['counts']['business']['new']==0 and other['counts']['source_rows']['unresolved']==6
    with store.read() as db:
        assert len(transactions(db))==6
        assert all(t['counterparty_account_id'] is None for t in transactions(db).values())


def alipay_xml(order='synthetic-order'):
    from scripts.alipay_purchase_details import REQUIRED_HEADERS,TAG
    root=ET.Element(TAG+'Workbook'); table=ET.SubElement(ET.SubElement(root,TAG+'Worksheet'),TAG+'Table')
    record={'创建时间':'2027-01-02 12:00:00','支付宝交易号':order,'商户订单号':'merchant-1','商品名称':'合成商品','订单金额(元)':'10.00','累计退款总额(元)':'2.00','优惠(元)':'0.00','交易状态':'成功','买卖标志':'买入','支付渠道':'合成信用卡','对方名称':'合成商户','银行订单号':'bank-1'}
    for values in [REQUIRED_HEADERS,[record.get(k,'') for k in REQUIRED_HEADERS]]:
        row=ET.SubElement(table,TAG+'Row')
        for value in values:
            ET.SubElement(ET.SubElement(row,TAG+'Cell'),TAG+'Data',{TAG+'Type':'String'}).text=value
    return ET.tostring(root,encoding='utf-8',xml_declaration=True)


def test_alipay_is_evidence_only_with_exact_order_link(tmp_path):
    store=setup(tmp_path)
    initial=apply(store,preview(store,[['payment','2027-01-02','商品','-10.00','expense','5001']]))
    tid=initial['transaction_ids'][0]
    with store.write() as db:
        # Synthetic prior channel evidence supplies the strong order identity.
        db.execute('INSERT INTO source_rows VALUES(?,?,?,?,?,?,?)',('channel',initial['id'],99,'channel-1',encode({'交易订单号':'synthetic-order'}),tid,None))
    before=initial['revision_after']
    batch=imports_v2.create_preview(store,alipay_xml(),'purchases.xls','a',str(uuid4()))['batch']; batch=apply(store,batch)
    assert batch['counts']['business']['new']==0 and batch['counts']['business']['evidence_added']==1
    assert batch['transaction_ids']==[tid] and batch['revision_after']==before
    unmatched=imports_v2.create_preview(store,alipay_xml('unmatched'),'purchases2.xls','a',str(uuid4()))['batch']; unmatched=apply(store,unmatched)
    assert unmatched['counts']['business']['new']==0 and unmatched['counts']['tasks']['needs_review']==1
    with store.read() as db: assert len(transactions(db))==1
    with pytest.raises(DomainError): import_parsers.parse(b'<!DOCTYPE root><root/>','bad.xls','a')


def test_route_contract_and_confirmation_required(tmp_path):
    store=setup(tmp_path); app=FastAPI()
    @app.exception_handler(DomainError)
    async def error(request,exc): return JSONResponse({'error':{'code':exc.code,'message':exc.message}},status_code=exc.status)
    imports_v2.register_routes(app,store)
    with TestClient(app) as client:
        response=client.post('/api/v1/import-batches',data={'account_id':'a','request_id':str(uuid4())},files={'file':('sample.csv',csv_bytes([['s','2027-01-02','合成','-1.00','expense']]),'text/csv')})
        assert response.status_code==200,response.text
        batch=response.json()['batch']; detail=client.get('/api/v1/import-batches/'+batch['id']).json()
        assert detail['rows'][0]['status']=='pending' and detail['data_revision']==0
        applied=client.post('/api/v1/import-batches/'+batch['id']+'/apply',json=command(batch))
        assert applied.status_code==200,applied.text
        assert applied.json()['batch']['counts']['business']['new']==1
        assert len(client.get('/api/v1/import-batches/formats').json()['items'])==3


@pytest.mark.parametrize('same_id',[True,False])
def test_account_local_ids_never_confirm_or_duplicate_peer_movement(tmp_path,same_id):
    from app.domain import movements
    store=setup(tmp_path,'cash')
    with store.write() as db:
        db.execute('INSERT INTO accounts VALUES(?,?,?,?,?,?,?,?,?)',('card','合成信用账户','credit','CNY',None,None,None,1,now()))
    first=apply(store,preview(store,[['local-1','2027-01-12','银行还款','-100.00','repayment','','card']]))
    raw=csv_bytes([['local-1' if same_id else 'local-2','2027-01-12','信用账户收到还款','100.00','repayment','','a']])
    second=imports_v2.create_preview(store,raw,'card.csv','card',str(uuid4()))['batch']; second=apply(store,second)
    assert first['counts']['business']['new']==second['counts']['business']['new']==1
    with store.read() as db:
        txs=transactions(db)
        assert len(txs)==2
        assert all(not t['counterparty_confirmed'] for t in txs.values())
        assert all(len(movements(t))==1 for t in txs.values())
        assert sum(movements(t).get('card',0) for t in txs.values())==10000
        assert sum(movements(t).get('a',0) for t in txs.values())==-10000
        assert db.execute('SELECT COUNT(*) FROM source_rows').fetchone()[0]==2
        assert {r[0] for r in db.execute('SELECT COUNT(*) FROM source_rows GROUP BY transaction_id')}=={1}


def test_same_source_id_in_distinct_namespace_is_not_merged(tmp_path,monkeypatch):
    store=setup(tmp_path); rows=[['local','2027-01-02','消费','-1.00','expense']]
    apply(store,preview(store,rows)); original=import_parsers.parse
    def another_namespace(*args):
        value=original(*args); value['parser_version']='synthetic_other_v1'; value['source_namespace']='synthetic_other'; return value
    monkeypatch.setattr(import_parsers,'parse',another_namespace)
    batch=apply(store,preview(store,rows))
    assert batch['counts']['business']['new']==1
    with store.read() as db: assert len(transactions(db))==2


def test_same_native_artifact_links_legacy_original_without_overwriting(tmp_path,monkeypatch):
    native_parser(monkeypatch); store=setup(tmp_path,'credit'); content=b'%PDF-synthetic-legacy'; sha=store.archive(content,'legacy.pdf')
    observations=parse_text_pages([FIXTURE.read_text()],'a',sha)['rows']
    with store.write() as db:
        db.execute('INSERT INTO artifacts VALUES(?,?,?,?)',(sha,'legacy.pdf',len(content),now()))
        db.execute('INSERT INTO imports VALUES(?,?,?,?,?,?,?,?,?,?,?,?)',('legacy',sha,'legacy.pdf','a','reviewed_legacy_v1','completed',6,6,0,0,'[]',now()))
        for n,item in enumerate(observations):
            p={**item['payload'],'description':'已保留的人工解释'}; tid='legacy-'+str(n)
            save_transaction(db,tid,p,'migrate',actor='migration')
            db.execute('INSERT INTO source_rows VALUES(?,?,?,?,?,?,?)',(tid,'legacy',n+1,tid,encode(item['raw']),tid,None))
    batch=imports_v2.create_preview(store,content,'reimport.pdf','a',str(uuid4()))['batch']; batch=apply(store,batch)
    assert batch['counts']['business']['new']==0 and batch['counts']['source_rows']['duplicate']==6
    with store.read() as db:
        assert len(transactions(db))==6 and revision(db)==0
        assert all(t['description']=='已保留的人工解释' for t in transactions(db).values())
        assert db.execute('SELECT COUNT(*) FROM source_rows').fetchone()[0]==12


def test_concurrent_same_request_returns_the_same_receipt_without_http_500(tmp_path,monkeypatch):
    store=setup(tmp_path); batch=preview(store,[['same','2027-01-02','合成消费','-1.00','expense'],['other','2027-01-02','另笔消费','-2.00','expense']]); body=command(batch,limit=1)
    app=FastAPI(); imports_v2.register_routes(app,store)
    barrier=threading.Barrier(2)
    def post():
        with TestClient(app,raise_server_exceptions=False) as client:
            barrier.wait(timeout=5)
            response=client.post('/api/v1/import-batches/'+batch['id']+'/apply',json=body)
            assert response.status_code==200,response.text
            return response.json()
    with ThreadPoolExecutor(max_workers=2) as pool:
        first=pool.submit(post); second=pool.submit(post)
        response=first.result(timeout=10)
        assert response==second.result(timeout=10)
        assert response['batch']['counts']['source_rows']['pending']==1
        assert response['batch']['counts']['business']['new']==1
    with store.read() as db:
        assert len(transactions(db))==1 and revision(db)==1
        assert db.execute('SELECT COUNT(*) FROM operations WHERE request_id=?',(body['request_id'],)).fetchone()[0]==1


def test_concurrent_different_apply_ids_cannot_start_from_same_version(tmp_path,monkeypatch):
    store=setup(tmp_path); batch=preview(store,[['one','2027-01-02','合成消费','-1.00','expense']]); original=imports_v2._apply_one
    started=threading.Event(); release=threading.Event()
    def paused(*args):
        started.set(); assert release.wait(timeout=5)
        return original(*args)
    monkeypatch.setattr(imports_v2,'_apply_one',paused)
    with ThreadPoolExecutor(max_workers=1) as pool:
        first=pool.submit(imports_v2.process_batch,store,batch['id'],command(batch))
        assert started.wait(timeout=5)
        with pytest.raises(DomainError) as exc:
            imports_v2.process_batch(store,batch['id'],command(batch))
        assert exc.value.code in {'version_conflict','batch_running'}
        current=imports_v2.get_batch(store,batch['id'])['batch']
        with pytest.raises(DomainError) as exc:
            imports_v2.process_batch(store,batch['id'],command(current),'resume')
        assert exc.value.code=='batch_running'
        release.set(); assert first.result(timeout=10)['batch']['counts']['business']['new']==1


def test_cancel_is_not_blocked_by_running_request_lock(tmp_path,monkeypatch):
    store=setup(tmp_path); batch=preview(store,[[str(i),'2027-01-02','合成消费','-1.00','expense'] for i in range(3)]); original=imports_v2._apply_one
    committed=threading.Event(); release=threading.Event(); calls=0
    def paused(*args):
        nonlocal calls
        calls+=1
        if calls==2:
            committed.set(); assert release.wait(timeout=5)
        return original(*args)
    monkeypatch.setattr(imports_v2,'_apply_one',paused)
    with ThreadPoolExecutor(max_workers=1) as pool:
        running=pool.submit(imports_v2.process_batch,store,batch['id'],command(batch))
        assert committed.wait(timeout=5)
        current=imports_v2.get_batch(store,batch['id'])['batch']
        cancelled=imports_v2.cancel_batch(store,batch['id'],command(current))['batch']
        assert cancelled['status']=='cancelled'
        release.set(); result=running.result(timeout=10)['batch']
        assert result['status']=='cancelled' and result['counts']['business']['new']==1
        assert result['counts']['source_rows']['pending']==2
