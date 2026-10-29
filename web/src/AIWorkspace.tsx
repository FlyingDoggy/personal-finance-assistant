import { useEffect, useRef, useState } from "react";
import { Button } from "@tuja/ui/components/button";
import { Heading } from "@tuja/ui/components/heading";
import { Card } from "@tuja/ui/components/card";
import { Text } from "@tuja/ui/components/text";
import { stack } from "@tuja/ui/primitives/stack.stylex";
import { api, createPendingRequest, errorText } from "./api";
import { Field, Notice } from "./ui";

type Work = { id: string; target_id: string; generation: number; status: string; reason: string | null; attempt_count: number };
type Queue = { items: Work[]; total: number; counts: Record<string, number>; runs: { id: string; status: string }[]; last_connected_at: string | null; configured: boolean };
const names: Record<string, string> = { queued: "等待 Codex", leased: "Codex 处理中", applied: "已应用", needs_review: "需要人工确认", rejected: "已拒绝", cancelled: "已关闭" };
export function AIWorkspace({ onChanged, onTransaction }: { onChanged: () => void | Promise<void>; onTransaction: (id: string) => void }) {
  const [data, setData] = useState<Queue | null>(null), [status, setStatus] = useState(""), [offset, setOffset] = useState(0),
    [error, setError] = useState(""), [busy, setBusy] = useState(false), [instruction, setInstruction] = useState("");
  const attempt = useRef(createPendingRequest());
  async function load() {
    setData(await api<Queue>(`/ai/work-items?limit=25&offset=${offset}${status ? `&status=${status}` : ""}`));
  }
  useEffect(() => { void load().catch(e => setError(errorText(e))); }, [offset, status]);
  async function refresh() { setError(""); try { await load(); await onChanged(); } catch (e) { setError(errorText(e)); } }
  async function retry(item: Work) {
    setBusy(true); setError("");
    const body = { retry_work_item_id: item.id, expected_generation: item.generation };
    try {
      const result = await api<{ instruction: string }>("/ai/prepare", { method: "POST", body: JSON.stringify({ ...body, request_id: attempt.current.key(body) }) });
      attempt.current.clear(); setInstruction(result.instruction); await load();
    } catch (e) { setError(errorText(e)); } finally { setBusy(false); }
  }
  return <div css={stack.group}>
    <Heading level={1} look="h3">AI 待办</Heading>
    <Text tone="muted">上传后先在导入批次中准备任务，再把任务指令交给 Codex。这里保留进度与处理结果，关闭页面不会丢失任务。</Text>
    <Notice>{data?.configured ? "本机 MCP 通道已配置。" : "本机 MCP 通道尚未配置。"} {data?.last_connected_at ? `最近连接成功：${new Date(data.last_connected_at).toLocaleString("zh-CN")}。` : "尚无 Codex 成功连接记录。"}当前只开放新流水的未分类消费科目与描述；历史判断不会被 AI 自动覆盖。</Notice>
    {error && <Notice kind="error">{error}</Notice>}
    <div className="screen-toolbar"><Field label="任务状态"><select value={status} onChange={e => { setStatus(e.target.value); setOffset(0); }}><option value="">全部</option>{Object.entries(names).map(([key, name]) => <option key={key} value={key}>{name}</option>)}</select></Field><Button disabled={busy} onClick={() => void refresh()}>刷新处理结果</Button><Button look="ghost" onClick={() => { location.hash = "imports"; }}>导入批次</Button></div>
    {instruction && <Notice><Text look="bodySmall">把以下内容交给已连接财务 MCP 的 Codex：</Text><p className="wrap-path">{instruction}</p><Button size="sm" onClick={() => navigator.clipboard.writeText(instruction).catch(() => setError("无法复制，请手动选中上方指令。"))}>复制任务指令</Button></Notice>}
    {data && data.runs.some(run => ["waiting_for_codex", "running"].includes(run.status)) && <details><summary>继续此前准备的任务</summary><div css={stack.tight}>{data.runs.filter(run => ["waiting_for_codex", "running"].includes(run.status)).map(run => <div key={run.id}><Button size="sm" look="ghost" onClick={() => setInstruction(`通过财务 MCP 继续处理范围 ${run.id}：列出并领取任务，阅读证据，仅补允许的科目和客观描述；证据不足提交待人工；完成后核对操作回执。`)}>显示此批指令</Button><Text look="caption">{run.id}</Text></div>)}</div></details>}
    {data?.items.length === 0 && <Text tone="muted">当前没有此类任务。未分类不代表已启动 AI；可从导入批次准备。</Text>}
    {data?.items.map(item => <Card key={item.id}><div css={stack.tight}><Text look="label">{names[item.status] || item.status} · 第 {item.generation} 次任务</Text><Text look="caption" tone="muted">已尝试 {item.attempt_count} 次 · {item.id}</Text>{item.reason && <Text look="bodySmall">{item.reason}</Text>}<div className="screen-toolbar"><Button size="sm" onClick={() => onTransaction(item.target_id)}>查看交易并处理疑点</Button>{["needs_review", "rejected", "cancelled"].includes(item.status) && <Button size="sm" look="ghost" disabled={busy} onClick={() => void retry(item)}>重新准备此任务</Button>}</div></div></Card>)}
    {data && <div className="screen-toolbar"><Text look="caption">共 {data.total} 项</Text><Button size="sm" disabled={offset === 0} onClick={() => setOffset(Math.max(0, offset - 25))}>上一页</Button><Button size="sm" disabled={offset + 25 >= data.total} onClick={() => setOffset(offset + 25)}>下一页</Button></div>}
  </div>;
}
