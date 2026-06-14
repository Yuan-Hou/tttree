import { useState } from "react";

interface Props {
  disabled: boolean;
  streaming: boolean;
  onSubmit: (text: string) => void;
}

/** 行动输入。注意:仅在「文本线正在涌现」时禁用发送;
 *  出图(图片线)在后台进行时,这里始终可用 —— 这正是异步解耦的意义。 */
export function Composer({ disabled, streaming, onSubmit }: Props) {
  const [text, setText] = useState("");
  const send = () => {
    const t = text.trim();
    if (!t || disabled || streaming) return;
    setText("");
    onSubmit(t);
  };

  return (
    <div className="border-t border-line bg-surface px-10 py-4">
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
          rows={1}
          placeholder={streaming ? "叙事正在涌现…" : "写下你的行动,回车推进(Shift+Enter 换行)"}
          disabled={disabled}
          className="max-h-40 min-h-[44px] flex-1 resize-none rounded-xl border border-line-strong bg-paper px-4 py-2.5 text-[14px] leading-relaxed text-ink placeholder:text-ink-faint focus:border-accent focus:outline-none disabled:opacity-50"
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
