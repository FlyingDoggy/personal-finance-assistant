import { useRef, useState } from "react";
import { Button } from "@tuja/ui/components/button";
import { api, createPendingRequest, errorText, money } from "./api";
import type { BudgetResponse, ChartAccount, Transaction } from "./types";
import { Field, Modal, Notice } from "./ui";

export function BatchClassifier({
  items,
  chart,
  budget,
  onClose,
  onSaved,
}: {
  items: Transaction[];
  chart: ChartAccount[];
  budget: BudgetResponse | null;
  onClose: () => void;
  onSaved: () => Promise<void>;
}) {
  const [category, setCategory] = useState(""),
    [line, setLine] = useState("keep"),
    [reason, setReason] = useState(""),
    [busy, setBusy] = useState(false),
    [error, setError] = useState("");
  const attempt = useRef(createPendingRequest());
  async function save() {
    const patches = {
      ...(category ? { category_code: category } : {}),
      ...(line !== "keep" ? { budget_line_id: line || null } : {}),
    };
    const body = {
      patches,
      expected_versions: Object.fromEntries(
        items.map((t) => [t.id, t.version]),
      ),
      reason,
    };
    setBusy(true);
    setError("");
    try {
      await api("/transactions/batch-classify", {
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
      setBusy(false);
    }
  }
  return (
    <Modal
      title={`批量确认 ${items.length} 笔消费`}
      onClose={() => !busy && onClose()}
    >
      <form
        onSubmit={(e) => {
          e.preventDefault();
          void save();
        }}
      >
        <div className="modal-body">
          {error && <Notice kind="error">{error}</Notice>}
          <Field label="批量归入科目">
            <select
              value={category}
              onChange={(e) => setCategory(e.target.value)}
            >
              <option value="">不修改科目</option>
              {chart
                .filter(
                  (c) => c.type === "expense" && c.enabled && c.selectable,
                )
                .map((c) => (
                  <option key={c.code} value={c.code}>
                    {c.name}
                  </option>
                ))}
            </select>
          </Field>
          <Field label="批量预算关联">
            <select value={line} onChange={(e) => setLine(e.target.value)}>
              <option value="keep">不修改项目</option>
              <option value="">清空项目关联</option>
              {budget?.budget &&
                items.every((t) =>
                  t.date?.startsWith(String(budget.budget!.year)),
                ) &&
                budget.budget.lines
                  .filter((l) => l.actual_basis === "consumption")
                  .map((l) => (
                    <option key={l.id} value={l.id}>
                      {l.group} / {l.name}
                    </option>
                  ))}
            </select>
          </Field>
          <Field label="批量修改依据">
            <textarea
              required
              maxLength={2000}
              rows={3}
              value={reason}
              onChange={(e) => setReason(e.target.value)}
            />
          </Field>
          <Notice>
            本次会修改以下消费的当前分类并保留历史；已关联退款继承原消费的科目。任一交易版本冲突时整批退回核对。
          </Notice>
          <ul>
            {items.map((t) => (
              <li key={t.id}>
                {t.date} · {t.description} · 原始金额 ¥{money(t.amount_minor)}
              </li>
            ))}
          </ul>
        </div>
        <footer className="modal-footer">
          <Button disabled={busy} type="button" onClick={onClose}>
            返回
          </Button>
          <Button
            look="primary"
            type="submit"
            disabled={busy || (!category && line === "keep")}
          >
            {busy ? "正在保存…" : "确认修改选中的消费"}
          </Button>
        </footer>
      </form>
    </Modal>
  );
}
