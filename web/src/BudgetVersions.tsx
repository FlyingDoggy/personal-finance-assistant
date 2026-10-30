import { useEffect, useRef, useState } from "react";
import { Button } from "@tuja/ui/components/button";
import { Card } from "@tuja/ui/components/card";
import { Text } from "@tuja/ui/components/text";
import { stack } from "@tuja/ui/primitives/stack.stylex";
import { api, errorText, money, createPendingRequest } from "./api";
import type { Budget, BudgetResponse } from "./types";
import { Modal, Notice } from "./ui";

type Version = {
  year: number;
  version: number;
  status: string;
  can_adopt: boolean;
  plan: Budget;
  base_adopted_version: number;
  impact: { removed_line_names: string[]; orphaned_link_count: number };
};
export function BudgetVersions({
  year,
  data,
  refresh,
  onSaved,
  onEdit,
}: {
  year: number;
  data: BudgetResponse | null;
  refresh: number;
  onSaved: () => Promise<void>;
  onEdit: (data: BudgetResponse) => void;
}) {
  const [items, setItems] = useState<Version[]>([]),
    [latest, setLatest] = useState(0),
    [error, setError] = useState(""),
    [confirm, setConfirm] = useState<Version | null>(null),
    [busy, setBusy] = useState(false);
  async function load() {
    const r = await api<{ items: Version[]; latest_version: number }>(
      `/budget-versions?year=${year}`,
    );
    setItems(r.items);
    setLatest(r.latest_version);
  }
  useEffect(() => {
    void load().catch((e) => setError(errorText(e)));
  }, [year, refresh, data?.version]);
  const attempt = useRef(createPendingRequest());
  async function adopt() {
    if (!confirm) return;
    setBusy(true);
    setError("");
    try {
      await api(`/budget-versions/${year}/${confirm.version}/adopt`, {
        method: "POST",
        body: JSON.stringify({
          request_id: attempt.current.key({
            year,
            version: confirm.version,
            expected: data?.version ?? 0,
          }),
          expected_adopted_version: data?.version ?? 0,
        }),
      });
      attempt.current.clear();
      setConfirm(null);
      await load();
      await onSaved();
    } catch (e) {
      setError(errorText(e));
    } finally {
      setBusy(false);
    }
  }
  const drafts = items.filter((v) => v.status === "draft");
  return (
    <div css={stack.item}>
      {error && <Notice kind="error">{error}</Notice>}
      {drafts.length > 0 && (
        <Card>
          <div css={stack.item}>
            <Text look="label">待采用草稿 · {drafts.length} 版</Text>
            <Text look="bodySmall" tone="muted">
              首页和预实继续使用采用版。核对草稿差异后，再决定是否采用。
            </Text>
            {drafts.map((v) => (
              <div key={v.version} css={stack.tight}>
                <Text>
                  草稿第 {v.version} 版 · 年度支出 ¥
                  {money(v.plan.annual.planned_minor)} · 计划留存 ¥
                  {money(v.plan.annual.planned_retention_minor)}
                </Text>
                <div className="filter-controls">
                  <Button
                    size="sm"
                    onClick={() =>
                      onEdit({
                        ...(data || {}),
                        version: data?.version ?? 0,
                        latest_version: latest,
                        budget: v.plan,
                      } as BudgetResponse)
                    }
                  >
                    另存修订草稿
                  </Button>
                  <Button
                    size="sm"
                    look="primary"
                    disabled={!v.can_adopt}
                    onClick={() => setConfirm(v)}
                  >
                    核对并采用
                  </Button>
                </div>
                {!v.can_adopt && (
                  <Text look="caption" tone="muted">
                    采用版已变化；请另存修订草稿，重新核对。
                  </Text>
                )}
              </div>
            ))}
          </div>
        </Card>
      )}
      {items.some((v) => v.status === "superseded") && (
        <details>
          <summary>过去的采用版本</summary>
          {items
            .filter((v) => v.status === "superseded")
            .map((v) => (
              <p key={v.version}>
                第 {v.version} 版 · 年度支出 ¥
                {money(v.plan.annual.planned_minor)} · 留存 ¥
                {money(v.plan.annual.planned_retention_minor)}
              </p>
            ))}
        </details>
      )}
      {confirm && (
        <Modal
          title="核对预算采用差异"
          onClose={() => !busy && setConfirm(null)}
        >
          <div className="modal-body" css={stack.item}>
            <Text>
              将采用 {year} 年第 {confirm.version} 版草稿。
            </Text>
            <div className="table-scroll">
              <table>
                <thead>
                  <tr>
                    <th>年度计划</th>
                    <th>当前采用版</th>
                    <th>草稿</th>
                    <th>变化</th>
                  </tr>
                </thead>
                <tbody>
                  {(
                    [
                      ["支出", "planned_minor"],
                      ["留存", "planned_retention_minor"],
                      ["目标差额", "target_gap_minor"],
                    ] as const
                  ).map(([label, key]) => (
                    <tr key={key}>
                      <th>{label}</th>
                      <td>
                        {data?.budget
                          ? money(data.budget.annual[key])
                          : "未设置"}
                      </td>
                      <td>{money(confirm.plan.annual[key])}</td>
                      <td>
                        {data?.budget
                          ? money(
                              String(
                                BigInt(confirm.plan.annual[key]) -
                                  BigInt(data.budget.annual[key]),
                              ),
                              true,
                            )
                          : "首次采用"}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
            <details>
              <summary>逐项核对计划</summary>
              {confirm.plan.lines.map((l) => (
                <p key={l.id}>
                  {l.name} · 全年 ¥
                  {money(
                    String(
                      l.monthly_minor.reduce((sum, n) => sum + BigInt(n), 0n),
                    ),
                  )}
                </p>
              ))}
            </details>
            {confirm.impact.orphaned_link_count > 0 && (
              <Notice>
                草稿移除了 {confirm.impact.removed_line_names.join("、")}，有{" "}
                {confirm.impact.orphaned_link_count}{" "}
                笔记录将失去当前项目映射，已知消费金额仍计入总占用。请核对这些用途安排。
              </Notice>
            )}
            <Notice>
              采用后首页和预实使用这个版本，原采用版保留历史。实际交易仍按各自证据计算。
            </Notice>
            {error && <Notice kind="error">{error}</Notice>}
          </div>
          <footer className="modal-footer">
            <Button disabled={busy} onClick={() => setConfirm(null)}>
              返回核对
            </Button>
            <Button look="primary" disabled={busy} onClick={() => void adopt()}>
              {busy ? "正在采用…" : "确认采用此预算"}
            </Button>
          </footer>
        </Modal>
      )}
    </div>
  );
}
