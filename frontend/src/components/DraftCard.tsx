import type { PickedRef } from "../types";
import type { DraftCard as Card } from "../useStoryEngine";
import { RefPicker } from "./RefPicker";
import { Button, Tag } from "./ui";

interface Props {
  card: Card;
  onEditPrompt: (key: string, prompt: string) => void;
  onSetRefs: (key: string, picked: PickedRef[]) => void;
  onConfirm: (key: string) => void;
  onReuse: (key: string) => void;
  onSkip: (key: string) => void;
  onDismiss: (key: string) => void;
}

/** 人在回路的绘图稿卡:写稿中 → 审阅(可编辑提示词 + 自由选择参考图)→ 确认出图 / 复用 / 跳过。 */
export function DraftCard({ card, onEditPrompt, onSetRefs, onConfirm, onReuse, onSkip, onDismiss }: Props) {
  const { draft } = card;

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

  // review
  return (
    <div className="rounded-xl border border-accent/30 bg-surface p-3.5 shadow-[0_1px_2px_rgba(28,37,48,0.04)]">
      <div className="flex items-baseline gap-2">
        <span className="font-serif text-[13.5px] text-ink">绘图稿</span>
        <Tag tone="accent">{draft.scene}</Tag>
        <Tag>{draft.kind}</Tag>
        {draft.draw_turn != null && <Tag>第{draft.draw_turn}轮</Tag>}
        <button onClick={() => onDismiss(card.key)} className="ml-auto text-ink-faint hover:text-ink">✕</button>
      </div>

      {card.warn && (
        <div className="mt-2.5 rounded-lg border border-danger/40 bg-danger-soft px-3 py-2 text-[12px] leading-snug text-danger">
          ⚠ 重绘基底:本场景已有 variant 变体图。重绘 new_scene 基底会让已有变体的基底改变、可能不连贯;
          旧变体会原样保留(需要时自行重画)。确认出图即代表你已知晓。
        </div>
      )}

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
        <Button variant="ghost" onClick={() => onReuse(card.key)}>复用已有图</Button>
        <Button variant="quiet" onClick={() => onSkip(card.key)}>跳过</Button>
        <span className="ml-auto font-mono text-[10px] text-ink-faint">确认即调用 gpt-image-2(花钱)</span>
      </div>
    </div>
  );
}
