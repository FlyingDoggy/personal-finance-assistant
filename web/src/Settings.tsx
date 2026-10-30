import { Button } from "@tuja/ui/components/button";
import { AnchorButton } from "@tuja/ui/components/anchor-button";
import { Card } from "@tuja/ui/components/card";
import { List } from "@tuja/ui/components/list";
import { ListRow } from "@tuja/ui/components/list-row";
import { Text } from "@tuja/ui/components/text";
import { stack } from "@tuja/ui/primitives/stack.stylex";
import { useEffect, useState } from "react";
import { AppearanceOptions } from "./ThemeOptions";
import { CatalogPage, type CatalogTab } from "./LedgerWorkspace";
import { SettingsFrame } from "./upstream/settings-frame";
import { SettingsPanel } from "./upstream/settings-panel";
import { isSettingsSection, type SettingsSection } from "./upstream/settings-sections";
import { Notice } from "./ui";
import { MaintenancePanel } from "./MaintenancePanel";
import { CoveragePanel } from "./CoveragePanel";
import type { Account, Status } from "./types";

function sectionFromHash(): SettingsSection | null {
  const section = location.hash.split("/")[1] || null;
  return isSettingsSection(section) ? section : null;
}
const catalogSections: Partial<Record<SettingsSection, { tab: CatalogTab; title: string; description: string }>> = {
  groups: { tab: "budget_lines", title: "预算项目", description: "项目使用稳定编号；额度保存在各年度的预算版本中。" },
  categories: { tab: "chart_accounts", title: "科目", description: "查看当前账本的科目结构和已支持的业务范围。" },
  payees: { tab: "merchants", title: "商户与别名", description: "整理明确的来源商户名称，保留每个名称的交易关联。" },
  tags: { tab: "tags", title: "标签", description: "查看已确认的消费行为和报销标记。" },
};
export function SettingsPage({ accounts, status, onAccounts, onImport, onBudget, onVouchers, onChanged }: {
  accounts: Account[]; status: Status | null; onAccounts: () => void; onImport: () => void;
  onBudget: () => void; onVouchers: () => void;
  onChanged: () => void | Promise<void>;
}) {
  const [current, setCurrent] = useState<SettingsSection | null>(sectionFromHash);
  useEffect(() => { const change = () => setCurrent(sectionFromHash()); window.addEventListener("hashchange", change); return () => window.removeEventListener("hashchange", change); }, []);
  const section = current || "accounts";
  const catalog = catalogSections[section];
  let content;
  if (catalog) {
    content = <SettingsPanel title={catalog.title} description={catalog.description} actions={section === "groups" ? <Button onClick={onBudget}>打开年度预算</Button> : undefined}><CatalogPage key={section} initialTab={catalog.tab} embedded/></SettingsPanel>;
  } else if (section === "preferences") {
    content = <SettingsPanel title="偏好设置" description="这些设置只对本设备生效，不改变账本数据和预算口径。"><AppearanceOptions/></SettingsPanel>;
  } else if (section === "data") {
    content = <SettingsPanel title="数据" description="保留可核对的数据副本，查看当前资料范围。">
      <Card><div css={stack.item}><Text look="label">导出账本数据</Text><Text look="bodySmall" tone="muted">下载当前交易、修改历史、预算和基础资料的 JSON 副本。导出不含银行原件附件，也不能替代完整备份。</Text><div><AnchorButton href="/api/v1/data/export" download>导出账本 JSON</AnchorButton></div></div></Card>
      <List><ListRow onClick={() => { location.hash = "rules"; }} detail="先预览影响，再启用或应用">分类规则</ListRow><ListRow onClick={() => { location.hash = "analysis"; }} detail="同窗报销标准化、结构和预实">消费与报销分析</ListRow></List>
      <MaintenancePanel/>
      <CoveragePanel accounts={accounts} onSaved={onChanged}/>
      <List><ListRow onClick={onVouchers} detail="查看派生分录、来源证据和修改版本">凭证明细</ListRow><ListRow onClick={onImport} detail="招商信用卡文本 PDF、标准 CSV、支付宝商品明细">导入流水</ListRow><ListRow onClick={() => { location.hash = "imports"; }} detail="查看预览、疑点与续传结果">导入批次</ListRow><ListRow onClick={() => { location.hash = "ai"; }} detail="准备任务后，由 Codex 领取处理">AI 待办</ListRow></List>
      <Notice>Excel／PDF 报表导出尚未接入。JSON 下载用于查看和核对数据；需要恢复账本时，请使用完整备份包。</Notice>
    </SettingsPanel>;
  } else if (section === "accounts") {
    content = <SettingsPanel title="账户" description="这份账本保存在当前电脑，收入、费用、现金收付按各自口径记录。" actions={<><Button onClick={onAccounts}>管理账户</Button><Button look="ghost" onClick={onVouchers}>查看凭证</Button></>}>
      <Card><dl className="settings-facts"><div><dt>账本类型</dt><dd>个人账本 · 本机存储</dd></div><div><dt>记账币种</dt><dd>人民币 CNY</dd></div><div><dt>已登记账户</dt><dd>{accounts.length} 个</dd></div><div><dt>当前交易</dt><dd>{status?.transaction_count ?? "—"} 条</dd></div><div><dt>账本修订</dt><dd>{status?.data_revision ?? "—"}</dd></div></dl></Card>
      <Notice>账户期初未知时，资产和负债的绝对余额仍待补齐；未登记资料不会按零计算。预算月份与实际交易月份分别选择。</Notice>
    </SettingsPanel>;
  } else {
    content = null;
  }

  return <SettingsFrame current={current}>{content}</SettingsFrame>;
}
