import { useEffect, useRef, useState } from "react";
import type { PickedRef } from "../types";
import type { DraftCard as Card } from "../useStoryEngine";
import { RefPicker } from "./RefPicker";
import { SubstituteDialog } from "./SubstituteDialog";
import { Button, Tag } from "./ui";

interface Props {
  card: Card;
  onGenerate: (key: string) => void; // (重新)生成提示词:用卡上的「附加指令」让绘图 Agent 出稿
  onEditInstruction: (key: string, text: string) => void; // 编辑「附加指令」
  onEditPrompt: (key: string, prompt: string) => void;
  onSetRefs: (key: string, picked: PickedRef[]) => void;
  onConfirm: (key: string) => void;
  onReuse: (key: string) => void;
  onSkip: (key: string) => void;
  onSubstitute: (key: string, pick: { imagegenId?: number; file?: File }) => Promise<void>;
  onDismiss: (key: string) => void;
}

/** 人在回路的绘图稿卡:填附加指令 → 生成提示词 → 审阅(可编辑提示词 + 自由选择参考图)→
 *  确认出图 / 复用 / 替代 / 跳过。出稿前可填给绘图 Agent 的附加指令,出稿后可改指令重新生成。 */
export function DraftCard({ card, onGenerate, onEditInstruction, onEditPrompt, onSetRefs, onConfirm, onReuse, onSkip, onSubstitute, onDismiss }: Props) {
  const { draft } = card;
  const [subOpen, setSubOpen] = useState(false);
  const [subBusy, setSubBusy] = useState(false);
  const rootRef = useRef<HTMLDivElement>(null); // 卡一出现就滚到它(手动绘图触发「画这个场景」时)

  // 新卡挂载即滚入视野 —— 用户点「画这个场景」后,绘图窗口直接呈现在眼前。
  useEffect(() => {
    rootRef.current?.scrollIntoView({ behavior: "smooth", block: "start" });
  }, []);

  if (card.status === "writing")
    return (
      <div className="rounded-xl border border-line bg-paper p-3">
        <span className="breathe font-mono text-[12px] text-ink-soft">为 {draft.scene} 写稿中…</span>
      </div>
    );

  if (card.status === "submitted")
    return (
      <div className="rounded-xl border border-accent/30 bg-accent-soft/40 p-3">
        <div className="flex items-center justify-between">
          <span className="font-mono text-[11.5px] text-accent-ink">⟳ {draft.scene} · 后台生成中</span>
          <span className="text-[11px] text-ink-faint">画在场景里浮现</span>
        </div>
      </div>
    );

  if (card.status === "failed")
    return (
      <div className="rounded-xl border border-danger/30 bg-danger-soft p-3">
        <div className="flex items-center justify-between gap-2">
          <span className="text-[12px] text-danger">写稿/出图失败:{card.note}</span>
          <button onClick={() => onDismiss(card.key)} className="text-ink-faint hover:text-danger">✕</button>
        </div>
      </div>
    );

  // review(含「待生成」子态:尚未出稿 → 只显示附加指令 + 生成按钮)
  const written = Boolean(card.draft.draft_id);
  return (
    <div ref={rootRef} className="scroll-mt-2 rounded-xl border border-accent/30 bg-surface p-3.5 shadow-[0_1px_2px_rgba(28,37,48,0.04)]">
      <div className="flex items-baseline gap-2">
        <span className="font-serif text-[13.5px] text-ink">绘图稿</span>
        <Tag tone="accent">{draft.scene}</Tag>
        {draft.kind && <Tag>{draft.kind}</Tag>}
        {draft.draw_turn != null && <Tag>第{draft.draw_turn}轮</Tag>}
        <button onClick={() => onDismiss(card.key)} className="ml-auto text-ink-faint hover:text-ink">✕</button>
      </div>

      {card.warn && (
        <div className="mt-2.5 rounded-lg border border-danger/40 bg-danger-soft px-3 py-2 text-[12px] leading-snug text-danger">
          ⚠ 重绘基底:本场景已有 variant 变体图。重绘 new_scene 基底会让已有变体的基底改变、可能不连贯;
          旧变体会原样保留(需要时自行重画)。确认出图即代表你已知晓。
        </div>
      )}

      {/* 附加指令:出稿前可写、出稿后可改;原样接到绘图写稿 Agent 输入末尾,再点(重新)生成 */}
      <label className="mt-3 block font-mono text-[11px] text-ink-faint">附加指令(可选 · 直接追加到绘图 Agent 输入末尾)</label>
      <textarea
        value={card.instruction}
        onChange={(e) => onEditInstruction(card.key, e.target.value)}
        rows={2}
        spellCheck={false}
        placeholder="例如:强调逆光、压暗整体;别画人物正脸…(留空则不追加)"
        className="mt-1.5 w-full resize-y rounded-lg border border-line-strong bg-paper px-3 py-2 text-[12.5px] leading-relaxed text-ink placeholder:text-ink-faint focus:border-accent focus:outline-none"
      />
      <div className="mt-2 flex items-center gap-2">
        <Button variant={written ? "ghost" : "primary"} onClick={() => onGenerate(card.key)}>
          {written ? "↻ 重新生成提示词" : "✎ 生成提示词"}
        </Button>
        <span className="font-mono text-[10px] text-ink-faint">写稿不出图、不花钱</span>
      </div>

      {!written ? (
        <p className="mt-3 rounded-lg border border-dashed border-accent/40 bg-accent-soft/40 px-3 py-2 text-[12px] text-accent-ink">
          填好附加指令(可留空)后点「生成提示词」,绘图 Agent 据该场景 + 画风 + 参考图库写稿。
        </p>
      ) : (
      <>
      <label className="mt-3 block font-mono text-[11px] text-ink-faint">提示词(可编辑)</label>
      <textarea
        value={card.prompt}
        onChange={(e) => onEditPrompt(card.key, e.target.value)}
        rows={5}
        spellCheck={false}
        className="mt-1.5 w-full resize-y rounded-lg border border-line-strong bg-paper px-3 py-2 text-[12.5px] leading-relaxed text-ink focus:border-accent focus:outline-none"
      />

      <div className="mt-3">
        <div className="font-mono text-[11px] text-ink-faint">参考图(自由选择 · 可增删)</div>
        <div className="mt-2">
          <RefPicker
            library={draft.library ?? []}
            pastImages={draft.past_images ?? []}
            value={card.picked}
            onChange={(v) => onSetRefs(card.key, v)}
          />
        </div>
      </div>

      <div className="mt-3.5 flex flex-wrap items-center gap-2">
        <Button variant="primary" onClick={() => onConfirm(card.key)}>
          {card.warn ? "我已知晓,重绘出图" : "确认出图"}
        </Button>
        <Button variant="ghost" onClick={() => setSubOpen(true)}>▣ 替代图片(不花钱)</Button>
        <Button variant="ghost" onClick={() => onReuse(card.key)}>复用已有图</Button>
        <Button variant="quiet" onClick={() => onSkip(card.key)}>跳过</Button>
        <span className="ml-auto font-mono text-[10px] text-ink-faint">确认即调用 gpt-image-2(花钱)</span>
      </div>

      {subOpen && (
        <SubstituteDialog
          pastImages={draft.past_images ?? []}
          busy={subBusy}
          onClose={() => setSubOpen(false)}
          onSubmit={async (pick) => {
            setSubBusy(true);
            try {
              await onSubstitute(card.key, pick);
            } catch {
              setSubBusy(false); // 失败留在对话框,可重试;成功则卡片已被移除
            }
          }}
        />
      )}
      </>
      )}
    </div>
  );
}
