import { Button } from "@tuja/ui/components/button";
import { Card } from "@tuja/ui/components/card";
import { Heading } from "@tuja/ui/components/heading";
import { useState } from "react";
import { money } from "./api";
import type { Reports, ReportLine } from "./types";
import { Metric } from "./Budget";
import { Empty, Icon, Notice } from "./ui";
import type { TransactionFilters } from "./Transactions";
export function ReportsPage({
  data,
  onTransactions,
  readOnly = false,
}: {
  data: Reports | null;
  onTransactions: (filters?: TransactionFilters) => void;
  readOnly?: boolean;
}) {
  const [tab, setTab] = useState<"income" | "cash" | "balance">("income");
  if (!data)
    return (
      <Empty icon="reports" title="报表暂未就绪">
        请刷新页面重新读取。
      </Empty>
    );
  const ie = data.income_expense,
    cf = data.cash_flow,
    bs = data.balance_sheet;
  return (
    <>
      <div className="page-title">
        <div>
          <p className="eyebrow">同一份事实，三个视角</p>
          <Heading level={1} look="h3">
            {readOnly ? "报表快照" : "管理报表"}
          </Heading>
          <p>各项金额保留自己的统计口径，不互相替代。</p>
        </div>
        <span className="count-label">{data.month} · 人民币</span>
      </div>
      <div className="report-tabs" role="tablist">
        <button
          role="tab"
          aria-selected={tab === "income"}
          onClick={() => setTab("income")}
          className={tab === "income" ? "active" : ""}
        >
          <span>收支结余</span>
          <small>消费与收入</small>
        </button>
        <button
          role="tab"
          aria-selected={tab === "cash"}
          onClick={() => setTab("cash")}
          className={tab === "cash" ? "active" : ""}
        >
          <span>现金流</span>
          <small>真实资金收付</small>
        </button>
        <button
          role="tab"
          aria-selected={tab === "balance"}
          onClick={() => setTab("balance")}
          className={tab === "balance" ? "active" : ""}
        >
          <span>资产与负债</span>
          <small>纳入的账户范围</small>
        </button>
      </div>
      {data.coverage.status !== "complete" && (
        <Notice>
          {data.coverage.status === "empty"
            ? "尚未导入交易，以下为当前账本记录。"
            : `统计来自已导入资料，尚未证明覆盖完整。${data.coverage.reasons.join("；")}`}
        </Notice>
      )}
      {tab === "income" ? (
        <>
          <div className="stats-grid">
            <Metric label="已确认收入" value={ie.income_minor} />
            <Metric label="已确认费用" value={ie.expense_minor} />
            <Metric label="收支结余" value={ie.surplus_minor} />
          </div>
          <div className="two-column">
            <Card>
              <div className="section-heading">
                <Heading level={2} look="h4">
                  收入构成
                </Heading>
                <span className="badge">按确认日期</span>
              </div>
              <ReportLines
                readOnly={readOnly}
                lines={ie.income_lines}
                empty="本月暂无已确认收入"
                onTransactions={(line) =>
                  onTransactions({
                    scope: "included",
                    categoryCode: line.code,
                    transactionIds: line.transaction_ids,
                  })
                }
              />
              <div className="report-notes">
                <div>
                  <span>其中：已到账报销</span>
                  <b>¥ {money(ie.reimbursement_minor)}</b>
                </div>
                <div>
                  <span>其中：家庭赞助</span>
                  <b>¥ {money(ie.family_support_minor)}</b>
                </div>
              </div>
            </Card>
            <Card>
              <div className="section-heading">
                <Heading level={2} look="h4">
                  费用构成
                </Heading>
                <span className="badge">消费发生</span>
              </div>
              <ReportLines
                readOnly={readOnly}
                lines={ie.expense_lines}
                empty="本月暂无已确认费用"
                onTransactions={(line) =>
                  onTransactions({
                    scope: "included",
                    categoryCode: line.code,
                    transactionIds: line.transaction_ids,
                  })
                }
              />
              <div className="report-notes">
                <div>
                  <span>其中：未分类费用</span>
                  <b>¥ {money(ie.unclassified_expense_minor)}</b>
                </div>
              </div>
            </Card>
          </div>
          <p className="footnote">
            费用总额包含工作垫付；报销在到账月计入收入。信用消费按发生额计费用，还款不再次计费。以上未经预算报销标准化调整。
          </p>
        </>
      ) : tab === "cash" ? (
        <>
          <div className="stats-grid">
            <Metric label="现金流入" value={cf.inflow_minor} />
            <Metric label="现金流出" value={cf.outflow_minor} />
            <Metric label="现金净变动" value={cf.net_minor} />
          </div>
          <Card>
            <div className="section-heading">
              <Heading level={2} look="h4">
                现金收付构成
              </Heading>
              <span className="badge">真实收付</span>
            </div>
            <ReportLines
              readOnly={readOnly}
              lines={cf.lines}
              empty="本月暂无现金收付"
              onTransactions={(line) =>
                onTransactions({
                  allPeriods: true,
                  scope: "included",
                  transactionIds: line.transaction_ids,
                })
              }
            />
            <div className="report-notes">
              <div>
                <span>待解释流入</span>
                <b>¥ {money(cf.unexplained_inflow_minor)}</b>
              </div>
              <div>
                <span>待解释流出</span>
                <b>¥ {money(cf.unexplained_outflow_minor)}</b>
              </div>
            </div>
          </Card>
          <div className="stats-grid two">
            <Metric label="期初现金" value={cf.opening_minor} />
            <Metric label="期末现金" value={cf.closing_minor} />
          </div>
          <p className="footnote">
            现金按实际收付日统计。缺少同范围、同期间的起始余额时，只报告已知净变动，不推断绝对余额。
          </p>
        </>
      ) : (
        <>
          <div className="stats-grid">
            <Metric
              label="纳入账户资产"
              value={bs.assets_minor}
              hint={`已知小计 ¥${money(bs.known_assets_minor)}`}
            />
            <Metric
              label="纳入账户负债"
              value={bs.liabilities_minor}
              hint={`已知小计 ¥${money(bs.known_liabilities_minor)}`}
            />
            <Metric label="纳入范围净额" value={bs.scope_net_minor} />
          </div>
          <Card>
            <div className="section-heading">
              <div>
                <Heading level={2} look="h4">
                  账户资产与负债表
                </Heading>
                <p>截至 {bs.as_of} · 仅覆盖已登记账户</p>
              </div>
            </div>
            {bs.accounts.length ? (
              <div className="table-scroll">
                <table>
                  <thead>
                    <tr>
                      <th>账户</th>
                      <th>类型</th>
                      <th className="align-right">期末金额（元）</th>
                      <th className="align-right">已知变动（元）</th>
                    </tr>
                  </thead>
                  <tbody>
                    {bs.accounts.map((a) => (
                      <tr key={a.id}>
                        <td>
                          {a.name}
                          <small>
                            {a.opening_date
                              ? `起点 ${a.opening_date}`
                              : "起点尚未确认"}
                          </small>
                        </td>
                        <td>{a.kind === "credit" ? "信用负债" : "现金资产"}</td>
                        <td className="amount align-right">
                          {money(a.balance_minor)}
                        </td>
                        <td className="amount align-right">
                          {money(a.known_change_minor, true)}
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            ) : (
              <Empty icon="wallet" title="尚未登记账户">
                在「更多」中添加账户，已知起始余额可一并填写。
              </Empty>
            )}
          </Card>
          {!!bs.observations?.length && (
            <Card>
              <div className="section-heading">
                <Heading level={2} look="h4">
                  历史资料快照
                </Heading>
              </div>
              <Notice>
                各自截至日，不代表所选月末。以下观察值保留来源口径和限制，不合并为当前资产、负债或净额。
              </Notice>
              <div className="table-scroll">
                <table>
                  <thead>
                    <tr>
                      <th>账户 / 项目</th>
                      <th>截至日期</th>
                      <th className="align-right">观察金额（元）</th>
                      <th>账面记录</th>
                      <th>依据与限制</th>
                    </tr>
                  </thead>
                  <tbody>
                    {bs.observations.map((observation, index) => (
                      <tr
                        key={`${observation.kind}-${observation.code}-${index}`}
                      >
                        <td>
                          {observation.account}
                          <small>
                            {observation.kind} · {observation.code}
                          </small>
                        </td>
                        <td>{observation.date || "日期未标注"}</td>
                        <td className="amount align-right">
                          {money(observation.amount_minor)}
                        </td>
                        <td>{observation.ledger_balance || "待说明"}</td>
                        <td>
                          {observation.basis || "依据待补"}
                          <small>{observation.limitation}</small>
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </Card>
          )}
          <p className="footnote">
            纳入范围净额不衡量完整家庭财富。未登记账户、未知起点和资料缺口均不按零处理。
          </p>
        </>
      )}
      <div className="report-footer">
        <span>来源：{readOnly ? "保存时的有效交易" : "当前有效交易"} · 数据修订 {data.data_revision}</span>
        {!readOnly && <Button
          onClick={() => onTransactions({ scope: "included" })}
          look="ghost"
          size="sm"
        >
          查看本月明细
          <Icon name="arrow" size={16} />
        </Button>}
      </div>
    </>
  );
}
function ReportLines({
  lines,
  empty,
  onTransactions,
  readOnly = false,
}: {
  lines: ReportLine[];
  empty: string;
  onTransactions: (line: ReportLine) => void;
  readOnly?: boolean;
}) {
  const visible = lines.filter((l) => l.amount_minor !== "0");
  return visible.length ? (
    <div className="report-lines">
      {visible.map((l, i) => (
        <button
          key={`${l.code || l.kind}-${i}`}
          disabled={readOnly}
          onClick={() => onTransactions(l)}
        >
          <span>{l.name}</span>
          <span>
            <b className="amount">¥ {money(l.amount_minor)}</b>
            {!readOnly && <Icon name="chevron" size={15} />}
          </span>
        </button>
      ))}
    </div>
  ) : (
    <div className="inline-empty">{empty}</div>
  );
}
