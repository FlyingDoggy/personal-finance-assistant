import { Heading } from "@tuja/ui/components/heading";
import { Text } from "@tuja/ui/components/text";
import { money } from "./api";
import type { Reports } from "./types";
import type { TransactionFilters } from "./Transactions";
import { AnalyticsSummary } from "./upstream/analytics-summary";

export function MonthlyOverview({ reports, onTransactions }: {
  reports: Reports | null;
  onTransactions: (filters: TransactionFilters) => void;
}) {
  const ie = reports?.income_expense, cf = reports?.cash_flow;
  const empty = ie && cf && !ie.income_transaction_ids.length && !ie.expense_transaction_ids.length && !cf.transaction_ids.length;
  const open = (ids: string[] | undefined) => () => onTransactions({
    allPeriods: true, scope: "included", transactionIds: ids || [],
  });
  return <section className="monthly-overview" aria-label="当月实际收支概况">
    <div className="section-heading">
      <Heading level={2} look="h4">实际收支</Heading>
      <Text look="caption" tone="muted">人民币 · 已知记录</Text>
    </div>
    <div className="monthly-metrics">
      <AnalyticsSummary heading="本月收入" totalText={`¥${money(ie?.income_minor)}`}
        detail={ie ? `含报销 ¥${money(ie.reimbursement_minor)}、家庭赞助 ¥${money(ie.family_support_minor)}` : "正在读取账本。"}
        onTotalClick={ie?.income_transaction_ids.length ? open(ie.income_transaction_ids) : undefined}/>
      <AnalyticsSummary heading="本月消费支出" totalText={`¥${money(ie?.expense_minor)}`}
        detail="按费用确认日，已扣退款；还款和本人互转不再计消费。"
        onTotalClick={ie?.expense_transaction_ids.length ? open(ie.expense_transaction_ids) : undefined}/>
      <AnalyticsSummary heading="本月收支结余" totalText={`¥${money(ie?.surplus_minor, true)}`}
        detail="本月收入 − 消费支出；与现金余额变动分开。"
        onTotalClick={(ie?.income_transaction_ids.length || ie?.expense_transaction_ids.length) ? open([...new Set([...(ie?.income_transaction_ids || []), ...(ie?.expense_transaction_ids || [])])]) : undefined}/>
      <AnalyticsSummary heading="本月现金净流入" totalText={`¥${money(cf?.net_minor, true)}`}
        detail={cf ? `流入 ¥${money(cf.inflow_minor)} − 流出 ¥${money(cf.outflow_minor)}；含实际还款，剔除已确认本人互转。` : "正在读取账本。"}
        onTotalClick={cf?.transaction_ids.length ? open(cf.transaction_ids) : undefined}/>
    </div>
    <p className="muted monthly-basis">{empty ? "所选月份没有已知交易，零值不代表资料已齐。" : "当前资料尚未证明完整；这里显示已知记录，不依赖是否设置预算。"}
      {!!reports?.review.count && ` 尚有 ${reports.review.count} 笔交易待核。`}
      {cf && (BigInt(cf.unexplained_inflow_minor) > 0n || BigInt(cf.unexplained_outflow_minor) > 0n) && ` 未解释现金流入 ¥${money(cf.unexplained_inflow_minor)}、流出 ¥${money(cf.unexplained_outflow_minor)}，已包含在现金收付中，尚未计为收入或消费。`}
    </p>
  </section>;
}
