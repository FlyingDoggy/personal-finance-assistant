import { useCallback, useEffect, useRef, useState, type FormEvent } from "react";
import { Button } from "@tuja/ui/components/button";
import { Card } from "@tuja/ui/components/card";
import { Heading } from "@tuja/ui/components/heading";
import { Text } from "@tuja/ui/components/text";
import { stack } from "@tuja/ui/primitives/stack.stylex";
import { api, ApiError, createPendingRequest, errorText, money } from "./api";
import type { Account, BudgetResponse, ChartAccount } from "./types";
import { TransactionDrawer } from "./Transactions";
import { Empty, Field, Icon, Modal, Notice } from "./ui";

type Batch = {
  id: string; import_id: string; filename: string; sha256: string; account_id: string;
  status: string; file_status: string; version: number; parser_version: string; source_namespace: string;
  role: string; encoding: string; field_mapping: Record<string, string>;
  created_at: string; updated_at: string; revision_before: number; revision_after: number;
  warnings: string[]; source_account_options: string[]; error: { code: string; message: string } | null;
  summary: { period_start: string | null; period_end: string | null; currency: string; inflow_minor: string; outflow_minor: string; amount_basis?: string };
  counts: {
    source_rows: { total: number; processed: number; pending: number; mapped: number; duplicate: number; evidence_only: number; unresolved: number; invalid: number; failed: number };
    business: { new: number; evidence_added: number; duplicate_skipped: number; linked_transactions: number };
    tasks: { unclassified: number; waiting_for_codex: number; running: number; needs_review: number; applied: number };
  };
  can_apply: boolean; can_resume: boolean; can_cancel: boolean; transaction_ids: string[];
};
type ImportRow = {
  id: string; row_number: number; source_id: string | null; source_namespace: string;
  identity_quality: string; role: string; raw: Record<string, unknown>;
  parsed: Record<string, unknown> | null; preview: { status: string; message?: string; candidate_transaction_ids?: string[] };
  status: string; transaction_id: string | null; attempts: number;
  error: { code: string; message: string } | null; message: string | null; warning: string | null;
};
type Detail = { batch: Batch; rows: ImportRow[]; total: number; limit: number; offset: number };
type Prepared = { scope_id: string; status: string; instruction: string; count: number };
type Props = { accounts: Account[]; onSaved: () => void | Promise<void>; onAddAccount: () => void };
const selectedKey = "finance-selected-import-batch";
const statusNames: Record<string, string> = {
  awaiting_confirmation: "等待确认", processing: "正在整理", partial: "尚有未处理行", completed: "本阶段已结束", cancelled: "已停止",
  pending: "尚未处理", mapped: "新增业务", duplicate: "重复来源", evidence_only: "仅补充证据", unresolved: "待你核对", invalid: "解析或字段错误", failed: "处理失败",
};
const kindNames: Record<string, string> = { expense: "消费", income: "收入", repayment: "信用还款", refund: "退款", unknown: "性质待确认", transfer: "本人互转", reimbursement: "报销回款", family_support: "家庭赞助" };

function rememberedBatch() { try { return localStorage.getItem(selectedKey) || ""; } catch { return ""; } }
function rememberBatch(id: string) { try { localStorage.setItem(selectedKey, id); } catch { /* Optional device preference. */ } }
function displayValue(value: unknown): string { return value == null ? "—" : typeof value === "object" ? JSON.stringify(value) : String(value); }

function UploadForm({ accounts, onAddAccount, onPreview }: Pick<Props, "accounts" | "onAddAccount"> & { onPreview: (batch: Batch, reused: boolean) => void }) {
  const [account, setAccount] = useState(accounts[0]?.id || "");
  const [file, setFile] = useState<File | null>(null), [busy, setBusy] = useState(false), [error, setError] = useState("");
  const attempt = useRef(createPendingRequest());
  useEffect(() => { if (!accounts.some(a => a.id === account)) setAccount(accounts[0]?.id || ""); }, [accounts, account]);
  async function upload(event: FormEvent) {
    event.preventDefault(); setError("");
    if (!file || !account) { setError("请选择文件及所属账户。"); return; }
    if (file.size > 10 * 1024 * 1024) { setError("文件超过 10 MiB，请按账单期间分别导出。"); return; }
    setBusy(true);
    try {
      const form = new FormData(); form.set("file", file); form.set("account_id", account);
      form.set("request_id", attempt.current.key({ account, name: file.name, size: file.size, lastModified: file.lastModified }));
      const result = await api<{ batch: Batch; reused: boolean }>("/import-batches", { method: "POST", body: form });
      attempt.current.clear(); onPreview(result.batch, result.reused);
    } catch (e) { setError(errorText(e)); } finally { setBusy(false); }
  }
  return <form onSubmit={upload}><div css={stack.item}>
    <Text look="bodySmall" tone="muted">先看文件识别结果，再确认导入。上传和预览不会新增账务金额。</Text>
    <details className="template-help"><summary>当前支持哪些文件？</summary>
      <ul><li>招商信用卡个人消费卡人民币文本 PDF，已核对 2026 年 1—10 月同版型；扫描件和其他银行版型暂不支持。</li>
        <li>支付宝「买入交易」Excel 2003 XML XLS，仅补充商品证据，不单独新增消费或退款。</li>
        <li>UTF-8 标准 CSV。<a href="/生活账本-流水模板.csv" download>下载标准模板</a></li></ul>
      <p>每份文件最多 10 MiB、10,000 行。一个信用账户可包含多张卡片，末四位保留在来源中。</p>
    </details>
    {error && <Notice kind="error">{error}</Notice>}
    {!accounts.length ? <Empty title="先添加账单所属账户" action={<Button type="button" onClick={onAddAccount}>添加账户</Button>}>信用卡账单请选择信用账户，原始文件会与账户一起保存。</Empty> : <>
      <Field label="账单所属账户" hint="核对账户和币种。支付宝商品证据请选择对应的付款账户。"><select value={account} disabled={busy} onChange={e => setAccount(e.target.value)}>{accounts.map(a => <option value={a.id} key={a.id}>{a.name}（{a.kind === "credit" ? "信用账户" : "现金账户"}）</option>)}</select></Field>
      <Field label="选择账单文件"><input type="file" accept=".pdf,.xls,.xml,.csv,application/pdf,text/csv" disabled={busy} onChange={e => { setFile(e.target.files?.[0] || null); attempt.current.clear(); }} /></Field>
      <div><Button type="submit" look="primary" disabled={busy || !file}>{busy ? "正在识别文件…" : "上传并查看预览"}<Icon name="upload" size={16}/></Button></div>
    </>}
  </div></form>;
}

function BatchCounts({ batch }: { batch: Batch }) {
  const { source_rows: rows, business, tasks } = batch.counts;
  return <div css={stack.item}>
    <div><Text look="label">文件行处理</Text><div className="ledger-summary"><span>来源 {rows.total} 行</span><span>尚未处理 {rows.pending}</span><span>重复 {rows.duplicate}</span><span>仅证据 {rows.evidence_only}</span><span>待核 {rows.unresolved}</span><span>错误 {rows.invalid + rows.failed}</span></div></div>
    <div><Text look="label">本批累计业务变化</Text><div className="ledger-summary"><span>新增业务 <b>{business.new}</b> 笔</span><span>补充证据 {business.evidence_added} 行</span><span>关联既有或新增交易 {business.linked_transactions} 笔</span></div></div>
    <div><Text look="label">后续待办</Text><div className="ledger-summary"><span>已记账待分类 {tasks.unclassified}</span><span>等待 Codex {tasks.waiting_for_codex}</span><span>已领取 {tasks.running}</span><span>待你确认 {tasks.needs_review}</span><span>已应用分类 {tasks.applied}</span></div></div>
    <Text look="bodySmall" tone="muted">三组数字分别描述来源行、业务和待办，不能相加。同一消费可以同时计为新增业务和待分类；待分类费用已经入账。</Text>
  </div>;
}

function BatchPanel({ batchId, accounts, onSaved, onUpdated, onOpenAI }: Props & { batchId: string; onUpdated: (batch: Batch) => void; onOpenAI?: () => void }) {
  const [data, setData] = useState<Detail | null>(null), [offset, setOffset] = useState(0), [refresh, setRefresh] = useState(0);
  const [loading, setLoading] = useState(true), [busy, setBusy] = useState(false), [cancelling, setCancelling] = useState(false), [preparing, setPreparing] = useState(false), [error, setError] = useState("");
  const [prepared, setPrepared] = useState<Prepared | null>(null), [copied, setCopied] = useState(false);
  const [drawer, setDrawer] = useState<{ id: string; chart: ChartAccount[]; budget: BudgetResponse | null } | null>(null);
  const attempt = useRef(createPendingRequest()), cancelAttempt = useRef(createPendingRequest()), prepareAttempt = useRef(createPendingRequest());
  const stopped = useRef(false), mounted = useRef(true);
  useEffect(() => { mounted.current = true; return () => { mounted.current = false; stopped.current = true; }; }, []);
  useEffect(() => {
    let active = true; setLoading(true);
    api<Detail>(`/import-batches/${encodeURIComponent(batchId)}?limit=50&offset=${offset}`).then(result => { if (active) { setData(result); onUpdated(result.batch); setLoading(false); } }).catch(e => { if (active) { setError(errorText(e)); setLoading(false); } });
    return () => { active = false; };
  }, [batchId, offset, refresh, onUpdated]);
  async function refreshResult() {
    const result = await api<Detail>(`/import-batches/${encodeURIComponent(batchId)}?limit=50&offset=${offset}`);
    if (mounted.current) { setData(result); onUpdated(result.batch); }
    return result.batch;
  }
  async function process(action: "apply" | "resume") {
    if (!data) return;
    setBusy(true); setError(""); stopped.current = false;
    let batch = data.batch, nextAction = action;
    try {
      // Each response is a durable checkpoint. Stop between chunks, and the
      // server also observes cancellation between individual business rows.
      do {
        const payload = { expected_version: batch.version, limit: 100 };
        const response = await api<{ batch: Batch }>(`/import-batches/${encodeURIComponent(batchId)}/${nextAction}`, { method: "POST", body: JSON.stringify({ ...payload, request_id: attempt.current.key({ batchId, action: nextAction, ...payload }) }) });
        attempt.current.clear(); batch = response.batch;
        if (mounted.current) { setData(old => old ? { ...old, batch } : old); onUpdated(batch); }
        if (stopped.current || batch.status !== "partial" || batch.counts.source_rows.failed || !batch.counts.source_rows.pending) break;
        nextAction = "resume";
      } while (mounted.current);
      if (mounted.current) await refreshResult();
      await onSaved();
    } catch (e) { if (mounted.current) { setError(`${errorText(e)} 已提交行会保留，可刷新批次后继续处理。`); setRefresh(n => n + 1); } }
    finally { if (mounted.current) setBusy(false); }
  }
  async function cancel() {
    stopped.current = true; setCancelling(true); setError("");
    try {
      // Read the latest version because another chunk may have just committed.
      for (let retries = 0; retries < 3; retries++) {
        const current = await refreshResult();
        if (!current.can_cancel) break;
        const payload = { expected_version: current.version };
        try {
          const response = await api<{ batch: Batch }>(`/import-batches/${encodeURIComponent(batchId)}/cancel`, { method: "POST", body: JSON.stringify({ ...payload, request_id: cancelAttempt.current.key({ batchId, ...payload }) }) });
          cancelAttempt.current.clear(); onUpdated(response.batch); break;
        } catch (e) { if (!(e instanceof ApiError) || e.code !== "version_conflict" || retries === 2) throw e; }
      }
      await refreshResult(); await onSaved();
    } catch (e) { setError(errorText(e)); } finally { setCancelling(false); }
  }
  async function prepare() {
    setPreparing(true); setError(""); setCopied(false);
    try {
      const payload = { import_id: batchId };
      const result = await api<Prepared>("/ai/prepare", { method: "POST", body: JSON.stringify({ ...payload, request_id: prepareAttempt.current.key(payload) }) });
      prepareAttempt.current.clear(); setPrepared(result); await refreshResult();
    } catch (e) { setError(errorText(e)); } finally { setPreparing(false); }
  }
  async function openTransaction(row: ImportRow) {
    if (!row.transaction_id) return;
    try {
      const year = String(row.parsed?.date || new Date().getFullYear()).slice(0, 4);
      const [chart, budget] = await Promise.all([api<{ items: ChartAccount[] }>("/chart-accounts"), api<BudgetResponse>(`/budgets?year=${year}`)]);
      setDrawer({ id: row.transaction_id, chart: chart.items, budget });
    } catch (e) { setError(errorText(e)); }
  }
  const batch = data?.batch;
  return <div css={stack.item}>
    {error && <Notice kind="error">{error}</Notice>}
    {!batch ? <Text>{loading ? "正在读取批次…" : "批次尚未读取。"}<Button look="ghost" onClick={() => setRefresh(n => n + 1)}>重新读取</Button></Text> : <>
      <Card><div css={stack.item}>
        <div className="section-heading"><div><Heading level={2} look="h4">{batch.filename}</Heading><Text look="bodySmall" tone="muted">{accounts.find(a => a.id === batch.account_id)?.name || "账户待核对"} · {statusNames[batch.status] || batch.status}</Text></div><Button look="ghost" size="sm" onClick={() => setRefresh(n => n + 1)}><Icon name="refresh" size={16}/>刷新</Button></div>
        {batch.error && <Notice kind="error">{batch.error.message} 原件已保存；请核对支持格式后上传正确文件。</Notice>}
        {batch.can_apply && <Notice>这是识别预览，尚未新增业务。请核对所属账户、期间和每行金额后再确认。</Notice>}
        {batch.status === "cancelled" && <Notice>后续处理已停止，之前成功提交的业务仍保留。可继续处理剩余行。</Notice>}
        {batch.summary.period_start && <div className="ledger-summary"><span>{batch.summary.period_start} 至 {batch.summary.period_end}</span><span>账户增加 ¥ {money(batch.summary.inflow_minor)}</span><span>账户减少 ¥ {money(batch.summary.outflow_minor)}</span></div>}
        {batch.summary.amount_basis && <Text look="bodySmall" tone="muted">{batch.summary.amount_basis}</Text>}
        {batch.source_account_options.length > 0 && <Text look="bodySmall">同一信用账户下的卡片末四位：{batch.source_account_options.join("、")}。</Text>}
        {batch.warnings.length > 0 && <details><summary>识别范围与待核事项</summary><ul>{batch.warnings.map(text => <li key={text}>{text}</li>)}</ul></details>}
        <BatchCounts batch={batch}/>
        <div className="filter-controls">
          {batch.can_apply && <Button look="primary" disabled={busy || cancelling} onClick={() => void process("apply")}>{busy ? "正在导入并整理…" : "确认导入并整理"}</Button>}
          {batch.can_resume && <Button look="primary" disabled={busy || cancelling} onClick={() => void process("resume")}>{busy ? "正在继续处理…" : "继续处理剩余行"}</Button>}
          {(batch.can_cancel || busy) && <Button disabled={cancelling} onClick={() => void cancel()}>{cancelling ? "正在停止…" : "停止后续处理"}</Button>}
          {batch.counts.tasks.unclassified > 0 && !batch.can_apply && <Button disabled={busy || preparing || cancelling} onClick={() => void prepare()}>{preparing ? "正在准备任务…" : "准备交给 Codex"}</Button>}
        </div>
        {busy && <Notice>已提交 {batch.counts.source_rows.processed} / {batch.counts.source_rows.total} 行。可以停止后续处理；关闭页面后，当前已开始的一组处理仍会保存进度。</Notice>}
        <Text look="bodySmall" tone="muted">本批账务修订：{batch.revision_before} → {batch.revision_after}。只有实际业务金额或解释改变才推进修订。</Text>
        {prepared && <Notice kind="success">已准备 {prepared.count} 个分类任务。{prepared.count ? "需要你在已连接财务 MCP 的 Codex 会话发送下面的指令；目前尚未开始运行。" : "当前没有可交给 Codex 的未分类消费。"}
          {prepared.count > 0 && <div css={stack.tight}><Field label="复制到 Codex 的指令"><textarea readOnly rows={3} value={prepared.instruction}/></Field><div><Button size="sm" onClick={() => { void navigator.clipboard.writeText(prepared.instruction).then(() => setCopied(true)).catch(() => setError("未能复制，请选中指令文本手动复制。")); }}>{copied ? "已复制" : "复制任务指令"}</Button></div></div>}
          <a href="#ai" onClick={onOpenAI}>查看 Codex 任务与回执</a>
        </Notice>}
        <details><summary>批次与来源信息</summary><dl><dt>批次编号</dt><dd>{batch.id}</dd><dt>解析版本</dt><dd>{batch.parser_version}</dd><dt>来源范围</dt><dd>{batch.source_namespace}</dd><dt>原件校验</dt><dd className="wrap-path">{batch.sha256}</dd></dl></details>
      </div></Card>
      <Card><div css={stack.item}>
        <Heading level={2} look="h4">逐行核对</Heading>
        <Text look="bodySmall" tone="muted">展开查看原始字段和解析结果；已关联的业务可进入交易详情查看来源和修改历史。</Text>
        {loading && <Text look="bodySmall">正在刷新来源行…</Text>}
        {data?.rows.map(row => <details key={row.id} className="import-item"><summary><span>第 {row.row_number} 行 · {displayValue(row.parsed?.description || row.raw.description_raw || row.raw["商品名称"] || row.raw.description || "来源记录")}</span><span className="badge">{statusNames[row.status === "pending" ? row.preview.status : row.status] || row.status}{row.status === "pending" ? "（预览）" : ""}</span></summary>
          <div css={stack.item}>
            {row.parsed && <div className="ledger-summary"><span>{displayValue(row.parsed.date)}</span>{typeof row.parsed.amount_minor === "string" && <span>¥ {money(row.parsed.amount_minor, true)}</span>}{typeof row.parsed.kind === "string" && <span>{kindNames[row.parsed.kind] || row.parsed.kind}</span>}<span>{row.role === "enrich" ? "商品证据，不增加金额" : "账户资金来源"}</span></div>}
            {row.error && <Notice kind="error">{row.error.message}</Notice>}
            {row.warning && <Notice>{row.warning}</Notice>}
            {!row.error && (row.message || row.preview.message) && <Text look="bodySmall">{row.message || row.preview.message}</Text>}
            {row.transaction_id && <div><Button size="sm" onClick={() => void openTransaction(row)}>查看已关联交易 <Icon name="arrow" size={14}/></Button></div>}
            {row.identity_quality === "observation" && <Text look="bodySmall" tone="muted">此来源没有稳定交易号，使用原件页/行定位；相似记录需要核对。</Text>}
            <details><summary>原始字段</summary><div className="table-scroll"><table><tbody>{Object.entries(row.raw).map(([key, value]) => <tr key={key}><th>{key}</th><td style={{ whiteSpace: "pre-wrap", overflowWrap: "anywhere" }}>{displayValue(value)}</td></tr>)}</tbody></table></div></details>
            <details><summary>解析后字段</summary><div className="table-scroll"><table><tbody>{Object.entries(row.parsed || {}).map(([key, value]) => <tr key={key}><th>{key}</th><td style={{ overflowWrap: "anywhere" }}>{displayValue(value)}</td></tr>)}</tbody></table></div></details>
          </div>
        </details>)}
        {!data?.rows.length && <Text look="bodySmall">没有可展示的来源行，请查看上方文件识别结果。</Text>}
        {data && data.total > 50 && <div className="pagination"><Button size="sm" disabled={offset === 0 || loading} onClick={() => setOffset(n => Math.max(0, n - 50))}>上一页</Button><span>{offset + 1}–{Math.min(offset + 50, data.total)} / {data.total} 行</span><Button size="sm" disabled={offset + 50 >= data.total || loading} onClick={() => setOffset(n => n + 50)}>下一页</Button></div>}
      </div></Card>
    </>}
    {drawer && <TransactionDrawer id={drawer.id} accounts={accounts} chart={drawer.chart} budget={drawer.budget} onClose={() => setDrawer(null)} onChanged={async () => { await onSaved(); await refreshResult(); }}/>}
  </div>;
}

export function ImportWorkspace({ accounts, onSaved, onAddAccount }: Props) {
  const [items, setItems] = useState<Batch[]>([]), [selected, setSelected] = useState(rememberedBatch), [error, setError] = useState("");
  const [reused, setReused] = useState(false);
  const update = useCallback((batch: Batch) => { setItems(old => [batch, ...old.filter(item => item.id !== batch.id)]); }, []);
  const select = useCallback((batch: Batch, wasReused: boolean) => { update(batch); setSelected(batch.id); rememberBatch(batch.id); setReused(wasReused); }, [update]);
  const load = useCallback(async () => {
    const result = await api<{ items: Batch[] }>("/import-batches?limit=100"); setItems(result.items);
    setSelected(old => { const id = old || result.items[0]?.id || ""; if (id) rememberBatch(id); return id; });
  }, []);
  useEffect(() => { void load().catch(e => setError(errorText(e))); }, [load]);
  return <div css={stack.item}>
    <div className="page-title"><div><p className="eyebrow">原件、业务与处理进度一起保存</p><Heading level={1} look="h3">添加账单</Heading><p>预览后确认导入，随时回来继续处理。</p></div><Button look="ghost" size="sm" onClick={() => void load().catch(e => setError(errorText(e)))}>刷新批次</Button></div>
    {error && <Notice kind="error">{error}</Notice>}
    <Card><div css={stack.item}><Heading level={2} look="h4">上传新文件</Heading><UploadForm accounts={accounts} onAddAccount={onAddAccount} onPreview={select}/></div></Card>
    {reused && <Notice>原件已存在，正在查看原批次；本次上传新增业务为 0。下方显示原批次累计结果。</Notice>}
    {(items.length > 0 || selected) && <Field label="查看已保存的批次"><select value={selected} onChange={e => { setSelected(e.target.value); rememberBatch(e.target.value); }}><option value="">选择批次</option>{selected && !items.some(x => x.id === selected) && <option value={selected}>此前查看的批次</option>}{items.map(item => <option key={item.id} value={item.id}>{new Date(item.created_at).toLocaleDateString("zh-CN")} · {item.filename} · {statusNames[item.status] || item.status}</option>)}</select></Field>}
    {selected && <BatchPanel key={selected} batchId={selected} accounts={accounts} onSaved={onSaved} onAddAccount={onAddAccount} onUpdated={update}/>}
  </div>;
}

export function ImportModal({ accounts, onClose, onSaved, onAddAccount }: Props & { onClose: () => void }) {
  const [batch, setBatch] = useState<Batch | null>(null);
  const [reused, setReused] = useState(false);
  const select = useCallback((value: Batch, wasReused: boolean) => { setBatch(value); rememberBatch(value.id); setReused(wasReused); }, []);
  const update = useCallback((value: Batch) => setBatch(value), []);
  return <Modal title="添加账单" onClose={onClose}><div className="modal-body" css={stack.item}>
    {reused && batch && <Notice>原件已存在，正在查看原批次；本次上传新增业务为 0。下方显示原批次累计结果。</Notice>}
    {!batch ? <UploadForm accounts={accounts} onAddAccount={onAddAccount} onPreview={select}/> : <BatchPanel batchId={batch.id} accounts={accounts} onSaved={onSaved} onAddAccount={onAddAccount} onUpdated={update} onOpenAI={onClose}/>}
  </div><footer className="modal-footer">{batch && <Button onClick={() => setBatch(null)}>添加另一个文件</Button>}<Button onClick={onClose}>{batch ? "关闭，保留进度" : "关闭"}</Button><a href="#imports" onClick={onClose}>查看所有批次</a></footer></Modal>;
}
