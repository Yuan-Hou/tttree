import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import {
  Background,
  BackgroundVariant,
  Controls,
  MarkerType,
  ReactFlow,
  useNodesState,
  type Edge,
  type Node,
  type ReactFlowInstance,
} from "@xyflow/react";
import "@xyflow/react/dist/style.css";
import { getSceneMap } from "../api";
import type { SceneMap as SceneMapData } from "../types";
import { clearPositions, loadPositions, savePosition } from "../nodeLayout";
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

// ── 滚动聚焦「本轮相关图片网格」的临时布局参数 ──
const NODE_H = 196; // SceneNode 卡片估高(4:3 图 + 页脚),用于网格行距与宽高比匹配
const GRID_GX = 44; // 网格相邻格水平间距
const GRID_GY = 44; // 网格相邻格垂直间距
const CELL_W = NODE_W + GRID_GX;
const CELL_H = NODE_H + GRID_GY;
const ENTER_MS = 360; // 进入网格:快速归位(留下「图飞过来」的明确动作)
const RESTORE_MS = 950; // 解除网格:缓慢回家(留视觉线索,便于用户找到对应节点)
const FIT_MS = 460; // 视角聚焦到网格的动画时长
const EASE = "cubic-bezier(.22,.61,.36,1)";

/** 本轮相关节点的临时网格:锚点(本轮落点场景)固定在右下角不动,其余节点向左上铺成一个
 *  宽高比贴近视口的网格(让 fitView 后每张图尽量大)。返回「其余节点 → 临时坐标」(锚点不含)。
 *  锚点在网格右下格,网格整体相对锚点 home 坐标向左/上偏移展开。填充顺序:自右下角起、贴着锚点
 *  那一行先向左铺满,再逐行上移,保证紧凑、不在锚点旁留空格。 */
function computeGridPositions(
  anchorPos: { x: number; y: number },
  others: string[],
  vw: number,
  vh: number,
): Map<string, { x: number; y: number }> {
  const m = others.length + 1; // 含锚点
  const targetAspect = Math.max(0.2, vw / Math.max(1, vh));
  let cols = 1;
  let bestErr = Infinity;
  for (let c = 1; c <= m; c++) {
    const rows = Math.ceil(m / c);
    const aspect = (c * CELL_W) / (rows * CELL_H);
    const err = Math.abs(Math.log(aspect / targetAspect));
    if (err < bestErr) {
      bestErr = err;
      cols = c;
    }
  }
  const rows = Math.ceil(m / cols);
  // 填充顺序:行从底到顶、行内从右到左,跳过锚点占据的右下格。
  const cells: { c: number; r: number }[] = [];
  for (let r = rows - 1; r >= 0; r--) {
    for (let c = cols - 1; c >= 0; c--) {
      if (r === rows - 1 && c === cols - 1) continue; // 锚点格
      cells.push({ c, r });
    }
  }
  const pos = new Map<string, { x: number; y: number }>();
  others.forEach((id, k) => {
    const cell = cells[k];
    if (!cell) return;
    const dx = (cell.c - (cols - 1)) * CELL_W; // 锚点在 c=cols-1 → 其余向左为负
    const dy = (cell.r - (rows - 1)) * CELL_H; // 锚点在 r=rows-1 → 其余向上为负
    pos.set(id, { x: anchorPos.x + dx, y: anchorPos.y + dy });
  });
  return pos;
}

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
  // 本轮聚焦:锚点 + 每个相关节点应翻到的页(nonce 变化即重应用,支持重复聚焦同一轮)。
  const [groupFocus, setGroupFocus] = useState<{ anchor: string; pages: Map<string, number>; nonce: number } | null>(null);
  // 临时网格:gridPos=当前被挪到网格的节点(快速进入);restoring=正在缓慢回家的节点。两者互斥。
  const [gridPos, setGridPos] = useState<Map<string, { x: number; y: number }>>(new Map());
  const [restoring, setRestoring] = useState<Set<string>>(new Set());
  const gridPosRef = useRef(gridPos); // 供事件回调/解除读到最新网格集(避免闭包旧值)
  const restoringRef = useRef(restoring);
  const loadedFor = useRef<string | null>(null);
  const rfRef = useRef<ReactFlowInstance | null>(null);
  const flowWrapRef = useRef<HTMLDivElement>(null);
  const nodesRef = useRef<Node[]>([]); // 同步 home 坐标,供网格相对锚点定位(避免闭包取到旧值)
  const programmaticMove = useRef(false); // 我方 fitView 期间忽略 zoom 检测,避免自触发解除
  const lastZoom = useRef(1);
  const restoreTimer = useRef<ReturnType<typeof setTimeout> | null>(null);

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

  // 缓慢回家:把一批节点标记为 restoring(渲染时给慢速过渡 → home),并在动画结束后清出集合。
  const armRestore = useCallback((ids: Iterable<string>) => {
    const add = [...ids];
    if (add.length === 0) return;
    setRestoring((prev) => new Set([...prev, ...add]));
    if (restoreTimer.current) clearTimeout(restoreTimer.current);
    restoreTimer.current = setTimeout(() => setRestoring(new Set()), RESTORE_MS + 80);
  }, []);

  // 解除网格:全部被挪节点缓慢回家(拖动/缩放/切故事触发)。纯平移画布不触发。
  const dissolve = useCallback(() => {
    if (gridPosRef.current.size === 0) return;
    armRestore(gridPosRef.current.keys());
    setGridPos(new Map());
  }, [armRestore]);

  // 单点居中(无网格场景:本轮只有锚点一张相关图)。
  const centerOnNode = useCallback((slug: string) => {
    const inst = rfRef.current;
    if (!inst) return;
    setTimeout(() => {
      const n = inst.getNode(slug);
      if (!n) {
        inst.fitView({ nodes: [{ id: slug }], maxZoom: 3, duration: FIT_MS, padding: 0.5 });
        return;
      }
      const w = n.measured?.width ?? NODE_W;
      const h = n.measured?.height ?? NODE_H;
      const box = flowWrapRef.current;
      const vw = box?.clientWidth ?? 600;
      const vh = box?.clientHeight ?? 600;
      const frac = 0.7;
      const zoom = Math.max(1, Math.min(4, Math.min((vw * frac) / w, (vh * frac) / h)));
      programmaticMove.current = true;
      inst.setCenter(n.position.x + w / 2, n.position.y + h / 2, { zoom, duration: FIT_MS });
      setTimeout(() => {
        programmaticMove.current = false;
        lastZoom.current = inst.getViewport().zoom;
      }, FIT_MS + 120);
    }, 40);
  }, []);

  // 滚动对话 → 聚焦该轮**所有相关场景图**:锚点(落点场景)不动,其余相关节点临时挪进
  // 一个最大化网格、视角聚焦到网格;每个相关节点翻到它本轮出的那张图。只随 nonce 触发。
  useEffect(() => {
    if (!focusReq || !data) return;
    const turnN = focusReq.turnIndex;
    const edge = data.solid_edges.find((e) => e.turn_index === turnN);
    if (!edge) return;
    const anchor = edge.to;

    // 相关节点 = gallery 里有「本轮出的图」的节点;page = 该图在变体序列里的下标(取最后一张)。
    const pages = new Map<string, number>();
    for (const node of data.nodes) {
      const g = node.gallery ?? [];
      let page = -1;
      for (let k = 0; k < g.length; k++) if (g[k].turn === turnN) page = k;
      if (page >= 0) pages.set(node.slug, page);
    }
    // 锚点无本轮图但实线带图 → 也给锚点定位到该图(老行为兜底)。
    if (!pages.has(anchor) && edge.image_path) {
      const an = data.nodes.find((n) => n.slug === anchor);
      const k = an ? an.image_paths.indexOf(edge.image_path) : -1;
      if (k >= 0) pages.set(anchor, k);
    }
    setGroupFocus({ anchor, pages, nonce: focusReq.nonce });

    const inst = rfRef.current;
    const others = [...pages.keys()].filter((id) => id !== anchor).sort();
    if (others.length === 0 || !inst) {
      // 无需网格:把此前被挪的节点缓慢送回,单点居中锚点。
      armRestore(gridPosRef.current.keys());
      setGridPos(new Map());
      centerOnNode(anchor);
      return;
    }

    // 网格:相对锚点 home 坐标展开。锚点 home 从 nodesRef 取(渲染坐标可能是上轮网格位)。
    const anchorHome = nodesRef.current.find((n) => n.id === anchor)?.position ?? { x: 0, y: 0 };
    const box = flowWrapRef.current;
    const vw = box?.clientWidth ?? 600;
    const vh = box?.clientHeight ?? 600;
    const gp = computeGridPositions(anchorHome, others, vw, vh);
    // 该恢复的恢复:上轮被挪/在挪、但本轮不在新网格里的 → 缓慢回家。
    const leaving = [...gridPosRef.current.keys(), ...restoringRef.current].filter((id) => !gp.has(id));
    armRestore(leaving);
    setGridPos(gp);
    // 视角聚焦到网格(锚点 + 其余),fitView 期间屏蔽 zoom 自触发。
    programmaticMove.current = true;
    setTimeout(() => {
      inst.fitView({ nodes: [anchor, ...others].map((id) => ({ id })), padding: 0.22, duration: FIT_MS, maxZoom: 2 });
      setTimeout(() => {
        programmaticMove.current = false;
        lastZoom.current = inst.getViewport().zoom;
      }, FIT_MS + 120);
    }, 50);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [focusReq]);

  // 卸载清理定时器
  useEffect(() => () => {
    if (restoreTimer.current) clearTimeout(restoreTimer.current);
  }, []);

  // 悬停某实线 → 终点节点高亮
  const hoveredTarget = useMemo(() => {
    if (!hoverId) return null;
    const e = base.edges.find((x) => x.id === hoverId);
    return e ? e.target : null;
  }, [hoverId, base.edges]);

  // 自动布局 + 实时 data 叠加(高亮/聚焦)。手动拖动的坐标在下方 effect 中覆盖到其上。
  const computed = useMemo(
    () =>
      base.nodes.map((n) => {
        let d = n.data as SceneNodeData;
        if (hoveredTarget && n.id === hoveredTarget) d = { ...d, hl: true };
        if (groupFocus) {
          const page = groupFocus.pages.get(n.id);
          if (page != null) d = { ...d, focused: true, pageTo: page, pageNonce: groupFocus.nonce };
        }
        return d === n.data ? n : { ...n, data: d };
      }),
    [base.nodes, hoveredTarget, groupFocus],
  );
  const edges = useMemo(() => decorateEdges(base.edges, hoverId), [base.edges, hoverId]);

  // 手动布局:地图是一张随故事整体生长的图,按 `${storyId}.map` 分桶(不分轮)。节点身份 = slug(跨刷新稳定)。
  const scope = `${storyId}.map`;
  const [nodes, setNodes, onNodesChange] = useNodesState<Node>([]);
  const seededStory = useRef<string>("");
  const [hasOverrides, setHasOverrides] = useState(false);

  // 同步:地图刷新/高亮变化 → 只更新 data,保留当前(可能已拖动的)坐标;换故事 → 按本故事存档重新落位。
  useEffect(() => {
    const reseed = seededStory.current !== storyId;
    const saved = loadPositions(scope);
    setNodes((prev) => {
      const prevPos = new Map(prev.map((n) => [n.id, n.position]));
      return computed.map((n) => {
        // 同故事内:沿用上一帧坐标(含刚拖到的);换故事/新场景:用存档,无则默认自动布局。
        const pos = (!reseed ? prevPos.get(n.id) : undefined) ?? saved[n.id] ?? n.position;
        return { ...n, position: pos };
      });
    });
    if (reseed) {
      seededStory.current = storyId;
      setHasOverrides(Object.keys(saved).length > 0);
    }
  }, [computed, storyId, scope, setNodes]);

  // home 坐标镜像:供网格定位与「回家」目标读取(渲染坐标可能是临时网格位)。
  useEffect(() => {
    nodesRef.current = nodes;
  }, [nodes]);
  useEffect(() => {
    gridPosRef.current = gridPos;
  }, [gridPos]);
  useEffect(() => {
    restoringRef.current = restoring;
  }, [restoring]);

  // 渲染坐标 = home(nodes)叠加临时网格:被挪节点→网格位(快速、且禁拖以免与回家冲突);
  // 回家中节点→home(慢速过渡);其余→home(无过渡,保证手动拖拽即时跟手)。
  const renderNodes = useMemo(
    () =>
      nodes.map((n) => {
        const g = gridPos.get(n.id);
        if (g) {
          return { ...n, position: g, draggable: false, style: { ...n.style, transition: `transform ${ENTER_MS}ms ${EASE}` } };
        }
        if (restoring.has(n.id)) {
          return { ...n, style: { ...n.style, transition: `transform ${RESTORE_MS}ms ${EASE}` } };
        }
        return (n.style as { transition?: string } | undefined)?.transition ? { ...n, style: { ...n.style, transition: undefined } } : n;
      }),
    [nodes, gridPos, restoring],
  );

  // 拖完落盘 + 标记本故事有手动布局(亮出「重置布局」)。
  const onNodeDragStop = useCallback(
    (_: unknown, node: Node) => {
      savePosition(scope, node.id, node.position);
      setHasOverrides(true);
    },
    [scope],
  );

  // 重置:清掉本故事地图的手动坐标,各场景回到自动布局默认位。
  const resetLayout = useCallback(() => {
    clearPositions(scope);
    const def = new Map(computed.map((n) => [n.id, n.position]));
    setNodes((prev) => prev.map((n) => ({ ...n, position: def.get(n.id) ?? n.position })));
    setHasOverrides(false);
  }, [scope, computed, setNodes]);

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

      <div ref={flowWrapRef} className="relative min-h-0 flex-1">
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
            nodes={renderNodes}
            edges={edges}
            nodeTypes={nodeTypes}
            edgeTypes={edgeTypes}
            fitView
            fitViewOptions={{ padding: 0.22, maxZoom: 1 }}
            minZoom={0.25}
            maxZoom={4}
            nodesConnectable={false}
            nodeDragThreshold={5}
            elementsSelectable
            zoomOnDoubleClick={false}
            proOptions={{ hideAttribution: true }}
            onInit={(inst) => {
              rfRef.current = inst;
              lastZoom.current = inst.getViewport().zoom;
            }}
            onNodesChange={onNodesChange}
            onNodeDragStart={dissolve} // 拖动任一节点 → 临时网格缓慢解除
            onNodeDragStop={onNodeDragStop}
            onMove={(_, vp) => {
              // 缩放(非纯平移)→ 解除网格;我方 fitView 期间忽略,避免自触发。
              if (programmaticMove.current) {
                lastZoom.current = vp.zoom;
                return;
              }
              if (gridPos.size > 0 && Math.abs(vp.zoom - lastZoom.current) > 1e-3) dissolve();
              lastZoom.current = vp.zoom;
            }}
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
        {hasOverrides && data && data.nodes.length > 0 && (
          <button
            type="button"
            onClick={resetLayout}
            title="清掉本故事地图拖动过的节点位置,回到自动布局"
            className="absolute bottom-3 right-3 z-10 rounded-full border border-line-strong bg-surface/95 px-3 py-1 font-mono text-[10.5px] text-ink-soft shadow-sm transition hover:bg-sunken"
          >
            ↺ 重置布局
          </button>
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
      draggable: true,
      data: { variant: "start", name: "起点" } satisfies SceneNodeData,
    },
    ...data.nodes.map((n) => ({
      id: n.slug,
      type: "scene",
      position: pos[n.slug],
      draggable: true,
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
      draggable: true,
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
