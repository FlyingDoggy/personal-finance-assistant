import { useEffect, useRef, useState } from "react";
import { Button } from "@tuja/ui/components/button";
import { Card } from "@tuja/ui/components/card";
import { Heading } from "@tuja/ui/components/heading";
import { Text } from "@tuja/ui/components/text";
import { stack } from "@tuja/ui/primitives/stack.stylex";
import { api, createPendingRequest, errorText, money, toMinor } from "./api";
import { Field, Notice } from "./ui";

type Params = {
  start_month: string;
  end_month: string;
  mode: string;
  estimate: boolean;
  minimum_minor: string;
  large_basis: string;
  living_scope: string;
};
type Row = {
  id: string;
  description: string;
  date: string;
  expense_minor: string;
  net_in_window_minor?: string;
  category_code: string;
};
type Result = {
  parameters: Params;
  dependencies: Record<string, unknown>;
  totals: Record<string, string>;
  averages: Record<string, string>;
  annualized_adjusted_expense_minor: string | null;
  quality: {
    complete: boolean;
    estimated: boolean;
    months: number;
    transaction_count: number;
    unknown_transaction_ids: string[];
    unclassified_minor: string;
  };
  merchants: {
    name: string;
    basis: string;
    amount_minor: string;
    transaction_ids: string[];
  }[];
  purposes: {
    year: number;
    name: string;
    basis: string;
    planned_minor: string;
    known_usage_minor: string;
  }[];
  categories: {
    code: string;
    name: string;
    amount_minor: string;
    count: number;
    transaction_ids: string[];
  }[];
  months: {
    month: string;
    income_minor: string;
    expense_minor: string;
    received_reimbursement_minor: string;
    adjustment_minor: string;
    adjusted_expense_minor: string;
    surplus_minor: string;
  }[];
  large_expenses: Row[];
  refunds: Row[];
  expense_rows: Row[];
  budget_actuals: {
    month: string;
    budget_version: number;
    planned_minor: string;
    known_usage_minor: string;
    daily_remaining_minor: string | null;
  }[];
  alerts: {
    code: string;
    month: string;
    message: string;
    amount_minor: string;
  }[];
  warnings: { code: string; message: string }[];
  living_normalization: {
    reason: string;
    status: string;
    gross_source_minor?: string;
    gross_usage_minor?: string;
    applicable_reimbursement_minor?: string;
    adjusted_source_minor?: string;
    adjusted_usage_minor?: string;
    surplus_minor?: string;
    warnings?: { code: string; message: string }[];
  };
  notes: string[];
};
type Snapshot = {
  id: string;
  created_at: string;
  stale: boolean;
  parameters: Params;
};

export function AnalysisWorkspace({
  month,
  onTransaction,
}: {
  month: string;
  onTransaction: (id: string) => void;
}) {
  const [start, setStart] = useState(month.slice(0, 4) + "-01"),
    [end, setEnd] = useState(month),
    [mode, setMode] = useState("period"),
    [estimate, setEstimate] = useState(false),
    [data, setData] = useState<Result | null>(null),
    [busy, setBusy] = useState(false),
    [error, setError] = useState(""),
    [snapshots, setSnapshots] = useState<Snapshot[]>([]),
    [frozen, setFrozen] = useState<Snapshot | null>(null),
    [category, setCategory] = useState<string | null>(null),
    [offset, setOffset] = useState(0);
  const [threshold, setThreshold] = useState("2000"),
    [largeBasis, setLargeBasis] = useState("gross");
  const [livingScope, setLivingScope] = useState("undeclared");
  const [filtersOpen, setFiltersOpen] = useState(
    () => window.matchMedia("(min-width: 768px)").matches,
  );
  useEffect(() => {
    const query = window.matchMedia("(min-width: 768px)");
    const change = () => setFiltersOpen(query.matches);
    query.addEventListener("change", change);
    return () => query.removeEventListener("change", change);
  }, []);
  const attempt = useRef(createPendingRequest());
  async function list() {
    setSnapshots(
      (await api<{ items: Snapshot[] }>("/analysis-snapshots")).items,
    );
  }
  async function load() {
    setBusy(true);
    setError("");
    try {
      setData(
        await api<Result>(
          `/analysis?${new URLSearchParams({ start_month: start, end_month: end, mode, estimate: String(estimate), minimum_minor: toMinor(threshold), large_basis: largeBasis, living_scope: livingScope })}`,
        ),
      );
      setFrozen(null);
      setCategory(null);
      setOffset(0);
      await list();
    } catch (e) {
      setError(errorText(e));
    } finally {
      setBusy(false);
    }
  }
  useEffect(() => {
    void load();
  }, []);
  async function save() {
    if (!data) return;
    setBusy(true);
    setError("");
    const body = {
      parameters: data.parameters,
      expected_dependencies: data.dependencies,
    };
    try {
      await api("/analysis-snapshots", {
        method: "POST",
        body: JSON.stringify({
          ...body,
          request_id: attempt.current.key(body),
        }),
      });
      attempt.current.clear();
      await list();
    } catch (e) {
      setError(errorText(e));
    } finally {
      setBusy(false);
    }
  }
  async function readSnapshot(s: Snapshot) {
    setError("");
    setBusy(true);
    try {
      const r = await api<Snapshot & { analysis: Result }>(
        `/analysis-snapshots/${s.id}`,
      );
      setData(r.analysis);
      setFrozen(r);
      setStart(r.parameters.start_month);
      setEnd(r.parameters.end_month);
      setMode(r.parameters.mode);
      setEstimate(r.parameters.estimate);
      setThreshold(toDecimal(r.parameters.minimum_minor));
      setLargeBasis(r.parameters.large_basis);
      setLivingScope(r.parameters.living_scope);

      setCategory(null);
      setOffset(0);
    } catch (e) {
      setError(errorText(e));
    } finally {
      setBusy(false);
    }
  }
  const details =
    data?.expense_rows.filter(
      (r) => category === null || r.category_code === category,
    ) || [];
  return (
    <div css={stack.group}>
      <Heading level={1} look="h3">
        消费与报销分析
      </Heading>
      <Text tone="muted">
        同一窗口汇总收入、费用和已到账报销。调整只用于比较生活负担，原始三表继续保留总额。
      </Text>
      <details
        className="analysis-filter-box"
        open={filtersOpen}
        onToggle={(event) => setFiltersOpen(event.currentTarget.open)}
      >
        <summary>
          分析设置 · {start} 至 {end}
        </summary>
        <form
          className="analysis-controls"
          onSubmit={(e) => {
            e.preventDefault();
            void load();
          }}
        >
          <Field label="开始月份">
            <input
              type="month"
              required
              value={start}
              onChange={(e) => setStart(e.target.value)}
            />
          </Field>
          <Field label="结束月份">
            <input
              type="month"
              required
              value={end}
              onChange={(e) => setEnd(e.target.value)}
            />
          </Field>
          <Field label="报销调整方式">
            <select value={mode} onChange={(e) => setMode(e.target.value)}>
              <option value="period">按实际到账月调整</option>
              <option value="budget_comparison">累计报销平滑到窗口各月</option>
            </select>
          </Field>
          <Field label="大额门槛（元）">
            <input
              inputMode="decimal"
              required
              value={threshold}
              onChange={(e) => setThreshold(e.target.value)}
            />
          </Field>
          <Field label="大额筛选口径">
            <select
              value={largeBasis}
              onChange={(e) => setLargeBasis(e.target.value)}
            >
              <option value="gross">发生期毛额</option>
              <option value="net">窗口内已记录净额</option>
            </select>
          </Field>
          <Field
            label="生活预算来源范围"
            hint="消费标准化始终按同窗报销计算；生活占用须明确资金来源是否包含报销。"
          >
            <select
              value={livingScope}
              onChange={(event) => setLivingScope(event.target.value)}
            >
              <option value="undeclared">暂不声明生活来源范围</option>
              <option value="observed_income">
                使用包含报销的实际到账收入
              </option>
              <option value="net_salary_plan">
                来源为已排除报销的计划工资
              </option>
            </select>
          </Field>
          <label className="check-field">
            <input
              type="checkbox"
              checked={estimate}
              onChange={(e) => setEstimate(e.target.checked)}
            />
            允许在资料不完整时给出年化估计
          </label>
          <Button type="submit" look="primary" disabled={busy}>
            {busy ? "正在计算…" : "更新分析"}
          </Button>
        </form>
      </details>
      {error && <Notice kind="error">{error}</Notice>}
      {data && (
        <>
          <div className="section-heading">
            <Text look="label">
              当前结果：{data.parameters.start_month} 至{" "}
              {data.parameters.end_month} · {data.quality.months} 个自然月 ·{" "}
              {data.quality.transaction_count} 条记录
              {frozen ? " · 已保存快照" : " · 当前账本"}
            </Text>
            <Button disabled={busy || !!frozen} onClick={() => void save()}>
              保存分析快照
            </Button>
          </div>
          {frozen && (
            <Notice kind={frozen.stale ? "error" : "info"}>
              {frozen.stale
                ? "数据或计算依据已变化。下方保留保存时结果，交易详情打开当前版本。"
                : "正在查看保存时的分析结果。"}
              <Button size="sm" look="ghost" onClick={() => void load()}>
                读取当前账本
              </Button>
            </Notice>
          )}
          {!data.quality.complete && (
            <Notice>
              资料覆盖不完整，以下是已知记录。
              {data.quality.estimated
                ? "已明确使用估计模式，年化仅供参考。"
                : "未自动年化。"}{" "}
              待解释性质 {data.quality.unknown_transaction_ids.length}{" "}
              条；已知未分类费用 ¥{money(data.quality.unclassified_minor)}。
            </Notice>
          )}
          <Card>
            <div css={stack.item}>
              <Text look="label">收入与消费口径桥接</Text>
              <div className="table-scroll">
                <table>
                  <thead>
                    <tr>
                      <th>口径</th>
                      <th>收入</th>
                      <th>费用</th>
                      <th>结余</th>
                    </tr>
                  </thead>
                  <tbody>
                    <tr>
                      <th>原始已知总额</th>
                      <td>{money(data.totals.income_minor)}</td>
                      <td>{money(data.totals.expense_minor)}</td>
                      <td>{money(data.totals.surplus_minor)}</td>
                    </tr>
                    <tr>
                      <th>两侧减同期到账报销</th>
                      <td>−{money(data.totals.reimbursement_minor)}</td>
                      <td>−{money(data.totals.reimbursement_minor)}</td>
                      <td>0.00</td>
                    </tr>
                    <tr>
                      <th>标准化总额</th>
                      <td>{money(data.totals.adjusted_income_minor)}</td>
                      <td>{money(data.totals.adjusted_expense_minor)}</td>
                      <td>{money(data.totals.surplus_minor)}</td>
                    </tr>
                    <tr>
                      <th>标准化已知月均</th>
                      <td>{money(data.averages.adjusted_income_minor)}</td>
                      <td>{money(data.averages.adjusted_expense_minor)}</td>
                      <td>—</td>
                    </tr>
                  </tbody>
                </table>
              </div>
              <Text look="bodySmall" tone="muted">
                家庭赞助 ¥{money(data.totals.family_support_minor)}{" "}
                保留在收入中，未当作报销抵费用。无法精准匹配的酒店支出也保留，只在这里汇总抵减报销一次。
              </Text>
              {data.annualized_adjusted_expense_minor !== null && (
                <Text>
                  标准化费用年化参考：¥
                  {money(data.annualized_adjusted_expense_minor)}
                  ；该结果不会写入下一年预算。
                </Text>
              )}
              {data.warnings.map((w) => (
                <Notice key={w.code}>{w.message}</Notice>
              ))}
            </div>
          </Card>
          <Card>
            <div css={stack.item}>
              <Text look="label">逐月原始金额与调整</Text>
              <div className="table-scroll">
                <table>
                  <thead>
                    <tr>
                      <th>月份</th>
                      <th>收入</th>
                      <th>费用</th>
                      <th>到账报销</th>
                      <th>本月抵减</th>
                      <th>调整后费用</th>
                    </tr>
                  </thead>
                  <tbody>
                    {data.months.map((m) => (
                      <tr key={m.month}>
                        <th>{m.month}</th>
                        <td>{money(m.income_minor)}</td>
                        <td>{money(m.expense_minor)}</td>
                        <td>{money(m.received_reimbursement_minor)}</td>
                        <td>{money(m.adjustment_minor)}</td>
                        <td>{money(m.adjusted_expense_minor)}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
              <Text look="caption">
                所选模式：
                {data.parameters.mode === "period"
                  ? "按到账月调整"
                  : "累计报销平滑月均"}
                。平滑不会改变收入减费用的结余。
              </Text>
            </div>
          </Card>
          <Card>
            <div css={stack.item}>
              <Text look="label">费用结构 · 原始已知费用减退款</Text>
              {data.categories.map((c) => (
                <Button
                  key={c.code}
                  look="ghost"
                  onClick={() => {
                    setCategory(c.code);
                    setOffset(0);
                  }}
                >
                  {c.name} · ¥{money(c.amount_minor)} · {c.count} 条
                </Button>
              ))}
              <Text look="bodySmall" tone="muted">
                报销汇总调整不按科目摊分，不能据此声称某家酒店已经精准剔除。
              </Text>
            </div>
          </Card>
          <details>
            <summary>商户与原始摘要汇总</summary>
            <div className="table-scroll">
              <table>
                <thead>
                  <tr>
                    <th>来源名称</th>
                    <th>识别依据</th>
                    <th>已知费用</th>
                  </tr>
                </thead>
                <tbody>
                  {data.merchants.map((r, i) => (
                    <tr key={i}>
                      <td>{r.name}</td>
                      <td>
                        {r.basis === "merchant" ? "明确商户字段" : "原始摘要"}
                      </td>
                      <td>{money(r.amount_minor)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </details>
          {data.purposes.length > 0 && (
            <details>
              <summary>生活用途预实</summary>
              <div className="table-scroll">
                <table>
                  <thead>
                    <tr>
                      <th>用途 / 计量口径</th>
                      <th>窗口计划</th>
                      <th>已知占用</th>
                    </tr>
                  </thead>
                  <tbody>
                    {data.purposes.map((r, i) => (
                      <tr key={i}>
                        <th>
                          {r.year} · {r.name} /{" "}
                          {r.basis === "payment" ? "现金付款" : "消费"}
                        </th>
                        <td>{money(r.planned_minor)}</td>
                        <td>{money(r.known_usage_minor)}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </details>
          )}
          <Card>
            <div css={stack.item}>
              <Text look="label">
                大额消费 ·{" "}
                {data.parameters.large_basis === "gross" ? "毛额" : "窗口净额"}
                至少 ¥{money(data.parameters.minimum_minor)}
              </Text>
              {data.large_expenses.length === 0 && (
                <Text tone="muted">当前窗口没有达到门槛的已知费用。</Text>
              )}
              {data.large_expenses.map((r) => (
                <Button
                  look="ghost"
                  key={r.id}
                  onClick={() => onTransaction(r.id)}
                >
                  {r.date} · {r.description} · ¥{money(r.expense_minor)} ·
                  窗口净额 ¥{money(r.net_in_window_minor)}
                </Button>
              ))}
              {data.refunds.length > 0 && (
                <Text look="caption">
                  退款另有 {data.refunds.length}{" "}
                  条，保留各自发生期，可在下方明细查看。
                </Text>
              )}
            </div>
          </Card>
          <Card>
            <div css={stack.item}>
              <Text look="label">采用预算与已知占用</Text>
              {data.budget_actuals.length === 0 ? (
                <Text tone="muted">本窗口没有已采用预算。</Text>
              ) : (
                <div className="table-scroll">
                  <table>
                    <thead>
                      <tr>
                        <th>月份 / 版本</th>
                        <th>计划</th>
                        <th>已知占用</th>
                        <th>日常剩余</th>
                      </tr>
                    </thead>
                    <tbody>
                      {data.budget_actuals.map((r) => (
                        <tr key={r.month}>
                          <th>
                            {r.month} / v{r.budget_version}
                          </th>
                          <td>{money(r.planned_minor)}</td>
                          <td>{money(r.known_usage_minor)}</td>
                          <td>
                            {r.daily_remaining_minor === null
                              ? "资料待补"
                              : money(r.daily_remaining_minor)}
                          </td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              )}
              {data.alerts.map((r, i) => (
                <Notice key={i}>
                  {r.month}：{r.message} 已知超出 ¥{money(r.amount_minor)}。
                </Notice>
              ))}
              {data.living_normalization.status !== "unavailable" && (
                <div className="table-scroll">
                  <table>
                    <thead>
                      <tr>
                        <th>生活资金口径</th>
                        <th>实际收入来源</th>
                        <th>已知生活占用</th>
                        <th>结余</th>
                      </tr>
                    </thead>
                    <tbody>
                      <tr>
                        <th>原始总额</th>
                        <td>
                          {money(data.living_normalization.gross_source_minor)}
                        </td>
                        <td>
                          {money(data.living_normalization.gross_usage_minor)}
                        </td>
                        <td>
                          {money(data.living_normalization.surplus_minor)}
                        </td>
                      </tr>
                      <tr>
                        <th>两侧抵同期报销后</th>
                        <td>
                          {money(
                            data.living_normalization.adjusted_source_minor,
                          )}
                        </td>
                        <td>
                          {money(
                            data.living_normalization.adjusted_usage_minor,
                          )}
                        </td>
                        <td>
                          {money(data.living_normalization.surplus_minor)}
                        </td>
                      </tr>
                    </tbody>
                  </table>
                </div>
              )}
              {data.living_normalization.warnings?.map((warning) => (
                <Notice key={warning.code}>{warning.message}</Notice>
              ))}
              <Text look="bodySmall" tone="muted">
                {data.living_normalization.reason}
              </Text>
            </div>
          </Card>
          <Card>
            <div css={stack.item}>
              <div className="section-heading">
                <Text look="label">费用明细 · {details.length} 条</Text>
                {category && (
                  <Button
                    size="sm"
                    look="ghost"
                    onClick={() => {
                      setCategory(null);
                      setOffset(0);
                    }}
                  >
                    清除科目筛选
                  </Button>
                )}
              </div>
              {details.slice(offset, offset + 25).map((r) => (
                <Button
                  look="ghost"
                  key={r.id}
                  onClick={() => onTransaction(r.id)}
                >
                  {r.date} · {r.description} · ¥{money(r.expense_minor)}
                </Button>
              ))}
              <div className="filter-controls">
                <Button
                  size="sm"
                  disabled={offset === 0}
                  onClick={() => setOffset(Math.max(0, offset - 25))}
                >
                  上一页
                </Button>
                <Button
                  size="sm"
                  disabled={offset + 25 >= details.length}
                  onClick={() => setOffset(offset + 25)}
                >
                  下一页
                </Button>
              </div>
            </div>
          </Card>
        </>
      )}
      {snapshots.length > 0 && (
        <details>
          <summary>已保存的分析快照</summary>
          {snapshots.map((s) => (
            <p key={s.id}>
              <Button
                size="sm"
                look="ghost"
                disabled={busy}
                onClick={() => void readSnapshot(s)}
              >
                {s.parameters.start_month} 至 {s.parameters.end_month} ·{" "}
                {new Date(s.created_at).toLocaleString("zh-CN")} ·{" "}
                {s.stale ? "依据已变化" : "依据当前有效"}
              </Button>
            </p>
          ))}
        </details>
      )}
    </div>
  );
}
