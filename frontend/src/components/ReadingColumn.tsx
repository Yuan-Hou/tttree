import { useEffect, useRef } from "react";
import type { TurnView } from "../useStoryEngine";
import { Tag } from "./ui";

/** 阅读列 = 界面主角。开放的冷白空间,叙事在舒适的行宽里成块呼吸;
 *  左侧一根「树干」发丝线,每一拍挂一个生长节点 —— 故事正向下生长。 */
export function ReadingColumn({ turns }: { turns: TurnView[] }) {
  const endRef = useRef<HTMLDivElement>(null);
  const streamingText = turns.length ? turns[turns.length - 1] : null;

  useEffect(() => {
    endRef.current?.scrollIntoView({ block: "end", behavior: "smooth" });
  }, [turns.length, streamingText?.narrative]);

  return (
    <div className="min-h-0 flex-1 overflow-y-auto">
      <div className="mx-auto w-full max-w-[640px] px-10 pb-10 pt-8">
        {turns.length === 0 ? (
          <p className="pt-10 text-center font-serif text-[17px] text-ink-faint">
            写下第一个行动,故事自此生长。
          </p>
        ) : (
          <div className="relative">
            {/* 树干 */}
            <div className="absolute bottom-2 left-[5px] top-2 w-px bg-line" aria-hidden />
            <div className="flex flex-col">
              {turns.map((t, i) => (
                <TurnBlock key={t.key} turn={t} latest={i === turns.length - 1} />
              ))}
            </div>
          </div>
        )}
        <div ref={endRef} />
      </div>
    </div>
  );
}

function TurnBlock({ turn, latest }: { turn: TurnView; latest: boolean }) {
  return (
    <article className="relative pb-9 pl-7">
      {/* 生长节点 */}
      <span
        className={`absolute left-0 top-[5px] h-[11px] w-[11px] rounded-full ${
          latest ? "bg-accent ring-4 ring-accent-soft" : "border-[1.5px] border-line-strong bg-surface"
        }`}
        aria-hidden
      />
      {/* 行动眉:玩家做了什么 + 这一拍的小标题 */}
      <header className="mb-2.5 flex items-baseline gap-2">
        <span className="font-serif text-[14px] italic text-ink-soft">{turn.user_input}</span>
        {turn.beat_title && <Tag tone="accent">{turn.beat_title}</Tag>}
      </header>
      {/* 叙事正文:无衬线、偏大、行距舒适 */}
      <div
        className={`whitespace-pre-wrap font-sans text-[18px] leading-[1.85] tracking-[0.002em] text-ink ${
          turn.streaming ? "caret" : ""
        }`}
      >
        {turn.narrative}
      </div>
      {turn.error && (
        <p className="mt-3 rounded-lg bg-danger-soft px-3 py-2 text-[13px] text-danger">
          叙事中断:{turn.error}
        </p>
      )}
    </article>
  );
}
