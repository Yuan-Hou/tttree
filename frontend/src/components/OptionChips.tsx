interface Props {
  options: string[];
  disabled: boolean; // 落盘未完成(turnStreaming)时禁用 —— 呼应「落盘前禁止新输入」
  onPick: (text: string) => void;
}

/** 输入框上方的「下一步可选项」(本轮 Options agent 给的建议)。点一条 → 预填到输入框,
 *  用户可再编辑后手动发送;不选也可直接忽略、自己输入。无选项时不渲染。 */
export function OptionChips({ options, disabled, onPick }: Props) {
  if (!options.length) return null;
  return (
    <div className="border-t border-line bg-surface px-10 pt-3">
      <div className="mx-auto flex w-full max-w-[640px] flex-wrap items-center gap-2">
        <span className="font-mono text-[10.5px] uppercase tracking-[0.14em] text-ink-faint">下一步</span>
        {options.map((opt, i) => (
          <button
            key={i}
            disabled={disabled}
            onClick={() => onPick(opt)}
            title="填入输入框,可再编辑后发送"
            className="rounded-full border border-line-strong bg-paper px-3 py-1 text-[12.5px] text-ink-soft transition hover:border-accent hover:text-accent-ink disabled:cursor-not-allowed disabled:opacity-40"
          >
            {opt}
          </button>
        ))}
      </div>
    </div>
  );
}
