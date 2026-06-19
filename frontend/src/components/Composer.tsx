import { useEffect, useState } from "react";

interface Props {
  disabled: boolean;
  streaming: boolean;
  onSubmit: (text: string) => void;
  prefillText?: string; // 点选项条 → 预填到输入框(用户可再编辑后发送)
  prefillKey?: number; // 每次点选项都自增 → 触发预填(即便文本相同也重填)
  onFocusChange?: (focused: boolean) => void; // 输入框激活态 → 控制选项条仅在聚焦时显示
  onRollback?: () => void; // 回退最新一轮(与导演工作台同效);回退后由上层把该轮输入预填回来
  onFork?: () => void; // 建副本(与导演工作台同效)
  canRollback?: boolean; // 有已落盘轮且空闲时才可回退
}

/** 行动输入。注意:仅在「文本线正在涌现」时禁用发送;
 *  出图(图片线)在后台进行时,这里始终可用 —— 这正是异步解耦的意义。 */
export function Composer({ disabled, streaming, onSubmit, prefillText, prefillKey, onFocusChange, onRollback, onFork, canRollback }: Props) {
  const [text, setText] = useState("");

  // 选项预填:用户点了输入框上方某条选项 → 填进来,焦点交给用户继续编辑/发送。
  useEffect(() => {
    if (prefillKey && prefillText) setText(prefillText);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [prefillKey]);
  const send = () => {
    const t = text.trim();
    if (!t || disabled || streaming) return;
    setText("");
    onSubmit(t);
  };

  return (
    <div className="border-t border-line bg-surface px-10 py-4">
      {(onRollback || onFork) && (
        <div className="mx-auto mb-2 flex w-full max-w-[640px] items-center gap-2">
          <button
            type="button"
            onClick={onRollback}
            disabled={!canRollback}
            title="回退最新一轮(与导演工作台一致);回退后把这一轮的输入填回输入框,便于改写重来"
            className="flex items-center gap-1 rounded-lg border border-line-strong bg-paper px-2.5 py-1 text-[12px] text-ink-soft transition hover:border-accent hover:text-accent-ink disabled:cursor-not-allowed disabled:opacity-40"
          >
            <span className="text-accent">↩</span> 回退
          </button>
          <button
            type="button"
            onClick={onFork}
            title="完整克隆当前故事到书架(与导演工作台一致),作主动后悔药"
            className="flex items-center gap-1 rounded-lg border border-line-strong bg-paper px-2.5 py-1 text-[12px] text-ink-soft transition hover:border-accent hover:text-accent-ink"
          >
            <span className="text-accent">⎘</span> 建副本
          </button>
        </div>
      )}
      <div className="mx-auto flex w-full max-w-[640px] items-end gap-2.5">
        <textarea
          value={text}
          onChange={(e) => setText(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === "Enter" && !e.shiftKey) {
              e.preventDefault();
              send();
            }
          }}
          onFocus={() => onFocusChange?.(true)}
          onBlur={() => onFocusChange?.(false)}
          rows={2}
          placeholder={streaming ? "叙事正在涌现…" : "写下你的行动,回车推进(Shift+Enter 换行)"}
          disabled={disabled}
          className="max-h-[60vh] min-h-[56px] flex-1 resize-y rounded-xl border border-line-strong bg-paper px-4 py-2.5 text-[14px] leading-relaxed text-ink placeholder:text-ink-faint focus:border-accent focus:outline-none disabled:opacity-50"
        />
        <button
          onClick={send}
          disabled={disabled || streaming || !text.trim()}
          className="h-[44px] shrink-0 rounded-xl bg-accent px-5 text-[13px] font-medium text-white transition-colors hover:bg-accent-ink disabled:cursor-not-allowed disabled:opacity-40 focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-accent"
        >
          {streaming ? "涌现中" : "推进"}
        </button>
      </div>
      <p className="mx-auto mt-2 w-full max-w-[640px] font-mono text-[10.5px] text-ink-faint">
        发起出图后,可立刻在此继续推进剧情 —— 插画在后台生成,好了再异步浮现。
      </p>
    </div>
  );
}
