import { Handle, Position, type NodeProps } from "@xyflow/react";
import type { StepStatus } from "../types";

export interface AgentNodeData {
  glyph: string;
  title: string;
  subtitle: string;
  status: StepStatus;
  selected: boolean;
  variant: "main" | "draw";
  badge?: string; // 绘图节点:场景名 / kind
  onSelect: () => void;
  [k: string]: unknown;
}

const STATUS_LABEL: Record<StepStatus, string> = {
  pending: "待运行",
  running: "运行中",
  done: "已完成",
  error: "出错",
};

/** 克制有性格的 agent 节点 —— 不是 React Flow 默认方块。点击打开编辑区(重试在编辑区里,不在节点上)。
 *  状态视觉:待运行=虚边灰静,运行中=墨绿描边+呼吸点,已完成=实心墨绿点。
 *
 *  拖动:标题栏下方有一道横向把手(`.node-drag-handle`,React Flow 的 dragHandle 机制)。只有抓把手才能
 *  移动节点;标题/正文一律 `nodrag` —— 点它们只选中、不拖动。把手必须不落在任何 `nodrag` 祖先之下,
 *  否则会被 React Flow 的 nodrag 过滤器一并拦掉。 */
export function AgentNode({ data }: NodeProps) {
  const d = data as AgentNodeData;
  const draw = d.variant === "draw";

  const border =
    d.status === "error"
      ? "border-danger"
      : d.status === "running"
        ? "border-accent"
        : d.status === "done"
          ? "border-line-strong"
          : "border-dashed border-line";
  const ring = d.selected ? "ring-2 ring-accent ring-offset-2 ring-offset-paper" : "";

  return (
    <div
      className={`group flex flex-col rounded-xl border bg-surface text-left shadow-[0_1px_2px_rgba(28,37,48,0.04)] transition-colors ${border} ${ring} ${
        draw ? "w-[158px]" : "w-[184px]"
      }`}
    >
      <Handle type="target" position={Position.Left} className="!h-1.5 !w-1.5 !border-0 !bg-line-strong" />
      <Handle type="source" position={Position.Right} className="!h-1.5 !w-1.5 !border-0 !bg-line-strong" />

      {/* 标题栏(可点击选中,不拖动) */}
      <button type="button" onClick={d.onSelect} className="nodrag flex w-full items-center gap-2 px-3.5 pt-3 text-left">
        <span
          className={`flex h-6 w-6 shrink-0 items-center justify-center rounded-md font-mono text-[12px] ${
            d.status === "error"
              ? "bg-danger-soft text-danger"
              : d.status === "pending"
                ? "bg-sunken text-ink-faint"
                : "bg-accent-soft text-accent-ink"
          }`}
        >
          {d.status === "error" ? "⚠" : d.glyph}
        </span>
        <span className={`font-serif ${draw ? "text-[13px]" : "text-[14.5px]"} text-ink`}>{d.title}</span>
        <StatusDot status={d.status} />
      </button>

      {/* 拖拽把手:标题栏下方一道横向抓手(两排点的经典抓取纹理);只有这里能移动节点 */}
      <div
        className="node-drag-handle mx-3 mt-2 flex h-5 cursor-grab flex-col items-center justify-center gap-[3px] rounded-md border border-line bg-sunken transition-colors hover:border-line-strong hover:bg-line/40 active:cursor-grabbing"
        title="拖动此处移动节点"
      >
        {[0, 1].map((row) => (
          <div key={row} className="flex gap-[5px]">
            {[0, 1, 2, 3].map((i) => (
              <span key={i} className="h-[2.5px] w-[2.5px] rounded-full bg-ink-faint" />
            ))}
          </div>
        ))}
      </div>

      {/* 正文(可点击选中,不拖动) */}
      <button type="button" onClick={d.onSelect} className="nodrag flex w-full flex-col px-3.5 pb-3 pt-1.5 text-left">
        <p className="line-clamp-2 text-[11.5px] leading-snug text-ink-soft">{d.subtitle}</p>
        <div className="mt-2 flex w-full items-center gap-2">
          <span
            className={`font-mono text-[10px] ${
              d.status === "error" ? "text-danger" : d.status === "running" ? "text-accent-ink" : "text-ink-faint"
            }`}
          >
            {STATUS_LABEL[d.status]}
          </span>
          {d.badge && (
            <span className="ml-auto truncate rounded-[5px] bg-sunken px-1.5 py-px font-mono text-[10px] text-ink-soft">
              {d.badge}
            </span>
          )}
        </div>
      </button>
    </div>
  );
}

function StatusDot({ status }: { status: StepStatus }) {
  if (status === "error") return <span className="ml-auto h-2.5 w-2.5 rounded-full bg-danger ring-4 ring-danger-soft" />;
  if (status === "running")
    return <span className="breathe ml-auto h-2.5 w-2.5 rounded-full bg-accent ring-4 ring-accent-soft" />;
  if (status === "done") return <span className="ml-auto h-2.5 w-2.5 rounded-full bg-accent" />;
  return <span className="ml-auto h-2.5 w-2.5 rounded-full border border-line-strong bg-surface" />;
}
