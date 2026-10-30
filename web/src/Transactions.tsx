import { BatchClassifier } from "./BatchClassifier";
import * as stylex from "@stylexjs/stylex";
import { NavigationSplitView } from "@tuja/ui/components/navigation-split-view";
import { Button } from "@tuja/ui/components/button";
import { Heading } from "@tuja/ui/components/heading";
import { Text } from "@tuja/ui/components/text";
import { TextField } from "@tuja/ui/components/text-field";
import { Select } from "@tuja/ui/components/select";
import { stack } from "@tuja/ui/primitives/stack.stylex";
import { TransactionFace } from "./upstream/transaction-face";
import { useEffect, useRef, useState, type ReactNode } from "react";
import {
  api,
  errorText,
  money,
  createPendingRequest,
  toDecimal,
  toMinor,
} from "./api";
import type {
  Account,
  BudgetResponse,
  ChartAccount,
  Meta,
  Transaction,
  TransactionDetail,
} from "./types";
import { Empty, Field, Icon, Modal, Notice } from "./ui";
export const kindLabels: Record<string, string> = {
  expense: "消费",
  income: "收入",
  reimbursement: "报销回款",
  family_support: "家庭赞助",
  repayment: "还款",
  transfer: "账户互转",
  refund: "退款",
  unknown: "性质待确认",
};
export function transactionBusinessName(transaction: Transaction) {
  return (
    transaction.historical?.event ||
    kindLabels[transaction.kind] ||
    "业务待确认"
  );
}
export function transactionAccountName(transaction: Transaction) {
  return (
    transaction.account_name ||
    transaction.historical?.source_account_label ||
    transaction.historical?.account
  );
}
export function transactionStatusName(transaction: Transaction) {
  return transaction.status === "excluded"
    ? "范围外"
    : transaction.status === "needs_review"
      ? "待确认"
      : "已记账";
}
function sourceValue(value: unknown) {
  return value == null
    ? "未提供"
    : typeof value === "object"
      ? JSON.stringify(value, null, 2)
      : String(value);
}
function historicalSourceLocation(raw: Record<string, unknown>) {
  if (typeof raw.source_locator === "string" && raw.source_locator.trim()) {
    return raw.source_locator;
  }
  const parts = [
    raw.source_page != null && raw.source_page !== ""
      ? `第 ${sourceValue(raw.source_page)} 页`
      : null,
    raw.source_line != null && raw.source_line !== ""
      ? `第 ${sourceValue(raw.source_line)} 行`
      : null,
  ].filter(Boolean);
  return parts.length ? parts.join(" · ") : "原始资料定位未提供";
}
function coreSourceFields(raw: Record<string, unknown>) {
  const names: Record<string, string> = {
    source_id: "来源编号",
    date: "来源日期",
    description: "来源描述",
    amount: "原始金额",
    currency: "结算币种",
    original_currency_raw: "原币币种",
    original_amount_raw: "原币金额",
  };
  return Object.keys(names)
    .filter((key) => raw[key] != null)
    .map((key) => ({ key, label: names[key], value: raw[key] }));
}
export function transactionAmountLabel(transaction: Transaction) {
  return transaction.kind === "historical"
    ? transaction.version > 1
      ? "当前业务金额"
      : "原始金额"
    : "账户变动";
}
export function transactionDisplayAmount(transaction: Transaction) {
  return transaction.kind === "historical" && transaction.historical
    ? transaction.version > 1
      ? transactionEditAmount(transaction)
      : transaction.historical.raw_amount_minor
    : transaction.amount_minor;
}
function transactionEditAmount(transaction: Transaction) {
  if (!transaction.historical) return transaction.amount_minor;
  const value =
    transaction.business_amount_minor ??
    transaction.historical.amount_minor ??
    transaction.amount_minor;
  const amount = BigInt(value || "0");
  return String(amount < 0n ? -amount : amount);
}
const historicalIncomeCategories: Record<string, string> = {
  工资实收: "4001",
  存款利息: "4004",
  家庭赞助: "4005",
  报销回款: "4005",
  一次性其他收入: "4005",
  个税退税: "4005",
};
function defaultHistoricalCategory(event: string, current: string | null) {
  if (historicalIncomeCategories[event])
    return historicalIncomeCategories[event];
  if (event === "性质待确认") return null;
  if (event === "利息或贴息") return "5012";
  if (event === "车辆租赁付款") return "5004";
  if (["消费", "消费退款", "消费退款或返利"].includes(event))
    return current?.startsWith("5") ? current : "5016";
  return current;
}
function historyValue(key: string, value: unknown) {
  if (
    key.endsWith("_minor") &&
    typeof value === "string" &&
    /^-?\d+$/.test(value)
  )
    return `¥ ${money(value)}`;
  if (key === "in_scope" && typeof value === "boolean")
    return value ? "纳入统计" : "范围外";
  return sourceValue(value);
}
function historyChanges(
  before: Record<string, unknown> | null | undefined,
  after: Record<string, unknown> | undefined,
) {
  if (!before || !after) return [];
  const fields: Record<string, string> = {
    date: "确认日期",
    cash_date: "收付日期",
    description: "描述",
    kind: "业务性质",
    account_id: "记账账户",
    category_code: "统计科目",
    amount_minor: "账户变动（元）",
    business_amount_minor: "业务金额（元）",
    budget_line_id: "预算项目",
    refund_of: "原消费关联",
  };
  const changes = Object.entries(fields)
    .filter(
      ([key]) => JSON.stringify(before[key]) !== JSON.stringify(after[key]),
    )
    .map(([key, label]) => ({
      label,
      before: historyValue(key, before[key]),
      after: historyValue(key, after[key]),
    }));
  const bh = before.historical as Record<string, unknown> | undefined;
  const ah = after.historical as Record<string, unknown> | undefined;
  if (bh && ah) {
    const historicalFields: Record<string, string> = {
      event: "业务性质",
      amount_minor: "业务金额（元）",
      in_scope: "纳入统计",
      review: "复核状态",
      income_minor: "计入收入（元）",
      expense_minor: "计入费用（元）",
      cash_minor: "现金变动（元）",
      debt_delta_minor: "债务变动（元）",
    };
    for (const [key, label] of Object.entries(historicalFields))
      if (JSON.stringify(bh[key]) !== JSON.stringify(ah[key]))
        changes.push({
          label,
          before: historyValue(key, bh[key]),
          after: historyValue(key, ah[key]),
        });
  }
  return changes;
}
export type TransactionFilters = {
  allPeriods?: boolean;
  reviewOnly?: boolean;
  categoryCode?: string;
  kind?: string;
  accountId?: string;
  startDate?: string;
  endDate?: string;
  scope?: "all" | "included" | "excluded";
  transactionIds?: string[];
};
export function TransactionsPage({
  month,
  accounts,
  chart,
  budget,
  onChanged,
  initialFilters = {},
  toolbar,
}: {
  month: string;
  accounts: Account[];
  chart: ChartAccount[];
  budget: BudgetResponse | null;
  onChanged: () => Promise<void>;
  initialFilters?: TransactionFilters;
  toolbar?: ReactNode;
}) {
  const [items, setItems] = useState<Transaction[]>([]),
    [total, setTotal] = useState(0),
    [offset, setOffset] = useState(0),
    [account, setAccount] = useState(initialFilters.accountId || ""),
    [review, setReview] = useState(!!initialFilters.reviewOnly),
    [period, setPeriod] = useState(
      initialFilters.startDate || initialFilters.endDate
        ? "range"
        : initialFilters.allPeriods
          ? "all"
          : "month",
    ),
    [search, setSearch] = useState(""),
    [queryText, setQueryText] = useState(""),
    [category, setCategory] = useState(initialFilters.categoryCode || ""),
    [kind, setKind] = useState(initialFilters.kind || ""),
    [scope, setScope] = useState(initialFilters.scope || "all"),
    [startDate, setStartDate] = useState(initialFilters.startDate || ""),
    [endDate, setEndDate] = useState(initialFilters.endDate || ""),
    [transactionIds, setTransactionIds] = useState(
      initialFilters.transactionIds || [],
    ),
    [kinds, setKinds] = useState<string[]>([]),
    [loading, setLoading] = useState(true),
    [error, setError] = useState(""),
    [selected, setSelected] = useState<string | null>(null),
    [reload, setReload] = useState(0);
  const [batchItems, setBatchItems] = useState<Transaction[]>([]);
  const [batchOpen, setBatchOpen] = useState(false);
  useEffect(() => {
    const timer = window.setTimeout(() => setQueryText(search.trim()), 250);
    return () => window.clearTimeout(timer);
  }, [search]);
  useEffect(() => {
    setOffset(0);
  }, [
    month,
    account,
    review,
    period,
    queryText,
    category,
    kind,
    scope,
    startDate,
    endDate,
    transactionIds,
  ]);
  useEffect(() => {
    let current = true;
    setLoading(true);
    setError("");
    const query = new URLSearchParams({
      limit: "50",
      offset: String(offset),
    });
    if (period === "month") query.set("month", month);
    if (period === "range" && startDate) query.set("start_date", startDate);
    if (period === "range" && endDate) query.set("end_date", endDate);
    if (queryText) query.set("q", queryText);
    if (category) query.set("category_code", category);
    if (kind) query.set("kind", kind);
    if (scope !== "all") query.set("scope", scope);
    if (transactionIds.length) query.set("ids", transactionIds.join(","));
    if (account) query.set("account_id", account);
    if (review) query.set("review_only", "true");
    api<
      Meta & {
        items: Transaction[];
        total: number;
        filter_options?: { kinds: string[] };
      }
    >(`/transactions?${query}`)
      .then((r) => {
        if (current) {
          setTotal(r.total);
          if (r.filter_options) setKinds(r.filter_options.kinds);
          if (offset > 0 && offset >= r.total) {
            current = false;
            setOffset(r.total > 0 ? Math.floor((r.total - 1) / 50) * 50 : 0);
            return;
          }
          setItems(r.items);
        }
      })
      .catch((e) => {
        if (current) setError(errorText(e));
      })
      .finally(() => {
        if (current) setLoading(false);
      });
    return () => {
      current = false;
    };
  }, [
    month,
    account,
    review,
    period,
    queryText,
    category,
    kind,
    scope,
    startDate,
    endDate,
    transactionIds,
    offset,
    reload,
  ]);
  const shown = items;
  function clearFilters() {
    setAccount("");
    setReview(false);
    setPeriod("all");
    setSearch("");
    setQueryText("");
    setCategory("");
    setKind("");
    setScope("all");
    setStartDate("");
    setEndDate("");
    setTransactionIds([]);
    setOffset(0);
  }
  const master = (
    <div css={[stack.group, listStyles.master]}>
      {toolbar}
      <header css={stack.item}>
        <div className="section-heading">
          <div css={stack.text}>
            <Heading level={1} look="h3">
              交易
            </Heading>
            <Text look="bodySmall" tone="muted">
              {total} 笔交易 ·{" "}
              {period === "all"
                ? "全部期间（含无日期）"
                : period === "range"
                  ? `${startDate || "不限起日"} 至 ${endDate || "不限止日"}`
                  : month}
            </Text>
          </div>
        </div>
        <div className="filter-controls"><Button size="sm" disabled={!batchItems.length} onClick={() => setBatchOpen(true)}>批量分类（{batchItems.length}）</Button>{batchItems.length > 0 && <Button size="sm" look="ghost" onClick={() => setBatchItems([])}>清除选择</Button>}</div>
        {batchOpen && <BatchClassifier items={batchItems} chart={chart} budget={budget} onClose={() => setBatchOpen(false)} onSaved={async () => { setBatchOpen(false); setBatchItems([]); setReload(n => n + 1); await onChanged(); }}/>}
        <search css={stack.tight}>
          <TextField
            type="search"
            label="搜索全部交易"
            labelHidden
            placeholder="搜索描述、商品、来源文件或交易编号"
            leading={<Icon name="search" size={18} />}
            value={search}
            onChange={(e) => setSearch(e.target.value)}
          />
          <div className="filter-controls">
            <Select
              label="交易期间"
              labelHidden
              value={period}
              onChange={(e) => setPeriod(e.target.value)}
            >
              <option value="month">所选月份</option>
              <option value="all">全部期间（含无日期）</option>
              <option value="range">自定义日期</option>
            </Select>
            <Button
              size="sm"
              look={!review ? "primary" : "ghost"}
              onClick={() => setReview(false)}
            >
              全部交易
            </Button>
            <Button
              size="sm"
              look={review ? "primary" : "ghost"}
              onClick={() => setReview(true)}
            >
              待确认
            </Button>
            <Select
              label="账户"
              labelHidden
              value={account}
              onChange={(e) => setAccount(e.target.value)}
            >
              <option value="">全部账户</option>
              {accounts.map((a) => (
                <option key={a.id} value={a.id}>
                  {a.name}
                </option>
              ))}
            </Select>
          </div>
          {period === "range" && (
            <div className="filter-controls date-filters">
              <TextField
                label="开始日期"
                type="date"
                value={startDate}
                onChange={(e) => setStartDate(e.target.value)}
              />
              <TextField
                label="结束日期"
                type="date"
                value={endDate}
                onChange={(e) => setEndDate(e.target.value)}
              />
            </div>
          )}
          <div className="filter-controls">
            <Select
              label="科目筛选"
              labelHidden
              value={category}
              onChange={(e) => setCategory(e.target.value)}
            >
              <option value="">全部科目</option>
              {chart.map((c) => (
                <option key={c.code} value={c.code}>
                  {c.name} · {c.code}
                </option>
              ))}
            </Select>
            <Select
              label="业务性质筛选"
              labelHidden
              value={kind}
              onChange={(e) => setKind(e.target.value)}
            >
              <option value="">全部业务性质</option>
              {[...new Set([...kinds, ...(kind ? [kind] : [])])].map((k) => (
                <option key={k} value={k}>
                  {kindLabels[k] || k}
                </option>
              ))}
            </Select>
            <Select
              label="统计范围筛选"
              labelHidden
              value={scope}
              onChange={(e) =>
                setScope(e.target.value as "all" | "included" | "excluded")
              }
            >
              <option value="all">全部统计范围</option>
              <option value="included">纳入报表</option>
              <option value="excluded">范围外</option>
            </Select>
            <Button size="sm" look="ghost" onClick={clearFilters}>
              重置筛选
            </Button>
          </div>
          {!!transactionIds.length && (
            <Text look="caption" tone="muted">
              正在查看所选报表项目的 {transactionIds.length}{" "}
              笔来源交易。重置筛选可查看全部。
            </Text>
          )}
          <Text look="caption" tone="muted">
            筛选覆盖全部记录；打开任意交易可查看原始来源、当前统计影响和修改历史。
          </Text>
        </search>
      </header>
      {error ? (
        <Notice kind="error">
          {error}
          <Button
            look="ghost"
            size="sm"
            onClick={() => setReload((n) => n + 1)}
          >
            重试
          </Button>
        </Notice>
      ) : loading ? (
        <Text tone="muted">正在读取交易…</Text>
      ) : shown.length ? (
        <div aria-label="按日分组的交易">
          {shown.map((t, i) => (
            <div key={t.id}>
              {(i === 0 || shown[i - 1].date !== t.date) && (
                <div className="day-heading">
                  <Text look="caption" tone="muted">
                    {t.date || "日期未标注"}
                  </Text>
                </div>
              )}
              {(t.historical ? BigInt(t.historical.expense_minor || "0") > 0n : t.kind === "expense") && <label className="batch-select"><input type="checkbox" aria-label={`选择消费 ${t.description}`} checked={batchItems.some(x => x.id === t.id)} disabled={batchItems.length >= 200 && !batchItems.some(x => x.id === t.id)} onChange={e => setBatchItems(old => e.target.checked ? [...old, t] : old.filter(x => x.id !== t.id))}/>选择这笔消费</label>}
              <button
                className="plain-transaction-row"
                aria-current={selected === t.id ? "true" : undefined}
                onClick={() => setSelected(t.id)}
              >
                <TransactionFace
                  icon={<Icon name="transactions" size={18} />}
                  title={t.description || "未填写描述"}
                  amount={money(
                    transactionDisplayAmount(t),
                    !t.historical || t.version === 1,
                  )}
                  subtitle={`${transactionAccountName(t)} · ${t.category_name || "用途待确认"}`}
                  trailing={transactionAmountLabel(t)}
                >
                  <Text as="span" look="caption" tone="muted">
                    {transactionBusinessName(t)} · {transactionStatusName(t)}
                    {t.historical && t.version > 1
                      ? ` · 原始 ¥${money(t.historical.raw_amount_minor, true)}`
                      : ""}
                  </Text>
                </TransactionFace>
              </button>
            </div>
          ))}
        </div>
      ) : (
        <Empty
          title={
            search
              ? "没有匹配的交易"
              : review
                ? "所选范围没有待确认交易"
                : "所选范围没有交易"
          }
        >
          {search
            ? "换一个关键词或调整筛选条件；搜索覆盖全部记录。"
            : "导入流水或切换月份后，交易会显示在这里。"}
        </Empty>
      )}
      {total > 50 && (
        <div className="pagination">
          <Text look="caption" tone="muted">
            {offset + 1}–{Math.min(offset + 50, total)} / {total} 笔
          </Text>
          <Button
            size="sm"
            disabled={offset === 0 || loading}
            onClick={() => setOffset((n) => Math.max(0, n - 50))}
          >
            上一页
          </Button>
          <Button
            size="sm"
            disabled={offset + 50 >= total || loading}
            onClick={() => setOffset((n) => n + 50)}
          >
            下一页
          </Button>
          <Button
            size="sm"
            disabled={offset + 50 >= total || loading}
            onClick={() => setOffset(Math.floor((total - 1) / 50) * 50)}
          >
            末页
          </Button>
        </div>
      )}
      <Text look="caption" tone="muted">
        迁入记录保留原始金额与来源符号；修改后显示当前业务金额，并附原始值。新记录显示账户变动。收入、费用和现金影响在详情中分别列出。
      </Text>
    </div>
  );
  return (
    <NavigationSplitView
      closeLabel="关闭交易详情"
      replace={() => setSelected(null)}
      content={{ label: "交易", node: master }}
      detail={
        selected
          ? {
              label: "交易详情",
              key: selected,
              back: { href: "#transactions", label: "交易" },
              node: (
                <TransactionDrawer
                  inline
                  id={selected}
                  accounts={accounts}
                  chart={chart}
                  budget={budget}
                  onClose={() => setSelected(null)}
                  onChanged={async () => {
                    setReload((n) => n + 1);
                    await onChanged();
                  }}
                />
              ),
            }
          : null
      }
    />
  );
}
const listStyles = stylex.create({
  master: { inlineSize: "100%", maxInlineSize: "52rem", marginInline: "auto" },
});
function InlineDetail({
  children,
}: {
  title: string;
  onClose: () => void;
  children: ReactNode;
}) {
  return <section className="inline-detail">{children}</section>;
}
export function TransactionDrawer({
  id,
  accounts,
  chart,
  budget,
  onClose,
  onChanged,
  inline = false,
}: {
  id: string;
  accounts: Account[];
  chart: ChartAccount[];
  budget: BudgetResponse | null;
  onClose: () => void;
  onChanged: () => Promise<void>;
  inline?: boolean;
}) {
  const attempt = useRef(createPendingRequest());
  const detailSequence = useRef(0);
  const [detail, setDetail] = useState<TransactionDetail | null>(null),
    [error, setError] = useState(""),
    [loading, setLoading] = useState(true),
    [editing, setEditing] = useState(false),
    [saving, setSaving] = useState(false),
    [draft, setDraft] = useState<Transaction | null>(null),
    [amount, setAmount] = useState(""),
    [reason, setReason] = useState(""),
    [undoing, setUndoing] = useState(false),
    [message, setMessage] = useState(""),
    [transactionBudgets, setTransactionBudgets] = useState<BudgetResponse[]>(
      budget ? [budget] : [],
    );
  async function load() {
    const sequence = ++detailSequence.current;
    setLoading(true);
    setError("");
    try {
      const r = await api<TransactionDetail>(`/transactions/${id}`);
      if (sequence !== detailSequence.current) return;
      setDetail(r);
      setDraft({
        ...r.transaction,
        historical: r.transaction.historical
          ? { ...r.transaction.historical }
          : undefined,
      });
      setAmount(toDecimal(transactionEditAmount(r.transaction)));
    } catch (e) {
      if (sequence === detailSequence.current) setError(errorText(e));
    } finally {
      if (sequence === detailSequence.current) setLoading(false);
    }
  }
  useEffect(() => {
    setEditing(false);
    setUndoing(false);
    setMessage("");
    setDetail(null);
    setDraft(null);
    setReason("");
    void load();
    return () => {
      detailSequence.current += 1;
    };
  }, [id]);
  useEffect(() => {
    const years = [
      ...new Set(
        [draft?.date?.slice(0, 4), draft?.cash_date?.slice(0, 4)].filter(
          (year): year is string => !!year,
        ),
      ),
    ];
    let active = true;
    setTransactionBudgets([]);
    Promise.all(
      years.map((year) => api<BudgetResponse>(`/budgets?year=${year}`)),
    )
      .then((values) => {
        if (active) setTransactionBudgets(values);
      })
      .catch(() => {
        if (active) setTransactionBudgets([]);
      });
    return () => {
      active = false;
    };
  }, [draft?.date?.slice(0, 4), draft?.cash_date?.slice(0, 4)]);
  const budgetChoices = transactionBudgets.flatMap((response) =>
    response.budget
      ? response.budget.lines
          .filter(
            (line) =>
              String(response.budget!.year) ===
              (line.actual_basis === "payment"
                ? draft?.cash_date
                : draft?.date
              )?.slice(0, 4),
          )
          .map((line) => ({ ...line, year: response.budget!.year }))
      : [],
  );
  const patch = (value: Partial<Transaction>) =>
    setDraft((d) => (d ? { ...d, ...value } : d));
  async function save(undo = false) {
    if (!draft || !detail || detail.read_only || detail.transaction.read_only)
      return;
    setSaving(true);
    setError("");
    setMessage("");
    try {
      const payload = undo
        ? { expected_version: detail.transaction.version, reason }
        : {
            expected_version: detail.transaction.version,
            reason,
            payload: draft.historical
              ? {
                  date: draft.date || null,
                  cash_date: draft.cash_date || null,
                  account_id: draft.account_id,
                  description: draft.description,
                  category_code: draft.category_code || null,
                  budget_line_id: draft.budget_line_id || null,
                  historical_event: draft.historical.event,
                  business_amount_minor: toMinor(amount),
                  in_scope: draft.historical.in_scope,
                  review: draft.historical.review,
                }
              : {
                  date: draft.date,
                  description: draft.description,
                  kind: draft.kind,
                  category_code: [
                    "transfer",
                    "repayment",
                    "refund",
                    "unknown",
                  ].includes(draft.kind)
                    ? null
                    : draft.category_code || null,
                  amount_minor: toMinor(amount),
                  counterparty_account_id:
                    draft.counterparty_account_id || null,
                  refund_of: draft.refund_of || null,
                  budget_line_id: draft.budget_line_id || null,
                  counterparty_confirmed:
                    ["transfer", "repayment"].includes(draft.kind)
                      ? !!draft.counterparty_confirmed
                      : false,
                },
          };
      const request_id = attempt.current.key(payload);
      await api(`/transactions/${id}/${undo ? "undo" : "edit"}`, {
        method: "POST",
        body: JSON.stringify({ ...payload, request_id }),
      });
      attempt.current.clear();
      setEditing(false);
      setUndoing(false);
      setReason("");
      await load();
      await onChanged();
      setMessage(
        undo
          ? "已撤销上次修改，历史记录已保留。"
          : "已保存修改，相关报表已更新。",
      );
    } catch (e) {
      setError(errorText(e));
    } finally {
      setSaving(false);
    }
  }
  const t = detail?.transaction;
  const readOnly = !!(detail?.read_only || t?.read_only);
  const DetailFrame = inline ? InlineDetail : Modal;
  return (
    <DetailFrame title="交易详情" onClose={() => !saving && onClose()}>
      <div className="modal-body">
        {error && <Notice kind="error">{error}</Notice>}
        {message && <Notice kind="success">{message}</Notice>}
        {loading ? (
          <div className="loading-state">
            <span className="spinner" />
            正在读取详情…
          </div>
        ) : t && draft ? (
          <>
            {editing && !readOnly ? (
              <>
                <p className="form-intro">
                  原始文件和旧版本保留。保存后重算相关报表、账户变动和已关联预算；有关联的交易会一起校验。
                </p>
                <div className="form-grid">
                  <Field label="日期">
                    <input
                      type="date"
                      value={draft.date || ""}
                      onChange={(e) => patch({ date: e.target.value })}
                    />
                  </Field>
                  {draft.historical ? (
                    <Field label="业务性质">
                      <select
                        value={draft.historical.event}
                        onChange={(e) =>
                          patch({
                            historical: {
                              ...draft.historical!,
                              event: e.target.value,
                              review:
                                e.target.value === "性质待确认"
                                  ? "待确认"
                                  : "人工确认",
                            },
                            category_code: defaultHistoricalCategory(
                              e.target.value,
                              draft.category_code,
                            ),
                          })
                        }
                      >
                        {[
                          ...new Set([
                            draft.historical.event,
                            ...(t.edit_options?.events || []),
                          ]),
                        ].map((event) => (
                          <option key={event} value={event}>
                            {event}
                          </option>
                        ))}
                      </select>
                    </Field>
                  ) : (
                    <Field label="业务性质">
                      <select
                        value={draft.kind}
                        onChange={(e) =>
                          patch({
                            kind: e.target.value,
                            category_code: [
                              "reimbursement",
                              "family_support",
                            ].includes(e.target.value)
                              ? "4005"
                              : null,
                            counterparty_confirmed: false,
                          })
                        }
                      >
                        {Object.entries(kindLabels).map(([v, label]) => (
                          <option key={v} value={v}>
                            {label}
                          </option>
                        ))}
                      </select>
                    </Field>
                  )}
                  {draft.historical && (
                    <Field
                      label="记账账户"
                      hint="可以更正到同类型账户，原始来源账户保持不变。"
                    >
                      <select
                        value={draft.account_id}
                        onChange={(e) => patch({ account_id: e.target.value })}
                      >
                        {accounts
                          .filter(
                            (account) =>
                              account.kind ===
                              accounts.find(
                                (original) => original.id === t.account_id,
                              )?.kind,
                          )
                          .map((account) => (
                            <option key={account.id} value={account.id}>
                              {account.name}
                            </option>
                          ))}
                      </select>
                    </Field>
                  )}
                  <Field label="交易描述">
                    <input
                      value={draft.description}
                      onChange={(e) => patch({ description: e.target.value })}
                    />
                  </Field>
                  <Field
                    label={
                      draft.historical ? "业务金额（元）" : "账户净变动（元）"
                    }
                    hint={
                      draft.historical
                        ? "填写非负金额；收入、费用和现金方向由业务性质决定，原始文件金额保持不变。"
                        : "增加为正；信用负债增加为负。"
                    }
                  >
                    <input
                      inputMode="decimal"
                      value={amount}
                      onChange={(e) => {
                        setAmount(e.target.value);
                        if (draft.kind === "transfer")
                          patch({ counterparty_confirmed: false });
                      }}
                    />
                  </Field>
                  {!["transfer", "repayment", "refund", "unknown"].includes(
                    draft.kind,
                  ) && (
                    <Field label="统计科目">
                      <select
                        disabled={
                          draft.historical
                            ? !!historicalIncomeCategories[
                                draft.historical.event
                              ]
                            : ["reimbursement", "family_support"].includes(
                                draft.kind,
                              )
                        }
                        value={draft.category_code || ""}
                        onChange={(e) =>
                          patch({ category_code: e.target.value || null })
                        }
                      >
                        <option value="">用途待确认</option>
                        {chart
                          .filter(
                            (c) =>
                              (c.selectable ||
                                (draft.historical &&
                                  c.code === draft.category_code)) &&
                              c.enabled &&
                              (draft.historical
                                ? historicalIncomeCategories[
                                    draft.historical.event
                                  ]
                                  ? c.code ===
                                    historicalIncomeCategories[
                                      draft.historical.event
                                    ]
                                  : c.code === draft.category_code ||
                                    c.code.startsWith("5")
                                : draft.kind === "expense"
                                  ? c.code.startsWith("5")
                                  : ["4001", "4004", "4005"].includes(c.code)),
                          )
                          .map((c) => (
                            <option key={c.code} value={c.code}>
                              {c.name} · {c.code}
                            </option>
                          ))}
                      </select>
                    </Field>
                  )}
                  <Field
                    label="预算项目"
                    hint={!draft.historical && draft.kind === "repayment" ? "普通信用还款不重复占用预算；本版暂不开放新增付款预算关联。" : "消费预算按确认日期所属年度；付款预算按收付日期所属年度。"}
                  >
                    <select
                      value={draft.budget_line_id || ""}
                      onChange={(e) =>
                        patch({ budget_line_id: e.target.value || null })
                      }
                    >
                      <option value="">尚未分配</option>
                      {draft.budget_line_id &&
                        !budgetChoices.some(
                          (line) => line.id === draft.budget_line_id,
                        ) && (
                          <option value={draft.budget_line_id}>
                            当前关联（请核对预算年度）
                          </option>
                        )}
                      {budgetChoices.map((l) => (
                        <option key={`${l.year}-${l.id}`} value={l.id} disabled={!draft.historical && draft.kind === "repayment" && l.actual_basis === "payment" && l.id !== t.budget_line_id}>
                          {l.year} · {l.group} / {l.name} ·{" "}
                          {l.actual_basis === "payment"
                            ? "按收付日期"
                            : "按确认日期"}
                        </option>
                      ))}
                    </select>
                  </Field>
                  {draft.historical && (
                    <>
                      <Field
                        label="收付日期"
                        hint="现金流和付款预算按收付日期统计。"
                      >
                        <input
                          type="date"
                          value={draft.cash_date || ""}
                          onChange={(e) =>
                            patch({ cash_date: e.target.value || null })
                          }
                        />
                      </Field>
                      <Field label="复核状态">
                        <select
                          value={draft.historical.review || ""}
                          onChange={(e) =>
                            patch({
                              historical: {
                                ...draft.historical!,
                                review: e.target.value,
                              },
                            })
                          }
                        >
                          {[
                            ...new Set([
                              draft.historical.review || "",
                              "人工确认",
                              "待确认",
                              "分类暂定",
                            ]),
                          ].map((review) => (
                            <option key={review} value={review}>
                              {review || "未填写"}
                            </option>
                          ))}
                        </select>
                      </Field>
                      <label className="check-field">
                        <input
                          type="checkbox"
                          checked={draft.historical.in_scope}
                          onChange={(e) =>
                            patch({
                              historical: {
                                ...draft.historical!,
                                in_scope: e.target.checked,
                              },
                            })
                          }
                        />
                        <span>纳入报表统计范围</span>
                      </label>
                    </>
                  )}
                  {t.edit_options?.relation_note && (
                    <Notice>{t.edit_options.relation_note}</Notice>
                  )}
                  {["transfer", "repayment"].includes(draft.kind) && (
                    <Field label="对方账户">
                      <select
                        value={draft.counterparty_account_id || ""}
                        onChange={(e) =>
                          patch({
                            counterparty_account_id: e.target.value || null,
                            counterparty_confirmed: false,
                          })
                        }
                      >
                        <option value="">请选择</option>
                        {accounts
                          .filter((a) => a.id !== draft.account_id)
                          .map((a) => (
                            <option key={a.id} value={a.id}>
                              {a.name}
                            </option>
                          ))}
                      </select>
                    </Field>
                  )}
                  {["transfer", "repayment"].includes(draft.kind) && (
                    <div>
                    <label className="check-field">
                      <input
                        type="checkbox"
                        checked={!!draft.counterparty_confirmed}
                        disabled
                      />
                      <span>{draft.counterparty_confirmed ? "保留已有的两端确认" : "对端关系尚待核对"}</span>
                    </label>
                    <p className="footnote">本版保留各账户已知流水。新增两端确认需要完整关联操作，暂不在单笔编辑中开放；填选对端账户不会自动合并或推导另一笔。</p>
                    </div>
                  )}
                  {draft.kind === "refund" && (
                    <Field
                      label="原消费交易编号"
                      hint="在原消费详情的「账户变动与记账信息」中查看。"
                    >
                      <input
                        value={draft.refund_of || ""}
                        onChange={(e) =>
                          patch({ refund_of: e.target.value || null })
                        }
                      />
                    </Field>
                  )}
                </div>
                <Field
                  label="修改原因"
                  hint={
                    draft.historical
                      ? "金额、业务性质、日期、账户或统计范围有变化时需填写。"
                      : "金额、业务性质或互转依据有变化时需填写。"
                  }
                >
                  <textarea
                    rows={3}
                    value={reason}
                    onChange={(e) => setReason(e.target.value)}
                    placeholder="例如：核对订单后确认是餐饮消费"
                  />
                </Field>
              </>
            ) : (
              <>
                <div className="detail-summary">
                  <span
                    className={`badge ${t.status === "needs_review" ? "amber" : "mint"}`}
                  >
                    {transactionStatusName(t)}
                  </span>
                  <Heading level={3} look="h4">
                    {t.description}
                  </Heading>
                  <p>
                    {t.date || "日期未标注"} · {transactionAccountName(t)} ·{" "}
                    {transactionBusinessName(t)}
                  </p>
                </div>
                {t.historical && (
                  <Notice>
                    历史记录可直接修改，原始凭据和每个版本均保留。
                    {t.status === "excluded"
                      ? "此记录在统计范围外，不计入当前报表。"
                      : ""}
                  </Notice>
                )}
                {t.historical && (
                  <section className="detail-section">
                    <Heading level={3} look="h4">
                      当前业务判断
                    </Heading>
                    <dl>
                      <div>
                        <dt>原始来源账户</dt>
                        <dd>
                          {t.historical.source_account_label ||
                            t.historical.account ||
                            "未提供"}
                        </dd>
                      </div>
                      <div>
                        <dt>当前业务金额</dt>
                        <dd>¥ {money(transactionEditAmount(t))}</dd>
                      </div>
                      <div>
                        <dt>业务性质</dt>
                        <dd>{t.historical.event || "未填写"}</dd>
                      </div>
                      <div>
                        <dt>复核状态</dt>
                        <dd>{t.historical.review || "未填写"}</dd>
                      </div>
                      <div>
                        <dt>判断依据</dt>
                        <dd>{t.historical.reason || "未填写"}</dd>
                      </div>
                      <div>
                        <dt>统计范围</dt>
                        <dd>
                          {t.historical.in_scope ? "纳入统计范围" : "范围外"}
                        </dd>
                      </div>
                      <div>
                        <dt>现金统计</dt>
                        <dd>
                          {t.historical.cash_included
                            ? "按原口径计入"
                            : "不计入原现金统计"}
                        </dd>
                      </div>
                      {t.historical.product_description && (
                        <div>
                          <dt>商品说明</dt>
                          <dd>{t.historical.product_description}</dd>
                        </div>
                      )}
                      {t.historical.family && (
                        <div>
                          <dt>账单来源</dt>
                          <dd>{t.historical.family}</dd>
                        </div>
                      )}
                      {t.historical.behavior && (
                        <div>
                          <dt>行为标签</dt>
                          <dd>{t.historical.behavior}</dd>
                        </div>
                      )}
                      {t.historical.reimb && (
                        <div>
                          <dt>报销标记</dt>
                          <dd>{t.historical.reimb}</dd>
                        </div>
                      )}
                    </dl>
                  </section>
                )}
                {t.review_reasons.length > 0 && (
                  <Notice>
                    {t.review_reasons
                      .map((r) =>
                        typeof r === "string"
                          ? r
                          : r.message || r.description || r.reason_code,
                      )
                      .join("；")}
                  </Notice>
                )}
                <section className="detail-section">
                  <Heading level={3} look="h4">
                    金额影响
                  </Heading>
                  <div className="original-amount">
                    <span>原始文件金额</span>
                    <b>
                      {detail.sources[0]?.raw?.amount != null
                        ? String(detail.sources[0].raw.amount)
                        : "无来源金额"}{" "}
                      <small>
                        {String(detail.sources[0]?.raw?.currency || t.currency)}
                      </small>
                    </b>
                    <p>
                      {t.kind === "historical"
                        ? "金额与正负方向沿用原始来源，不转换为新模板的账户变动规则。"
                        : "标准模板中，现金账户增加为正；信用账户负债增加为负。"}
                    </p>
                  </div>
                  <dl className="impact-grid">
                    {[
                      ["计入收入", "income_minor"],
                      ["计入费用", "expense_minor"],
                      ["现金流入", "cash_inflow_minor"],
                      ["现金流出", "cash_outflow_minor"],
                      ["预算占用", "budget_usage_minor"],
                    ].map(([label, key]) => {
                      const value =
                        detail.effects?.[
                          key as keyof NonNullable<TransactionDetail["effects"]>
                        ];
                      return (
                        <div key={key}>
                          <dt>{label}</dt>
                          <dd className="amount">
                            {value == null
                              ? "待确认"
                              : value === "0"
                                ? "不计入"
                                : `¥ ${money(value)}`}
                          </dd>
                        </div>
                      );
                    })}
                  </dl>
                  <p className="footnote">
                    {t.kind === "historical"
                      ? "按当前有效版本计算。原始来源金额保留不变，预算实际按已关联项目和执行口径计算；范围外记录不计入报表。"
                      : "收入与费用按确认日期；现金按真实收付；预算占用按已关联项目的执行规则。"}
                  </p>
                  {detail.effects?.expense_minor?.startsWith("-") && (
                    <p className="footnote">
                      费用为负表示退款、返利或贴息抵减支出，不计为新增收入。业务金额显示绝对值，统计影响以上述收入、费用与现金列为准。
                    </p>
                  )}
                </section>
                <section className="detail-section">
                  <Heading level={3} look="h4">
                    来源证据 <span>{detail.sources.length} 份</span>
                  </Heading>
                  {detail.sources.map((s) => (
                    <details className="source-detail" key={s.id}>
                      <summary>
                        <span>
                          {s.filename}
                          <small>
                            {t.kind === "historical"
                              ? historicalSourceLocation(s.raw)
                              : `第 ${s.row_number} 行`}
                            {" · 原始记录"}
                          </small>
                        </span>
                        <Icon name="chevron" size={16} />
                      </summary>
                      <dl>
                        {coreSourceFields(s.raw).map(
                          ({ key, label, value }) => (
                            <div key={key}>
                              <dt>{label}</dt>
                              <dd>{sourceValue(value)}</dd>
                            </div>
                          ),
                        )}
                      </dl>
                      <details>
                        <summary>查看完整来源记录</summary>
                        <pre className="source-json">
                          {JSON.stringify(s.raw, null, 2)}
                        </pre>
                      </details>
                    </details>
                  ))}
                </section>
                <section className="detail-section">
                  <Heading level={3} look="h4">
                    修改历史
                  </Heading>
                  <ol className="history-list">
                    {detail.history.map((h) => (
                      <li key={h.version}>
                        <i />
                        <div>
                          <b>
                            {h.action === "migrate" || h.action === "migration"
                              ? "迁入历史记录"
                              : h.action === "import"
                                ? "导入记录"
                                : h.action === "undo"
                                  ? "撤销修改"
                                  : "修改记录"}{" "}
                            <span>第 {h.version} 版</span>
                          </b>
                          <p>{h.reason || "未填写补充说明"}</p>
                          {!!historyChanges(h.before, h.after).length && (
                            <details className="history-changes">
                              <summary>查看本次变更</summary>
                              <dl>
                                {historyChanges(h.before, h.after).map(
                                  (change, index) => (
                                    <div key={`${change.label}-${index}`}>
                                      <dt>{change.label}</dt>
                                      <dd>
                                        <span>{change.before}</span>
                                        <span aria-label="变更为"> → </span>
                                        <strong>{change.after}</strong>
                                      </dd>
                                    </div>
                                  ),
                                )}
                              </dl>
                            </details>
                          )}
                          <small>
                            {new Date(h.created_at).toLocaleString("zh-CN", {
                              hour12: false,
                            })}
                          </small>
                        </div>
                      </li>
                    ))}
                  </ol>
                </section>
                <details className="advanced">
                  <summary>账户变动与记账信息</summary>
                  <dl>
                    <div>
                      <dt>交易编号</dt>
                      <dd className="break-all">{t.id}</dd>
                    </div>
                    <div>
                      <dt>{transactionAmountLabel(t)}</dt>
                      <dd>
                        ¥{" "}
                        {money(
                          transactionDisplayAmount(t),
                          !t.historical || t.version === 1,
                        )}
                      </dd>
                    </div>
                    <div>
                      <dt>统计科目</dt>
                      <dd>
                        {t.category_name || "用途待确认"} {t.category_code}
                      </dd>
                    </div>
                    <div>
                      <dt>预算项目</dt>
                      <dd>
                        {budgetChoices.find((l) => l.id === t.budget_line_id)
                          ?.name ||
                          (t.budget_line_id
                            ? "其他预算年度的项目"
                            : "尚未分配")}
                      </dd>
                    </div>
                  </dl>
                  {detail.ledger_lines.length > 0 && (
                    <div className="table-scroll">
                      <table>
                        <thead>
                          <tr>
                            <th>科目</th>
                            <th>借方</th>
                            <th>贷方</th>
                          </tr>
                        </thead>
                        <tbody>
                          {detail.ledger_lines.map((l, i) => (
                            <tr key={i}>
                              <td>{l.name}</td>
                              <td>{money(l.debit_minor)}</td>
                              <td>{money(l.credit_minor)}</td>
                            </tr>
                          ))}
                        </tbody>
                      </table>
                    </div>
                  )}
                </details>
                {undoing && !readOnly && (
                  <div className="undo-confirm">
                    <Heading level={3} look="h4">
                      撤销上次修改？
                    </Heading>
                    <p>
                      会恢复上一个版本的业务字段，并重新计算报表。来源文件和所有历史仍保留。
                    </p>
                    <Field label="撤销说明（可选）">
                      <input
                        value={reason}
                        onChange={(e) => setReason(e.target.value)}
                      />
                    </Field>
                  </div>
                )}
              </>
            )}
          </>
        ) : (
          !error && <Empty title="暂时没有交易详情">请关闭后重新打开。</Empty>
        )}
      </div>
      {t && !readOnly && (
        <footer className="modal-footer">
          {editing && !readOnly ? (
            <>
              <Button
                disabled={saving}
                onClick={() => {
                  setEditing(false);
                  setDraft({
                    ...t,
                    historical: t.historical ? { ...t.historical } : undefined,
                  });
                  setAmount(toDecimal(transactionEditAmount(t)));
                  setError("");
                }}
              >
                取消修改
              </Button>
              <Button
                disabled={saving}
                onClick={() => void save()}
                look="primary"
              >
                {saving ? "正在保存…" : "保存修改"}
              </Button>
            </>
          ) : undoing ? (
            <>
              <Button disabled={saving} onClick={() => setUndoing(false)}>
                保留修改
              </Button>
              <Button
                disabled={saving}
                onClick={() => void save(true)}
                look="primary"
              >
                {saving ? "正在撤销…" : "确认撤销"}
              </Button>
            </>
          ) : (
            <>
              <Button
                disabled={detail.history.at(-1)?.action !== "edit"}
                onClick={() => setUndoing(true)}
              >
                <Icon name="undo" size={17} />
                撤销上次修改
              </Button>
              <Button
                onClick={() => {
                  setEditing(true);
                  setMessage("");
                }}
                look="primary"
              >
                <Icon name="edit" size={17} />
                修改这笔
              </Button>
            </>
          )}
        </footer>
      )}
    </DetailFrame>
  );
}
