import { useEffect, useState } from "react";
import * as api from "../api";
import { imgUrl } from "../api";
import type { ContextMessage } from "../types";
import { useProposalDraw } from "../useProposalDraw";
import { useToast } from "./Toast";
import { Button } from "./ui";

interface Props {
  storyId: string;
  proposalId: number;
  canAct: boolean;
  onChanged: () => void; // 写稿变化 → 让画图节点/绘图台重取
  onWriting?: (proposalId: number, on: boolean) => void; // 写稿/重写中 → 点亮显微镜写稿节点
}

const ROLE: Record<string, string> = { system: "system · 系统", user: "user · 输入", assistant: "assistant · 历史叙事" };

/** 写稿节点(绘图 Agent / DeepSeek):输入按区块展示+可编辑;输出是「提示词文本」,绝不是图;
 *  重试 = 重写提示词(可用编辑后的输入)。 */
export function WriteNodeEditor({ storyId, proposalId, canAct, onChanged, onWriting }: Props) {
  const { data, loading, reload } = useProposalDraw(storyId, proposalId);
  const toast = useToast();
  const [msgs, setMsgs] = useState<ContextMessage[]>([]);
  const [busy, setBusy] = useState<null | "save" | "write">(null);
  const [error, setError] = useState<string | null>(null);

  const orig = data ? JSON.stringify(data.draft_messages) : "[]";
  useEffect(() => {
    setMsgs(data ? data.draft_messages.map((m) => ({ ...m })) : []);
  }, [orig, data]);
  const dirty = JSON.stringify(msgs) !== orig;
  const written = Boolean(data?.draft_prompt);

  if (loading && !data) return <Shell title="写稿"><Dim>读取中…</Dim></Shell>;
  if (!data) return <Shell title="写稿"><Dim>—</Dim></Shell>;

  const runWrite = async (fn: () => Promise<unknown>) => {
    setBusy("write");
    setError(null);
    onWriting?.(proposalId, true);
    try {
      await fn();
      await reload();
      onChanged();
    } catch (e) {
      setError(String(e));
      toast(`绘图写稿出错:${String(e)}`);
    } finally {
      onWriting?.(proposalId, false);
      setBusy(null);
    }
  };
  const firstWrite = () => runWrite(() => api.writeDraft(storyId, proposalId));
  const rewrite = () =>
    runWrite(async () => {
      if (dirty) await api.saveDraftMessages(storyId, proposalId, msgs);
      await api.writeDraft(storyId, proposalId, dirty ? msgs : undefined);
    });
  const save = async () => {
    setBusy("save");
    try {
      await api.saveDraftMessages(storyId, proposalId, msgs);
    } finally {
      setBusy(null);
    }
  };

  return (
    <Shell
      title="写稿(绘图 Agent)"
      note="绘图 Agent 写提示词。输入截断到提案所属轮;输出是文字稿(不是图)。"
      scene={data.scene_slug}
      kind={data.kind}
    >
      {error && (
        <div className="rounded-lg border border-danger/30 bg-danger-soft px-3 py-2.5 text-[12px] leading-relaxed text-danger">
          <div className="mb-0.5 font-medium">⚠ 写稿调用失败</div>
          <div className="whitespace-pre-wrap break-words font-mono text-[11px]">{error}</div>
        </div>
      )}
      {!written && data.draft_messages.length === 0 ? (
        <div className="flex flex-col gap-2">
          <p className="text-[12.5px] leading-relaxed text-ink-faint">还没写稿。让绘图 Agent 据该轮黑板+画风+参考图库写第一版提示词。</p>
          <Button variant="primary" disabled={!canAct || busy !== null} onClick={firstWrite}>
            {busy === "write" ? "写稿中…" : "✎ 让绘图 Agent 写稿"}
          </Button>
        </div>
      ) : (
        <>
          <Label>输入 · 完整 messages({msgs.length})· 可编辑</Label>
          <div className="flex flex-col gap-1.5">
            {msgs.map((m, i) => (
              <details key={i} open={i === msgs.length - 1} className="rounded-lg border border-line bg-paper">
                <summary className="flex cursor-pointer items-center gap-2 px-3 py-2 text-[11px] text-ink-soft">
                  <span className="font-mono text-accent-ink">{ROLE[m.role] ?? m.role}</span>
                  <span className="ml-auto font-mono text-[10px] text-ink-faint">{m.content.length} 字</span>
                </summary>
                <textarea
                  value={m.content}
                  readOnly={!canAct}
                  spellCheck={false}
                  onChange={(e) => setMsgs((d) => d.map((x, j) => (j === i ? { ...x, content: e.target.value } : x)))}
                  className="block max-h-72 min-h-24 w-full resize-y border-t border-line bg-paper px-3 py-2 font-mono text-[11.5px] leading-relaxed text-ink focus:outline-none read-only:text-ink-soft"
                />
              </details>
            ))}
          </div>

          <Label>输出 · 提示词稿(文字,非图)</Label>
          <div className="whitespace-pre-wrap rounded-lg border border-line bg-paper px-3 py-2 text-[12.5px] leading-relaxed text-ink">
            {data.draft_prompt || <span className="text-ink-faint">(尚未写稿)</span>}
          </div>

          {data.draft_manifest.length > 0 && (
            <>
              <Label>输出 · 建议参考图(语义名)</Label>
              <div className="flex flex-wrap gap-2">
                {data.draft_manifest.map((r, i) => (
                  <figure key={i} className="w-[68px]" title={r.purpose}>
                    {r.preview_path ? (
                      <img src={imgUrl(r.preview_path)} alt={r.semantic_name} className="h-[44px] w-[68px] rounded-md border border-line object-cover" />
                    ) : (
                      <div className="flex h-[44px] w-[68px] items-center justify-center rounded-md border border-dashed border-line-strong text-[9px] text-ink-faint">无图</div>
                    )}
                    <figcaption className="mt-0.5 truncate text-[10px] text-ink-soft">{r.semantic_name}</figcaption>
                  </figure>
                ))}
              </div>
            </>
          )}

          {canAct && (
            <div className="sticky bottom-0 -mx-5 mt-1 flex items-center gap-2 border-t border-line bg-surface px-5 py-3">
              <Button variant="ghost" disabled={!dirty || busy !== null} onClick={save}>
                {busy === "save" ? "保存中…" : dirty ? "保存输入" : "已保存"}
              </Button>
              <Button variant="primary" disabled={busy !== null} onClick={rewrite}>
                {busy === "write" ? "重写中…" : "↻ 重写提示词"}
              </Button>
              <span className="ml-auto font-mono text-[10px] text-ink-faint">写稿不出图、不花钱</span>
            </div>
          )}
        </>
      )}
    </Shell>
  );
}

function Shell({ title, note, scene, kind, children }: { title: string; note?: string; scene?: string; kind?: string; children: React.ReactNode }) {
  return (
    <div className="flex min-h-0 flex-1 flex-col">
      <div className="border-b border-line px-5 py-3.5">
        <div className="flex items-baseline gap-2">
          <span className="font-serif text-[15px] text-ink">{title}</span>
          {scene && <span className="rounded-[5px] bg-accent-soft px-1.5 py-px font-mono text-[10.5px] text-accent-ink">{scene}</span>}
          {kind && <span className="rounded-[5px] bg-sunken px-1.5 py-px font-mono text-[10.5px] text-ink-soft">{kind}</span>}
        </div>
        {note && <div className="mt-0.5 text-[11.5px] text-ink-faint">{note}</div>}
      </div>
      <div className="flex min-h-0 flex-1 flex-col gap-3 overflow-y-auto px-5 py-4">{children}</div>
    </div>
  );
}
const Label = ({ children }: { children: React.ReactNode }) => (
  <div className="mt-1 font-mono text-[10.5px] uppercase tracking-[0.14em] text-ink-faint">{children}</div>
);
const Dim = ({ children }: { children: React.ReactNode }) => <span className="text-[12.5px] text-ink-faint">{children}</span>;
