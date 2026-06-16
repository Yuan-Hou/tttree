import { useMemo } from "react";
import { Background, BackgroundVariant, ReactFlow, type Edge, type Node } from "@xyflow/react";
import "@xyflow/react/dist/style.css";
import type { AgentStep, DrawItem, StepStatus } from "../types";
import { drawNodeStatuses } from "../drawNodeState";
import { AgentNode, type AgentNodeData } from "./AgentNode";

const nodeTypes = { agent: AgentNode };

interface Props {
  stages: Record<AgentStep, StepStatus>;
  draws: DrawItem[];
  writingIds: number[]; // 正在写稿/重写的 proposal_id → 写稿节点亮"运行中"
  generatingIds: number[]; // 正在出图的 proposal_id → 绘图节点亮"运行中"(按 proposal_id 索引,跨轮不串)
  selectedId: string | null;
  onSelectNode: (id: string) => void;
}

const MAIN: { step: AgentStep; glyph: string; title: string; subtitle: string; x: number }[] = [
  { step: "director_a", glyph: "A", title: "导演 A", subtitle: "读黑板与设定,给写手一份引导 brief", x: 40 },
  { step: "writer", glyph: "W", title: "写手", subtitle: "据 brief 流式写出本回合叙事", x: 282 },
  { step: "director_b", glyph: "B", title: "导演 B", subtitle: "据成稿全量改写黑板(唯一状态权威)", x: 524 },
  { step: "reducer", glyph: "⤓", title: "落盘", subtitle: "纯逻辑落盘,盖诞生点写入 Turn", x: 766 },
];

export function AgentFlow({ stages, draws, writingIds, generatingIds, selectedId, onSelectNode }: Props) {
  const nodes: Node[] = useMemo(() => {
    const ns: Node[] = MAIN.map((d) => ({
      id: d.step,
      type: "agent",
      position: { x: d.x, y: 70 },
      draggable: false,
      data: {
        glyph: d.glyph,
        title: d.title,
        subtitle: d.subtitle,
        status: stages[d.step],
        selected: selectedId === d.step,
        variant: "main",
        onSelect: () => onSelectNode(d.step),
      } satisfies AgentNodeData,
    }));

    draws.forEach((it, i) => {
      const y = 18 + i * 132;
      const { draft: draftStatus, img: imgStatus } = drawNodeStatuses(it, writingIds, generatingIds);
      ns.push({
        id: `draw:${i}:prompt`,
        type: "agent",
        position: { x: 1018, y },
        draggable: false,
        data: {
          glyph: "✎",
          title: "写稿",
          subtitle: "绘图 Agent 写提示词",
          status: draftStatus,
          selected: selectedId === `draw:${i}:prompt`,
          variant: "draw",
          badge: it.scene_slug,
          onSelect: () => onSelectNode(`draw:${i}:prompt`),
        } satisfies AgentNodeData,
      });
      ns.push({
        id: `draw:${i}:image`,
        type: "agent",
        position: { x: 1216, y },
        draggable: false,
        data: {
          glyph: "❖",
          title: "绘图",
          subtitle: "gpt-image-2 出图",
          status: imgStatus,
          selected: selectedId === `draw:${i}:image`,
          variant: "draw",
          badge: it.kind,
          onSelect: () => onSelectNode(`draw:${i}:image`),
        } satisfies AgentNodeData,
      });
    });
    return ns;
  }, [stages, draws, writingIds, generatingIds, selectedId, onSelectNode]);

  const edges: Edge[] = useMemo(() => {
    const e: Edge[] = [];
    const seq: AgentStep[] = ["director_a", "writer", "director_b", "reducer"];
    seq.slice(0, -1).forEach((s, i) => {
      const target = seq[i + 1];
      const active = stages[target] === "running";
      e.push({ id: `${s}-${target}`, source: s, target, animated: active, style: edgeStyle(active) });
    });
    draws.forEach((it, i) => {
      const st = drawNodeStatuses(it, writingIds, generatingIds);
      e.push({
        id: `reducer-d${i}`,
        source: "reducer",
        target: `draw:${i}:prompt`,
        animated: st.draft === "running",
        style: edgeStyle(st.draft === "running"),
      });
      e.push({
        id: `d${i}-img`,
        source: `draw:${i}:prompt`,
        target: `draw:${i}:image`,
        animated: st.img === "running",
        style: edgeStyle(st.img === "running"),
      });
    });
    return e;
  }, [stages, draws, writingIds, generatingIds]);

  return (
    <ReactFlow
      nodes={nodes}
      edges={edges}
      nodeTypes={nodeTypes}
      fitView
      fitViewOptions={{ padding: 0.2, maxZoom: 1 }}
      minZoom={0.4}
      maxZoom={1.4}
      nodesConnectable={false}
      onNodeClick={(_, node) => onSelectNode(node.id)}
      className="bg-paper"
    >
      <Background variant={BackgroundVariant.Dots} gap={22} size={1} color="#dfe4ea" />
    </ReactFlow>
  );
}

const edgeStyle = (active: boolean) => ({
  stroke: active ? "var(--color-accent)" : "var(--color-line-strong)",
  strokeWidth: 1.5,
});
