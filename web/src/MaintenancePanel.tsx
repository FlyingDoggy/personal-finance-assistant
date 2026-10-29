import { useEffect, useState } from "react";
import { Button } from "@tuja/ui/components/button";
import { AnchorButton } from "@tuja/ui/components/anchor-button";
import { Card } from "@tuja/ui/components/card";
import { Text } from "@tuja/ui/components/text";
import { stack } from "@tuja/ui/primitives/stack.stylex";
import { api, errorText } from "./api";
import { Field, Notice } from "./ui";

type Backup = { id: string; created_at: string; data_revision: number; size: number; download_url: string; counts: Record<string, number> };
type Restored = { destination: string; data_revision: number; counts: Record<string, number> };
export function MaintenancePanel() {
  const [items, setItems] = useState<Backup[]>([]), [busy, setBusy] = useState(false),
    [error, setError] = useState(""), [file, setFile] = useState<File | null>(null),
    [restored, setRestored] = useState<Restored | null>(null);
  async function load() { const result = await api<{ items: Backup[] }>("/maintenance/backups"); setItems(result.items); }
  useEffect(() => { void load().catch(e => setError(errorText(e))); }, []);
  async function create() {
    setBusy(true); setError("");
    try { await api("/maintenance/backups", { method: "POST" }); await load(); }
    catch (e) { setError(errorText(e)); } finally { setBusy(false); }
  }
  async function restore() {
    if (!file) return;
    setBusy(true); setError(""); setRestored(null);
    const form = new FormData(); form.append("file", file);
    try { setRestored((await api<{ restore: Restored }>("/maintenance/restore-check", { method: "POST", body: form })).restore); }
    catch (e) { setError(errorText(e)); } finally { setBusy(false); }
  }
  return <Card><div css={stack.item}>
    <Text look="label">完整备份与恢复</Text>
    <Text look="bodySmall" tone="muted">备份包含 SQLite 账本、修改历史、预算、任务与原始附件。恢复会校验文件哈希和数据库，并生成独立目录，当前账本继续保留。</Text>
    {error && <Notice kind="error">{error}</Notice>}
    <div><Button look="primary" disabled={busy} onClick={() => void create()}>{busy ? "正在处理…" : "创建完整备份"}</Button></div>
    {items.slice(0, 5).map(item => <div key={item.id} className="backup-row"><Text look="bodySmall">{new Date(item.created_at).toLocaleString("zh-CN")} · 修订 {item.data_revision} · {item.counts.transactions} 笔交易 · {(item.size / 1048576).toFixed(1)} MB</Text><AnchorButton size="sm" look="ghost" href={item.download_url} download>下载备份</AnchorButton></div>)}
    <Field label="验证并恢复备份包" hint="选择本应用生成的 ZIP。成功后可用显示的目录启动恢复后的账本。"><input type="file" accept=".zip" disabled={busy} onChange={e => { setFile(e.target.files?.[0] || null); setRestored(null); }}/></Field>
    <div><Button disabled={busy || !file} onClick={() => void restore()}>恢复到独立目录</Button></div>
    {restored && <Notice kind="success">恢复验证通过：{restored.counts.transactions} 笔交易，修订 {restored.data_revision}。当前账本未替换。<br/>恢复目录：<code className="wrap-path">{restored.destination}</code><br/>需要切换时，停止服务后用启动脚本的 --data-dir 指向此目录。</Notice>}
  </div></Card>;
}
