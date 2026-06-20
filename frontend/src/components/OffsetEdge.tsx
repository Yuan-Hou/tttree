import { BaseEdge, EdgeLabelRenderer, useInternalNode, useStore, type EdgeProps } from "@xyflow/react";

const NODE_W_FALLBACK = 208;
const NODE_H_FALLBACK = 196;
const CLEAR = 28; // 绕行时离被挡节点保留的安全余量(也是「算不算挡路」的判定余量)
const MAX_PUSH = 440; // 绕行位移上限,避免极端堆叠把线甩出很远

interface Rect {
  id: string;
  x: number;
  y: number;
  w: number;
  h: number;
}

/** 中心 (cx,cy)、尺寸 w×h 的节点被「中心→(tx,ty)」射线穿出的边界点。
 *  circle=true(起点是个圆,包围盒四角是空白)→ 按内切圆求交,避免对角拖动时连线落到角上留缝。 */
function clip(cx: number, cy: number, w: number, h: number, tx: number, ty: number, circle: boolean) {
  const dx = tx - cx;
  const dy = ty - cy;
  if (dx === 0 && dy === 0) return { x: cx, y: cy };
  if (circle) {
    const r = Math.min(w, h) / 2;
    const len = Math.hypot(dx, dy);
    return { x: cx + (dx / len) * r, y: cy + (dy / len) * r };
  }
  const sx = dx !== 0 ? w / 2 / Math.abs(dx) : Infinity;
  const sy = dy !== 0 ? h / 2 / Math.abs(dy) : Infinity;
  const s = Math.min(sx, sy);
  return { x: cx + dx * s, y: cy + dy * s };
}

/** 平行边(同一对节点间的实线/虚线/多重转移)。默认边都画在两节点中心连线上 → 互相重叠。
 *  这里按 data.offset 沿连线法向把每条边鼓成一道二次贝塞尔弧,同组各边均匀分到两侧 → 自动错开;
 *  标签置于各自弧顶,也随之分离。法向取「按 id 规范排序的端点方向」,保证同组每条边(含反向转移)
 *  用同一条法线、offset 正负一致地落在两侧。高亮/降调沿用上层 decorateEdges 改写的 style。
 *
 *  绕行(Tier B):再叠一层「躲开挡路节点」——找出被本边直线穿过的其他节点,把弧顶推到更省力的
 *  一侧、刚好绕过它们。单弧单侧的启发式:挡路节点分处两侧时只能让开较省力的一侧(另一侧可能仍压)。 */
export function OffsetEdge({ source, target, markerEnd, style, label, data }: EdgeProps) {
  const s = useInternalNode(source);
  const t = useInternalNode(target);
  // 所有节点的实时矩形(绕行检测用)。内容相等时跳过 → 仅当某矩形真正移动/缩放才触发本边重渲染。
  const rects = useStore(
    (st) => {
      const arr: Rect[] = [];
      st.nodeLookup.forEach((n) => {
        const p = n.internals.positionAbsolute;
        arr.push({ id: n.id, x: p.x, y: p.y, w: n.measured?.width ?? NODE_W_FALLBACK, h: n.measured?.height ?? NODE_H_FALLBACK });
      });
      return arr;
    },
    (a, b) =>
      a.length === b.length &&
      a.every((r, i) => r.id === b[i].id && r.x === b[i].x && r.y === b[i].y && r.w === b[i].w && r.h === b[i].h),
  );
  if (!s || !t) return null;

  const sw = s.measured?.width ?? NODE_W_FALLBACK;
  const sh = s.measured?.height ?? NODE_H_FALLBACK;
  const tw = t.measured?.width ?? NODE_W_FALLBACK;
  const th = t.measured?.height ?? NODE_H_FALLBACK;
  const scx = s.internals.positionAbsolute.x + sw / 2;
  const scy = s.internals.positionAbsolute.y + sh / 2;
  const tcx = t.internals.positionAbsolute.x + tw / 2;
  const tcy = t.internals.positionAbsolute.y + th / 2;

  const offset = (data as { offset?: number } | undefined)?.offset ?? 0;
  const dx = tcx - scx;
  const dy = tcy - scy;
  const len = Math.hypot(dx, dy) || 1;
  const wx = -dy / len; // 世界法向单位向量(法线)
  const wy = dx / len;
  // 平行错开:沿规范法向(source<target 为正)把同组各边的弧顶偏移 offset。
  const flip = source < target ? 1 : -1;
  const baseApex = flip * offset;

  // 绕行:挡路节点把弧顶「至少要到」的两侧门槛累加,最后挑更省力的一侧。
  let plusNeed = -Infinity; // +法向侧:apex ≥ 此值才让开所有挡路节点
  let minusNeed = Infinity; // -法向侧:apex ≤ 此值
  if (len > 1) {
    for (const r of rects) {
      if (r.id === source || r.id === target) continue;
      const ocx = r.x + r.w / 2;
      const ocy = r.y + r.h / 2;
      const tt = ((ocx - scx) * dx + (ocy - scy) * dy) / (len * len);
      if (tt <= 0.02 || tt >= 0.98) continue; // 投影点不在两端之间 → 不挡这条边
      const cpx = scx + tt * dx;
      const cpy = scy + tt * dy;
      // 直线上离该节点最近的点是否落进其矩形(含余量)→ 视为穿过/擦到。
      if (Math.abs(cpx - ocx) >= r.w / 2 + CLEAR || Math.abs(cpy - ocy) >= r.h / 2 + CLEAR) continue;
      const d = (ocx - scx) * wx + (ocy - scy) * wy; // 节点中心到直线的有符号法向距离
      const ext = Math.abs((r.w / 2) * wx) + Math.abs((r.h / 2) * wy) + CLEAR; // 矩形法向半投影 + 余量
      plusNeed = Math.max(plusNeed, d + ext);
      minusNeed = Math.min(minusNeed, d - ext);
    }
  }

  let apex = baseApex;
  if (plusNeed > -Infinity) {
    const aPlus = Math.max(baseApex, plusNeed); // 走 + 侧:抬到刚好清空
    const aMinus = Math.min(baseApex, minusNeed); // 走 - 侧
    apex = aPlus - baseApex <= baseApex - aMinus ? aPlus : aMinus; // 选位移更小的一侧
    apex = Math.max(-MAX_PUSH, Math.min(MAX_PUSH, apex));
  }

  // 二次贝塞尔:控制点 = 中点 + 2×apex 法向(t=0.5 弧顶恰好偏移 apex)。apex=0 → 直线。
  const mx = (scx + tcx) / 2 + wx * apex * 2;
  const my = (scy + tcy) / 2 + wy * apex * 2;
  const sCircle = (s.data as { variant?: string })?.variant === "start";
  const tCircle = (t.data as { variant?: string })?.variant === "start";
  const sp = clip(scx, scy, sw, sh, mx, my, sCircle);
  const tp = clip(tcx, tcy, tw, th, mx, my, tCircle);
  const path = `M ${sp.x},${sp.y} Q ${mx},${my} ${tp.x},${tp.y}`;

  // 标签置于弧顶(二次贝塞尔 t=0.5):0.25·起 + 0.5·控制 + 0.25·终。
  const lx = 0.25 * sp.x + 0.5 * mx + 0.25 * tp.x;
  const ly = 0.25 * sp.y + 0.5 * my + 0.25 * tp.y;

  const stroke = (style as { stroke?: string } | undefined)?.stroke;
  const opacity = (style as { opacity?: number } | undefined)?.opacity;
  const highlighted = stroke === "var(--color-accent)";
  const dimmed = opacity != null && opacity < 0.5;

  return (
    <>
      <BaseEdge path={path} markerEnd={markerEnd} style={{ ...style, vectorEffect: "non-scaling-stroke" }} />
      {label && (
        <EdgeLabelRenderer>
          <div
            style={{
              position: "absolute",
              transform: `translate(-50%, -50%) translate(${lx}px, ${ly}px)`,
              pointerEvents: "none",
              fontSize: 10,
              lineHeight: 1.2,
              padding: "2px 4px",
              borderRadius: 4,
              whiteSpace: "nowrap",
              background: "var(--color-surface)",
              color: highlighted ? "var(--color-accent-ink)" : "var(--color-ink-soft)",
              opacity: dimmed ? 0.25 : 1,
            }}
            className="font-mono"
          >
            {label}
          </div>
        </EdgeLabelRenderer>
      )}
    </>
  );
}
