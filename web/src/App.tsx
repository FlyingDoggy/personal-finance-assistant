import * as stylex from "@stylexjs/stylex";
import { TabView } from "@tuja/ui/components/tab-view";
import { Button } from "@tuja/ui/components/button";
import { Heading } from "@tuja/ui/components/heading";
import { Text } from "@tuja/ui/components/text";
import { List } from "@tuja/ui/components/list";
import { ListRow } from "@tuja/ui/components/list-row";
import { stack } from "@tuja/ui/primitives/stack.stylex";
import { HouseIcon } from "@phosphor-icons/react/dist/ssr/House";
import { ReceiptIcon } from "@phosphor-icons/react/dist/ssr/Receipt";
import { CalendarBlankIcon } from "@phosphor-icons/react/dist/ssr/CalendarBlank";
import { ChartBarIcon } from "@phosphor-icons/react/dist/ssr/ChartBar";
import { GearIcon } from "@phosphor-icons/react/dist/ssr/Gear";
import { FinancePageColumn } from "./upstream/finance-page-column";
import { FinanceLogo } from "./upstream/finance-logo";
import { AnalyticsSummary } from "./upstream/analytics-summary";
import { TransactionFace } from "./upstream/transaction-face";
import { useCallback, useEffect, useRef, useState } from "react";
import { api, errorText, money } from "./api";
import { BudgetPage, budgetConnected } from "./Budget";
import { AccountModal, MorePage } from "./More";
import { ImportModal, ImportWorkspace } from "./ImportWorkspace";
import { AIWorkspace } from "./AIWorkspace";
import { CatalogPage, ReportsWorkspace, VouchersPage } from "./LedgerWorkspace";
import { SettingsPage } from "./Settings";
import {
  transactionBusinessName,
  transactionAccountName,
  transactionAmountLabel,
  transactionDisplayAmount,
  transactionStatusName,
  TransactionDrawer,
  TransactionsPage,
  type TransactionFilters,
} from "./Transactions";
import type {
  Account,
  BudgetResponse,
  ChartAccount,
  ImportBatch,
  Meta,
  Reports,
  Status,
  Transaction,
} from "./types";
import { Empty, Icon, Notice } from "./ui";
type Page = "home" | "transactions" | "budget" | "reports" | "more" | "vouchers" | "catalog" | "settings" | "imports" | "ai";
const navigation: { id: Page; label: string; icon: typeof HouseIcon }[] = [
  { id: "home", label: "本月", icon: HouseIcon },
  { id: "transactions", label: "交易", icon: ReceiptIcon },
  { id: "budget", label: "预算", icon: CalendarBlankIcon },
  { id: "reports", label: "报表", icon: ChartBarIcon },
  { id: "more", label: "更多", icon: GearIcon },
];
const todayMonth = () =>
  `${new Date().getFullYear()}-${String(new Date().getMonth() + 1).padStart(2, "0")}`;
function savedMonth(key: string) {
  try {
    const value = localStorage.getItem(key);
    return value && /^\d{4}-(0[1-9]|1[0-2])$/.test(value) ? value : null;
  } catch {
    return null;
  }
}
function initialMonth() {
  return savedMonth("ledger-month") || todayMonth();
}

function initialPage(): Page {
  const hash = location.hash.slice(1);
  if (hash === "settings" || hash.startsWith("settings/")) return "settings";
  return navigation.some((p) => p.id === hash) || ["vouchers", "catalog", "imports", "ai"].includes(hash) ? (hash as Page) : "home";
}
export default function App() {
  const [page, setPage] = useState<Page>(initialPage),
    [month, setMonth] = useState(initialMonth),
    [budgetMonth, setBudgetMonth] = useState(
      () => savedMonth("ledger-budget-month") || todayMonth(),
    ),
    [planBudget, setPlanBudget] = useState<BudgetResponse | null>(null),
    [loadedBudgetMonth, setLoadedBudgetMonth] = useState(""),
    [accounts, setAccounts] = useState<Account[]>([]),
    [imports, setImports] = useState<ImportBatch[]>([]),
    [chart, setChart] = useState<ChartAccount[]>([]),
    [reports, setReports] = useState<Reports | null>(null),
    [budget, setBudget] = useState<BudgetResponse | null>(null),
    [status, setStatus] = useState<Status | null>(null),
    [recent, setRecent] = useState<Transaction[]>([]),
    [ready, setReady] = useState(false),
    [loading, setLoading] = useState(true),
    [error, setError] = useState(""),
    [modal, setModal] = useState<"account" | "import" | null>(null),
    [selected, setSelected] = useState<string | null>(null),
    [transactionFilters, setTransactionFilters] = useState<TransactionFilters>(
      {},
    ),
    [transactionViewKey, setTransactionViewKey] = useState(0),
    [loadedMonth, setLoadedMonth] = useState("");
  const initialSelections = useRef({
    actual: savedMonth("ledger-month"),
    budget: savedMonth("ledger-budget-month"),
  });
  const loadSequence = useRef(0);
  const load = useCallback(async () => {
    const sequence = ++loadSequence.current;
    setLoading(true);
    setError("");
    try {
      const [a, i, c, r, b, s, t, pb] = await Promise.all([
        api<Meta & { items: Account[] }>("/accounts"),
        api<Meta & { items: ImportBatch[] }>("/imports"),
        api<{ items: ChartAccount[] }>("/chart-accounts"),
        api<Reports>(`/reports?month=${month}`),
        api<BudgetResponse>(`/budgets?year=${month.slice(0, 4)}`),
        api<Status>("/status"),
        api<Meta & { items: Transaction[] }>(
          `/transactions?month=${month}&limit=5`,
        ),
        api<BudgetResponse>(`/budgets?year=${budgetMonth.slice(0, 4)}`),
      ]);
      if (sequence !== loadSequence.current) return;
      setAccounts(a.items);
      setImports(i.items);
      setChart(c.items);
      setReports(r);
      setBudget(b);
      setPlanBudget(pb);
      setLoadedBudgetMonth(budgetMonth);
      setStatus(s);
      setRecent(t.items);
      setLoadedMonth(month);
    } catch (e) {
      if (sequence === loadSequence.current) setError(errorText(e));
    } finally {
      if (sequence === loadSequence.current) setLoading(false);
    }
  }, [month, budgetMonth]);
  useEffect(() => {
    let active = true;
    api("/session")
      .then(() => api<Status>("/status"))
      .then((initialStatus) => {
        if (!active) return;
        setStatus(initialStatus);
        const latestActual = [...(initialStatus.available_months || [])]
          .sort()
          .at(-1);
        const latestBudget = [...(initialStatus.budget_years || [])]
          .sort((a, b) => a - b)
          .at(-1);
        if (!initialSelections.current.actual && latestActual)
          setMonth(latestActual);
        if (!initialSelections.current.budget && latestBudget)
          setBudgetMonth(`${latestBudget}-01`);
        setReady(true);
      })
      .catch((e) => {
        if (active) {
          setError(errorText(e));
          setLoading(false);
        }
      });
    return () => {
      active = false;
    };
  }, []);
  useEffect(() => {
    if (ready) void load();
  }, [ready, load]);
  useEffect(() => {
    try {
      localStorage.setItem("ledger-month", month);
    } catch {
      /* persistent selection is optional */
    }
  }, [month]);
  useEffect(() => {
    try {
      localStorage.setItem("ledger-budget-month", budgetMonth);
    } catch {
      /* preference persistence is optional */
    }
  }, [budgetMonth]);
  useEffect(() => {
    const onHash = () => setPage(initialPage());
    window.addEventListener("hashchange", onHash);
    return () => window.removeEventListener("hashchange", onHash);
  }, []);
  function navigate(next: Page, review = false) {
    if (next === "transactions") {
      setTransactionFilters({ reviewOnly: review });
      setTransactionViewKey((n) => n + 1);
    }
    setPage(next);
    location.hash = next;
    window.scrollTo({ top: 0, behavior: "instant" });
  }
  function openTransactions(filters: TransactionFilters = {}) {
    navigate("transactions");
    setTransactionFilters(filters);
  }
  const activeMonth = page === "budget" ? budgetMonth : month;
  const setActiveMonth = page === "budget" ? setBudgetMonth : setMonth;
  function changeMonth(delta: number) {
    const date = new Date(
      Number(activeMonth.slice(0, 4)),
      Number(activeMonth.slice(5)) - 1 + delta,
      1,
    );
    setActiveMonth(
      `${date.getFullYear()}-${String(date.getMonth() + 1).padStart(2, "0")}`,
    );
  }
  const readableMonth = `${activeMonth.slice(0, 4)} 年 ${Number(activeMonth.slice(5))} 月`;
  const periodReady =
    page === "budget"
      ? loadedBudgetMonth === budgetMonth
      : loadedMonth === month;
  const pageContent = (
    <>
      <div className="screen-toolbar">
        <div className="month-picker">
          <Button
            look="ghost"
            size="sm"
            aria-label="上个月"
            onClick={() => changeMonth(-1)}
          >
            <Icon name="chevron" size={16} />
          </Button>
          <label>
            <span>{readableMonth}</span>
            <input
              aria-label="选择月份"
              type="month"
              value={activeMonth}
              onChange={(e) => e.target.value && setActiveMonth(e.target.value)}
            />
          </label>
          <Button
            look="ghost"
            size="sm"
            aria-label="下个月"
            onClick={() => changeMonth(1)}
          >
            <Icon name="chevron" size={16} />
          </Button>
        </div>
        <Button
          look="primary"
          size="sm"
          icon={<Icon name="upload" size={17} />}
          onClick={() => setModal("import")}
        >
          导入流水
        </Button>
      </div>
      <Text look="caption" tone="muted">
        {loading
          ? "正在同步账本"
          : page === "budget" && planBudget?.budget
            ? planBudget.migration
              ? budgetConnected(planBudget, budgetMonth)
                ? "预算计划已迁入；实际按已关联交易计算"
                : "预算计划已迁入；实际尚未完整关联"
              : "预算计划已保存；实际按交易关联情况计算"
            : reports?.coverage.status === "empty"
              ? "尚无资料"
              : reports?.coverage.status === "complete"
                ? "当前范围已核对"
                : "资料部分覆盖，金额以已知记录为准"}
        {status ? ` · 修订 ${status.data_revision}` : ""}
      </Text>
      {error && (
        <Notice kind="error">
          {error}
          <Button
            look="ghost"
            size="sm"
            onClick={() => (ready ? void load() : location.reload())}
          >
            重新读取
          </Button>
        </Notice>
      )}
      {!periodReady && !error ? (
        <div className="initial-loading">
          <Text tone="muted">正在读取 {readableMonth}…</Text>
        </div>
      ) : !ready || !periodReady ? (
        <Empty icon="alert" title="账本尚未连接">
          请确认本机服务正在运行，然后重新读取。
        </Empty>
      ) : page === "home" ? (
        <Home
          month={month}
          budget={budget}
          reports={reports}
          accounts={accounts}
          recent={recent}
          onNavigate={navigate}
          onAccount={() => setModal("account")}
          onImport={() => setModal("import")}
          onTransaction={setSelected}
          onRawData={() => openTransactions({ allPeriods: true })}
          availableBudgetYears={status?.budget_years || []}
        />
      ) : page === "budget" ? (
        <BudgetPage
          key={budgetMonth.slice(0, 4)}
          data={planBudget}
          month={budgetMonth}
          availableYears={status?.budget_years || []}
          onYearChange={(year) =>
            setBudgetMonth(`${year}-${budgetMonth.slice(5)}`)
          }
          onSaved={load}
        />
      ) : page === "reports" ? (
        <ReportsWorkspace
          data={reports}
          onTransactions={(filters) => openTransactions(filters)}
        />
      ) : page === "vouchers" ? (
        <VouchersPage month={month} accounts={accounts} chart={chart} budget={budget} onChanged={load} onTransactions={() => openTransactions({ allPeriods: true })}/>
      ) : page === "imports" ? (
        <ImportWorkspace accounts={accounts} onSaved={load} onAddAccount={() => setModal("account")}/>
      ) : page === "ai" ? (
        <AIWorkspace onChanged={load} onTransaction={setSelected}/>
      ) : page === "catalog" ? (
        <CatalogPage onBack={() => navigate("more")}/>
      ) : (
        <MorePage
          accounts={accounts}
          imports={imports}
          status={status}
          onAddAccount={() => setModal("account")}
          onImport={() => setModal("import")}
          onVouchers={() => navigate("vouchers")}
          onCatalog={() => navigate("catalog")}
          onSettings={() => navigate("settings")}
        />
      )}
    </>
  );
  return (
    <div css={appStyles.tokens}>
      <TabView
        label="生活账本"
        effect="none"
        header={
          <a href="#home" aria-label="生活账本" css={appStyles.logo}>
            <FinanceLogo css={appStyles.logo} />
          </a>
        }
        footer={
          <Button
            look="ghost"
            size="sm"
            aria-label="本机账本状态"
            onClick={() => navigate("more")}
          >
            <Icon name="wallet" size={19} />
          </Button>
        }
        items={navigation.map((item) => ({
          href: `#${item.id}`,
          label: item.label,
          icon: item.icon,
          current: page === item.id || (page === "vouchers" && item.id === "transactions") || (["catalog", "settings", "imports", "ai"].includes(page) && item.id === "more"),
        }))}
        screenKey={page}
      >
        {page === "settings" && ready ? (
          <SettingsPage accounts={accounts} status={status} onAccounts={() => navigate("more")} onImport={() => setModal("import")} onBudget={() => navigate("budget")} onVouchers={() => navigate("vouchers")} onChanged={load}/>
        ) : page === "transactions" && ready && loadedMonth === month ? (
          <TransactionsPage
            key={`transactions-${transactionViewKey}`}
            month={month}
            accounts={accounts}
            chart={chart}
            budget={budget}
            initialFilters={transactionFilters}
            onChanged={load}
            toolbar={
              <div className="screen-toolbar">
                <label className="compact-month">
                  <input
                    type="month"
                    aria-label="选择月份"
                    value={month}
                    onChange={(e) => e.target.value && setMonth(e.target.value)}
                  />
                </label>
                <Button look="ghost" size="sm" onClick={() => navigate("vouchers")}>凭证明细</Button>
                <Button
                  look="primary"
                  size="sm"
                  onClick={() => setModal("import")}
                >
                  导入流水
                </Button>
              </div>
            }
          />
        ) : (
          <FinancePageColumn>
            <div className="page-content">{pageContent}</div>
          </FinancePageColumn>
        )}
      </TabView>
      {modal === "account" && (
        <AccountModal onClose={() => setModal(null)} onSaved={load} />
      )}{" "}
      {modal === "import" && (
        <ImportModal
          accounts={accounts}
          onClose={() => setModal(null)}
          onSaved={load}
          onAddAccount={() => setModal("account")}
        />
      )}{" "}
      {selected && (
        <TransactionDrawer
          id={selected}
          accounts={accounts}
          chart={chart}
          budget={budget}
          onClose={() => setSelected(null)}
          onChanged={load}
        />
      )}
    </div>
  );
}
function Home({
  month,
  budget,
  reports,
  accounts,
  recent,
  onNavigate,
  onAccount,
  onImport,
  onTransaction,
  onRawData,
  availableBudgetYears,
}: {
  month: string;
  budget: BudgetResponse | null;
  reports: Reports | null;
  accounts: Account[];
  recent: Transaction[];
  onNavigate: (page: Page, review?: boolean) => void;
  onAccount: () => void;
  onImport: () => void;
  onTransaction: (id: string) => void;
  onRawData: () => void;
  availableBudgetYears: number[];
}) {
  const b = budget?.budget,
    m = b?.months[Number(month.slice(5)) - 1],
    connected = budgetConnected(budget, month, "consumption"),
    fixedConnected = m?.fixed_outstanding_minor != null;
  return (
    <div css={stack.group}>
      <header css={stack.text}>
        <Heading level={1} look="h3">
          本月
        </Heading>
        <Text look="bodySmall" tone="muted">
          {Number(month.slice(5))} 月生活安排
          {b ? ` · 已采用第 ${budget?.version} 版预算` : ""}
        </Text>
      </header>
      <AnalyticsSummary
        heading="本月日常剩余"
        totalText={
          !b
            ? "本期未设预算"
            : !connected
              ? "资料待补齐"
              : `¥${money(m?.daily_remaining_minor)}`
        }
        detail={
          !b
            ? availableBudgetYears.length
              ? `已保存 ${availableBudgetYears.join("、")} 年预算，可在预算页面查看。`
              : "先按生活用途安排每月资金。"
            : !connected
              ? `已知日常占用 ¥${money(m?.daily_usage_minor)}；覆盖范围或分类未齐，剩余额度暂不作结论。`
              : "普通日常额度减已确认占用；年度安排单独保留。"
        }
      />
      {!b && (
        <div>
          <Button look="primary" onClick={() => onNavigate("budget")}>
            {availableBudgetYears.length ? "查看已保存预算" : "创建年度预算"}
          </Button>
        </div>
      )}
      <div className="summary-columns">
        <AnalyticsSummary
          heading="固定及历史分期待付"
          totalText={
            !b
              ? "未设置"
              : !fixedConnected
                ? "付款资料待补齐"
                : `¥${money(m?.fixed_outstanding_minor)}`
          }
          detail={b ? `本月固定安排 ¥${money(m?.fixed_minor)}` : undefined}
        />
        <AnalyticsSummary
          heading="年度项目剩余"
          totalText={!b ? "未设置" : !connected ? "实际待关联" : "在预算中查看"}
          detail="年度额度不等于本月已付款。"
        />
      </div>
      {b && (
        <>
          <div className="summary-columns">
            <AnalyticsSummary
              heading="预计全年预算留存"
              totalText={`¥${money(b.annual.planned_retention_minor)}`}
            />
            <AnalyticsSummary
              heading="达到目标还需调整"
              totalText={`¥${money(b.annual.target_gap_minor)}`}
              detail={`年度目标 ¥${money(b.annual.target_minor)}`}
            />
          </div>
          <div>
            <Button onClick={() => onNavigate("budget")}>查看生活预算</Button>
          </div>
        </>
      )}
      {!accounts.length && (
        <div css={stack.tight}>
          <Heading level={2} look="h4">
            还没有账户
          </Heading>
          <Text>先添加现金账户或信用账户，再导入真实流水。</Text>
          <Text look="bodySmall" tone="muted">
            起始余额未知可以留空。这里只记录你的真实资料。
          </Text>
          <div>
            <Button
              look="primary"
              icon={<Icon name="plus" size={17} />}
              onClick={onAccount}
            >
              添加账户
            </Button>
          </div>
        </div>
      )}
      <section css={stack.item}>
        <div className="section-heading">
          <Heading level={2} look="h4">
            最近交易
          </Heading>
          <Button
            look="ghost"
            size="sm"
            onClick={() => onNavigate("transactions")}
          >
            全部交易
          </Button>
        </div>
        {recent.length ? (
          <div>
            {recent.map((t) => (
              <button
                className="plain-transaction-row"
                key={t.id}
                onClick={() => onTransaction(t.id)}
              >
                <TransactionFace
                  icon={<Icon name="transactions" size={19} />}
                  title={t.description}
                  amount={money(
                    transactionDisplayAmount(t),
                    !t.historical || t.version === 1,
                  )}
                  subtitle={`${t.date || "日期未标注"} · ${transactionAccountName(t)} · ${transactionBusinessName(t)} · ${transactionStatusName(t)}`}
                  trailing={transactionAmountLabel(t)}
                />
              </button>
            ))}
          </div>
        ) : (
          <div css={stack.item}>
            <Text tone="muted">本月还没有交易。</Text>
            <div>
              <Button onClick={onImport}>导入流水</Button>
            </div>
          </div>
        )}
      </section>
      <List aria-label="待处理与资料">
        <ListRow
          onClick={() => onNavigate("vouchers")}
          detail="按账户和科目查看借贷分录，追溯来源与修改"
          trailing={<Icon name="chevron" size={18} />}
        >
          凭证明细
        </ListRow>
        <ListRow
          onClick={onRawData}
          detail="跨月筛选全部交易，查看原始凭据、分类和统计影响"
          trailing={<Icon name="chevron" size={18} />}
        >
          全部交易与原始明细
        </ListRow>
        <ListRow
          onClick={() => onNavigate("transactions", true)}
          detail={
            reports?.review.count
              ? "补齐业务性质、用途或关联依据"
              : "本月暂无待确认交易"
          }
          trailing={
            <Text as="span" numeric>
              {reports?.review.count ?? "—"} 笔
            </Text>
          }
        >
          待确认交易
        </ListRow>
        <ListRow
          onClick={() => onNavigate("more")}
          detail="未知余额与资料缺口不会按零处理"
        >
          账户与资料
        </ListRow>
      </List>
    </div>
  );
}
const appStyles = stylex.create({
  logo: { display: "block", inlineSize: "2.5rem", blockSize: "2.5rem" },
  tokens: { minBlockSize: "100dvh" },
});
