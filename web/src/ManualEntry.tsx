import { Button } from "@tuja/ui/components/button";
import { Heading } from "@tuja/ui/components/heading";
import { useEffect, useRef, useState } from "react";
import { api, createPendingRequest, errorText, money, toMinor } from "./api";
import type { Account, BudgetLine, BudgetResponse, ChartAccount, Transaction, TransactionDetail } from "./types";
import { Field, Modal, Notice } from "./ui";

type Preview = {
  payload: Record<string, unknown>; reason: string; expected_data_revision: number;
  effects: TransactionDetail["effects"]; review_reasons: string[]; source_basis: string;
  possible_duplicate_count: number;
  possible_duplicates: { id: string; description: string; date: string; version: number }[];
};
const kinds = { expense: "消费", income: "收入", reimbursement: "报销回款", family_support: "家庭赞助", refund: "退款", repayment: "信用还款", transfer: "本人互转", unknown: "性质待确认" };

export function ManualEntry({ accounts, chart, month, initialAccountId, onClose, onSaved }: {
  accounts: Account[]; chart: ChartAccount[]; month: string; initialAccountId?: string;
  onClose: () => void; onSaved: (id: string) => Promise<void>;
}) {
  const attempt = useRef(createPendingRequest());
  const [account, setAccount] = useState(initialAccountId || accounts[0]?.id || "");
  const [kind, setKind] = useState("expense"), [day, setDay] = useState(`${month}-01`), [cashDay, setCashDay] = useState(`${month}-01`);
  const [amount, setAmount] = useState(""), [description, setDescription] = useState(""), [reason, setReason] = useState("");
  const [category, setCategory] = useState("5016"), [direction, setDirection] = useState("out"), [peer, setPeer] = useState(""), [refund, setRefund] = useState("");
  const [line, setLine] = useState(""), [lines, setLines] = useState<BudgetLine[]>([]);
  const [preview, setPreview] = useState<Preview | null>(null), [duplicate, setDuplicate] = useState(false), [busy, setBusy] = useState(false), [error, setError] = useState("");
  const selectedAccount = accounts.find(a => a.id === account);
  const receipt = ["income", "reimbursement", "family_support"].includes(kind);
  useEffect(() => { setPreview(null); setDuplicate(false); }, [account, kind, day, cashDay, amount, description, reason, category, direction, peer, refund, line]);
  useEffect(() => {
    let current = true;
    const years = [...new Set([day.slice(0,4), cashDay.slice(0,4)].filter(y => /^\d{4}$/.test(y)))];
    setLines([]);
    Promise.all(years.map(y => api<BudgetResponse>(`/budgets?year=${y}`))).then(results => {
      if (!current) return;
      const choices = results.flatMap(r => (r.budget?.lines || []).filter(l =>
        (l.actual_basis === "consumption" ? day : cashDay).startsWith(String(r.budget?.year))
        && ["expense", "refund"].includes(kind)
        && (l.actual_basis === "consumption" || selectedAccount?.kind === "cash")));
      setLines(choices.filter((l,i) => choices.findIndex(x => x.id === l.id) === i));
    }).catch(e => { if (current) setError(errorText(e)); });
    return () => { current = false; };
  }, [day, cashDay, kind, selectedAccount?.kind]);
  async function check() {
    setBusy(true); setError("");
    try {
      const positive = BigInt(toMinor(amount));
      if (positive <= 0n) throw new Error("金额填写正数，收付方向由业务性质和方向选项决定。");
      let sign = -1n;
      if (receipt || kind === "refund" || (kind === "repayment" && selectedAccount?.kind === "credit") || (["transfer", "unknown"].includes(kind) && direction === "in")) sign = 1n;
      const p = { account_id: account, date: day, cash_date: kind === "reimbursement" ? day : cashDay,
        currency: "CNY", amount_minor: String(sign * positive), kind, description,
        category_code: ["expense", "income"].includes(kind) ? category : null,
        counterparty_account_id: ["transfer", "repayment"].includes(kind) ? peer || null : null,
        counterparty_confirmed: false, refund_of: kind === "refund" ? refund || null : null,
        budget_line_id: ["expense", "refund"].includes(kind) ? line || null : null };
      setPreview(await api<Preview>("/transactions/manual/preview", { method: "POST", body: JSON.stringify({payload:p,reason}) }));
    } catch (e) { setError(errorText(e)); } finally { setBusy(false); }
  }
  async function save() {
    if (!preview) return;
    setBusy(true); setError("");
    try {
      const payload = { payload: preview.payload, reason: preview.reason, expected_data_revision: preview.expected_data_revision, allow_possible_duplicate: duplicate };
      const result = await api<{transaction:Transaction}>("/transactions", {method:"POST",body:JSON.stringify({...payload,request_id:attempt.current.key(payload)})});
      attempt.current.clear(); await onSaved(result.transaction.id);
    } catch (e) { setError(errorText(e)); } finally { setBusy(false); }
  }
  return <Modal title="手工补录交易" onClose={() => !busy && onClose()}>
    <div className="modal-body">
      <p className="form-intro">用于补记现金或遗漏的真实交易。依据与初始值保留在修改历史中；先检查统计影响，再保存。</p>
      {error && <Notice kind="error">{error}</Notice>}
      <fieldset className="plain-fieldset" disabled={busy}>
      {!preview ? <>
        <div className="form-grid">
          <Field label="补录账户"><select value={account} onChange={e=>setAccount(e.target.value)}><option value="">请选择账户</option>{accounts.filter(a=>!receipt || a.kind==="cash").map(a=><option key={a.id} value={a.id}>{a.name}</option>)}</select></Field>
          <Field label="补录业务性质"><select value={kind} onChange={e=>{ const k=e.target.value; setKind(k); setCategory(k==="expense"?"5016":k==="income"?"4001":"");setLine("");setPeer("");if (["income","reimbursement","family_support"].includes(k) && selectedAccount?.kind!=="cash") setAccount(accounts.find(a=>a.kind==="cash")?.id || ""); }}>{Object.entries(kinds).map(([v,l])=><option key={v} value={v}>{l}</option>)}</select></Field>
          <Field label={kind==="reimbursement"?"报销到账日期":"确认日期"}><input type="date" value={day} onChange={e=>{setDay(e.target.value);if(cashDay===day)setCashDay(e.target.value);}}/></Field>
          {kind!=="reimbursement" && <Field label="现金收付或入账日期"><input type="date" value={cashDay} onChange={e=>setCashDay(e.target.value)}/></Field>}
          <Field label="补录金额（元）" hint="填正数，下一步会单列账户变动、收入、费用与现金方向。"><input inputMode="decimal" value={amount} onChange={e=>setAmount(e.target.value)}/></Field>
          {["transfer","unknown"].includes(kind) && <Field label="账户变动方向"><select value={direction} onChange={e=>setDirection(e.target.value)}><option value="out">转出 / 净额减少</option><option value="in">转入 / 净额增加</option></select></Field>}
          {["expense","income"].includes(kind) && <Field label="补录科目"><select value={category} onChange={e=>setCategory(e.target.value)}>{chart.filter(c=>c.enabled && (kind==="expense"?c.code.startsWith("5"):["4001","4004","4005"].includes(c.code))).map(c=><option key={c.code} value={c.code}>{c.name}</option>)}</select></Field>}
          {["transfer","repayment"].includes(kind) && <Field label="已知对方账户" hint="只保留本端事实；选择账户不会自动生成对端流水或完成关联。"><select value={peer} onChange={e=>setPeer(e.target.value)}><option value="">尚未确认</option>{accounts.filter(a=>a.id!==account && (kind==="transfer"?a.kind==="cash":a.kind!==selectedAccount?.kind)).map(a=><option key={a.id} value={a.id}>{a.name}</option>)}</select></Field>}
          {kind==="refund" && <Field label="退款原消费编号" hint="支持同账户的历史或新消费；留空则保留待核，不自动抵费用。"><input value={refund} onChange={e=>setRefund(e.target.value)}/></Field>}
          {["expense","refund"].includes(kind) && <Field label="补录预算项目" hint="未分配的确定消费也会进入已知总占用；退款默认跟随原消费。"><select value={line} onChange={e=>setLine(e.target.value)}><option value="">尚未分配 / 退款跟随原消费</option>{lines.map(l=><option key={l.id} value={l.id}>{l.name} · {l.actual_basis==="payment"?"实际付款":"消费"}</option>)}</select></Field>}
        </div>
        <Field label="交易描述"><input value={description} onChange={e=>setDescription(e.target.value)} maxLength={2000}/></Field>
        <Field label="补录依据"><textarea rows={3} value={reason} onChange={e=>setReason(e.target.value)} placeholder="例如：核对现金小票后发现尚未入账" maxLength={2000}/></Field>
      </> : <section className="detail-section" aria-label="手工补录检查">
        <Heading level={3} look="h4">统计影响</Heading>
        <dl className="data-pairs">
          <div><dt>账户净变动</dt><dd>¥{money(String(preview.payload.amount_minor),true)}</dd></div>
          <div><dt>计入收入</dt><dd>¥{money(preview.effects?.income_minor)}</dd></div>
          <div><dt>计入费用</dt><dd>¥{money(preview.effects?.expense_minor)}</dd></div>
          <div><dt>现金流入 / 流出</dt><dd>¥{money(preview.effects?.cash_inflow_minor)} / ¥{money(preview.effects?.cash_outflow_minor)}</dd></div>
        </dl>
        <p>{String(preview.payload.description)} · {String(preview.payload.date)} · {selectedAccount?.name}</p>
        <p className="muted">{preview.source_basis}</p>
        {!!preview.review_reasons.length && <Notice>{preview.review_reasons.join("；")}</Notice>}
        {!!preview.possible_duplicate_count && <Notice>
          <b>发现 {preview.possible_duplicate_count} 笔同账户、同日、同额的候选</b>
          <ul>{preview.possible_duplicates.map(t=><li key={t.id}>{t.date} · {t.description} · 第{t.version}版<br/><code className="break-id">{t.id}</code></li>)}</ul>
          <label className="check-field"><input type="checkbox" checked={duplicate} onChange={e=>setDuplicate(e.target.checked)}/><span>已核对，这是另一笔真实交易，仍要补录</span></label>
        </Notice>}
      </section>}
      <div className="modal-actions">
        {preview ? <><Button look="ghost" disabled={busy} onClick={()=>setPreview(null)}>返回修改</Button><Button look="primary" disabled={busy || (!!preview.possible_duplicate_count && !duplicate)} onClick={()=>void save()}>确认补录</Button></> : <Button look="primary" disabled={busy || !accounts.length} onClick={()=>void check()}>检查补录</Button>}
      </div>
      </fieldset>
    </div>
  </Modal>;
}
