import type { PickedRef } from "../types";
import type { DraftCard as Card } from "../useStoryEngine";
import { DraftCard } from "./DraftCard";
import { Eyebrow } from "./ui";

interface Props {
  drafts: Card[];
  onGenerate: (key: string) => void;
  onEditInstruction: (key: string, text: string) => void;
  onEditPrompt: (key: string, prompt: string) => void;
  onSetRefs: (key: string, picked: PickedRef[]) => void;
  onConfirm: (key: string) => void;
  onReuse: (key: string) => void;
  onSkip: (key: string) => void;
  onSubstitute: (key: string, pick: { imagegenId?: number; file?: File }) => Promise<void>;
  onDismiss: (key: string) => void;
}

/** 手动绘图 · 私人草稿:用户从「画这个场景」手动发起的绘图稿,与「绘图台·按场景」(导演 B 提案
 *  的正典待办)分开。出图后是「非正式」草稿——不进故事正典、对 AI 隐身,仅你可见可用。 */
export function ManualDeck(p: Props) {
  if (p.drafts.length === 0) return null; // 没有手动稿时不占位

  return (
    <section className="border-b border-line px-6 py-5">
      <Eyebrow>手动绘图 · 私人草稿</Eyebrow>
      <p className="mt-2 text-[12px] leading-relaxed text-ink-faint">
        你手动发起的绘图。出图为「非正式」草稿,不进故事正典、对 AI 隐身;仅你可见,可在参考图里手动引用。
      </p>
      <div className="mt-3 flex flex-col gap-3">
        {p.drafts.map((c) => (
          <DraftCard
            key={c.key}
            card={c}
            onGenerate={p.onGenerate}
            onEditInstruction={p.onEditInstruction}
            onEditPrompt={p.onEditPrompt}
            onSetRefs={p.onSetRefs}
            onConfirm={p.onConfirm}
            onReuse={p.onReuse}
            onSkip={p.onSkip}
            onSubstitute={p.onSubstitute}
            onDismiss={p.onDismiss}
          />
        ))}
      </div>
    </section>
  );
}
