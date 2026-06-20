import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import {
  Background,
  BackgroundVariant,
  ReactFlow,
  useNodesState,
  type Edge,
  type Node,
} from "@xyflow/react";
import "@xyflow/react/dist/style.css";
import type { AgentStep, DrawItem, SettingsSection, StepStatus } from "../types";
import { drawNodeStatuses } from "../drawNodeState";
import { AgentNode, type AgentNodeData } from "./AgentNode";
import { DataSourceNode, type DataSourceNodeData } from "./DataSourceNode";
import { clearPositions, loadPositions, savePosition } from "../nodeLayout";

const nodeTypes = { agent: AgentNode, datasource: DataSourceNode };
const DRAG_HANDLE = ".node-drag-handle"; // 与 AgentNode 把手类名对应:只有抓把手才移动节点

// 「数据源」节点:喂给各 agent 的恒定底座。id 用 ds: 前缀(工作台不当 agent 步处理)。
// targets = 该数据源喂给的 agent 节点 id;按钮 → 故事内设置对应分区。draws 列的写稿节点动态拼接。
const DATA_SOURCES: {
  id: string;
  glyph: string;
  title: string;
  subtitle: string;
  x: number;
  buttons: { label: string; section: SettingsSection }[];
  staticTargets: AgentStep[];
  feedsDraws?: boolean; // 是否连向本轮所有写稿节点(画风/图库喂绘图写稿)
}[] = [
  {
    id: "ds:knowledge", glyph: "📖", title: "知识库", subtitle: "设定圣经 · 只注入导演 A", x: 40,
    buttons: [{ label: "知识库", section: "knowledge" }], staticTargets: ["director_a"],
  },
  {
    id: "ds:style", glyph: "✒", title: "文风圣经", subtitle: "叙事文风 · 注入 A/写手/B/选项", x: 320,
    buttons: [{ label: "文风圣经", section: "style" }],
    staticTargets: ["director_a", "writer", "director_b", "options"],
  },
  {
    id: "ds:visual", glyph: "🎨", title: "画风圣经和图库", subtitle: "绘图风格与参考图 · 注入绘图写稿", x: 1018,
    buttons: [{ label: "画风圣经", section: "visual" }, { label: "图库", section: "gallery" }],
    staticTargets: [], feedsDraws: true,
  },
];
const DS_Y = -150; // 数据源行置于 agent 行(y=70)上方

interface Props {
  storyId: string;
  turn: number; // 当前显微镜聚焦的轮 —— 手动布局按 (storyId, turn) 分桶
  stages: Record<AgentStep, StepStatus>;
  draws: DrawItem[];
  writingIds: number[]; // 正在写稿/重写的 proposal_id → 写稿节点亮"运行中"
  generatingIds: number[]; // 正在出图的 proposal_id → 绘图节点亮"运行中"(按 proposal_id 索引,跨轮不串)
  selectedId: string | null;
  onSelectNode: (id: string) => void;
  onOpenSettings: (section: SettingsSection) => void;
}

const MAIN: { step: AgentStep; glyph: string; title: string; subtitle: string; x: number }[] = [
  { step: "director_a", glyph: "A", title: "导演 A", subtitle: "读黑板与设定,给写手一份引导 brief", x: 40 },
  { step: "writer", glyph: "W", title: "写手", subtitle: "据 brief 流式写出本回合叙事", x: 282 },
  { step: "director_b", glyph: "B", title: "导演 B", subtitle: "据成稿全量改写黑板(唯一状态权威)", x: 524 },
  { step: "reducer", glyph: "⤓", title: "落盘", subtitle: "纯逻辑落盘,盖诞生点写入 Turn", x: 766 },
];

interface BuildArgs {
  stages: Record<AgentStep, StepStatus>;
  draws: DrawItem[];
  writingIds: number[];
  generatingIds: number[];
  selectedId: string | null;
  onSelectNode: (id: string) => void;
  onOpenSettings: (section: SettingsSection) => void;
}

/** 自动布局:每个节点的默认坐标 + 实时 data。手动拖动的坐标在外层覆盖到这之上。 */
function buildNodes(a: BuildArgs): Node[] {
  const ns: Node[] = MAIN.map((d) => ({
    id: d.step,
    type: "agent",
    position: { x: d.x, y: 70 },
    draggable: true,
    dragHandle: DRAG_HANDLE,
    data: {
      glyph: d.glyph,
      title: d.title,
      subtitle: d.subtitle,
      status: a.stages[d.step],
      selected: a.selectedId === d.step,
      variant: "main",
      onSelect: () => a.onSelectNode(d.step),
    } satisfies AgentNodeData,
  }));

  // Options:Writer 后与 Director-B 并行的叶子,放在 B 正下方,不连 reducer。
  ns.push({
    id: "options",
    type: "agent",
    position: { x: 524, y: 210 },
    draggable: true,
    dragHandle: DRAG_HANDLE,
    data: {
      glyph: "⌥",
      title: "选项",
      subtitle: "据成稿出 1–3 个下一步可选项",
      status: a.stages.options,
      selected: a.selectedId === "options",
      variant: "main",
      onSelect: () => a.onSelectNode("options"),
    } satisfies AgentNodeData,
  });

  a.draws.forEach((it, i) => {
    const y = 18 + i * 132;
    const { draft: draftStatus, img: imgStatus } = drawNodeStatuses(it, a.writingIds, a.generatingIds);
    ns.push({
      id: `draw:${i}:prompt`,
      type: "agent",
      position: { x: 1018, y },
      draggable: true,
      dragHandle: DRAG_HANDLE,
      data: {
        glyph: "✎",
        title: "写稿",
        subtitle: "绘图 Agent 写提示词",
        status: draftStatus,
        selected: a.selectedId === `draw:${i}:prompt`,
        variant: "draw",
        badge: it.scene_slug,
        onSelect: () => a.onSelectNode(`draw:${i}:prompt`),
      } satisfies AgentNodeData,
    });
    ns.push({
      id: `draw:${i}:image`,
      type: "agent",
      position: { x: 1216, y },
      draggable: true,
      dragHandle: DRAG_HANDLE,
      data: {
        glyph: "❖",
        title: "绘图",
        subtitle: "gpt-image-2 出图",
        status: imgStatus,
        selected: a.selectedId === `draw:${i}:image`,
        variant: "draw",
        badge: it.kind,
        onSelect: () => a.onSelectNode(`draw:${i}:image`),
      } satisfies AgentNodeData,
    });
  });

  // 数据源节点(知识库 / 文风圣经 / 画风圣经和图库):整节点可拖(不设 dragHandle,按钮 nodrag)。
  DATA_SOURCES.forEach((ds) => {
    ns.push({
      id: ds.id,
      type: "datasource",
      position: { x: ds.x, y: DS_Y },
      draggable: true,
      data: {
        glyph: ds.glyph,
        title: ds.title,
        subtitle: ds.subtitle,
        buttons: ds.buttons,
        onOpen: a.onOpenSettings,
      } satisfies DataSourceNodeData,
    });
  });
  return ns;
}

export function AgentFlow({
  storyId,
  turn,
  stages,
  draws,
  writingIds,
  generatingIds,
  selectedId,
  onSelectNode,
  onOpenSettings,
}: Props) {
  const args: BuildArgs = { stages, draws, writingIds, generatingIds, selectedId, onSelectNode, onOpenSettings };
  const scope = `${storyId}.${turn}`; // 手动布局按 (故事, 轮) 分桶
  // 自动布局(默认坐标 + 实时 data),手动坐标在 effect 中覆盖到其上。
  const computed = useMemo(
    () => buildNodes(args),
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [stages, draws, writingIds, generatingIds, selectedId, onSelectNode, onOpenSettings],
  );

  // 初始种子:默认坐标叠加本地存的手动坐标(只算一次)→ fitView 首帧就能正确框住,无跳动。
  const seed = useRef<Node[] | null>(null);
  if (seed.current === null) {
    const saved = loadPositions(scope);
    seed.current = buildNodes(args).map((n) => ({ ...n, position: saved[n.id] ?? n.position }));
  }
  const [nodes, setNodes, onNodesChange] = useNodesState(seed.current);

  const seededKey = useRef<string>("");
  const [hasOverrides, setHasOverrides] = useState(false);

  // 同步:data/选中变化 → 只更新 data 保留当前(可能已拖动的)坐标;换故事/换轮 → 重新按本桶坐标落位。
  useEffect(() => {
    const reseed = seededKey.current !== scope;
    const saved = loadPositions(scope);
    setNodes((prev) => {
      const prevPos = new Map(prev.map((n) => [n.id, n.position]));
      return computed.map((n) => {
        // 同轮内:沿用上一帧坐标(含刚拖到的位置);换轮/新节点:用本桶存档,无则默认自动布局。
        const pos = (!reseed ? prevPos.get(n.id) : undefined) ?? saved[n.id] ?? n.position;
        return { ...n, position: pos };
      });
    });
    if (reseed) {
      seededKey.current = scope;
      setHasOverrides(Object.keys(saved).length > 0);
    }
  }, [computed, scope, setNodes]);

  // 拖完落盘:写本地 + 标记本轮有手动布局(亮出"重置布局")。
  const onNodeDragStop = useCallback(
    (_: unknown, node: Node) => {
      savePosition(scope, node.id, node.position);
      setHasOverrides(true);
    },
    [scope],
  );

  // 重置:清掉本 (故事, 轮) 的手动坐标,各节点回到自动布局默认位。
  const resetLayout = useCallback(() => {
    clearPositions(scope);
    const def = new Map(computed.map((n) => [n.id, n.position]));
    setNodes((prev) => prev.map((n) => ({ ...n, position: def.get(n.id) ?? n.position })));
    setHasOverrides(false);
  }, [scope, computed, setNodes]);

  const edges: Edge[] = useMemo(() => {
    const e: Edge[] = [];
    const seq: AgentStep[] = ["director_a", "writer", "director_b", "reducer"];
    seq.slice(0, -1).forEach((s, i) => {
      const target = seq[i + 1];
      const active = stages[target] === "running";
      e.push({ id: `${s}-${target}`, source: s, target, animated: active, style: edgeStyle(active) });
    });
    // Writer → Options(与 Writer → Director-B 并列的分叉;Options 不连 reducer)
    {
      const active = stages.options === "running";
      e.push({ id: "writer-options", source: "writer", target: "options", animated: active, style: edgeStyle(active) });
    }
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
    // 数据源 → 对应 agent(恒定底座的喂入,非控制流):浅虚线、不动画,与流程边区分。
    DATA_SOURCES.forEach((ds) => {
      const targets = [...ds.staticTargets];
      if (ds.feedsDraws) draws.forEach((_, i) => targets.push(`draw:${i}:prompt` as AgentStep));
      targets.forEach((target) => {
        e.push({ id: `${ds.id}->${target}`, source: ds.id, target, animated: false, style: feedEdgeStyle });
      });
    });
    return e;
  }, [stages, draws, writingIds, generatingIds]);

  return (
    <div className="relative h-full w-full">
      <ReactFlow
        nodes={nodes}
        edges={edges}
        nodeTypes={nodeTypes}
        onNodesChange={onNodesChange}
        onNodeDragStop={onNodeDragStop}
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
      {hasOverrides && (
        <button
          type="button"
          onClick={resetLayout}
          title="清掉本轮拖动过的节点位置,回到自动布局"
          className="absolute bottom-3 left-3 z-10 rounded-full border border-line-strong bg-surface/95 px-3 py-1 font-mono text-[10.5px] text-ink-soft shadow-sm transition hover:bg-sunken"
        >
          ↺ 重置布局
        </button>
      )}
    </div>
  );
}

const edgeStyle = (active: boolean) => ({
  stroke: active ? "var(--color-accent)" : "var(--color-line-strong)",
  strokeWidth: 1.5,
});

// 数据源喂入边:浅色虚线,与控制流边区分(数据底座,非流程)。
const feedEdgeStyle = {
  stroke: "var(--color-line-strong)",
  strokeWidth: 1.2,
  strokeDasharray: "4 4",
  opacity: 0.6,
};
