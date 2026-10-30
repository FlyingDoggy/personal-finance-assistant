import { Button } from "@tuja/ui/components/button";
import { Card } from "@tuja/ui/components/card";
import { Heading } from "@tuja/ui/components/heading";
import { useEffect, useRef, useState } from "react";
import { api, createPendingRequest, errorText, money, toDecimal, toMinor } from "./api";
import type { Account, ChartAccount } from "./types";
import type { TransactionFilters } from "./Transactions";
import { CoveragePanel } from "./CoveragePanel";
import { ManualEntry } from "./ManualEntry";
import { Empty, Field, Modal, Notice } from "./ui";

type Observation = { id: string; account_id: string; observed_date: string; version: number; balance_minor: string; definition: string; evidence: string; created_at: string };
type Reconciliation = {
  account: Account; observation: Observation | null; comparison_date: string;
  book_balance_minor: string | null; source_projection_minor: string | null; difference_minor: string | null;
  status: string; balance_reconciled: boolean; reasons: string[];
  transaction_ids: string[]; unexplained_ids: string[]; undated_ids: string[];
  quality: { complete: boolean; declarations: {month:string;basis:string;complete:boolean;stale:boolean}[] };
  interval: null | {start_date:string;end_date:string;observed_change_minor:string;known_change_minor:string;difference_minor:string;transaction_ids:string[];basis:string};
};
const statusNames: Record<string,string> = {no_observation:"待补银行余额",opening_unknown:"起点未知",needs_review:"账户变动待核",difference:"余额有差异",amounts_agree_partial:"金额一致 · 资料待核",reconciled:"该日余额核对一致"};
function monthEnd(month:string) { const [y,m]=month.split("-").map(Number); return `${month}-${new Date(y,m,0).getDate()}`; }

export function ReconciliationWorkspace({accounts,chart,month,onChanged,onTransactions,onAddAccount}: {
  accounts:Account[];chart:ChartAccount[];month:string;onChanged:()=>Promise<void>;
  onTransactions:(filters:TransactionFilters)=>void;onAddAccount:()=>void;
}) {
  const [asOf,setAsOf]=useState(monthEnd(month)),[account,setAccount]=useState("");
  const [items,setItems]=useState<Reconciliation[]>([]),[revision,setRevision]=useState<number|null>(null),[loading,setLoading]=useState(true),[reload,setReload]=useState(0),[error,setError]=useState("");
  const [observationFor,setObservationFor]=useState<Reconciliation|null>(null),[openingFor,setOpeningFor]=useState<Account|null>(null),[manualFor,setManualFor]=useState<string|null>(null);
  useEffect(()=>setAsOf(monthEnd(month)),[month]);
  useEffect(()=>{
    let current=true;setLoading(true);setError("");
    api<{items:Reconciliation[];data_revision:number}>(`/reconciliation?as_of=${asOf}${account?`&account_id=${encodeURIComponent(account)}`:""}`)
      .then(r=>{if(current){setItems(r.items);setRevision(r.data_revision);}}).catch(e=>{if(current)setError(errorText(e));}).finally(()=>{if(current)setLoading(false);});
    return()=>{current=false;};
  },[asOf,account,reload,accounts]);
  async function changed(){setReload(n=>n+1);await onChanged();}
  const openIds=(ids:string[])=>onTransactions({allPeriods:true,scope:"included",transactionIds:ids});
  return <>
    <div className="page-title"><div><p className="eyebrow">银行观察与账本分开核对</p><Heading level={1} look="h3">余额核对</Heading><p>发现差额后查看流水、补录或更正；系统不会自动补差入账。</p></div></div>
    <div className="filter-controls">
      <Field label="余额核对截止日"><input type="date" value={asOf} onChange={e=>e.target.value&&setAsOf(e.target.value)}/></Field>
      <Field label="余额核对账户"><select value={account} onChange={e=>setAccount(e.target.value)}><option value="">全部账户</option>{accounts.map(a=><option key={a.id} value={a.id}>{a.name}</option>)}</select></Field>
      <Button onClick={()=>setReload(n=>n+1)}>刷新核对</Button>
    </div>
    {error&&<Notice kind="error">{error}</Notice>}
    <Notice>银行观察是某日终余额，账户起点是某日初余额。信用账户填写净欠款，负数表示溢缴款；可用额度不作为欠款。未知起点时先展示已知变动，不能认定绝对余额已对账。</Notice>
    {!accounts.length ? <Empty title="先登记账户" action={<Button onClick={onAddAccount}>添加账户</Button>}>账户起点未知也可以先留空，随后用有依据的日初余额补齐。</Empty> : loading ? <p role="status">正在读取余额与流水…</p> : <>
      <p className="muted">修订 {revision} · {items.length} 个账户 · {items.filter(r=>r.balance_reconciled).length} 个账户在所示观察日核对一致</p>
      {items.map(row=><Card key={row.account.id}><section aria-label={`${row.account.name}余额核对`}>
        <div className="section-heading"><div><Heading level={2} look="h4">{row.account.name}</Heading><p>比较日期 {row.comparison_date}{row.observation&&row.comparison_date!==asOf?" · 使用该日观察，不与后续流水混比":""}</p></div><span className={`badge ${row.balance_reconciled?"mint":"amber"}`}>{statusNames[row.status]||row.status}</span></div>
        <dl className="reconciliation-values">
          <div><dt>银行日终{row.account.kind==="credit"?"净欠款":"余额"}</dt><dd>{row.observation?`¥${money(row.observation.balance_minor)}`:"尚未录入"}</dd></div>
          <div><dt>同日已解释账面</dt><dd>{row.book_balance_minor===null?"待核对":`¥${money(row.book_balance_minor)}`}</dd></div>
          <div><dt>按已知流水推演</dt><dd>{row.source_projection_minor===null?"起点未知":`¥${money(row.source_projection_minor)}`}</dd></div>
          <div><dt>银行 − 流水推演</dt><dd>{row.difference_minor===null?"无法确定":`¥${money(row.difference_minor,true)}`}</dd></div>
        </dl>
        <p className="muted">{row.account.opening_date?`日初起点 ${row.account.opening_date} · ¥${money(row.account.opening_balance_minor)}`:"尚无有依据的日初余额。"}</p>
        {!!row.reasons.length&&<ul className="reconciliation-reasons">{row.reasons.map(reason=><li key={reason}>{reason}</li>)}</ul>}
        {row.observation&&<details className="reconciliation-evidence"><summary>银行余额依据 · 第{row.observation.version}版</summary><p>{row.observation.evidence}</p></details>}
        {row.interval&&<details className="reconciliation-evidence"><summary>区间变动核对 · {row.interval.start_date} 至 {row.interval.end_date}</summary><p>{row.interval.basis}</p><p>银行变动 ¥{money(row.interval.observed_change_minor,true)}，已知账本变动 ¥{money(row.interval.known_change_minor,true)}，差异 ¥{money(row.interval.difference_minor,true)}。</p><Button size="sm" disabled={!row.interval.transaction_ids.length} onClick={()=>openIds(row.interval!.transaction_ids)}>查看区间流水</Button></details>}
        <div className="filter-controls reconciliation-actions">
          <Button onClick={()=>setObservationFor(row)}>录入银行余额</Button>
          <Button look="ghost" onClick={()=>setOpeningFor(row.account)}>设置账户起点</Button>
          <Button look="ghost" onClick={()=>setManualFor(row.account.id)}>补录遗漏交易</Button>
          <Button look="ghost" disabled={!row.transaction_ids.length} onClick={()=>openIds(row.transaction_ids)}>查看核对流水</Button>
          <Button look="ghost" disabled={!row.unexplained_ids.length&&!row.undated_ids.length} onClick={()=>openIds([...new Set([...row.unexplained_ids,...row.undated_ids])])}>待核记录（{new Set([...row.unexplained_ids,...row.undated_ids]).size}）</Button>
        </div>
      </section></Card>)}
      <details className="reconciliation-evidence"><summary>核对资料覆盖</summary><CoveragePanel accounts={accounts} month={asOf.slice(0,7)} onSaved={changed}/></details>
    </>}
    {observationFor&&<ObservationModal row={observationFor} day={asOf} onClose={()=>setObservationFor(null)} onSaved={async()=>{setObservationFor(null);await changed();}}/>}
    {openingFor&&<OpeningModal account={openingFor} day={asOf} onClose={()=>setOpeningFor(null)} onSaved={async()=>{setOpeningFor(null);await changed();}}/>}
    {manualFor!==null&&<ManualEntry accounts={accounts} chart={chart} initialAccountId={manualFor} month={asOf.slice(0,7)} onClose={()=>setManualFor(null)} onSaved={async()=>{setManualFor(null);await changed();}}/>}
  </>;
}

function ObservationModal({row,day,onClose,onSaved}:{row:Reconciliation;day:string;onClose:()=>void;onSaved:()=>Promise<void>}) {
  const attempt=useRef(createPendingRequest());
  const [date,setDate]=useState(day),[amount,setAmount]=useState(""),[evidence,setEvidence]=useState("");
  const [version,setVersion]=useState<number|null>(null),[history,setHistory]=useState<Observation[]>([]),[offset,setOffset]=useState(0),[total,setTotal]=useState(0),[busy,setBusy]=useState(false),[error,setError]=useState("");
  useEffect(()=>{let current=true;setVersion(null);setError("");api<{items:Observation[]}>(`/balance-observations?account_id=${row.account.id}&observed_date=${date}&limit=1`).then(r=>{if(current){const o=r.items[0];setVersion(o?.version||0);setAmount(toDecimal(o?.balance_minor));setEvidence(o?.evidence||"");}}).catch(e=>{if(current)setError(errorText(e));});return()=>{current=false;};},[row.account.id,date]);
  useEffect(()=>{let current=true;api<{items:Observation[];total:number}>(`/balance-observations?account_id=${row.account.id}&limit=20&offset=${offset}`).then(r=>{if(current){setHistory(r.items);setTotal(r.total);}}).catch(e=>{if(current)setError(errorText(e));});return()=>{current=false;};},[row.account.id,offset]);
  async function save(){setBusy(true);setError("");try{const p={account_id:row.account.id,observed_date:date,balance_minor:toMinor(amount),evidence,expected_version:version};await api("/balance-observations",{method:"POST",body:JSON.stringify({...p,request_id:attempt.current.key(p)})});attempt.current.clear();await onSaved();}catch(e){setError(errorText(e));}finally{setBusy(false);}}
  return <Modal title="录入银行日终余额" onClose={()=>!busy&&onClose()}><div className="modal-body"><p>{row.account.name} · {row.account.kind==="credit"?"净欠款，负数为溢缴款，不填可用额度。":"现金账户日终余额。"}</p>{error&&<Notice kind="error">{error}</Notice>}
    <fieldset className="plain-fieldset" disabled={busy}><Field label="银行余额日期"><input type="date" value={date} onChange={e=>e.target.value&&setDate(e.target.value)}/></Field>
    <Field label="银行日终余额（元）"><input inputMode="decimal" value={amount} disabled={version===null} onChange={e=>setAmount(e.target.value)}/></Field>
    <Field label="银行余额依据"><textarea rows={3} value={evidence} onChange={e=>setEvidence(e.target.value)} maxLength={2000} placeholder="例如：银行App显示的该日日终余额，已确认币种和余额定义"/></Field>
    <Notice>{version?`该日已有第${version}版观察；保存将追加更正版本，旧值保留。`:"仅登记外部观察，不自动修改账户起点或补差交易。"}</Notice>
    <div className="modal-actions"><Button look="primary" disabled={busy||version===null} onClick={()=>void save()}>保存银行余额观察</Button></div>
    </fieldset><details className="reconciliation-evidence"><summary>历史观察（{total}份，含更正）</summary>{history.map(o=><article className="observation-history" key={o.id}><b>{o.observed_date} · ¥{money(o.balance_minor)} · 第{o.version}版</b><p>{o.evidence}</p></article>)}{total>20&&<div className="pagination"><Button disabled={!offset} onClick={()=>setOffset(n=>n-20)}>上一页</Button><span>{offset+1}–{Math.min(offset+20,total)}</span><Button disabled={offset+20>=total} onClick={()=>setOffset(n=>n+20)}>下一页</Button></div>}</details>
  </div></Modal>;
}

function OpeningModal({account,day,onClose,onSaved}:{account:Account;day:string;onClose:()=>void;onSaved:()=>Promise<void>}) {
  const attempt=useRef(createPendingRequest());
  const [unknown,setUnknown]=useState(false),[date,setDate]=useState(account.opening_date||day),[amount,setAmount]=useState(toDecimal(account.opening_balance_minor)),[evidence,setEvidence]=useState(""),[reason,setReason]=useState("");
  const [busy,setBusy]=useState(false),[error,setError]=useState(""),[history,setHistory]=useState<{id:string;version:number;reason:string;created_at:string;before_payload:{opening_balance:string|null;opening_date:string|null};after_payload:{opening_balance:string|null;opening_date:string|null}}[]>([]);
  useEffect(()=>{let current=true;api<{items:typeof history}>(`/accounts/${account.id}/opening-history`).then(r=>{if(current)setHistory(r.items);}).catch(e=>{if(current)setError(errorText(e));});return()=>{current=false;};},[account.id]);
  async function save(){setBusy(true);setError("");try{const p={expected_version:account.version,opening_balance_minor:unknown?null:toMinor(amount),opening_date:unknown?null:date,evidence:unknown?"":evidence,reason};await api(`/accounts/${account.id}/opening`,{method:"POST",body:JSON.stringify({...p,request_id:attempt.current.key(p)})});attempt.current.clear();await onSaved();}catch(e){setError(errorText(e));}finally{setBusy(false);}}
  return <Modal title="设置有依据的账户起点" onClose={()=>!busy&&onClose()}><div className="modal-body"><p>{account.name} · 当前起点 {account.opening_date?`${account.opening_date}日初 / ¥${money(account.opening_balance_minor)}`:"未知"}</p>{error&&<Notice kind="error">{error}</Notice>}
    <Notice>填写某日开始时的余额。若凭据是前一日日终，起点日期应填次日。此操作重算账户余额，不增加收入、费用或银行现金收付记录。</Notice>
    <fieldset className="plain-fieldset" disabled={busy}><label className="check-field"><input type="checkbox" checked={unknown} onChange={e=>setUnknown(e.target.checked)}/><span>改为起点未知，清空原期初</span></label>
    {!unknown&&<><Field label="账户起点日期（日初）"><input type="date" value={date} onChange={e=>setDate(e.target.value)}/></Field><Field label={account.kind==="credit"?"日初净欠款（元）":"日初现金余额（元）"} hint={account.kind==="credit"?"欠款填正，溢缴款填负。":undefined}><input inputMode="decimal" value={amount} onChange={e=>setAmount(e.target.value)}/></Field><Field label="日初余额依据"><textarea value={evidence} onChange={e=>setEvidence(e.target.value)} rows={3} maxLength={2000}/></Field></>}
    <Field label="起点修改理由"><textarea value={reason} onChange={e=>setReason(e.target.value)} rows={2} maxLength={2000}/></Field>
    <div className="modal-actions"><Button look="primary" disabled={busy} onClick={()=>void save()}>保存账户起点</Button></div>
    </fieldset>{!!history.length&&<details className="reconciliation-evidence"><summary>起点修订历史（{history.length}次）</summary>{history.map(h=><article className="observation-history" key={h.id}><b>账户第{h.version}版 · {new Date(h.created_at).toLocaleString("zh-CN")}</b><p>{h.reason}</p><p>{h.before_payload.opening_date||"未知"} / {h.before_payload.opening_balance==null?"未知":`¥${money(String(h.before_payload.opening_balance))}`} → {h.after_payload.opening_date||"未知"} / {h.after_payload.opening_balance==null?"未知":`¥${money(String(h.after_payload.opening_balance))}`}</p></article>)}</details>}
  </div></Modal>;
}
