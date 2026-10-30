"""First-slice service tests use an isolated directory and synthetic records only."""
import csv
import hashlib
import io
import json
import sqlite3
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from app.domain import DomainError, MAX_MONEY
from app.main import create_app
from app.storage import Store


def client_at(path):
    c=TestClient(create_app(path),base_url='http://127.0.0.1')
    assert c.get('/api/v1/session').status_code==200
    return c


def add_account(c,kind='cash',opening=None,name='合成账户'):
    body={'request_id':str(uuid4()),'name':name,'kind':kind,'currency':'CNY'}
    if opening is not None: body.update(opening_balance_minor=opening,opening_date='2027-01-01',evidence='合成期初证据')
    r=c.post('/api/v1/accounts',json=body)
    assert r.status_code==200,r.text
    return r.json()['account']['id']


def csv_content(rows):
    buf=io.StringIO(); writer=csv.writer(buf)
    writer.writerow(['source_id','date','description','amount','kind','category_code','counterparty_account_id','refund_of','budget_line_id'])
    writer.writerows([r+['']*(9-len(r)) for r in rows])
    return buf.getvalue().encode()


def upload(c,aid,rows,request_id=None,filename='synthetic.csv'):
    r=c.post('/api/v1/imports',data={'account_id':aid,'request_id':request_id or str(uuid4())},files={'file':(filename,csv_content(rows),'text/csv')})
    assert r.status_code==200,r.text
    return r.json()


def list_transactions(c): return c.get('/api/v1/transactions').json()['items']
def get_report(c,month='2027-01'): return c.get('/api/v1/reports',params={'month':month}).json()

def edit(c,t,p,reason='合成核对更正',request_id=None):
    return c.post(f"/api/v1/transactions/{t['id']}/edit",json={'request_id':request_id or str(uuid4()),'expected_version':t['version'],'payload':p,'reason':reason})


def budget_payload():
    return {'year':2027,'currency':'CNY','funding_minor':['100000']*12,'target_minor':'120000','lines':[{'id':'food','name':'餐饮','group':'日常','allocation':'daily','execution_basis':'consumption','actual_basis':'consumption','monthly_minor':['20000']*12}],'usage':[]}


def save_budget(c,p,expected=0):
    current = c.get('/api/v1/budgets', params={'year':p['year']}).json()
    response = c.post('/api/v1/budgets',json={'request_id':str(uuid4()),'expected_version':expected,'payload':p})
    if response.status_code == 200:
        adoption = c.post(f"/api/v1/budget-versions/{p['year']}/{response.json()['version']}/adopt", json={'request_id':str(uuid4()),'expected_adopted_version':current['version']})
        assert adoption.status_code == 200, adoption.text
        return c.get('/api/v1/budgets', params={'year':p['year']})
    return response


def test_unknown_cash_preserves_evidence_and_separates_book_from_observation(tmp_path):
    c=client_at(tmp_path); aid=add_account(c,opening='100000')
    upload(c,aid,[['mystery','2027-01-02','只知道收款','90.00','unknown']])
    t=list_transactions(c)[0]
    before=c.get(f"/api/v1/transactions/{t['id']}").json()
    assert before['ledger_lines']==[]
    assert before['effects']['income_minor'] is None
    r=get_report(c)
    assert r['income_expense']['income_minor']=='0'
    assert r['cash_flow']['net_minor']=='9000'
    assert r['cash_flow']['closing_minor']=='109000'
    a=r['balance_sheet']['accounts'][0]
    assert a['balance_minor'] is None
    assert a['observed_balance_minor']=='109000'
    assert a['unexplained_change_minor']=='9000'
    assert r['balance_sheet']['scope_net_minor'] is None
    response=edit(c,t,{'kind':'income','category_code':'4001'})
    assert response.status_code==200,response.text
    after=c.get(f"/api/v1/transactions/{t['id']}").json()
    assert after['sources']==before['sources']
    assert get_report(c)['cash_flow']['net_minor']=='9000'
    assert get_report(c)['income_expense']['income_minor']=='9000'


def test_source_identity_and_file_hash_dedup_preserve_distinct_equal_purchases(tmp_path):
    c=client_at(tmp_path); aid=add_account(c)
    rows=[['a','2027-01-01','同额甲','-10.00','expense','5001'],['b','2027-01-01','同额乙','-10.00','expense','5001']]
    one=upload(c,aid,rows)
    assert one['import']['imported_count']==2
    assert upload(c,aid,rows,filename='renamed.csv')['reused'] is True
    upload(c,aid,[rows[0]])
    assert len(list_transactions(c))==2
    t=next(t for t in list_transactions(c) if t['source_id']=='a')
    changed=edit(c,t,{'category_code':'5004'}).json()['transaction']
    upload(c,aid,[rows[0]],filename='same-row.csv')
    assert next(x for x in list_transactions(c) if x['id']==changed['id'])['category_code']=='5004'
    conflict=upload(c,aid,[['a','2027-01-01','修改原件','-11.00','expense','5001']])
    assert conflict['import']['rejected_count']==1
    assert len(list_transactions(c))==2
    assert get_report(c)['income_expense']['expense_minor']=='2000'
    sha=one['import']['sha256']
    assert hashlib.sha256((tmp_path/'originals'/sha).read_bytes()).hexdigest()==sha


def test_double_ended_transfer_local_ids_preserve_each_endpoint_pending_relationship(tmp_path):
    c=client_at(tmp_path); a=add_account(c,opening='100000'); b=add_account(c,opening='0')
    upload(c,a,[['shared','2027-01-02','本人转出','-100.00','transfer','',b]])
    assert get_report(c)['review']['count']==1
    assert get_report(c)['cash_flow']['unexplained_outflow_minor']=='10000'
    upload(c,b,[['shared','2027-01-02','本人转入','100.00','transfer','',a]])
    assert len(list_transactions(c))==2
    r=get_report(c)
    assert r['cash_flow']['inflow_minor']==r['cash_flow']['outflow_minor']=='10000'
    assert r['cash_flow']['net_minor']=='0'
    assert r['balance_sheet']['scope_net_minor'] is None
    assert sorted(a['observed_balance_minor'] for a in r['balance_sheet']['accounts'])==['10000','90000']
    assert all(a['balance_minor'] is None for a in r['balance_sheet']['accounts'])
    for t in list_transactions(c):
        d=c.get(f"/api/v1/transactions/{t['id']}").json()
        assert len(d['sources'])==1
        assert d['ledger_lines']==[]
        assert not t['counterparty_confirmed']


def test_reciprocal_credit_repayment_local_ids_do_not_repeat_account_effects(tmp_path):
    c=client_at(tmp_path); bank=add_account(c,opening='100000'); card=add_account(c,'credit','0')
    upload(c,card,[['purchase','2027-01-02','合成信用消费','-200.00','expense','5016']])
    upload(c,bank,[['repayment-id','2027-01-03','合成信用还款','-200.00','repayment','',card]])
    upload(c,card,[['repayment-id','2027-01-03','另一端证据','200.00','repayment','',bank]])
    r=get_report(c)
    assert len(list_transactions(c))==3
    assert r['income_expense']['expense_minor']=='20000'
    assert r['cash_flow']['outflow_minor']=='20000'
    assert r['review']['unclassified_count']==1
    observed={a['id']:a['observed_balance_minor'] for a in r['balance_sheet']['accounts']}
    assert observed=={bank:'80000',card:'0'}
    assert all(t['source_count']==1 for t in list_transactions(c))


def test_undo_revalidates_later_refunds_and_optimistic_version(tmp_path):
    c=client_at(tmp_path); aid=add_account(c)
    upload(c,aid,[['buy','2027-01-01','原消费','-100.00','expense','5001']])
    t=list_transactions(c)[0]
    body={'request_id':str(uuid4()),'expected_version':1,'payload':{'amount_minor':'-20000'},'reason':'依据金额更正'}
    route=f"/api/v1/transactions/{t['id']}"
    result=c.post(route+'/edit',json=body)
    assert result.status_code==200
    assert c.post(route+'/edit',json=body).json()==result.json()
    body['payload']['amount_minor']='-30000'
    assert c.post(route+'/edit',json=body).json()['error']['code']=='idempotency_conflict'
    upload(c,aid,[['refund','2027-01-02','后续退款','150.00','refund','','',t['id']]])
    undo=c.post(route+'/undo',json={'request_id':str(uuid4()),'expected_version':2,'reason':'撤销金额'})
    assert undo.status_code==409
    assert undo.json()['error']['code']=='linked_amount_conflict'
    assert c.get(route).json()['transaction']['amount_minor']=='-20000'


def test_budget_immutable_versions_and_server_actual_projection(tmp_path):
    c=client_at(tmp_path); aid=add_account(c)
    p=budget_payload()
    initial=save_budget(c,p)
    assert initial.status_code==200,initial.text
    assert initial.json()['actuals_connected'] is False
    upload(c,aid,[['unassigned','2027-01-01','待分配消费','-50.00','expense','5001']])
    current=c.get('/api/v1/budgets?year=2027').json()
    assert current['actuals_connected'] is False
    assert current['unallocated_usage_minor']=='5000'
    t=list_transactions(c)[0]
    assert edit(c,t,{'budget_line_id':'food'}).status_code==200
    computed=c.get('/api/v1/budgets?year=2027').json()
    assert computed['actuals_connected'] is False
    assert computed['budget']['months'][0]['daily_remaining_minor'] is None
    assert computed['budget']['months'][0]['known_daily_remaining_minor']=='15000'
    assert computed['month_quality']['2027-01']['consumption']['complete'] is False
    assert computed['budget']['months'][0]['usage_minor']=='5000'
    assert computed['budget']['usage'][0]['id']==t['id']
    forged=budget_payload(); forged['usage']=[{'id':'forged','line_id':'food','date':'2027-01-01','amount_minor':'1','kind':'consumption'}]
    assert save_budget(c,forged,1).json()['error']['code']=='actuals_read_only'
    upload(c,aid,[['refund','2027-01-02','消费退款','20.00','refund','','',t['id']]])
    assert c.get('/api/v1/budgets?year=2027').json()['budget']['months'][0]['usage_minor']=='3000'
    p['target_minor']='240000'
    assert save_budget(c,p,1).json()['version']==2
    assert save_budget(c,p,1).status_code==409
    c.close(); c=client_at(tmp_path)
    assert c.get('/api/v1/budgets?year=2027').json()['version']==2
    with sqlite3.connect(tmp_path/'finance.sqlite3') as db:
        rows=db.execute('SELECT payload FROM budget_versions ORDER BY version').fetchall()
        assert json.loads(rows[0][0])['target_minor']=='120000'
        assert json.loads(rows[1][0])['target_minor']=='240000'


def test_payment_budget_without_payment_evidence_does_not_claim_actual_zero(tmp_path):
    c=client_at(tmp_path); aid=add_account(c)
    upload(c,aid,[['salary','2027-01-01','合成收入','1000.00','income','4001']])
    p=budget_payload(); p['lines'][0].update(execution_basis='scheduled_payment',actual_basis='payment',allocation='fixed')
    response=save_budget(c,p)
    assert response.status_code==200,response.text
    assert response.json()['actuals_connected'] is False
    assert response.json()['unsupported_payment_actuals'] is False


def test_money_dates_and_read_only_actor_validation_is_structured(tmp_path):
    c=client_at(tmp_path); aid=add_account(c)
    upload(c,aid,[['a','2027-01-01','消费','-1.00','expense','5001']])
    t=list_transactions(c)[0]
    for bad in (1.0,'1.2','01','-0','1e3',str(MAX_MONEY+1),'9'*5000):
        r=edit(c,t,{'amount_minor':bad})
        assert r.status_code==422,r.text
        assert 'error' in r.json()
    assert edit(c,t,{'date':'2027-02-30'}).status_code==422
    assert c.post('/api/v1/accounts',content='{',headers={'content-type':'application/json'}).status_code==422
    assert c.post('/api/v1/accounts',json={'actor':'human'}).status_code==422
    bad=upload(c,aid,[['huge','2027-01-01','大金额','9'*5000,'expense','5001']])
    assert bad['import']['rejected_count']==1
    assert len(list_transactions(c))==1


def test_all_37_chart_accounts_have_fixed_codes_and_no_system_manual_write(tmp_path):
    c=client_at(tmp_path); aid=add_account(c)
    chart=c.get('/api/v1/chart-accounts').json()['items']
    assert len(chart)==len({c['code'] for c in chart})==37
    assert next(c for c in chart if c['code']=='1004')['enabled'] is False
    upload(c,aid,[['bad','2027-01-01','不能补差','100.00','income','3001']])
    assert list_transactions(c)==[]


def test_statement_date_and_consumption_date_are_separate(tmp_path):
    c=client_at(tmp_path); aid=add_account(c)
    upload(c,aid,[['purchase','2027-01-31','付款证据日期','-100.00','expense','5001']])
    t=list_transactions(c)[0]
    assert edit(c,t,{'date':'2027-02-01'}).status_code==200
    assert get_report(c,'2027-01')['cash_flow']['outflow_minor']=='10000'
    assert get_report(c,'2027-01')['income_expense']['expense_minor']=='0'
    assert get_report(c,'2027-02')['cash_flow']['outflow_minor']=='0'
    assert get_report(c,'2027-02')['income_expense']['expense_minor']=='10000'


def test_future_schema_refused_before_writes_and_archive_never_overwrites(tmp_path):
    store=Store(tmp_path)
    sha=store.archive(b'original','unsafe/filename')
    assert store.archive(b'original','renamed')==sha
    assert not list((tmp_path/'originals').glob('.receiving-*'))
    with sqlite3.connect(store.path) as db:
        db.execute("UPDATE metadata SET value=99 WHERE key='schema_version'")
    with pytest.raises(DomainError,match='结构版本'): Store(tmp_path)
    with sqlite3.connect(store.path) as db:
        assert db.execute("SELECT value FROM metadata WHERE key='schema_version'").fetchone()[0]==99


def test_revision_pagination_rejects_mixed_snapshot(tmp_path):
    c=client_at(tmp_path); aid=add_account(c)
    before=c.get('/api/v1/transactions').json()['data_revision']
    upload(c,aid,[['a','2027-01-01','消费','-1.00','expense','5001']])
    assert c.get('/api/v1/transactions',params={'data_revision':before}).status_code==409
    assert c.get('/api/v1/transactions?limit=201').status_code==422


def test_known_credit_overpayment_is_asset_not_income_and_unknown_opening_is_not_guessed(tmp_path):
    c=client_at(tmp_path); bank=add_account(c,opening='100000'); card=add_account(c,'credit','30000')
    upload(c,bank,[['overpay','2027-01-02','有起点的超额还款','-500.00','repayment','',card]])
    t=list_transactions(c)[0]
    assert c.get(f"/api/v1/transactions/{t['id']}").json()['ledger_lines']==[]
    # Seed a trusted pre-v0.5 confirmed relation, retaining the compatibility
    # contract separately from the now forbidden peer-only public import.
    from app.storage import save_transaction, raw_payload, advance, transactions
    with Store(tmp_path).write() as db:
        original=transactions(db)[t['id']]
        save_transaction(db,t['id'],{**raw_payload(original),'counterparty_confirmed':True},'legacy_fixture','合成已核对双端证据')
        advance(db)
    lines=c.get(f"/api/v1/transactions/{t['id']}").json()['ledger_lines']
    assert next(x for x in lines if x['code']=='2001')['debit_minor']=='30000'
    assert next(x for x in lines if x['code']=='1003')['debit_minor']=='20000'
    assert get_report(c)['income_expense']['income_minor']=='0'
    upload(c,card,[['consume-overpayment','2027-01-03','先用溢缴款','-100.00','expense','5001']])
    purchase=next(x for x in list_transactions(c) if x['kind']=='expense')
    lines=c.get(f"/api/v1/transactions/{purchase['id']}").json()['ledger_lines']
    assert next(x for x in lines if x['code']=='1003')['credit_minor']=='10000'
    assert not any(x['code']=='2001' for x in lines)
    unknown=add_account(c,'credit')
    upload(c,bank,[['unknown-debt','2027-01-04','无期初债务的还款','-50.00','repayment','',unknown]])
    transaction=next(x for x in list_transactions(c) if x['source_id']=='unknown-debt')
    lines=c.get(f"/api/v1/transactions/{transaction['id']}").json()['ledger_lines']
    assert not any(x['code']=='1003' for x in lines)
    assert next(a for a in c.get('/api/v1/accounts').json()['items'] if a['id']==unknown)['balance_minor'] is None
