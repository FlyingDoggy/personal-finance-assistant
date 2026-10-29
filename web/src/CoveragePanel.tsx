import { Button } from "@tuja/ui/components/button";
import { Card } from "@tuja/ui/components/card";
import { Heading } from "@tuja/ui/components/heading";
import { Checkbox } from "@tuja/ui/components/checkbox";
import { stack } from "@tuja/ui/primitives/stack.stylex";
import { useEffect, useRef, useState } from "react";
import { api, createPendingRequest, errorText } from "./api";
import type { Account } from "./types";
import { Empty, Field, Notice } from "./ui";

type Basis = "consumption" | "payment";
type DeclarationStatus = "complete" | "partial" | "missing";
type Declaration = {
  account_id: string;
  month: string;
  basis: Basis;
  version: number;
  status: DeclarationStatus;
  declared_status: DeclarationStatus;
  evidence: string;
  confirmed: boolean;
  stale: boolean;
  complete: boolean;
};
type CoverageResponse = {
  items: Declaration[];
  coverage_version: number;
  complete: boolean;
  reasons: string[];
};
const statusNames: Record<DeclarationStatus, string> = {
  complete: "资料完整",
  partial: "部分资料",
  missing: "资料缺失",
};

export function CoveragePanel({
  accounts,
  month,
  onSaved,
}: {
  accounts: Account[];
  month?: string;
  onSaved?: () => void | Promise<void>;
}) {
  const [selectedMonth, setSelectedMonth] = useState(
    month ?? new Intl.DateTimeFormat("en-CA", { timeZone: "Asia/Shanghai", year: "numeric", month: "2-digit" }).format(new Date()),
  );
  const [accountId, setAccountId] = useState(accounts[0]?.id ?? "");
  const selectedAccount = accounts.find((account) => account.id === accountId) ?? accounts[0];
  const selectedId = selectedAccount?.id ?? "";
  const [basis, setBasis] = useState<Basis>("consumption");
  const [data, setData] = useState<CoverageResponse | null>(null);
  const [status, setStatus] = useState<DeclarationStatus>("partial");
  const [evidence, setEvidence] = useState("");
  const [confirmed, setConfirmed] = useState(false);
  const [loading, setLoading] = useState(false);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState("");
  const [saved, setSaved] = useState("");
  const [reload, setReload] = useState(0);
  const attempt = useRef(createPendingRequest());
  const current = data?.items.find((item) => item.account_id === selectedId && item.month === selectedMonth && item.basis === basis);

  useEffect(() => {
    let active = true;
    setData(null);
    setError("");
    setSaved("");
    setConfirmed(false);
    setLoading(false);
    if (!selectedId || !/^\d{4}-\d{2}$/.test(selectedMonth)) return;
    setLoading(true);
    const params = new URLSearchParams({ month: selectedMonth, basis, account_id: selectedId });
    api<CoverageResponse>(`/coverage?${params}`)
      .then((result) => {
        if (!active) return;
        setData(result);
        const declaration = result.items[0];
        setStatus(declaration?.status === "missing" ? "partial" : declaration?.status ?? "partial");
        setEvidence(declaration?.evidence ?? "");
      })
      .catch((caught) => { if (active) setError(errorText(caught)); })
      .finally(() => { if (active) setLoading(false); });
    return () => { active = false; };
  }, [selectedId, selectedMonth, basis, reload]);

  async function save() {
    if (!current || saving) return;
    if (status === "complete" && (!confirmed || !evidence.trim())) {
      setError("完整覆盖需要填写证据，并确认已经核对该账户的整月资料。");
      return;
    }
    setSaving(true);
    setError("");
    setSaved("");
    const payload = { account_id: selectedId, month: selectedMonth, basis, status,
      evidence: evidence.trim(), confirmed: status === "complete" && confirmed, expected_version: current.version };
    try {
      const result = await api<{ declaration: Declaration; coverage_version: number }>("/coverage", {
        method: "POST", body: JSON.stringify({ ...payload, request_id: attempt.current.key(payload) }),
      });
      attempt.current.clear();
      setData({ items: [result.declaration], coverage_version: result.coverage_version, complete: result.declaration.complete, reasons: [] });
      setConfirmed(false);
      setSaved(`${selectedAccount?.name} · ${selectedMonth} 的覆盖声明已保存。`);
      await onSaved?.();
    } catch (caught) {
      setError(errorText(caught));
    } finally {
      setSaving(false);
    }
  }

  return (
    <Card>
      <section css={stack.group} aria-label="账户与月份覆盖">
        <div css={stack.tight}>
          <Heading level={3} look="h4">账户与月份覆盖</Heading>
          <p>按账户核对整月资料，区分资料缺失与完整零交易。预算剩余只使用对应月份、口径和账户范围的有效声明。</p>
        </div>
        {!accounts.length ? <Empty title="先登记需要核对的账户">添加账户并保留银行或平台资料后，即可记录月份覆盖。</Empty> : <>
          <div className="form-grid three">
            <Field label="覆盖月份"><input type="month" value={selectedMonth} disabled={saving} onChange={(event) => setSelectedMonth(event.target.value)} /></Field>
            <Field label="核对账户"><select value={selectedId} disabled={saving} onChange={(event) => setAccountId(event.target.value)}>
              {accounts.map((account) => <option key={account.id} value={account.id}>{account.name}</option>)}
            </select></Field>
            <Field label="资料口径"><select value={basis} disabled={saving} onChange={(event) => setBasis(event.target.value as Basis)}>
              <option value="consumption">消费发生与收入确认</option><option value="payment">实际资金收付</option>
            </select></Field>
          </div>
          <div aria-live="polite">
            {loading && <p>正在读取覆盖声明…</p>}
            {error && <Notice kind="error">{error} <Button look="ghost" disabled={saving || loading} onClick={() => setReload((value) => value + 1)}>重新读取</Button></Notice>}
            {saved && <Notice kind="success">{saved}</Notice>}
          </div>
          {current && <>
            <dl className="settings-facts">
              <div><dt>当前状态</dt><dd>{current.stale ? "流水已变化 · 需复核" : statusNames[current.status]}</dd></div>
              <div><dt>此账户声明版本</dt><dd>{current.version ? `第 ${current.version} 版` : "尚未声明"}</dd></div>
            </dl>
            {current.stale && <Notice>声明后有相关流水变化。已知金额仍保留，重新核对本账户、月份和口径后可再次确认完整。</Notice>}
            <form css={stack.item} onSubmit={(event) => { event.preventDefault(); void save(); }}>
              <Field label="本次覆盖状态"><select value={status} disabled={saving} onChange={(event) => { setStatus(event.target.value as DeclarationStatus); setConfirmed(false); }}>
                <option value="partial">部分资料</option><option value="missing">资料缺失</option><option value="complete">资料完整（含完整零交易）</option>
              </select></Field>
              <Field label="核对证据与缺口说明" hint="写明账单或记录的期间、已核对的资料，以及尚缺什么。">
                <textarea value={evidence} maxLength={4000} rows={3} required={status === "complete"} disabled={saving} onChange={(event) => setEvidence(event.target.value)} />
              </Field>
              {status === "complete" && <Checkbox checked={confirmed} disabled={saving} onChange={(event) => setConfirmed(event.target.checked)} label="我已核对该账户、月份和口径的完整资料；没有交易时也有依据。" />}
              <p className="footnote">覆盖确认不等于余额对账通过。未知期初仍保持未知；只确认一个账户不会使其他账户或月份变完整。</p>
              <div><Button type="submit" look="primary" disabled={saving || loading || (status === "complete" && (!confirmed || !evidence.trim()))}>{saving ? "正在保存…" : "保存覆盖声明"}</Button></div>
            </form>
          </>}
        </>}
      </section>
    </Card>
  );
}
