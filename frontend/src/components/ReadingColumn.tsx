import { useEffect, useRef, useState } from "react";
import type { MouseEvent } from "react";
import type { TurnView } from "../useStoryEngine";
import { Tag } from "./ui";

/** 阅读列 = 界面主角。开放的冷白空间,叙事在舒适的行宽里成块呼吸;
 *  左侧一根「树干」发丝线,每一拍挂一个生长节点 —— 故事正向下生长。 */
export function ReadingColumn({
  turns,
  onTurnClick,
  onDismissFailure,
  onEditNarrative,
  editBusy,
}: {
  turns: TurnView[];
  onTurnClick?: (turnIndex: number) => void;
  onDismissFailure?: () => void;
  /** 提供则每条已落盘叙事可就地编辑(创作台);导出查看器不传 → 无编辑入口。 */
  onEditNarrative?: (turnIndex: number, narrative: string) => Promise<void>;
  /** 有回合/重试在跑时为真:暂时禁用编辑入口,避免与历史改写竞争。 */
  editBusy?: boolean;
}) {
  const endRef = useRef<HTMLDivElement>(null);
  const scrollRef = useRef<HTMLDivElement>(null);
  const lastActive = useRef<number | null>(null);
  const rafPending = useRef(false);
  const [showTop, setShowTop] = useState(false); // 滚下去一段后浮出「回顶部」
  const streamingText = turns.length ? turns[turns.length - 1] : null;

  useEffect(() => {
    endRef.current?.scrollIntoView({ block: "end", behavior: "smooth" });
  }, [turns.length, streamingText?.narrative]);

  const scrollToTop = () => scrollRef.current?.scrollTo({ top: 0, behavior: "smooth" });

  // 滚动联动:占据「页面中线」的那一轮即当前轮 —— 某轮分界线越过中线就切到对应节点聚焦。
  const onScroll = () => {
    const cont = scrollRef.current;
    if (cont) setShowTop(cont.scrollTop > 600); // 同值不触发重渲染,React 自会跳过
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
    <div className="relative min-h-0 flex-1">
      {showTop && (
        <button
          onClick={scrollToTop}
          title="回到对话流顶部"
          className="absolute left-1/2 top-3 z-10 flex -translate-x-1/2 items-center gap-1 rounded-full border border-line-strong bg-surface/95 px-3 py-1 text-[12px] text-ink-soft shadow-sm backdrop-blur transition hover:border-accent hover:text-accent-ink"
        >
          <span className="text-accent">↑</span> 回顶部
        </button>
      )}
      <div ref={scrollRef} onScroll={onScroll} className="h-full overflow-y-auto">
      <div className="mx-auto w-full max-w-[640px] px-6 pb-8 pt-6">
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
                <TurnBlock
                  key={t.key}
                  turn={t}
                  latest={i === turns.length - 1}
                  onClick={onTurnClick}
                  onDismissFailure={onDismissFailure}
                  onEditNarrative={onEditNarrative}
                  editBusy={editBusy}
                />
              ))}
            </div>
          </div>
        )}
        <div ref={endRef} />
      </div>
      </div>
    </div>
  );
}

function TurnBlock({
  turn,
  latest,
  onClick,
  onDismissFailure,
  onEditNarrative,
  editBusy,
}: {
  turn: TurnView;
  latest: boolean;
  onClick?: (turnIndex: number) => void;
  onDismissFailure?: () => void;
  onEditNarrative?: (turnIndex: number, narrative: string) => Promise<void>;
  editBusy?: boolean;
}) {
  const [editing, setEditing] = useState(false);
  const [draft, setDraft] = useState("");
  const [saving, setSaving] = useState(false);
  // 已落盘、非流式、非失败的叙事可编辑(需上层提供回调 → 导出查看器无回调即只读)。
  const canEdit =
    !!onEditNarrative && turn.turn_index != null && !turn.streaming && !turn.error;

  // 单击该轮 → 让地图聚焦对应场景节点。选中文字时不触发(避免劫持划词)。
  const handleClick = () => {
    if (editing) return; // 编辑态下不联动地图
    if (turn.turn_index == null || !onClick) return;
    if ((window.getSelection()?.toString() ?? "") !== "") return;
    onClick(turn.turn_index);
  };

  const startEdit = (e: MouseEvent) => {
    e.stopPropagation();
    setDraft(turn.narrative);
    setEditing(true);
  };
  const save = async () => {
    if (!onEditNarrative || turn.turn_index == null || saving) return;
    setSaving(true);
    try {
      await onEditNarrative(turn.turn_index, draft);
      setEditing(false);
    } finally {
      setSaving(false);
    }
  };

  return (
    <article
      id={turn.turn_index != null ? `turn-${turn.turn_index}` : undefined}
      onClick={handleClick}
      className={`group relative scroll-mt-6 pb-6 pl-7 ${onClick && !editing ? "cursor-pointer" : ""}`}
    >
      {/* 生长节点 */}
      <span
        className={`absolute left-0 top-[5px] h-[11px] w-[11px] rounded-full ${
          latest ? "bg-accent ring-4 ring-accent-soft" : "border-[1.5px] border-line-strong bg-surface"
        }`}
        aria-hidden
      />
      {/* 编辑入口:悬于左侧树干线上(节点下方),hover 浮出。导出查看器无回调即不渲染。 */}
      {canEdit && !editing && (
        <button
          onClick={editBusy ? undefined : startEdit}
          disabled={editBusy}
          title={editBusy ? "生成进行中,暂不可编辑" : "编辑这段成稿(只改文本与后续上下文,不重算黑板)"}
          aria-label="编辑成稿"
          className="absolute left-[-3px] top-7 z-[1] flex h-4 w-4 items-center justify-center rounded-full border border-line-strong bg-surface text-[9px] leading-none text-ink-soft opacity-0 shadow-sm transition hover:border-accent hover:text-accent-ink group-hover:opacity-100 disabled:cursor-not-allowed disabled:opacity-0"
        >
          ✎
        </button>
      )}
      {/* 行动眉:玩家做了什么 + 这一拍的小标题 */}
      <header className="mb-2 flex items-baseline gap-2">
        <span className="font-serif text-[13px] italic text-ink-soft">{turn.user_input}</span>
        {turn.beat_title && <Tag tone="accent">{turn.beat_title}</Tag>}
      </header>
      {/* 叙事正文:无衬线、偏大、行距舒适。编辑态 → 就地文本框。 */}
      {editing ? (
        <div onClick={(e) => e.stopPropagation()}>
          <textarea
            value={draft}
            onChange={(e) => setDraft(e.target.value)}
            autoFocus
            rows={Math.min(24, Math.max(6, draft.split("\n").length + 1))}
            className="w-full resize-y rounded-lg border border-line-strong bg-surface px-3 py-2 font-sans text-[15px] leading-[1.7] text-ink outline-none focus:border-accent"
          />
          <div className="mt-2 flex items-center gap-2">
            <button
              onClick={save}
              disabled={saving}
              className="rounded-md bg-accent px-3 py-1 text-[12px] text-paper transition hover:bg-accent-ink disabled:opacity-50"
            >
              {saving ? "保存中…" : "保存"}
            </button>
            <button
              onClick={() => setEditing(false)}
              disabled={saving}
              className="rounded-md border border-line-strong px-3 py-1 text-[12px] text-ink-soft transition hover:border-accent disabled:opacity-50"
            >
              取消
            </button>
            <span className="text-[11px] text-ink-faint">
              纯文本修改:改这段成稿与其后各轮的上下文,不重跑 agent、不重算黑板(世界状态)。
            </span>
          </div>
        </div>
      ) : (
        <div
          className={`whitespace-pre-wrap font-sans text-[15px] leading-[1.7] tracking-[0.002em] text-ink ${
            turn.streaming ? "caret" : ""
          }`}
        >
          {turn.narrative}
        </div>
      )}
      {turn.error && (
        <div className="mt-3 rounded-lg border border-danger/30 bg-danger-soft px-3 py-2.5 text-[13px] text-danger">
          <div className="flex items-center gap-2">
            <span className="font-medium">⚠ 本次提交失败 · 未计入故事</span>
            {onDismissFailure && (
              <button
                onClick={(e) => {
                  e.stopPropagation();
                  onDismissFailure();
                }}
                className="ml-auto rounded-md border border-danger/30 px-2 py-0.5 text-[11px] transition hover:bg-danger/10"
              >
                弃掉
              </button>
            )}
          </div>
          <div className="mt-1 whitespace-pre-wrap break-words font-mono text-[11px] leading-relaxed">{turn.error}</div>
          <div className="mt-1 text-[11px] text-danger/80">重试请重新输入并发送(下方输入框照常可用)。</div>
        </div>
      )}
    </article>
  );
}
