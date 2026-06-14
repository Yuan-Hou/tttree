import { imgUrl } from "../api";
import type { DrawProposal } from "../types";
import type { DraftCard } from "../useStoryEngine";
import { Button, Eyebrow, Tag } from "./ui";

interface Props {
  proposals: DrawProposal[];
  drafts: DraftCard[];
  onProposal: (p: DrawProposal) => void;
  onEditPrompt: (key: string, prompt: string) => void;
  onConfirm: (key: string) => void;
  onReuse: (key: string) => void;
  onSkip: (key: string) => void;
  onDismiss: (key: string) => void;
}

export function DraftReview({
  proposals, drafts, onProposal, onEditPrompt, onConfirm, onReuse, onSkip, onDismiss,
}: Props) {
  return (
    <section className="px-6 py-5">
      <Eyebrow>绘图台</Eyebrow>

      {proposals.length === 0 && drafts.length === 0 && (
        <p className="mt-3 text-[13px] leading-relaxed text-ink-faint">
          导演提议配图时会出现在这里;也可在上方场景里主动「画这个场景」。
        </p>
      )}

      <div className="mt-3.5 flex flex-col gap-3">
        {proposals.map((p, i) => (
          <div key={i} className="rounded-xl border border-line bg-paper p-3">
            <div className="flex items-baseline gap-2">
              <span className="text-[12px] text-ink-soft">导演提议配图</span>
              <Tag tone="accent">{p.scene_slug}</Tag>
              <Tag>{p.kind}</Tag>
            </div>
            <p className="mt-1.5 text-[12px] leading-snug text-ink-soft">{p.reason}</p>
            <div className="mt-2.5">
              <Button variant="ghost" onClick={() => onProposal(p)} className="px-2.5 py-1 text-[12px]">
                展开稿件
              </Button>
            </div>
          </div>
        ))}

        {drafts.map((c) => (
          <DraftCardView
            key={c.key}
            card={c}
            onEditPrompt={onEditPrompt}
            onConfirm={onConfirm}
            onReuse={onReuse}
            onSkip={onSkip}
            onDismiss={onDismiss}
          />
        ))}
      </div>
    </section>
  );
}

function DraftCardView({
  card, onEditPrompt, onConfirm, onReuse, onSkip, onDismiss,
}: {
  card: DraftCard;
  onEditPrompt: (key: string, prompt: string) => void;
  onConfirm: (key: string) => void;
  onReuse: (key: string) => void;
  onSkip: (key: string) => void;
  onDismiss: (key: string) => void;
}) {
  const { draft } = card;

  if (card.status === "writing") {
    return (
      <div className="rounded-xl border border-line bg-paper p-3">
        <span className="breathe font-mono text-[12px] text-ink-soft">为 {draft.scene} 写稿中…</span>
      </div>
    );
  }

  if (card.status === "submitted") {
    return (
      <div className="rounded-xl border border-accent/30 bg-accent-soft/40 p-3">
        <div className="flex items-center justify-between">
          <span className="font-mono text-[11.5px] text-accent-ink">
            ⟳ {draft.scene} · 后台生成中
          </span>
          <span className="text-[11px] text-ink-faint">画在上方场景里浮现</span>
        </div>
      </div>
    );
  }

  if (card.status === "failed") {
    return (
      <div className="rounded-xl border border-danger/30 bg-danger-soft p-3">
        <div className="flex items-center justify-between gap-2">
          <span className="text-[12px] text-danger">写稿/出图失败:{card.note}</span>
          <button onClick={() => onDismiss(card.key)} className="text-ink-faint hover:text-danger">
            ✕
          </button>
        </div>
      </div>
    );
  }

  // review
  return (
    <div className="rounded-xl border border-accent/30 bg-surface p-3.5 shadow-[0_1px_2px_rgba(28,37,48,0.04)]">
      <div className="flex items-baseline gap-2">
        <span className="font-serif text-[13.5px] text-ink">绘图稿</span>
        <Tag tone="accent">{draft.scene}</Tag>
        <Tag>{draft.kind}</Tag>
      </div>

      <label className="mt-3 block font-mono text-[11px] text-ink-faint">提示词(可编辑)</label>
      <textarea
        value={card.prompt}
        onChange={(e) => onEditPrompt(card.key, e.target.value)}
        rows={5}
        className="mt-1.5 w-full resize-y rounded-lg border border-line-strong bg-paper px-3 py-2 text-[12.5px] leading-relaxed text-ink focus:border-accent focus:outline-none"
      />

      {draft.refs.length > 0 && (
        <div className="mt-3">
          <div className="font-mono text-[11px] text-ink-faint">参考图(语义名)</div>
          <div className="mt-2 flex flex-wrap gap-2.5">
            {draft.refs.map((r, i) => (
              <figure key={i} className="w-[78px]" title={r.purpose}>
                {r.preview_path ? (
                  <img
                    src={imgUrl(r.preview_path)}
                    alt={r.semantic_name}
                    className="h-[52px] w-[78px] rounded-md border border-line object-cover"
                  />
                ) : (
                  <div className="flex h-[52px] w-[78px] items-center justify-center rounded-md border border-dashed border-line-strong text-[10px] text-ink-faint">
                    无图
                  </div>
                )}
                <figcaption className="mt-1 truncate text-[10.5px] text-ink-soft">
                  {r.semantic_name}
                </figcaption>
              </figure>
            ))}
          </div>
        </div>
      )}

      <div className="mt-3.5 flex flex-wrap items-center gap-2">
        <Button variant="primary" onClick={() => onConfirm(card.key)}>
          确认出图
        </Button>
        <Button variant="ghost" onClick={() => onReuse(card.key)}>
          复用已有图
        </Button>
        <Button variant="quiet" onClick={() => onSkip(card.key)}>
          跳过
        </Button>
        <span className="ml-auto font-mono text-[10px] text-ink-faint">确认即调用 gpt-image-2(花钱)</span>
      </div>
    </div>
  );
}
