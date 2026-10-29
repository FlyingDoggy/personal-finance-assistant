import { Button } from "@tuja/ui/components/button";
import { Card } from "@tuja/ui/components/card";
import { Heading } from "@tuja/ui/components/heading";
import { Select } from "@tuja/ui/components/select";
import { TextField } from "@tuja/ui/components/text-field";
import { useEffect, useRef, useState } from "react";
import { api, createPendingRequest, errorText, money } from "./api";
import type { Account, BudgetResponse, ChartAccount, Meta, Reports } from "./types";
import { TransactionDrawer, type TransactionFilters } from "./Transactions";
import { ReportsPage } from "./Reports";
import { Empty, Icon, Notice } from "./ui";

type Voucher = {
  transaction_id: string; transaction_version: number; date: string | null; cash_date: string | null;
  description: string; account_id: string; account_name: string; category_code: string | null;
  business_kind: string; status: string; source_count: number; related_transaction_ids: string[];
  group_id: string | null; debit_minor: string; credit_minor: string; line_count: number;
  rule_version: string; data_revision: number;
};
type Vouchers = Meta & {
  items: Voucher[]; total: number;
  summary: { debit_minor: string; credit_minor: string; line_count: number; transaction_count: number;
    paired_source_count: number; basis: string; note: string; status_counts?: Record<string, number> };
};
const voucherStatuses: Record<string, string> = {
  posted: "已有分录", review: "待核对", excluded: "范围外", no_journal: "未生成分录",
  evidence_only: "补充证据",
};
export function VouchersPage({ month, accounts, chart, budget, onChanged, onTransactions }: {
  month: string; accounts: Account[]; chart: ChartAccount[]; budget: BudgetResponse | null;
  onChanged: () => Promise<void>; onTransactions: () => void;
}) {
  const [data, setData] = useState<Vouchers | null>(null);
  const [period, setPeriod] = useState("all"), [account, setAccount] = useState(""), [category, setCategory] = useState("");
  const [status, setStatus] = useState(""), [search, setSearch] = useState(""), [query, setQuery] = useState("");
  const [offset, setOffset] = useState(0), [loading, setLoading] = useState(true), [error, setError] = useState("");
  const [selected, setSelected] = useState<string | null>(null), [refresh, setRefresh] = useState(0);
  useEffect(() => { const timer = window.setTimeout(() => setQuery(search.trim()), 250); return () => window.clearTimeout(timer); }, [search]);
  useEffect(() => { setOffset(0); }, [period, month, account, category, status, query]);
  useEffect(() => {
    let active = true; setLoading(true); setError("");
    const params = new URLSearchParams({ limit: "50", offset: String(offset) });
    if (period === "month") params.set("month", month);
    if (account) params.set("account_id", account);
    if (category) params.set("category_code", category);
    if (status) params.set("status", status);
    if (query) params.set("q", query);
    api<Vouchers>(`/vouchers?${params}`).then((value) => {
      if (!active) return;
      if (offset && offset >= value.total) { setOffset(Math.max(0, Math.floor((value.total - 1) / 50) * 50)); return; }
      setData(value); setLoading(false);
    }).catch((e) => { if (active) { setError(errorText(e)); setLoading(false); } });
    return () => { active = false; };
  }, [period, month, account, category, status, query, offset, refresh]);
  return <>
    <div className="page-title"><div><p className="eyebrow">从记账结果追到原始来源</p><Heading level={1} look="h3">凭证明细</Heading><p>查看每条交易解释对应的借贷分录；原始证据和修改历史仍在交易详情中。</p></div><Button look="ghost" size="sm" onClick={onTransactions}>交易明细 <Icon name="arrow" size={16}/></Button></div>
    <div className="ledger-filters">
      <TextField label="搜索凭证" placeholder="搜索描述或交易编号" type="search" value={search} onChange={(e) => setSearch(e.target.value)} />
      <div className="filter-controls">
        <Select label="凭证期间" value={period} onChange={(e) => setPeriod(e.target.value)}><option value="all">全部期间（含无日期）</option><option value="month">所选月份 · {month}</option></Select>
        <Select label="凭证来源账户" value={account} onChange={(e) => setAccount(e.target.value)}><option value="">全部账户</option>{accounts.map((a) => <option key={a.id} value={a.id}>{a.name}</option>)}</Select>
        <Select label="分录科目" value={category} onChange={(e) => setCategory(e.target.value)}><option value="">全部科目</option>{chart.map((c) => <option key={c.code} value={c.code}>{c.code} · {c.name}</option>)}</Select>
        <Select label="凭证状态" value={status} onChange={(e) => setStatus(e.target.value)}><option value="">全部状态</option>{Object.entries(voucherStatuses).map(([key, name]) => <option key={key} value={key}>{name}</option>)}</Select>
      </div>
    </div>
    {error && <Notice kind="error">{error}<Button look="ghost" size="sm" onClick={() => setRefresh((v) => v + 1)}>重新读取</Button></Notice>}
    {loading ? <p className="muted">正在读取凭证明细…</p> : data && <>
      <Notice>{data.summary.note || "此处合计是来源记录的分录合计，配对来源可能描述同一笔资金变动；不能作为去重后的总账、费用或现金收付合计。"}</Notice>
      <div className="ledger-summary"><span><b>{data.total}</b> 条交易解释</span><span><b>{data.summary.line_count}</b> 行分录</span><span>借方 ¥ {money(data.summary.debit_minor)}</span><span>贷方 ¥ {money(data.summary.credit_minor)}</span>{data.summary.paired_source_count > 0 && <span>{data.summary.paired_source_count} 条有关联来源</span>}{Object.entries(data.summary.status_counts || {}).filter(([key, count]) => key !== "posted" && count > 0).map(([key, count]) => <span key={key}>{voucherStatuses[key] || key} {count} 条</span>)}</div>
      {data.items.length ? <div className="voucher-list">{data.items.map((v) => <button className="voucher-row" key={v.transaction_id} onClick={() => setSelected(v.transaction_id)}>
        <div className="voucher-row-main"><div className="voucher-row-title"><b>{v.description || "未填写描述"}</b><span className={`badge ${v.status === "review" ? "amber" : "mint"}`}>{voucherStatuses[v.status] || v.status}</span></div><p>{v.date || "日期未确认"} · {v.account_name} · {v.business_kind}</p><small>交易第 {v.transaction_version} 版 · {v.source_count} 份来源 · {v.line_count} 行分录{v.related_transaction_ids.length ? ` · 关联 ${v.related_transaction_ids.length} 条交易` : ""}</small></div>
        <div className="voucher-amounts"><span>借 <b>{v.line_count ? money(v.debit_minor) : "—"}</b></span><span>贷 <b>{v.line_count ? money(v.credit_minor) : "—"}</b></span></div><Icon name="chevron" size={16}/>
      </button>)}</div> : <Empty title="没有匹配的凭证记录">可以调整筛选条件。未生成分录或范围外的交易也可以通过状态筛选查看。</Empty>}
      <div className="pagination"><span>{data.total ? `${offset + 1}–${Math.min(offset + 50, data.total)} / ${data.total}` : "0 条"}</span><Button size="sm" look="ghost" disabled={offset === 0} onClick={() => setOffset(Math.max(0, offset - 50))}>上一页</Button><Button size="sm" look="ghost" disabled={offset + 50 >= data.total} onClick={() => setOffset(offset + 50)}>下一页</Button></div>
      <p className="footnote">账户筛选按来源账户，科目筛选匹配包含该科目的整条交易解释；借贷合计包含匹配分录的两端，不是单科目发生额。未解释完整的记录不强造分录；修改交易后，这里和相关报表一起更新。</p>
    </>}
    {selected && <TransactionDrawer id={selected} accounts={accounts} chart={chart} budget={budget} onClose={() => setSelected(null)} onChanged={async () => { await onChanged(); setRefresh((v) => v + 1); }} />}
  </>;
}

type Catalog = {
  dimension_version: number;
  classification: { version: number; kind: string; created_at: string; notes: string | string[] };
  counts: Record<string, number>;
  chart_accounts: (ChartAccount & { supported: boolean; classification_version: number })[];
  accounts: { id: string; name: string; kind: string; currency: string; version: number; history_count: number }[];
  merchants: { id: string; name: string; aliases: string[]; transaction_count: number }[];
  tags: { id: string; name: string; namespace: string; transaction_count: number }[];
  budget_lines: { year: number; budget_version: number; id: string; name: string; group: string; allocation: string; execution_basis: string; actual_basis: string; annual_minor: string }[];
  limitations: string[];
};
const catalogTypes = [{ id: "chart_accounts", name: "科目" }, { id: "accounts", name: "账户" }, { id: "merchants", name: "商户与别名" }, { id: "tags", name: "标签" }, { id: "budget_lines", name: "预算项目" }] as const;
export type CatalogTab = typeof catalogTypes[number]["id"];
export function CatalogPage({ onBack, initialTab = "chart_accounts", embedded = false }: { onBack?: () => void; initialTab?: CatalogTab; embedded?: boolean }) {
  const [data, setData] = useState<Catalog | null>(null), [error, setError] = useState("");
  const [tab, setTab] = useState<CatalogTab>(initialTab), [search, setSearch] = useState("");
  useEffect(() => { let active = true; api<Catalog>("/catalog").then((v) => { if (active) setData(v); }).catch((e) => { if (active) setError(errorText(e)); }); return () => { active = false; }; }, []);
  const matches = (value: string) => value.toLocaleLowerCase().includes(search.trim().toLocaleLowerCase());
  return <>
    {!embedded && <div className="page-title"><div><p className="eyebrow">统一名称与稳定标识</p><Heading level={1} look="h3">基础资料</Heading><p>查看账本使用的科目、账户、商户、标签和预算项目。</p></div><Button size="sm" look="ghost" onClick={onBack}>返回更多</Button></div>}
    {error && <Notice kind="error">{error}</Notice>}
    {!data && !error ? <p>正在读取基础资料…</p> : data && <>
      <Notice>基础资料版本 {data.dimension_version} · 科目基线第 {data.classification.version} 版。当前保留交易修改历史和已保存报告快照；尚未启用按生效日期切换科目体系的完整 SCD2。</Notice>
      {!embedded && <div className="catalog-tabs" role="tablist" aria-label="基础资料类别">{catalogTypes.map((item) => <Button key={item.id} role="tab" aria-selected={tab === item.id} look={tab === item.id ? "primary" : "ghost"} size="sm" onClick={() => { setTab(item.id); setSearch(""); }}>{item.name} · {data.counts[item.id] ?? data[item.id].length}</Button>)}</div>}
      <TextField label="搜索基础资料" placeholder="按名称或编号搜索" type="search" value={search} onChange={(e) => setSearch(e.target.value)} />
      <Card><div className="catalog-items">
        {tab === "chart_accounts" && data.chart_accounts.filter((c) => matches(c.name + c.code)).map((c) => <article key={c.code}><div><b>{c.code} · {c.name}</b><small>{{ asset: "资产", liability: "负债", scope: "纳入范围净额", income: "收入", expense: "费用" }[c.type] || c.type} · 科目版本 {c.classification_version}</small></div><span className="badge">{!c.enabled ? "停用" : c.supported ? "已支持" : "预留科目"}</span></article>)}
        {tab === "accounts" && data.accounts.filter((a) => matches(a.name)).map((a) => <article key={a.id}><div><b>{a.name}</b><small>{a.kind === "credit" ? "信用账户" : "现金账户"} · {a.currency}</small></div><span>第 {a.version} 版 · {a.history_count} 条历史</span></article>)}
        {tab === "merchants" && data.merchants.filter((m) => matches(m.name + m.aliases.join(" "))).map((m) => <article key={m.id}><div><b>{m.name}</b><small>来源名称：{m.aliases.join("、") || m.name}</small></div><span>{m.transaction_count} 条交易</span></article>)}
        {tab === "tags" && data.tags.filter((t) => matches(t.name)).map((t) => <article key={t.id}><div><b>{t.name}</b><small>{t.namespace === "behavior" ? "消费行为" : t.namespace === "reimbursement" ? "报销标记" : t.namespace}</small></div><span>{t.transaction_count} 条交易</span></article>)}
        {tab === "budget_lines" && data.budget_lines.filter((b) => matches(b.name + b.group + b.year)).map((b) => <article key={`${b.year}-${b.budget_version}-${b.id}`}><div><b>{b.name}</b><small>{b.year} 年 · 预算第 {b.budget_version} 版 · {b.group} · {b.actual_basis === "payment" ? "付款口径" : "消费口径"}</small></div><span>¥ {money(b.annual_minor)} / 年</span></article>)}
        {data[tab].length === 0 && <Empty title="尚无已确认资料">明确的来源字段或后续人工确认后才会建立关联，不从模糊描述猜测。</Empty>}
      </div></Card>
      <p className="footnote">本页为基础资料查询。账户在“更多”登记；科目采用当前业务模板。商户别名只保留已取得的结构化来源，不自动把相似名称合并。</p>
      {data.limitations.length > 0 && <details className="advanced"><summary>当前维护与版本边界</summary><ul>{data.limitations.map((note) => <li key={note}>{note}</li>)}</ul></details>}
    </>}
  </>;
}

type SnapshotHeader = { id: string; month: string; data_revision: number; rule_version: string; dimension_version: number; created_at: string; stale: boolean };
type Snapshot = SnapshotHeader & { report: Reports };
export function ReportsWorkspace({ data, onTransactions }: { data: Reports | null; onTransactions: (filters?: TransactionFilters) => void }) {
  const [snapshots, setSnapshots] = useState<SnapshotHeader[]>([]), [selected, setSelected] = useState<Snapshot | null>(null);
  const [showList, setShowList] = useState(false), [busy, setBusy] = useState(false), [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const attempt = useRef(createPendingRequest());
  useEffect(() => { setSelected(null); setNotice(""); }, [data?.month]);
  useEffect(() => {
    if (!selected?.id) return;
    let active = true;
    api<{ snapshot: Snapshot }>(`/report-snapshots/${encodeURIComponent(selected.id)}`).then((r) => { if (active) setSelected(r.snapshot); }).catch((e) => { if (active) setError(errorText(e)); });
    return () => { active = false; };
  }, [data?.data_revision, selected?.id]);
  useEffect(() => { let active = true; api<{ items: SnapshotHeader[] }>("/report-snapshots").then((r) => { if (active) setSnapshots(r.items); }).catch((e) => { if (active) setError(errorText(e)); }); return () => { active = false; }; }, [data?.data_revision]);
  async function save() {
    if (!data || busy) return; setBusy(true); setError(""); setNotice("");
    const payload = { month: data.month };
    try {
      const result = await api<{ snapshot: Snapshot }>("/report-snapshots", { method: "POST", body: JSON.stringify({ ...payload, request_id: attempt.current.key(payload) }) });
      attempt.current.clear();
      setSnapshots((items) => [result.snapshot, ...items.filter((item) => item.id !== result.snapshot.id)]);
      setNotice(`已保存 ${result.snapshot.month} 报表快照，记录数据修订 ${result.snapshot.data_revision}。`);
      setShowList(true);
    } catch (e) { setError(errorText(e)); } finally { setBusy(false); }
  }
  async function view(id: string) {
    if (busy) return; setBusy(true); setError(""); setNotice("");
    try { const r = await api<{ snapshot: Snapshot }>(`/report-snapshots/${encodeURIComponent(id)}`); setSelected(r.snapshot); setShowList(false); } catch (e) { setError(errorText(e)); } finally { setBusy(false); }
  }
  return <>
    <div className="snapshot-toolbar"><Button size="sm" onClick={() => void save()} disabled={!data || busy || !!selected}>{busy ? "正在处理…" : "保存当前报表快照"}</Button><Button size="sm" look="ghost" onClick={() => setShowList(!showList)}>已保存快照 · {snapshots.length}</Button>{selected && <Button size="sm" look="primary" onClick={() => setSelected(null)}>返回当前报表</Button>}</div>
    {error && <Notice kind="error">{error}</Notice>}{notice && <Notice kind="success">{notice}</Notice>}
    {showList && <Card><Heading level={2} look="h4">已保存的报表</Heading>{snapshots.length ? <div className="snapshot-list">{snapshots.map((s) => <Button key={s.id} look="ghost" onClick={() => void view(s.id)} disabled={busy}><span>{s.month} · 数据修订 {s.data_revision}<small>{new Date(s.created_at).toLocaleString("zh-CN", { hour12: false })} · {s.stale ? "账本或规则已有更新" : "与当前版本一致"}</small></span><Icon name="chevron" size={16}/></Button>)}</div> : <p className="muted">尚未保存快照。保存后可查看当时的三张报表和版本信息。</p>}</Card>}
    {selected && <Notice><b>正在查看 {selected.month} 的只读快照。</b> 保存于 {new Date(selected.created_at).toLocaleString("zh-CN", { hour12: false })} · 数据修订 {selected.data_revision} · 规则 {selected.rule_version} · 基础资料版本 {selected.dimension_version}。{selected.stale ? "账本或规则已有更新，此处仍保留保存时的数值。" : "快照与当前版本一致。"} 快照不改写当前账本，也不跳转到可能已变化的当前交易。</Notice>}
    <ReportsPage data={selected?.report || data} onTransactions={onTransactions} readOnly={!!selected}/>
  </>;
}
