import { Card } from "@tuja/ui/components/card";
import { Button } from "@tuja/ui/components/button";
import { Heading } from "@tuja/ui/components/heading";
import { useRef, useState } from "react";
import { api, createPendingRequest, errorText, money, toMinor } from "./api";
import type { Account, ImportBatch, Status } from "./types";
import { Empty, Field, Icon, Modal, Notice } from "./ui";
export function MorePage({
  accounts,
  imports,
  status,
  onAddAccount,
  onImport,
  onVouchers,
  onCatalog,
  onSettings,
}: {
  accounts: Account[];
  imports: ImportBatch[];
  status: Status | null;
  onAddAccount: () => void;
  onImport: () => void;
  onVouchers: () => void;
  onCatalog: () => void;
  onSettings: () => void;
}) {
  return (
    <>
      <div className="page-title">
        <div>
          <p className="eyebrow">让账本有据可查</p>
          <Heading level={1} look="h3">
            账户与资料
          </Heading>
          <p>从账户开始，持续补齐流水和余额依据。</p>
        </div>
      </div>
      <div className="more-actions">
        <button onClick={onSettings}>
          <span className="action-icon"><Icon name="more" size={24} /></span>
          <span><b>设置</b><small>账户、基础资料、本设备偏好与数据</small></span>
          <Icon name="arrow" />
        </button>
        <button onClick={() => { location.hash = "imports"; }}><span className="action-icon"><Icon name="upload" size={24}/></span><span><b>导入批次</b><small>预览、处理结果与继续导入</small></span><Icon name="arrow"/></button>
        <button onClick={() => { location.hash = "ai"; }}><span className="action-icon"><Icon name="clock" size={24}/></span><span><b>AI 待办</b><small>Codex 分类任务与待核疑点</small></span><Icon name="arrow"/></button>
        <button onClick={onVouchers}>
          <span className="action-icon"><Icon name="transactions" size={24} /></span>
          <span><b>凭证明细</b><small>按科目和账户查看借贷分录及来源</small></span>
          <Icon name="arrow" />
        </button>
        <button onClick={onCatalog}>
          <span className="action-icon"><Icon name="budget" size={24} /></span>
          <span><b>基础资料</b><small>科目、账户、商户、标签与预算项目</small></span>
          <Icon name="arrow" />
        </button>
        <button onClick={onImport}>
          <span className="action-icon">
            <Icon name="upload" size={24} />
          </span>
          <span>
            <b>导入流水</b>
            <small>预览原生账单，查看每批结果</small>
          </span>
          <Icon name="arrow" />
        </button>
        <button onClick={onAddAccount}>
          <span className="action-icon">
            <Icon name="wallet" size={24} />
          </span>
          <span>
            <b>添加账户</b>
            <small>登记现金账户或信用账户</small>
          </span>
          <Icon name="plus" />
        </button>
      </div>
      <Card>
        <div className="section-heading">
          <div>
            <Heading level={2} look="h4">
              我的账户
            </Heading>
            <p>当前已登记 {accounts.length} 个账户 · 人民币</p>
          </div>
          <Button onClick={onAddAccount} look="ghost" size="sm">
            添加
            <Icon name="plus" size={16} />
          </Button>
        </div>
        {accounts.length ? (
          <div className="account-grid">
            {accounts.map((a) => (
              <article className="account-card" key={a.id}>
                <div>
                  <span className="account-icon">
                    <Icon name="wallet" />
                  </span>
                  <span className="badge">
                    {a.kind === "credit" ? "信用负债" : "现金账户"}
                  </span>
                </div>
                <Heading level={3} look="h4">
                  {a.name}
                </Heading>
                <span className="muted">
                  {a.kind === "credit"
                    ? a.overpayment_minor && BigInt(a.overpayment_minor) > 0n
                      ? "当前溢缴款"
                      : "当前账面负债"
                    : "当前账面余额"}
                </span>
                <strong className="account-balance">
                  {a.balance_minor == null
                    ? a.opening_balance_minor == null
                      ? "起点待补"
                      : "待核对"
                    : `¥ ${money(a.kind === "credit" && a.overpayment_minor && BigInt(a.overpayment_minor) > 0n ? a.overpayment_minor : a.balance_minor)}`}
                </strong>
                <p>
                  {a.kind === "credit" ? "已知负债变动" : "已知现金变动"}{" "}
                  <b>¥ {money(a.known_change_minor, true)}</b>
                </p>
                {a.balance_minor == null &&
                  a.observed_balance_minor != null && (
                    <p>
                      按流水观察 ¥{money(a.observed_balance_minor)}
                      <br />
                      其中待解释变动 ¥{money(a.unexplained_change_minor, true)}
                      ；尚不作为已核对账面余额。
                    </p>
                  )}
                <small>
                  {a.opening_date
                    ? `起点 ${a.opening_date} · ¥${money(a.opening_balance_minor)}`
                    : "未提供有依据的起始余额，暂不计算绝对余额。"}
                </small>
              </article>
            ))}
          </div>
        ) : (
          <Empty
            icon="wallet"
            title="先登记一个账户"
            action={
              <Button onClick={onAddAccount} look="primary">
                添加账户
                <Icon name="plus" size={16} />
              </Button>
            }
          >
            使用你熟悉的名称，比如工资卡、日常钱包。不清楚起始余额时可以留空。
          </Empty>
        )}
      </Card>
      <Card>
        <div className="section-heading">
          <div>
            <Heading level={2} look="h4">
              导入记录
            </Heading>
            <p>成功、重复和未导入的行分别保留结果</p>
          </div>
          <Button onClick={onImport} look="ghost" size="sm">
            导入流水
            <Icon name="upload" size={16} />
          </Button>
        </div>
        {imports.length ? (
          <div className="import-list">
            {imports.map((batch) => (
              <details key={batch.id} className="import-item">
                <summary>
                  <span className="import-file">
                    <Icon name="transactions" />
                    <span>
                      <b>{batch.filename}</b>
                      <small>
                        {new Date(batch.created_at).toLocaleString("zh-CN", {
                          hour12: false,
                        })}
                      </small>
                    </span>
                  </span>
                  <span
                    className={`badge ${batch.rejected_count ? "amber" : "mint"}`}
                  >
                    {batch.rejected_count ? "部分待处理" : "已处理"}
                  </span>
                </summary>
                <div className="import-result">
                  <div>
                    <span>成功导入</span>
                    <b>{batch.imported_count}</b>
                  </div>
                  <div>
                    <span>重复跳过</span>
                    <b>{batch.duplicate_count}</b>
                  </div>
                  <div>
                    <span>未导入</span>
                    <b>{batch.rejected_count}</b>
                  </div>
                </div>
                {batch.errors?.length > 0 && (
                  <ul className="import-errors">
                    {batch.errors.map((e, i) => (
                      <li key={i}>
                        第 {e.row} 行：{e.message}
                      </li>
                    ))}
                  </ul>
                )}
              </details>
            ))}
          </div>
        ) : (
          <div className="inline-empty">
            尚未导入文件。下载标准模板填入真实流水，即可开始。
          </div>
        )}
      </Card>
      {status?.migration && (
        <Card>
          <div className="section-heading">
            <Heading level={2} look="h4">
              历史账本迁移
            </Heading>
            <span className="badge">保留原口径</span>
          </div>
          <dl>
            <div>
              <dt>迁入原交易</dt>
              <dd>{status.migration.source_transaction_count} 笔</dd>
            </div>
            <div>
              <dt>纳入原统计范围</dt>
              <dd>{status.migration.scoped_transaction_count} 笔</dd>
            </div>
            <div>
              <dt>未标注日期</dt>
              <dd>{status.migration.undated_transaction_count} 笔</dd>
            </div>
            <div>
              <dt>已迁入预算</dt>
              <dd>{status.migration.budget_year} 年</dd>
            </div>
            <div>
              <dt>历史账本来源</dt>
              <dd>{status.migration.source_workbook.split(/[\\/]/).at(-1)}</dd>
            </div>
            <div>
              <dt>预算来源</dt>
              <dd>{status.migration.budget_workbook.split(/[\\/]/).at(-1)}</dd>
            </div>
            <div>
              <dt>迁移完成时间</dt>
              <dd>
                {new Date(status.migration.completed_at).toLocaleString(
                  "zh-CN",
                  { hour12: false },
                )}
              </dd>
            </div>
          </dl>
          <Notice>
            {status.migration.current_ledger_modified
              ? "迁移时与 Excel 一致；账本已更新"
              : status.migration.reconciliation_status || "核对状态待确认"}
            。历史记录只读保留原金额与原业务解释；无日期和范围外记录可在交易页选择“全部期间”查看。
          </Notice>
        </Card>
      )}
      <section className="panel status-panel">
        <div className="section-heading">
          <div>
            <Heading level={2} look="h4">
              账本状态
            </Heading>
            <p>资料完整程度与当前可用范围</p>
          </div>
          <span className="status-dot-label">
            <i />
            本机账本
          </span>
        </div>
        <dl>
          <div>
            <dt>已登记账户</dt>
            <dd>{status?.account_count ?? "—"} 个</dd>
          </div>
          <div>
            <dt>已保存交易</dt>
            <dd>{status?.transaction_count ?? "—"} 笔</dd>
          </div>
          <div>
            <dt>数据修订</dt>
            <dd>
              {status?.data_revision != null
                ? `第 ${status.data_revision} 次`
                : "待读取"}
            </dd>
          </div>
          <div>
            <dt>资料覆盖</dt>
            <dd>
              {status?.coverage.status === "complete"
                ? "已核对"
                : status?.coverage.status === "empty"
                  ? "尚无资料"
                  : "部分覆盖"}
            </dd>
          </div>
        </dl>
        {status?.coverage.reasons.length ? (
          <Notice>{status.coverage.reasons.join("；")}</Notice>
        ) : null}
        {status?.limitations?.length ? (
          <ul className="limitation-list">
            {status.limitations.map((l, i) => (
              <li key={i}>{l}</li>
            ))}
          </ul>
        ) : null}
      </section>
    </>
  );
}
export function AccountModal({
  onClose,
  onSaved,
}: {
  onClose: () => void;
  onSaved: () => Promise<void>;
}) {
  const attempt = useRef(createPendingRequest());
  const [name, setName] = useState(""),
    [kind, setKind] = useState("cash"),
    [opening, setOpening] = useState(""),
    [date, setDate] = useState(""),
    [evidence, setEvidence] = useState(""),
    [saving, setSaving] = useState(false),
    [error, setError] = useState("");
  async function save(e: React.FormEvent) {
    e.preventDefault();
    setSaving(true);
    setError("");
    try {
      if (!name.trim()) throw new Error("请填写账户名称。");
      if (opening.trim() && (!date || !evidence.trim()))
        throw new Error("请补充起始余额的日期和来源依据。");
      const body = {
        name: name.trim(),
        kind,
        currency: "CNY",
        opening_balance_minor: opening.trim() ? toMinor(opening) : null,
        opening_date: opening.trim() ? date : null,
        evidence: evidence.trim(),
      };
      await api("/accounts", {
        method: "POST",
        body: JSON.stringify({
          ...body,
          request_id: attempt.current.key(body),
        }),
      });
      attempt.current.clear();
      await onSaved();
      onClose();
    } catch (e) {
      setError(errorText(e));
    } finally {
      setSaving(false);
    }
  }
  return (
    <Modal title="添加账户" onClose={() => !saving && onClose()}>
      <form onSubmit={save}>
        <div className="modal-body">
          <p className="form-intro">
            用自己熟悉的名称区分来源。暂时不清楚余额，也可以先导入流水。
          </p>
          {error && <Notice kind="error">{error}</Notice>}
          <div className="form-grid">
            <Field label="账户名称">
              <input
                autoFocus
                required
                value={name}
                onChange={(e) => setName(e.target.value)}
                placeholder="例如：日常银行卡"
                maxLength={80}
              />
            </Field>
            <Field label="账户类型">
              <select value={kind} onChange={(e) => setKind(e.target.value)}>
                <option value="cash">现金账户（银行卡 / 钱包）</option>
                <option value="credit">信用账户（信用卡）</option>
              </select>
            </Field>
          </div>
          <Field
            label={
              kind === "credit"
                ? "起始负债（元，可留空）"
                : "起始余额（元，可留空）"
            }
            hint={
              kind === "credit"
                ? "填写正数表示尚欠金额。未知请留空，不能填 0 代替。"
                : "只有确认没有余额时才填 0。未知请留空。"
            }
          >
            <input
              inputMode="decimal"
              value={opening}
              onChange={(e) => setOpening(e.target.value)}
              placeholder="尚不清楚，留空"
            />
          </Field>
          {opening.trim() && (
            <div className="form-grid">
              <Field label="余额对应日期">
                <input
                  type="date"
                  required
                  value={date}
                  onChange={(e) => setDate(e.target.value)}
                />
              </Field>
              <Field label="余额来源依据">
                <input
                  required
                  value={evidence}
                  onChange={(e) => setEvidence(e.target.value)}
                  placeholder="例如：银行月结单期初余额"
                />
              </Field>
            </div>
          )}
          <Notice>
            后续交易只累计已知变动。缺少起始余额时，账本不会推断当前绝对余额。
          </Notice>
        </div>
        <footer className="modal-footer">
          <Button type="button" disabled={saving} onClick={onClose}>
            取消
          </Button>
          <Button type="submit" disabled={saving} look="primary">
            {saving ? "正在添加…" : "添加账户"}
          </Button>
        </footer>
      </form>
    </Modal>
  );
}
