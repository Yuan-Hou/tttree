import { useEffect, useState } from "react";
import type { AgentStep, ContextMessage, TurnContexts } from "../types";
import { Button } from "./ui";

interface Props {
  step: AgentStep;
  contexts: TurnContexts | null;
  loading: boolean;
  live: boolean;
  liveNarrative: string;
  editable: boolean; // 最新轮 && 非进行中 && 非重走中
  retrying: boolean;
  onSave: (step: Exclude<AgentStep, "reducer">, messages: ContextMessage[]) => Promise<void>;
  onRetry: (step: Exclude<AgentStep, "reducer">) => void;
}

const TITLE: Record<AgentStep, string> = {
  director_a: "导演 A",
  writer: "写手",
  director_b: "导演 B",
  reducer: "落盘(reducer)",
};
const ROLE_LABEL: Record<string, string> = {
  system: "system · 系统",
  user: "user · 输入",
  assistant: "assistant · 历史叙事",
};

export function NodeEditor(p: Props) {
  const { step } = p;
  const isMain = step !== "reducer";
  const sc = p.contexts && isMain ? p.contexts[step as Exclude<AgentStep, "reducer">] : null;

  const [draft, setDraft] = useState<ContextMessage[]>([]);
  const [saving, setSaving] = useState(false);
  const orig = sc ? JSON.stringify(sc.messages) : "[]";
  const dirty = JSON.stringify(draft) !== orig;

  useEffect(() => {
    setDraft(sc ? sc.messages.map((m) => ({ ...m })) : []);
  }, [orig, sc]);

  // ── reducer:无 LLM 调用、无输入记录,只读落盘后的黑板 ──
  if (step === "reducer")
    return (
      <Shell title="落盘(reducer)" note="纯逻辑落盘,无 LLM 调用、无可编辑输入;盖诞生点后写入 Turn。">
        <Label>落盘后的黑板</Label>
        {p.contexts ? <Json value={p.contexts.director_b.output} /> : p.live ? <LiveNote>本轮落盘后可见。</LiveNote> : <Dim />}
      </Shell>
    );

  if (p.live)
    return (
      <Shell title={TITLE[step]} note="本轮进行中,完整输入记录落盘后才可查看/编辑。">
        {step === "writer" && p.liveNarrative ? (
          <>
            <Label>实时成稿</Label>
            <div className="whitespace-pre-wrap font-sans text-[13px] leading-relaxed text-ink">
              {p.liveNarrative}
              <span className="caret" />
            </div>
          </>
        ) : (
          <LiveNote>本回合 {TITLE[step]} 完成后,这里可看到喂给它的完整输入并就地编辑。</LiveNote>
        )}
      </Shell>
    );

  if (p.loading) return <Shell title={TITLE[step]}><Dim /></Shell>;
  if (!sc) return <Shell title={TITLE[step]}><Dim /></Shell>;

  const update = (i: number, content: string) =>
    setDraft((d) => d.map((m, j) => (j === i ? { ...m, content } : m)));

  const save = async () => {
    setSaving(true);
    try {
      await p.onSave(step as Exclude<AgentStep, "reducer">, draft);
    } finally {
      setSaving(false);
    }
  };
  // 改完直接重试:先存(改后内容才进记录),再用这条改过的记录重走。
  const saveAndRetry = async () => {
    if (dirty) await save();
    p.onRetry(step as Exclude<AgentStep, "reducer">);
  };

  return (
    <Shell
      title={TITLE[step]}
      note={
        p.editable
          ? `输入 ${draft.length} 段 · 可就地编辑(直接改这一步存的记录)`
          : "历史轮 · 只读(要改请先回退到这一轮)"
      }
    >
      <Label>输入 · 完整 messages({draft.length})</Label>
      <div className="flex flex-col gap-1.5">
        {draft.map((m, i) => (
          <details key={i} open={i === draft.length - 1} className="rounded-lg border border-line bg-paper">
            <summary className="flex cursor-pointer items-center gap-2 px-3 py-2 text-[11px] text-ink-soft">
              <span className="font-mono text-accent-ink">{ROLE_LABEL[m.role] ?? m.role}</span>
              <span className="ml-auto font-mono text-[10px] text-ink-faint">{m.content.length} 字</span>
            </summary>
            <textarea
              value={m.content}
              onChange={(e) => update(i, e.target.value)}
              readOnly={!p.editable}
              spellCheck={false}
              className="block max-h-80 min-h-24 w-full resize-y border-t border-line bg-paper px-3 py-2 font-mono text-[11.5px] leading-relaxed text-ink focus:outline-none read-only:text-ink-soft"
            />
          </details>
        ))}
      </div>

      <Label>输出(信息源 · 改输入不动它)</Label>
      {step === "writer" ? (
        <div className="whitespace-pre-wrap font-sans text-[13px] leading-relaxed text-ink">{String(sc.output)}</div>
      ) : (
        <Json value={sc.output} />
      )}

      {p.editable && (
        <div className="sticky bottom-0 -mx-5 mt-1 flex items-center gap-2 border-t border-line bg-surface px-5 py-3">
          <Button variant="ghost" disabled={!dirty || saving} onClick={save}>
            {saving ? "保存中…" : dirty ? "保存修改" : "已保存"}
          </Button>
          <Button variant="primary" disabled={p.retrying} onClick={saveAndRetry}>
            {p.retrying ? "重走中…" : "从这里重试"}
          </Button>
          <span className="ml-auto font-mono text-[10px] text-ink-faint">改过输入 → 缓存从改动处往后 miss</span>
        </div>
      )}
    </Shell>
  );
}

function Shell({ title, note, children }: { title: string; note?: string; children: React.ReactNode }) {
  return (
    <div className="flex min-h-0 flex-1 flex-col">
      <div className="border-b border-line px-5 py-3.5">
        <div className="font-serif text-[15px] text-ink">{title}</div>
        {note && <div className="mt-0.5 text-[11.5px] text-ink-faint">{note}</div>}
      </div>
      <div className="flex min-h-0 flex-1 flex-col gap-3 overflow-y-auto px-5 py-4">{children}</div>
    </div>
  );
}
const Label = ({ children }: { children: React.ReactNode }) => (
  <div className="mt-1 font-mono text-[10.5px] uppercase tracking-[0.14em] text-ink-faint">{children}</div>
);
function Json({ value }: { value: unknown }) {
  return (
    <pre className="max-h-[360px] overflow-auto rounded-lg border border-line bg-paper px-3 py-2 font-mono text-[11.5px] leading-relaxed text-ink">
      {JSON.stringify(value, null, 2)}
    </pre>
  );
}
const LiveNote = ({ children }: { children: React.ReactNode }) => (
  <div className="rounded-lg border border-dashed border-accent/40 bg-accent-soft/40 px-4 py-3 text-[12px] text-accent-ink">{children}</div>
);
const Dim = () => <span className="text-[12.5px] text-ink-faint">—</span>;
