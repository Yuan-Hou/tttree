import { useEffect, useRef } from "react";
import type { TurnView } from "../useStoryEngine";
import { Tag } from "./ui";

/** 阅读列 = 界面主角。开放的冷白空间,叙事在舒适的行宽里成块呼吸;
 *  左侧一根「树干」发丝线,每一拍挂一个生长节点 —— 故事正向下生长。 */
export function ReadingColumn({
  turns,
  onTurnClick,
}: {
  turns: TurnView[];
  onTurnClick?: (turnIndex: number) => void;
}) {
  const endRef = useRef<HTMLDivElement>(null);
  const scrollRef = useRef<HTMLDivElement>(null);
  const lastActive = useRef<number | null>(null);
  const rafPending = useRef(false);
  const streamingText = turns.length ? turns[turns.length - 1] : null;

  useEffect(() => {
    endRef.current?.scrollIntoView({ block: "end", behavior: "smooth" });
  }, [turns.length, streamingText?.narrative]);

  // 滚动联动:占据「页面中线」的那一轮即当前轮 —— 某轮分界线越过中线就切到对应节点聚焦。
  const onScroll = () => {
    if (!onTurnClick || rafPending.current) return;
    rafPending.current = true;
    requestAnimationFrame(() => {
      rafPending.current = false;
      const cont = scrollRef.current;
      if (!cont) return;
      const cr = cont.getBoundingClientRect();
      const mid = cr.top + cr.height / 2;
      let active: number | null = turns[0]?.turn_index ?? null;
      for (const t of turns) {
        if (t.turn_index == null) continue;
        const el = document.getElementById(`turn-${t.turn_index}`);
        if (!el) continue;
        if (el.getBoundingClientRect().top <= mid) active = t.turn_index; // 顶部已过中线 → 候选
        else break; // 顺序排列,之后各轮顶部都在中线下方
      }
      if (active != null && active !== lastActive.current) {
        lastActive.current = active;
        onTurnClick(active);
      }
    });
  };

  return (
    <div ref={scrollRef} onScroll={onScroll} className="min-h-0 flex-1 overflow-y-auto">
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
                <TurnBlock key={t.key} turn={t} latest={i === turns.length - 1} onClick={onTurnClick} />
              ))}
            </div>
          </div>
        )}
        <div ref={endRef} />
      </div>
    </div>
  );
}

function TurnBlock({
  turn,
  latest,
  onClick,
}: {
  turn: TurnView;
  latest: boolean;
  onClick?: (turnIndex: number) => void;
}) {
  // 单击该轮 → 让地图聚焦对应场景节点。选中文字时不触发(避免劫持划词)。
  const handleClick = () => {
    if (turn.turn_index == null || !onClick) return;
    if ((window.getSelection()?.toString() ?? "") !== "") return;
    onClick(turn.turn_index);
  };
  return (
    <article
      id={turn.turn_index != null ? `turn-${turn.turn_index}` : undefined}
      onClick={handleClick}
      className={`relative scroll-mt-6 pb-9 pl-7 ${onClick ? "cursor-pointer" : ""}`}
    >
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
