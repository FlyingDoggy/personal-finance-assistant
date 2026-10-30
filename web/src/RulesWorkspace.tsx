import { useEffect, useRef, useState } from "react";
import { Button } from "@tuja/ui/components/button";
import { Card } from "@tuja/ui/components/card";
import { Heading } from "@tuja/ui/components/heading";
import { Text } from "@tuja/ui/components/text";
import { stack } from "@tuja/ui/primitives/stack.stylex";
import { api, createPendingRequest, errorText } from "./api";
import type { BudgetResponse, ChartAccount } from "./types";
import { Field, Notice } from "./ui";

type Definition = {
  name: string;
  match_field: string;
  terms: string[];
  category_code: string;
  priority: number;
  match_mode: string;
  budget_line_id: string | null;
};
type Rule = {
  id: string;
  version: number;
  status: string;
  definition: Definition;
};
type Preview = {
  id: string;
  rule_id: string;
  rule_version: number;
  matching_count: number;
  dependencies: { rule_revision: number };
  skipped: Record<string, number>;
  note: string;
  changes: {
    id: string;
    description: string;
    before_category: string;
    category_code: string;
  }[];
};
const blank: Definition = {
  name: "",
  match_field: "description",
  terms: [],
  category_code: "5001",
  priority: 0,
  match_mode: "any",
  budget_line_id: null,
};
export function RulesWorkspace({
  chart,
  years,
  onChanged,
  onTransaction,
}: {
  chart: ChartAccount[];
  years: number[];
  onChanged: () => Promise<void>;
  onTransaction: (id: string) => void;
}) {
  const [items, setItems] = useState<Rule[]>([]),
    [revision, setRevision] = useState(0),
    [definition, setDefinition] = useState<Definition>(blank),
    [terms, setTerms] = useState(""),
    [editing, setEditing] = useState<Rule | null>(null),
    [preview, setPreview] = useState<Preview | null>(null),
    [year, setYear] = useState(years.at(-1) || new Date().getFullYear()),
    [budget, setBudget] = useState<BudgetResponse | null>(null),
    [busy, setBusy] = useState(false),
    [error, setError] = useState(""),
    [success, setSuccess] = useState("");
  const attempt = useRef(createPendingRequest());
  async function load() {
    const r = await api<{ items: Rule[]; rule_revision: number }>(
      "/classification-rules",
    );
    setItems(r.items);
    setRevision(r.rule_revision);
  }
  useEffect(() => {
    void load().catch((e) => setError(errorText(e)));
  }, []);
  useEffect(() => {
    void api<BudgetResponse>(`/budgets?year=${year}`)
      .then(setBudget)
      .catch((e) => setError(errorText(e)));
  }, [year]);
  async function action(fn: () => Promise<void>) {
    setBusy(true);
    setError("");
    setSuccess("");
    try {
      await fn();
    } catch (e) {
      setError(errorText(e));
    } finally {
      setBusy(false);
    }
  }
  async function save() {
    const body = {
      expected_version: editing
        ? Math.max(
            ...items.filter((r) => r.id === editing.id).map((r) => r.version),
          )
        : 0,
      ...(editing ? { rule_id: editing.id } : {}),
      definition: {
        ...definition,
        terms: terms
          .split(/[\n,，]/)
          .map((s) => s.trim())
          .filter(Boolean),
      },
    };
    await api("/classification-rules", {
      method: "POST",
      body: JSON.stringify({ ...body, request_id: attempt.current.key(body) }),
    });
    attempt.current.clear();
    setEditing(null);
    setDefinition(blank);
    setTerms("");
    setPreview(null);
    await load();
    setSuccess("已保存规则草稿。请预览影响后启用。");
  }
  async function previewRule(r: Rule) {
    setPreview(
      (
        await api<{ preview: Preview }>(
          `/classification-rules/${r.id}/${r.version}/preview`,
          { method: "POST" },
        )
      ).preview,
    );
  }
  async function enable(r: Rule) {
    await api(`/classification-rules/${r.id}/${r.version}/enable`, {
      method: "POST",
      body: JSON.stringify({
        request_id: attempt.current.key({
          action: "enable",
          id: r.id,
          version: r.version,
          revision,
          preview_id: preview?.id,
        }),
        preview_id: preview?.id,
        expected_rule_revision: revision,
      }),
    });
    attempt.current.clear();
    setPreview(null);
    await load();
    setSuccess(
      "规则已启用，将用于之后导入的适用记录。已有记录需重新预览后另行应用。",
    );
  }
  async function disable(r: Rule) {
    await api(`/classification-rules/${r.id}/${r.version}/disable`, {
      method: "POST",
      body: JSON.stringify({
        request_id: attempt.current.key({
          action: "disable",
          id: r.id,
          version: r.version,
          revision,
        }),
        expected_rule_revision: revision,
      }),
    });
    attempt.current.clear();
    setPreview(null);
    await load();
    setSuccess("已停用此规则，之前的修改历史继续保留。");
  }
  const previewRuleVersion = items.find(
    (r) => r.id === preview?.rule_id && r.version === preview?.rule_version,
  );
  return (
    <div css={stack.group}>
      <Heading level={1} look="h3">
        分类规则
      </Heading>
      <Text tone="muted">
        先保存草稿、核对影响，再启用。明确商品规则优先于商户和摘要；同级冲突保留待核，人工分类优先。
      </Text>
      {error && (
        <Notice kind="error">
          {error}
          <Button
            size="sm"
            look="ghost"
            onClick={() =>
              void action(async () => {
                await load();
                setPreview(null);
              })
            }
          >
            刷新规则
          </Button>
        </Notice>
      )}
      {success && <Notice kind="success">{success}</Notice>}
      <Card>
        <details open={editing !== null || items.length === 0}>
          <summary>{editing ? "修订规则草稿" : "新建规则草稿"}</summary>
          <form
            css={stack.item}
            onSubmit={(e) => {
              e.preventDefault();
              void action(save);
            }}
          >
            <Text look="label">{editing ? "修订规则草稿" : "新规则草稿"}</Text>
            <Field label="规则名称">
              <input
                required
                maxLength={100}
                value={definition.name}
                onChange={(e) =>
                  setDefinition({ ...definition, name: e.target.value })
                }
              />
            </Field>
            <Field label="匹配内容">
              <select
                value={definition.match_field}
                onChange={(e) =>
                  setDefinition({ ...definition, match_field: e.target.value })
                }
              >
                <option value="description">交易描述</option>
                <option value="merchant">明确商户字段</option>
                <option value="product">明确商品字段</option>
              </select>
            </Field>
            <Field
              label="关键词"
              hint="每行一个，最多 5 个。仅按文字包含匹配。"
            >
              <textarea
                required
                rows={3}
                value={terms}
                onChange={(e) => setTerms(e.target.value)}
              />
            </Field>
            <div className="analysis-controls">
              <Field label="关键词条件">
                <select
                  value={definition.match_mode}
                  onChange={(e) =>
                    setDefinition({ ...definition, match_mode: e.target.value })
                  }
                >
                  <option value="any">包含任意一个</option>
                  <option value="all">包含全部</option>
                </select>
              </Field>
              <Field label="同类规则优先级">
                <input
                  type="number"
                  min={0}
                  max={1000}
                  value={definition.priority}
                  onChange={(e) =>
                    setDefinition({
                      ...definition,
                      priority: Number(e.target.value),
                    })
                  }
                />
              </Field>
            </div>
            <Field label="归入费用科目">
              <select
                value={definition.category_code}
                onChange={(e) =>
                  setDefinition({
                    ...definition,
                    category_code: e.target.value,
                  })
                }
              >
                {chart
                  .filter(
                    (c) =>
                      c.type === "expense" &&
                      c.code !== "5016" &&
                      c.selectable &&
                      c.enabled,
                  )
                  .map((c) => (
                    <option key={c.code} value={c.code}>
                      {c.name}
                    </option>
                  ))}
              </select>
            </Field>
            <div className="analysis-controls">
              <Field label="预算年度">
                <select
                  value={year}
                  onChange={(e) => {
                    setYear(Number(e.target.value));
                    setDefinition({ ...definition, budget_line_id: null });
                  }}
                >
                  {[...new Set([...years, year])].map((y) => (
                    <option key={y}>{y}</option>
                  ))}
                </select>
              </Field>
              <Field
                label="可选预算项目"
                hint="只在年度及计量口径符合、且该笔尚未分配时填写。"
              >
                <select
                  value={definition.budget_line_id || ""}
                  onChange={(e) =>
                    setDefinition({
                      ...definition,
                      budget_line_id: e.target.value || null,
                    })
                  }
                >
                  <option value="">只补科目</option>
                  {budget?.budget?.lines
                    .filter((l) => l.actual_basis === "consumption")
                    .map((l) => (
                      <option key={l.id} value={l.id}>
                        {l.group} / {l.name}
                      </option>
                    ))}
                </select>
              </Field>
            </div>
            <div className="filter-controls">
              <Button type="submit" look="primary" disabled={busy}>
                保存规则草稿
              </Button>
              {editing && (
                <Button
                  onClick={() => {
                    setEditing(null);
                    setDefinition(blank);
                    setTerms("");
                  }}
                >
                  取消修订
                </Button>
              )}
            </div>
          </form>
        </details>
      </Card>
      {items.length === 0 && (
        <Text tone="muted">
          还没有规则。导入仍可记入未分类费用，再由人工或 Codex 补充。
        </Text>
      )}
      {items.map((r) => (
        <Card key={`${r.id}-${r.version}`}>
          <div css={stack.item}>
            <Text look="label">
              {r.definition.name} · v{r.version} ·{" "}
              {
                { draft: "草稿", enabled: "已启用", disabled: "已停用" }[
                  r.status
                ]
              }
            </Text>
            <Text look="bodySmall">
              关键词：{r.definition.terms.join("、")} →{" "}
              {chart.find((c) => c.code === r.definition.category_code)?.name ||
                r.definition.category_code}
            </Text>
            <div className="filter-controls">
              <Button
                size="sm"
                disabled={busy}
                onClick={() => void action(() => previewRule(r))}
              >
                预览影响
              </Button>
              <Button
                size="sm"
                disabled={busy}
                onClick={() => {
                  setEditing(r);
                  setDefinition(r.definition);
                  setTerms(r.definition.terms.join("\n"));
                  window.scrollTo({ top: 0 });
                }}
              >
                另存修订
              </Button>
              {r.status === "enabled" && (
                <Button
                  size="sm"
                  look="ghost"
                  disabled={busy}
                  onClick={() => void action(() => disable(r))}
                >
                  停用
                </Button>
              )}
            </div>
          </div>
        </Card>
      ))}
      {preview && (
        <Card>
          <div css={stack.item}>
            <Text look="label">
              影响预览 · 命中 {preview.matching_count} 笔
            </Text>
            <Text look="bodySmall">{preview.note}</Text>
            {preview.changes.map((r) => (
              <Button
                look="ghost"
                key={r.id}
                onClick={() => onTransaction(r.id)}
              >
                {r.description} →{" "}
                {chart.find((c) => c.code === r.category_code)?.name ||
                  r.category_code}
              </Button>
            ))}
            {Object.entries(preview.skipped).map(([reason, count]) => (
              <Text look="caption" key={reason}>
                {reason} {count} 条
              </Text>
            ))}
            {previewRuleVersion?.status === "draft" && (
              <Button
                look="primary"
                disabled={busy}
                onClick={() => void action(() => enable(previewRuleVersion))}
              >
                确认启用此规则
              </Button>
            )}
            {previewRuleVersion?.status === "enabled" && (
              <Button
                look="primary"
                disabled={busy || !preview.changes.length}
                onClick={() =>
                  void action(async () => {
                    const r = await api<{ applied_count: number }>(
                      `/rule-previews/${preview.id}/apply`,
                      {
                        method: "POST",
                        body: JSON.stringify({
                          request_id: attempt.current.key({
                            preview_id: preview.id,
                          }),
                        }),
                      },
                    );
                    attempt.current.clear();
                    setPreview(null);
                    setSuccess(`已分类 ${r.applied_count} 笔。`);
                    await onChanged();
                  })
                }
              >
                确认应用以上 {preview.changes.length} 笔
              </Button>
            )}
          </div>
        </Card>
      )}
    </div>
  );
}
