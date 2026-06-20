import { Handle, Position, type NodeProps } from "@xyflow/react";
import type { SettingsSection } from "../types";

export interface DataSourceNodeData {
  glyph: string;
  title: string;
  subtitle: string; // 喂给谁 / 是什么
  buttons: { label: string; section: SettingsSection }[]; // 直达对应设置分区
  onOpen: (section: SettingsSection) => void;
  [k: string]: unknown;
}

/** 「数据源」节点(导演工作台):知识库 / 文风圣经 / 画风圣经和图库。不是 agent —— 无运行状态,
 *  代表喂给各 agent 的恒定底座。节点上有按钮,点击直达故事内设置的对应分区。
 *
 *  与 agent 节点视觉区分:暖灰描边 + 「数据源」角标 + 虚线下沿(数据流向)。整节点可拖动,
 *  按钮标 `nodrag` 且 stopPropagation —— 点按钮只开设置、不拖动也不选中。
 *  源句柄置于底沿:边由此向下汇入对应 agent 的左侧目标句柄(React Flow 默认边需要句柄才渲染)。 */
export function DataSourceNode({ data }: NodeProps) {
  const d = data as DataSourceNodeData;
  return (
    <div className="flex w-[176px] flex-col rounded-xl border border-dashed border-line-strong bg-sunken/60 text-left shadow-[0_1px_2px_rgba(28,37,48,0.04)]">
      <Handle type="source" position={Position.Bottom} className="!h-1.5 !w-1.5 !border-0 !bg-line-strong" />

      <div className="flex items-center gap-2 px-3.5 pt-3">
        <span className="flex h-6 w-6 shrink-0 items-center justify-center rounded-md bg-paper font-mono text-[12px] text-ink-soft">
          {d.glyph}
        </span>
        <span className="font-serif text-[14px] text-ink">{d.title}</span>
        <span className="ml-auto rounded-[5px] bg-paper px-1.5 py-px font-mono text-[9.5px] text-ink-faint">
          数据源
        </span>
      </div>

      <p className="line-clamp-2 px-3.5 pt-1.5 text-[11px] leading-snug text-ink-soft">{d.subtitle}</p>

      <div className="flex flex-wrap gap-1.5 px-3.5 pb-3 pt-2.5">
        {d.buttons.map((b) => (
          <button
            key={b.section}
            type="button"
            onClick={(e) => {
              e.stopPropagation();
              d.onOpen(b.section);
            }}
            className="nodrag rounded-lg border border-line-strong bg-paper px-2.5 py-1 font-mono text-[11px] text-ink-soft transition hover:border-accent hover:bg-accent-soft hover:text-accent-ink"
          >
            ⚙ {b.label}
          </button>
        ))}
      </div>
    </div>
  );
}
