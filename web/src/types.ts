export type Coverage = {
  status: "partial" | "empty" | "complete";
  reasons: string[];
};
export type Meta = { data_revision: number; coverage: Coverage };
export type Account = {
  id: string;
  name: string;
  kind: "cash" | "credit";
  currency: "CNY";
  opening_balance_minor: string | null;
  opening_date: string | null;
  balance_minor: string | null;
  known_change_minor: string;
  observed_balance_minor?: string | null;
  book_change_minor?: string;
  unexplained_change_minor?: string;
  overpayment_minor?: string | null;
  version: number;
};
export type HistoricalTransaction = {
  expense_minor?: string | null;
  event: string;
  in_scope: boolean;
  review: string;
  reason: string;
  raw_amount_minor: string | null;
  amount_minor?: string | null;
  family?: string;
  behavior?: string;
  reimb?: string;
  product_description?: string;
  source_account_label?: string;
  account?: string;
  cash_included: boolean;
};
export type Transaction = {
  id: string;
  version: number;
  date: string | null;
  cash_date?: string | null;
  read_only?: boolean;
  business_amount_minor?: string;
  edit_options?: {
    events: string[];
    category_type: "expense" | "income" | "none";
    related_transaction_ids: string[];
    relation_note: string | null;
  };
  historical?: HistoricalTransaction;
  description: string;
  account_id: string;
  account_name: string;
  amount_minor: string;
  currency: "CNY";
  kind: string;
  category_code: string | null;
  category_name: string | null;
  counterparty_account_id: string | null;
  counterparty_confirmed?: boolean;
  refund_of: string | null;
  budget_line_id: string | null;
  review_reasons: (
    string | { message?: string; reason_code?: string; description?: string }
  )[];
  status: "posted" | "needs_review" | "excluded";
  source_count: number;
};
export type Source = {
  id: string;
  filename: string;
  sha256: string;
  row_number: number;
  raw: Record<string, unknown>;
  parser_version: string;
};
export type TransactionDetail = Meta & {
  read_only?: boolean;
  transaction: Transaction;
  sources: Source[];
  history: {
    version: number;
    action: string;
    actor: string;
    reason: string;
    created_at: string;
    before?: Record<string, unknown> | null;
    after?: Record<string, unknown>;
  }[];
  ledger_lines: {
    code: string;
    name: string;
    account_id: string | null;
    debit_minor: string;
    credit_minor: string;
  }[];
  effects?: {
    income_minor: string | null;
    expense_minor: string | null;
    cash_inflow_minor: string | null;
    cash_outflow_minor: string | null;
    budget_usage_minor: string | null;
    basis?: string;
  };
};
export type ImportBatch = {
  id: string;
  filename: string;
  status: string;
  row_count: number;
  imported_count: number;
  duplicate_count: number;
  rejected_count: number;
  errors: { row: number; message: string }[];
  created_at: string;
};
export type ChartAccount = {
  code: string;
  name: string;
  type: string;
  selectable: boolean;
  enabled: boolean;
};
export type ReportLine = {
  code?: string;
  kind?: string;
  name: string;
  amount_minor: string;
  transaction_ids?: string[];
};
export type Reports = Meta & {
  month: string;
  basis: string;
  income_expense: {
    income_minor: string;
    expense_minor: string;
    surplus_minor: string;
    income_lines: ReportLine[];
    expense_lines: ReportLine[];
    reimbursement_minor: string;
    family_support_minor: string;
    unclassified_expense_minor: string;
    unrealized_minor: string | null;
  };
  cash_flow: {
    inflow_minor: string;
    outflow_minor: string;
    net_minor: string;
    unexplained_inflow_minor: string;
    unexplained_outflow_minor: string;
    lines: ReportLine[];
    opening_minor: string | null;
    closing_minor: string | null;
  };
  balance_sheet: {
    name: string;
    as_of: string;
    assets_minor: string | null;
    liabilities_minor: string | null;
    scope_net_minor: string | null;
    known_assets_minor: string;
    known_liabilities_minor: string;
    accounts: Account[];
    observations?: {
      kind: string;
      code: string;
      account: string;
      date: string | null;
      amount_minor: string | null;
      ledger_balance: string;
      basis: string;
      limitation: string;
    }[];
  };
  review: {
    count: number;
    unclassified_count: number;
    unknown_amount_minor: string;
  };
  transaction_count: number;
};
export type BudgetLine = {
  id: string;
  name: string;
  group: string;
  allocation: "fixed" | "daily";
  execution_basis: "consumption" | "scheduled_payment" | "annual_envelope";
  actual_basis: "consumption" | "payment";
  monthly_minor: string[];
};
export type BudgetPayload = {
  year: number;
  currency: "CNY";
  funding_minor: string[];
  target_minor: string;
  lines: BudgetLine[];
  usage: unknown[];
};
export type CoverageBasisQuality = {
  status: string; complete: boolean; exact?: boolean; reasons: string[];
  known_usage_minor?: string; unknown_usage_minor?: string;
};
export type BudgetMonth = {
  quality?: Record<'consumption' | 'payment', CoverageBasisQuality>;
  actuals_connected?: boolean;
  unallocated_usage_minor?: string;
  known_daily_remaining_minor?: string;
  known_schedule_variance_minor?: string;
  known_fixed_outstanding_minor?: string;
  month: string | number;
  funding_minor: string;
  fixed_minor: string;
  daily_minor: string;
  annual_allocation_minor: string;
  planned_minor: string;
  usage_minor: string | null;
  daily_usage_minor: string | null;
  daily_remaining_minor: string | null;
  fixed_outstanding_minor: string | null;
  schedule_variance_minor: string | null;
  planned_retention_minor: string;
  target_minor: string;
  target_gap_minor: string;
  daily_limit_minor?: string;
};
export type Budget = BudgetPayload & {
  months: BudgetMonth[];
  annual: {
    funding_minor: string;
    planned_minor: string;
    planned_retention_minor: string;
    target_minor: string;
    target_gap_minor: string;
  };
  line_results: {
    actuals_complete?: boolean;
    known_annual_remaining_minor?: string;
    id: string;
    annual_planned_minor: string;
    annual_used_minor: string | null;
    annual_remaining_minor: string | null;
    monthly_usage_minor: (string | null)[];
  }[];
  actuals_connected?: boolean;
  unallocated_usage_minor?: string | null;
  usage_status?: string;
};
export type BudgetResponse = Meta & {
  latest_version?: number;
  status?: string;
  month_quality?: Record<string, Record<'consumption' | 'payment', CoverageBasisQuality>>;
  unsupported_payment_actuals?: boolean;
  legacy_payment_transaction_ids?: string[];
  migration?: {
    source_workbook: string;
    source_version: string;
    notes: string[];
    funding_components: { name: string; monthly_minor: string[] }[];
  } | null;
  version: number;
  budget: Budget | null;
  actuals_connected?: boolean;
  unallocated_usage_minor?: string | null;
};
export type Status = Meta & {
  service: string;
  schema_version: number;
  transaction_count: number;
  account_count: number;
  import_count: number;
  capabilities: Record<string, boolean>;
  limitations: string[];
  available_months?: string[];
  budget_years?: number[];
  migration?: {
    source_transaction_count: number;
    scoped_transaction_count: number;
    undated_transaction_count: number;
    budget_year: number;
    completed_at: string;
    source_workbook: string;
    budget_workbook: string;
    reconciliation_status: string;
    current_ledger_modified?: boolean;
  } | null;
};
