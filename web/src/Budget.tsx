import { Select } from "@tuja/ui/components/select";
import { Switch } from "@tuja/ui/components/switch";
import { AnalyticsSummary } from "./upstream/analytics-summary";
import { Card } from "@tuja/ui/components/card";
import { Button } from "@tuja/ui/components/button";
import { Heading } from "@tuja/ui/components/heading";
import { useRef, useState } from "react";
import {
  api,
  createPendingRequest,
  errorText,
  money,
  requestId,
  toDecimal,
  toMinor,
} from "./api";
import type { BudgetLine, BudgetPayload, BudgetResponse } from "./types";
import { BudgetVersions } from "./BudgetVersions";
import { Empty, Field, Icon, Modal, Notice } from "./ui";
export const groups = [
  "住房与居家",
  "交通与用车",
  "数字与通信",
  "餐饮",
  "购物与个人生活",
  "健康与健身",
  "宠物",
  "历史分期",
  "其他安排",
];
export function budgetConnected(
  response: BudgetResponse | null,
  month?: string,
  basis?: "consumption" | "payment",
) {
  if (!month || response?.budget?.year !== Number(month.slice(0, 4))) return false;
  const current = response.budget.months[Number(month.slice(5)) - 1];
  const quality = current?.quality ?? response.month_quality?.[month];
  if (basis) return quality?.[basis]?.exact === true;
  const needed = new Set(["consumption", ...response.budget.lines.map((line) => line.actual_basis)]);
  return [...needed].every((item) => quality?.[item as "consumption" | "payment"]?.exact === true);
}
export function BudgetPage({
  data,
  month,
  onSaved,
  availableYears = [],
  onYearChange,
}: {
  data: BudgetResponse | null;
  month: string;
  onSaved: () => Promise<void>;
  availableYears?: number[];
  onYearChange?: (year: number) => void;
}) {
  const [editorData, setEditorData] = useState<BudgetResponse | null>(null);
  const [draftRefresh, setDraftRefresh] = useState(0);
  const [editing, setEditing] = useState(false),
    [view, setView] = useState<"month" | "year">("month"),
    [showZeroItems, setShowZeroItems] = useState(false);
  const budget = data?.budget,
    monthIndex = Number(month.slice(5)) - 1,
    current = budget?.months[monthIndex],
    connected = budgetConnected(data, month),
    dailyComplete = budgetConnected(data, month, "consumption");
  const visibleMonthLines =
    budget?.lines.filter((line) => {
      const usage = budget.line_results.find((result) => result.id === line.id)
        ?.monthly_usage_minor[monthIndex];
      // Unknown values stay visible; only a known zero plan and zero usage hide.
      return (
        showZeroItems || !budgetConnected(data, month, line.actual_basis) || line.monthly_minor[monthIndex] !== "0" || usage !== "0"
      );
    }) ?? [];
  return (
    <>
      <div className="page-title">
        <div>
          <p className="eyebrow">把生活安排在前面</p>
          <Heading level={1} look="h3">
            生活预算
          </Heading>
          <p>
            {month.slice(0, 4)} 年 ·{" "}
            {budget ? `已采用第 ${data?.version} 版` : "还没有采用预算"}
          </p>
        </div>
        <Button onClick={() => { setEditorData(data); setEditing(true); }} look="primary">
          <Icon name={budget ? "edit" : "plus"} />
          {budget ? "修订预算" : "创建年度预算"}
        </Button>
      </div>
      <BudgetVersions year={Number(month.slice(0, 4))} data={data} refresh={draftRefresh} onSaved={onSaved} onEdit={(d) => { setEditorData(d); setEditing(true); }}/>
      {availableYears.length > 0 && onYearChange && (
        <div className="filter-controls">
          <Select
            label="预算年度"
            value={month.slice(0, 4)}
            onChange={(event) => onYearChange(Number(event.target.value))}
          >
            {[...new Set([...availableYears, Number(month.slice(0, 4))])]
              .sort((a, b) => a - b)
              .map((year) => (
                <option key={year} value={year}>
                  {year} 年{availableYears.includes(year) ? " · 已保存" : ""}
                </option>
              ))}
          </Select>
        </div>
      )}
      {data?.migration && (
        <Notice>
          已迁入 {data.migration.source_workbook.split(/[\\/]/).at(-1)}{" "}
          的年度计划，保留来源版本 {data.migration.source_version}
          。实际占用待关联不会影响下方计划金额。
        </Notice>
      )}
      {!budget ? (
        <Card>
          <Empty
            icon="budget"
            title="从你的生活安排开始"
            action={
              <Button onClick={() => { setEditorData(data); setEditing(true); }} look="primary">
                创建 {month.slice(0, 4)} 年预算
                <Icon name="arrow" size={17} />
              </Button>
            }
          >
            填入每月可用资金、生活用途与年度留存目标。每次修订先保存为草稿，明确采用后才用于首页与预实。
          </Empty>
          <div className="budget-principles">
            <div>
              <b>按生活用途放在一起</b>
              <p>住房、餐饮、宠物，各自看清全年安排。</p>
            </div>
            <div>
              <b>分清消费和付款</b>
              <p>普通消费、按期付款、年度额度使用不同规则。</p>
            </div>
            <div>
              <b>计划与实际分开</b>
              <p>计划不会生成交易，也不会成为账户余额。</p>
            </div>
          </div>
        </Card>
      ) : (
        <>
          <div className="stats-grid four">
            <Metric label="全年资金来源" value={budget.annual.funding_minor} />
            <Metric label="全年生活安排" value={budget.annual.planned_minor} />
            <Metric
              label="预计预算留存"
              value={budget.annual.planned_retention_minor}
            />
            <Metric
              label="距留存目标还需调整"
              value={budget.annual.target_gap_minor}
              hint={`年度目标 ¥${money(budget.annual.target_minor)}`}
            />
          </div>
          {data?.migration && (
            <Card>
              <details>
                <summary>迁入依据与资金来源</summary>
                <div className="modal-body">
                  {data.migration.notes.length > 0 && (
                    <ul>
                      {data.migration.notes.map((note, index) => (
                        <li key={index}>{note}</li>
                      ))}
                    </ul>
                  )}
                  {data.migration.funding_components.length > 0 && (
                    <div className="table-scroll">
                      <table className="year-table">
                        <thead>
                          <tr>
                            <th>资金来源</th>
                            {Array.from({ length: 12 }, (_, index) => (
                              <th key={index}>{index + 1} 月</th>
                            ))}
                          </tr>
                        </thead>
                        <tbody>
                          {data.migration.funding_components.map(
                            (component, index) => (
                              <tr key={`${component.name}-${index}`}>
                                <th>{component.name}</th>
                                {component.monthly_minor.map(
                                  (value, monthIndex) => (
                                    <td key={monthIndex}>{money(value)}</td>
                                  ),
                                )}
                              </tr>
                            ),
                          )}
                        </tbody>
                      </table>
                    </div>
                  )}
                </div>
              </details>
            </Card>
          )}
          {!connected && (
            <Notice>
              {Number(month.slice(5))} 月资料尚未完整核对，下方保留当前已知占用，完整剩余和待付仍待确认。
              没有记录的月份不能直接按零支出计算。可在<a href="#settings/data">设置 · 数据</a>核对账户和月份覆盖。
            </Notice>
          )}
          {data?.unsupported_payment_actuals && (
            <Notice>
              存在此前关联的还款预算占用，已保留原解释；与消费是否重复占用仍需核对。
            </Notice>
          )}
          {data?.unallocated_usage_minor &&
            data.unallocated_usage_minor !== "0" && (
              <Notice>
                有 ¥{money(data.unallocated_usage_minor)}{" "}
                记录的预算用途或退款占用待核。已确认的普通消费已计入总占用和日常占用，后续分配不会再扣一次。
              </Notice>
            )}
          <div className="stats-grid">
            <Metric label="本月已知总占用" value={current?.usage_minor} hint="包含已确认但尚未分配用途的普通消费" />
            <Metric label="本月已知日常占用" value={current?.daily_usage_minor} />
            <Metric label="本月日常剩余" value={dailyComplete ? current?.daily_remaining_minor : null}
              hint={dailyComplete ? "按本月已确认资料范围计算" : "本月消费资料覆盖或业务性质待核"} />
          </div>
          <Card>
            <div className="section-heading">
              <div>
                <Heading level={2} look="h4">
                  {view === "month"
                    ? `${Number(month.slice(5))} 月的生活安排`
                    : "生活用途 × 12 个月"}
                </Heading>
                <p>金额单位：人民币元</p>
              </div>
              <div className="segmented">
                <button
                  className={view === "month" ? "active" : ""}
                  onClick={() => setView("month")}
                >
                  单月
                </button>
                <button
                  className={view === "year" ? "active" : ""}
                  onClick={() => setView("year")}
                >
                  全年
                </button>
              </div>
            </div>
            {view === "month" ? (
              <>
                <div className="budget-month-summary">
                  <div>
                    <span>本月资金来源</span>
                    <b>¥ {money(current?.funding_minor)}</b>
                  </div>
                  <div>
                    <span>本月生活安排</span>
                    <b>¥ {money(current?.planned_minor)}</b>
                  </div>
                  <div>
                    <span>预计本月留存</span>
                    <b>¥ {money(current?.planned_retention_minor)}</b>
                  </div>
                </div>
                <label className="budget-zero-toggle">
                  <Switch
                    size="sm"
                    value={showZeroItems ? "on" : "off"}
                    onChange={(value) => setShowZeroItems(value === "on")}
                  />
                  <span>显示本月零金额项目</span>
                </label>
                <div className="budget-groups">
                  {[...new Set(visibleMonthLines.map((x) => x.group))].map(
                    (group) => (
                      <div key={group} className="budget-group">
                        <Heading level={3} look="h4">
                          {group}
                        </Heading>
                        {visibleMonthLines
                          .filter((x) => x.group === group)
                          .map((line) => {
                            const result = budget.line_results.find(
                              (r) => r.id === line.id,
                            );
                            return (
                              <div className="budget-line" key={line.id}>
                                <div>
                                  <b>{line.name}</b>
                                  <span>
                                    {basisLabel(line)} ·{" "}
                                    {line.allocation === "fixed"
                                      ? "固定安排"
                                      : "日常额度"}
                                  </span>
                                </div>
                                <div>
                                  <strong>
                                    ¥ {money(line.monthly_minor[monthIndex])}
                                  </strong>
                                  <span>
                                    {budgetConnected(data, month, line.actual_basis) ? "已占用" : "已知占用"} ¥{money(result?.monthly_usage_minor[monthIndex])}
                                  </span>
                                </div>
                              </div>
                            );
                          })}
                      </div>
                    ),
                  )}
                </div>
                {budget.lines.length > 0 && visibleMonthLines.length === 0 && (
                  <p className="footnote">
                    本月没有非零计划或已关联占用，打开上方开关可查看零金额项目。
                  </p>
                )}
                {!budget.lines.length && (
                  <Empty
                    title="本年还没有生活项目"
                    action={
                      <Button onClick={() => { setEditorData(data); setEditing(true); }}>
                        添加预算项目
                      </Button>
                    }
                  >
                    资金来源和留存目标已保存，可以继续补充生活安排。
                  </Empty>
                )}
              </>
            ) : (
              <div className="table-scroll">
                <table className="year-table">
                  <thead>
                    <tr>
                      <th>生活项目</th>
                      {Array.from({ length: 12 }, (_, i) => (
                        <th
                          key={i}
                          className={i === monthIndex ? "selected-month" : ""}
                        >
                          {i + 1} 月
                        </th>
                      ))}
                      <th>全年</th>
                    </tr>
                  </thead>
                  <tbody>
                    <tr className="funding-row">
                      <th>可用资金来源</th>
                      {budget.funding_minor.map((v, i) => (
                        <td key={i}>{money(v)}</td>
                      ))}
                      <td>{money(budget.annual.funding_minor)}</td>
                    </tr>
                    {budget.lines.map((line) => (
                      <tr key={line.id}>
                        <th>
                          <b>{line.name}</b>
                          <small>
                            {line.group} · {basisLabel(line)}
                          </small>
                        </th>
                        {line.monthly_minor.map((v, i) => (
                          <td
                            key={i}
                            className={i === monthIndex ? "selected-month" : ""}
                          >
                            {money(v)}
                          </td>
                        ))}
                        <td>
                          {money(
                            budget.line_results.find((r) => r.id === line.id)
                              ?.annual_planned_minor,
                          )}
                        </td>
                      </tr>
                    ))}
                    <tr className="total-row">
                      <th>全部资金安排</th>
                      {budget.months.map((m, i) => (
                        <td key={i}>{money(m.planned_minor)}</td>
                      ))}
                      <td>{money(budget.annual.planned_minor)}</td>
                    </tr>
                    <tr className="total-row">
                      <th>预计预算留存</th>
                      {budget.months.map((m, i) => (
                        <td key={i}>{money(m.planned_retention_minor)}</td>
                      ))}
                      <td>{money(budget.annual.planned_retention_minor)}</td>
                    </tr>
                    <tr>
                      <th>已知实际占用</th>
                      {budget.months.map((m, i) => <td key={i}>{money(m.usage_minor)}</td>)}
                      <td>{money(budget.months.reduce((sum, m) => sum + BigInt(m.usage_minor ?? "0"), 0n).toString())}</td>
                    </tr>
                    <tr>
                      <th>消费资料覆盖</th>
                      {budget.months.map((m, i) => <td key={i}>{m.quality?.consumption.exact ? "已核对" : "待核对"}</td>)}
                      <td>逐月确认</td>
                    </tr>
                  </tbody>
                </table>
              </div>
            )}
          </Card>
          <p className="footnote">
            预计预算留存基于你设定的资金来源与安排。它不等于账户余额，也不代表完整家庭净资产增加。
          </p>
        </>
      )}
      {editing && (
        <BudgetEditor
          data={editorData ?? data}
          year={Number(month.slice(0, 4))}
          onClose={() => setEditing(false)}
          onSaved={async () => {
            await onSaved();
            setEditing(false);
            setDraftRefresh(n => n + 1);
          }}
        />
      )}
    </>
  );
}
export function Metric({
  label,
  value,
  hint,
}: {
  label: string;
  value: string | null | undefined;
  hint?: string;
}) {
  return (
    <AnalyticsSummary
      heading={label}
      totalText={value == null ? "待确认" : `¥${money(value)}`}
      detail={hint}
    />
  );
}
function basisLabel(line: BudgetLine) {
  return line.execution_basis === "annual_envelope"
    ? "年度额度"
    : line.execution_basis === "scheduled_payment"
      ? "按期付款"
      : "消费发生";
}
function BudgetEditor({
  data,
  year,
  onClose,
  onSaved,
}: {
  data: BudgetResponse | null;
  year: number;
  onClose: () => void;
  onSaved: () => Promise<void>;
}) {
  const attempt = useRef(createPendingRequest());
  const [draft, setDraft] = useState<BudgetPayload>(() =>
    data?.budget
      ? {
          year: data.budget.year,
          currency: "CNY",
          funding_minor: [...data.budget.funding_minor],
          target_minor: data.budget.target_minor,
          lines: data.budget.lines.map((l) => ({
            ...l,
            monthly_minor: [...l.monthly_minor],
          })),
          usage: [],
        }
      : {
          year,
          currency: "CNY",
          funding_minor: Array(12).fill("0"),
          target_minor: "0",
          lines: [],
          usage: [],
        },
  );
  const [funding, setFunding] = useState(draft.funding_minor.map(toDecimal)),
    [target, setTarget] = useState(toDecimal(draft.target_minor)),
    [amounts, setAmounts] = useState<Record<string, string[]>>(() =>
      Object.fromEntries(
        draft.lines.map((l) => [l.id, l.monthly_minor.map(toDecimal)]),
      ),
    );
  const [error, setError] = useState(""),
    [saving, setSaving] = useState(false),
    [step, setStep] = useState<"amounts" | "review">("amounts");
  const updateLine = (id: string, patch: Partial<BudgetLine>) =>
    setDraft((d) => ({
      ...d,
      lines: d.lines.map((l) => (l.id === id ? { ...l, ...patch } : l)),
    }));
  const addLine = () => {
    const id = requestId();
    setDraft((d) => ({
      ...d,
      lines: [
        ...d.lines,
        {
          id,
          name: "",
          group: groups[0],
          allocation: "daily",
          execution_basis: "consumption",
          actual_basis: "consumption",
          monthly_minor: Array(12).fill("0"),
        },
      ],
    }));
    setAmounts((a) => ({ ...a, [id]: Array(12).fill("0.00") }));
    setStep("amounts");
  };
  function payload() {
    return {
      ...draft,
      funding_minor: funding.map(toMinor),
      target_minor: toMinor(target),
      lines: draft.lines.map((l) => ({
        ...l,
        name: l.name.trim(),
        monthly_minor: amounts[l.id].map(toMinor),
      })),
      usage: [],
    };
  }
  function review() {
    try {
      const p = payload();
      if (p.lines.some((l) => !l.name))
        throw new Error("请为每个预算项目填写名称。");
      if (
        [
          ...p.funding_minor,
          p.target_minor,
          ...p.lines.flatMap((l) => l.monthly_minor),
        ].some((v) => BigInt(v) < 0n)
      )
        throw new Error("资金来源、目标和计划金额不能为负数。");
      setError("");
      setStep("review");
    } catch (e) {
      setError(errorText(e));
    }
  }
  async function save() {
    setSaving(true);
    setError("");
    try {
      const body = { expected_version: data?.latest_version ?? data?.version ?? 0, expected_adopted_version: data?.version ?? 0, payload: payload() };
      await api("/budgets", {
        method: "POST",
        body: JSON.stringify({
          ...body,
          request_id: attempt.current.key(body),
        }),
      });
      attempt.current.clear();
      await onSaved();
    } catch (e) {
      setError(errorText(e));
    } finally {
      setSaving(false);
    }
  }
  return (
    <Modal
      title={`${data?.budget ? "修订" : "创建"} ${year} 年生活预算`}
      onClose={() => !saving && onClose()}
      wide
    >
      <div className="modal-body">
        {error && <Notice kind="error">{error}</Notice>}
        {step === "review" ? (
          <>
            <div className="review-sheet">
              <span className="badge mint">待采用</span>
              <Heading level={3} look="h4">
                把这份安排保存为第 {(data?.version ?? 0) + 1} 版
              </Heading>
              <p>
                已填写 {draft.lines.length} 个生活项目，覆盖 1—12
                月。金额单位为人民币元。
              </p>
              <dl>
                <div>
                  <dt>年度留存目标</dt>
                  <dd>¥ {target}</dd>
                </div>
                <div>
                  <dt>本次保存</dt>
                  <dd>
                    {data?.budget
                      ? "保存修订草稿，核对后另行采用"
                      : "保存第一版年度预算草稿"}
                  </dd>
                </div>
              </dl>
            </div>
            <Notice>
              保存计划不会生成实际交易。实际预算占用须来自有依据并已关联预算项目的交易。
            </Notice>
          </>
        ) : (
          <>
            <p className="form-intro">
              先填可到账资金，再按用途安排生活。已知没有安排的月份填
              0，年度额度不会生成付款记录。
            </p>
            <div className="form-grid">
              <Field label="年度留存目标（元）">
                <input
                  inputMode="decimal"
                  value={target}
                  onChange={(e) => setTarget(e.target.value)}
                  placeholder="0.00"
                />
              </Field>
              <Field
                label="每月资金来源快捷填写（元）"
                hint="修改首月后，可应用到全年。"
              >
                <div className="input-action">
                  <input
                    inputMode="decimal"
                    value={funding[0]}
                    onChange={(e) =>
                      setFunding((f) =>
                        f.map((v, i) => (i === 0 ? e.target.value : v)),
                      )
                    }
                  />
                  <button
                    type="button"
                    onClick={() => setFunding(Array(12).fill(funding[0]))}
                  >
                    应用全年
                  </button>
                </div>
              </Field>
            </div>
            <details className="funding-detail">
              <summary>逐月调整资金来源</summary>
              <div className="month-input-grid">
                {funding.map((v, i) => (
                  <Field label={`${i + 1} 月`} key={i}>
                    <input
                      aria-label={`${i + 1}月资金来源`}
                      inputMode="decimal"
                      value={v}
                      onChange={(e) =>
                        setFunding((f) =>
                          f.map((x, j) => (j === i ? e.target.value : x)),
                        )
                      }
                    />
                  </Field>
                ))}
              </div>
            </details>
            <div className="section-heading tight">
              <Heading level={3} look="h4">
                生活预算项目
              </Heading>
              <Button type="button" onClick={addLine} size="sm">
                <Icon name="plus" size={16} />
                添加项目
              </Button>
            </div>
            {!draft.lines.length && (
              <div className="inline-empty">
                添加住房、餐饮、宠物等生活项目；也可以先保存资金来源和目标。
              </div>
            )}
            {draft.lines.map((line, index) => (
              <div className="editor-line" key={line.id}>
                <div className="line-top">
                  <b>项目 {index + 1}</b>
                  <Button
                    onClick={() => {
                      setDraft((d) => ({
                        ...d,
                        lines: d.lines.filter((l) => l.id !== line.id),
                      }));
                    }}
                    look="ghost"
                    size="sm"
                  >
                    删除项目
                  </Button>
                </div>
                <div className="form-grid three">
                  <Field label="项目名称">
                    <input
                      value={line.name}
                      onChange={(e) =>
                        updateLine(line.id, { name: e.target.value })
                      }
                      placeholder="例如：房租"
                      maxLength={80}
                    />
                  </Field>
                  <Field label="生活用途">
                    <select
                      value={line.group}
                      onChange={(e) =>
                        updateLine(line.id, { group: e.target.value })
                      }
                    >
                      {groups.map((g) => (
                        <option key={g}>{g}</option>
                      ))}
                    </select>
                  </Field>
                  <Field label="执行规则">
                    <select
                      value={line.execution_basis}
                      onChange={(e) => {
                        const basis = e.target
                          .value as BudgetLine["execution_basis"];
                        updateLine(line.id, {
                          execution_basis: basis,
                          actual_basis:
                            basis === "scheduled_payment"
                              ? "payment"
                              : "consumption",
                          allocation:
                            basis === "annual_envelope"
                              ? "daily"
                              : line.allocation,
                        });
                      }}
                    >
                      <option value="consumption">消费发生时占用</option>
                      <option value="scheduled_payment">
                        实际履约付款时占用
                      </option>
                      <option value="annual_envelope">
                        年度额度（按月安排）
                      </option>
                    </select>
                  </Field>
                  <Field label="资金归类">
                    <select
                      value={line.allocation}
                      disabled={line.execution_basis === "annual_envelope"}
                      onChange={(e) =>
                        updateLine(line.id, {
                          allocation: e.target.value as "fixed" | "daily",
                        })
                      }
                    >
                      <option value="daily">日常额度</option>
                      <option value="fixed">固定安排</option>
                    </select>
                  </Field>
                  {line.execution_basis === "annual_envelope" && (
                    <Field label="年度额度实际口径">
                      <select
                        value={line.actual_basis}
                        onChange={(e) =>
                          updateLine(line.id, {
                            actual_basis: e.target.value as
                              "consumption" | "payment",
                          })
                        }
                      >
                        <option value="consumption">消费发生</option>
                        <option value="payment">有依据的实际付款</option>
                      </select>
                    </Field>
                  )}
                  <Field label="首月金额（元）">
                    <div className="input-action">
                      <input
                        inputMode="decimal"
                        value={amounts[line.id][0]}
                        onChange={(e) =>
                          setAmounts((a) => ({
                            ...a,
                            [line.id]: a[line.id].map((v, i) =>
                              i === 0 ? e.target.value : v,
                            ),
                          }))
                        }
                      />
                      <button
                        type="button"
                        onClick={() =>
                          setAmounts((a) => ({
                            ...a,
                            [line.id]: Array(12).fill(a[line.id][0]),
                          }))
                        }
                      >
                        应用全年
                      </button>
                    </div>
                  </Field>
                </div>
                <details>
                  <summary>逐月调整 12 个月金额</summary>
                  <div className="month-input-grid">
                    {amounts[line.id].map((v, i) => (
                      <Field label={`${i + 1} 月`} key={i}>
                        <input
                          aria-label={`${line.name || "项目"}${i + 1}月金额`}
                          inputMode="decimal"
                          value={v}
                          onChange={(e) =>
                            setAmounts((a) => ({
                              ...a,
                              [line.id]: a[line.id].map((x, j) =>
                                j === i ? e.target.value : x,
                              ),
                            }))
                          }
                        />
                      </Field>
                    ))}
                  </div>
                </details>
              </div>
            ))}
          </>
        )}
      </div>
      <footer className="modal-footer">
        <Button
          onClick={() => (step === "review" ? setStep("amounts") : onClose())}
          disabled={saving}
        >
          {step === "review" ? "返回修改" : "取消"}
        </Button>
        <Button
          disabled={saving}
          onClick={() => (step === "review" ? void save() : review())}
          look="primary"
        >
          {saving
            ? "正在保存…"
            : step === "review"
              ? "保存预算草稿"
              : "检查草稿"}
          <Icon name={step === "review" ? "check" : "arrow"} size={17} />
        </Button>
      </footer>
    </Modal>
  );
}
