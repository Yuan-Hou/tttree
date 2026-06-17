import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import {
  Background,
  BackgroundVariant,
  Controls,
  MarkerType,
  ReactFlow,
  type Edge,
  type Node,
  type ReactFlowInstance,
} from "@xyflow/react";
import "@xyflow/react/dist/style.css";
import { getSceneMap } from "../api";
import type { SceneMap as SceneMapData } from "../types";
import { SceneNode, type SceneNodeData } from "./SceneNode";
import { SelfLoopEdge } from "./SelfLoopEdge";

const nodeTypes = { scene: SceneNode };
const edgeTypes = { selfloop: SelfLoopEdge };

const NODE_W = 208; // 与 SceneNode 卡片宽度一致
const ROW_H = 260; // 行距:留足竖直呼吸位 + 自环弧线不压上方节点
const X0 = 60;
const Y0 = 30;
const MIN_GAP = 64; // 无标签约束的相邻列之间的最小空白(节点间距)
const LABEL_PAD = 18; // 标签宽之外再留的安全余量(确保连线文字不贴/不压节点)

interface Props {
  storyId: string;
  onJumpToTurn: (turnIndex: number) => void; // 点实线 → 滚动对话到该轮
  refreshKey?: string | number; // 故事推进/新出图时变化 → 静默重取地图
  focusReq?: { turnIndex: number; nonce: number } | null; // 点对话 → 聚焦该轮落点节点 + 翻到对应图
}

/** 场景地图(常驻列,动态交互版)。只读 React Flow:节点=场景卡(变体翻页+当前高亮),
 *  实线=每轮转移(带轮次+beat 标签),虚线=空间相邻(纯装饰)。交互:
 *    - 悬停实线 → 该线高亮 + 终点节点高亮,其余实线降调;
 *    - 双击场景 → 弹 lightbox(在 SceneNode 内,取该卡当前变体页;无图 noop);
 *    - 点击实线 → 滚动对话到对应轮。
 *  refreshKey 变化时静默重取(不闪「载入中」、保留当前视野);仅切故事时才显示载入态。 */
export function SceneMap({ storyId, onJumpToTurn, refreshKey, focusReq }: Props) {
  const [data, setData] = useState<SceneMapData | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [hoverId, setHoverId] = useState<string | null>(null);
  const [focus, setFocus] = useState<{ slug: string; page: number; nonce: number } | null>(null);
  const loadedFor = useRef<string | null>(null);
  const rfRef = useRef<ReactFlowInstance | null>(null);
  const flowWrapRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    let alive = true;
    if (loadedFor.current !== storyId) {
      // 切故事:重置并显示载入态
      loadedFor.current = storyId;
      setData(null);
      setErr(null);
      setHoverId(null);
    }
    getSceneMap(storyId)
      .then((m) => alive && setData(m)) // 同故事内:直接替换,视野/缩放不丢
      .catch((e) => alive && setErr(String(e)));
    return () => {
      alive = false;
    };
  }, [storyId, refreshKey]);

  const base = useMemo(() => buildGraph(data), [data]);

  // 单击对话 → 聚焦该轮落点场景节点:放大到该节点 + 让卡片翻到该轮出的那张图。
  // 只随 nonce(每次点击)触发,不随地图刷新重跑(避免新出图时画布乱跳)。
  useEffect(() => {
    if (!focusReq || !data) return;
    const edge = data.solid_edges.find((e) => e.turn_index === focusReq.turnIndex);
    if (!edge) return;
    const slug = edge.to;
    const node = data.nodes.find((n) => n.slug === slug);
    let page = 0;
    if (edge.image_path && node) {
      const k = node.image_paths.indexOf(edge.image_path);
      if (k >= 0) page = k;
    }
    setFocus({ slug, page, nonce: focusReq.nonce });
    // 放大并居中到该节点。用 setCenter(显式 zoom)比 fitView 到单节点更稳;取节点实测尺寸算中心。
    const inst = rfRef.current;
    if (!inst) return;
    setTimeout(() => {
      const n = inst.getNode(slug);
      if (!n) {
        inst.fitView({ nodes: [{ id: slug }], maxZoom: 3, duration: 500, padding: 0.5 });
        return;
      }
      const w = n.measured?.width ?? 208;
      const h = n.measured?.height ?? 180;
      // 自适应缩放:让节点占地图视口约 70% 的限制维度,再夹到画布缩放区间 [1, 4]
      const box = flowWrapRef.current;
      const vw = box?.clientWidth ?? 600;
      const vh = box?.clientHeight ?? 600;
      const frac = 0.7;
      const zoom = Math.max(1, Math.min(4, Math.min((vw * frac) / w, (vh * frac) / h)));
      inst.setCenter(n.position.x + w / 2, n.position.y + h / 2, { zoom, duration: 500 });
    }, 40);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [focusReq]);

  // 悬停某实线 → 终点节点高亮
  const hoveredTarget = useMemo(() => {
    if (!hoverId) return null;
    const e = base.edges.find((x) => x.id === hoverId);
    return e ? e.target : null;
  }, [hoverId, base.edges]);

  const nodes = useMemo(
    () =>
      base.nodes.map((n) => {
        let d = n.data as SceneNodeData;
        if (hoveredTarget && n.id === hoveredTarget) d = { ...d, hl: true };
        if (focus && n.id === focus.slug) d = { ...d, focused: true, pageTo: focus.page, pageNonce: focus.nonce };
        return d === n.data ? n : { ...n, data: d };
      }),
    [base.nodes, hoveredTarget, focus],
  );
  const edges = useMemo(() => decorateEdges(base.edges, hoverId), [base.edges, hoverId]);

  const onEdgeClick = useCallback(
    (_: unknown, edge: Edge) => {
      const ti = (edge.data as { turnIndex?: number } | undefined)?.turnIndex;
      if (ti != null) onJumpToTurn(ti);
    },
    [onJumpToTurn],
  );

  return (
    <div className="flex min-h-0 flex-1 flex-col">
      <div className="flex items-center gap-3 border-b border-line bg-surface px-6 py-2 font-mono text-[10.5px] text-ink-faint">
        <span>实线=每轮转移(点击跳到该轮)</span>
        <span>· 虚线=空间相邻</span>
        <span>· 悬停高亮 · 双击场景看大图 · 点对话聚焦节点</span>
      </div>

      <div ref={flowWrapRef} className="min-h-0 flex-1">
        {err ? (
          <div className="flex h-full items-center justify-center text-[13px] text-danger">出错:{err}</div>
        ) : !data ? (
          <div className="flex h-full items-center justify-center text-[13px] text-ink-faint">载入中…</div>
        ) : data.nodes.length === 0 ? (
          <div className="flex h-full items-center justify-center text-[13px] text-ink-faint">
            这卷故事还没有场景。写下第一拍,地图就会生长。
          </div>
        ) : (
          <ReactFlow
            nodes={nodes}
            edges={edges}
            nodeTypes={nodeTypes}
            edgeTypes={edgeTypes}
            fitView
            fitViewOptions={{ padding: 0.22, maxZoom: 1 }}
            minZoom={0.25}
            maxZoom={4}
            nodesConnectable={false}
            nodesDraggable={false}
            elementsSelectable
            zoomOnDoubleClick={false}
            proOptions={{ hideAttribution: true }}
            onInit={(inst) => (rfRef.current = inst)}
            onEdgeClick={onEdgeClick}
            onEdgeMouseEnter={(_, edge) => {
              if ((edge.data as { turnIndex?: number } | undefined)?.turnIndex != null) setHoverId(edge.id);
            }}
            onEdgeMouseLeave={() => setHoverId(null)}
            className="bg-paper"
          >
            <Background variant={BackgroundVariant.Dots} gap={22} size={1} color="#dfe4ea" />
            <Controls showInteractive={false} className="!shadow-none" />
          </ReactFlow>
        )}
      </div>
    </div>
  );
}

/** 悬停某实线时:该实线高亮(加粗+墨绿+箭头变色),其余实线降调(opacity 降低);虚线不受影响。 */
function decorateEdges(edges: Edge[], hoverId: string | null): Edge[] {
  if (!hoverId) return edges;
  return edges.map((e) => {
    const isSolid = (e.data as { turnIndex?: number } | undefined)?.turnIndex != null;
    if (!isSolid) return e; // 虚线不参与高亮/降调
    if (e.id === hoverId) {
      return {
        ...e,
        style: { ...e.style, stroke: "var(--color-accent)", strokeWidth: 2.6, opacity: 1 },
        markerEnd: { type: MarkerType.ArrowClosed, color: "var(--color-accent)", width: 18, height: 18 },
        labelStyle: { ...(e.labelStyle as object), fill: "var(--color-accent-ink)" },
        zIndex: 10,
      };
    }
    return {
      ...e,
      style: { ...e.style, opacity: 0.16 },
      labelStyle: { ...(e.labelStyle as object), opacity: 0.25 },
    };
  });
}

/** 把后端三块装进 React Flow 节点/边。布局:按 origin_turn 分列的左→右时间线,起点在最左。
 *  实线端点引用了已不在 nodes 里的场景 → 合成 ghost 节点,不让边凭空丢失。 */
function buildGraph(data: SceneMapData | null): { nodes: Node[]; edges: Edge[] } {
  if (!data) return { nodes: [], edges: [] };

  const realSlugs = new Set(data.nodes.map((n) => n.slug));

  // ghost:实线端点里既非真实场景、又非起点哨兵的 slug(回退/改写后悬空)
  const ghosts: string[] = [];
  for (const e of data.solid_edges) {
    for (const s of [e.from, e.to]) {
      if (s !== data.start && !realSlugs.has(s) && !ghosts.includes(s)) ghosts.push(s);
    }
  }

  // ── 布局:origin_turn → 列(竖直居中 + 逐边界可变列距)──
  // 列由 origin_turn 定左→右时序。横向:列距只按「恰好跨这道边界那条转移的标签宽」给 —— 短标签的
  // 列自然靠近,不再被全局最长标签统一撑开。竖直:各列绕同一中线居中,单节点列正落在主时间线上,
  // 变体节点上下对称展开(不再都堆在顶沿、下方留大片空白)。
  const turnVals = Array.from(
    new Set(data.nodes.map((n) => n.origin_turn).filter((t): t is number => t != null)),
  ).sort((a, b) => a - b);
  const colOf = new Map<number, number>();
  turnVals.forEach((t, i) => colOf.set(t, i + 1)); // 第 0 列留给起点
  const nullCol = turnVals.length + 1; // origin_turn 缺失
  const ghostCol = turnVals.length + 2;

  // 先把每个节点分到 (列, 行):列内按 slug 稳定排序、依次往下堆。
  const colCount: Record<number, number> = {};
  const cell: Record<string, { col: number; row: number }> = {};
  const assign = (slug: string, col: number) => {
    const r = colCount[col] ?? 0;
    colCount[col] = r + 1;
    cell[slug] = { col, row: r };
  };
  assign(data.start, 0);
  [...data.nodes]
    .sort((a, b) => (a.origin_turn ?? 1e9) - (b.origin_turn ?? 1e9) || a.slug.localeCompare(b.slug))
    .forEach((n) => assign(n.slug, n.origin_turn != null ? (colOf.get(n.origin_turn) as number) : nullCol));
  ghosts.forEach((g) => assign(g, ghostCol));

  const colOfSlug = (slug: string) => cell[slug]?.col ?? ghostCol;
  // 标签像素宽估算(fontSize 10 混排 + 背景内边距);偏保守留余量,确保文字不被遮挡。
  const labelPx = (e: (typeof data.solid_edges)[number]) =>
    Math.ceil((e.beat ? `第${e.turn_index}拍 · ${e.beat}` : `第${e.turn_index}拍`).length * 10.5) + 16;

  // 逐边界列距:相邻两已用列之间,取「恰好跨这道边界」的转移的最长标签宽 + 余量;无则用最小列距。
  const usedCols = Array.from(new Set(Object.values(cell).map((c) => c.col))).sort((a, b) => a - b);
  const colX: Record<number, number> = {};
  usedCols.forEach((c, i) => {
    if (i === 0) {
      colX[c] = X0;
      return;
    }
    const prev = usedCols[i - 1];
    let lw = 0;
    for (const e of data.solid_edges) {
      const lo = Math.min(colOfSlug(e.from), colOfSlug(e.to));
      const hi = Math.max(colOfSlug(e.from), colOfSlug(e.to));
      if (lo === prev && hi === c) lw = Math.max(lw, labelPx(e)); // 自环 lo==hi 不约束横向
    }
    const gap = Math.max(MIN_GAP, lw > 0 ? lw + LABEL_PAD : 0);
    colX[c] = colX[prev] + NODE_W + gap;
  });

  // 竖直居中:各列绕同一中线对称展开(单节点列正落在中线 = 主时间线)。
  const maxRows = Math.max(1, ...Object.values(colCount));
  const yMid = Y0 + ((maxRows - 1) / 2) * ROW_H;
  const pos: Record<string, { x: number; y: number }> = {};
  for (const [slug, { col, row }] of Object.entries(cell)) {
    const k = colCount[col];
    pos[slug] = { x: colX[col], y: yMid + (row - (k - 1) / 2) * ROW_H };
  }
  const startPos = pos[data.start];

  const nodes: Node[] = [
    {
      id: data.start,
      type: "scene",
      position: startPos,
      draggable: false,
      data: { variant: "start", name: "起点" } satisfies SceneNodeData,
    },
    ...data.nodes.map((n) => ({
      id: n.slug,
      type: "scene",
      position: pos[n.slug],
      draggable: false,
      data: {
        variant: "scene",
        name: n.name,
        slug: n.slug,
        originTurn: n.origin_turn,
        images: n.image_paths,
        gallery: n.gallery ?? [],
        current: n.slug === data.current_scene,
      } satisfies SceneNodeData,
    })),
    ...ghosts.map((g) => ({
      id: g,
      type: "scene",
      position: pos[g],
      draggable: false,
      data: { variant: "ghost", name: g } satisfies SceneNodeData,
    })),
  ];

  // ── 边 ──
  // 自环(起点=终点:这一轮停留在同一场景)走自定义 selfloop 边,画成节点顶上的弧线 + 回落箭头,
  // 而非默认边那条横穿画布的直线。自定义边不自动渲染内置 label,故标签文本走 label prop、由组件自绘。
  const solid: Edge[] = data.solid_edges.map((e, i) => {
    const labelText = e.beat ? `第${e.turn_index}拍 · ${e.beat}` : `第${e.turn_index}拍`;
    if (e.from === e.to) {
      return {
        id: `s${i}`,
        source: e.from,
        target: e.to,
        type: "selfloop",
        data: { turnIndex: e.turn_index }, // 实线判别 + 点击跳转目标(悬停/点击沿用)
        label: labelText,
        markerEnd: { type: MarkerType.ArrowClosed, color: "#9aa4b0", width: 16, height: 16 },
        style: { stroke: "var(--color-line-strong)", strokeWidth: 1.4, cursor: "pointer" },
      };
    }
    return {
      id: `s${i}`,
      source: e.from,
      target: e.to,
      data: { turnIndex: e.turn_index }, // 实线判别 + 点击跳转目标
      label: labelText,
      labelStyle: { fontSize: 10, fill: "var(--color-ink-soft)" },
      labelBgStyle: { fill: "var(--color-surface)", fillOpacity: 0.9 },
      labelBgPadding: [4, 2] as [number, number],
      labelBgBorderRadius: 4,
      type: "default",
      markerEnd: { type: MarkerType.ArrowClosed, color: "#9aa4b0", width: 16, height: 16 },
      style: { stroke: "var(--color-line-strong)", strokeWidth: 1.4, cursor: "pointer" },
    };
  });

  // 虚线:无向相邻,装饰无标签。id 用排序对,天然去重
  const dashed: Edge[] = data.dashed_edges.map((e) => ({
    id: `d-${e.a}-${e.b}`,
    source: e.a,
    target: e.b,
    type: "default",
    selectable: false,
    style: { stroke: "var(--color-line-strong)", strokeWidth: 1.1, strokeDasharray: "5 4", opacity: 0.7 },
  }));

  return { nodes, edges: [...dashed, ...solid] };
}
