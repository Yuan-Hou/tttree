import { BaseEdge, EdgeLabelRenderer, useInternalNode, type EdgeProps } from "@xyflow/react";

const NODE_W_FALLBACK = 208;
const NODE_H_FALLBACK = 196;
const CTRL = 50; // 控制点外伸量(弧顶 ≈ 0.75×CTRL 离节点边界,决定花瓣伸多远)
const SPLAY = 22; // 控制点沿切向张开(花瓣胖瘦)
const FOOT = 8; // 两只脚沿切向的半间距
const LABEL_GAP = 12; // 标签较弧顶再往外推的距离

/** 节点矩形(中心 cx,cy,半宽 hw,半高 hh)沿外向单位 (ux,uy) 的边界交点(花瓣根部)。 */
function edgePoint(cx: number, cy: number, hw: number, hh: number, ux: number, uy: number) {
  const sx = ux !== 0 ? hw / Math.abs(ux) : Infinity;
  const sy = uy !== 0 ? hh / Math.abs(uy) : Infinity;
  const s = Math.min(sx, sy);
  return { x: cx + ux * s, y: cy + uy * s };
}

/** 自环边(某轮转移的起点=终点:停留在同一场景)。一个节点的多个自环沿其四周整圈均分成等大花瓣:
 *  第 loopIndex 个花瓣朝向 = 从正上方起、按 loopCount 均分的角度;单环退化为「顶上一朵」。每瓣是一道
 *  向外鼓的三次贝塞尔环 + 回落箭头,标签置于弧顶外侧。几何锚在节点实测包围盒,故不论句柄怎么摆都贴合。
 *  高亮/降调沿用上层 decorateEdges 改写的 style(BaseEdge 直接消费),标签据 style 同步明暗。 */
export function SelfLoopEdge({ source, markerEnd, style, label, data }: EdgeProps) {
  const node = useInternalNode(source);
  if (!node) return null;

  const idx = (data as { loopIndex?: number } | undefined)?.loopIndex ?? 0;
  const n = Math.max(1, (data as { loopCount?: number } | undefined)?.loopCount ?? 1);
  const w = node.measured?.width ?? NODE_W_FALLBACK;
  const h = node.measured?.height ?? NODE_H_FALLBACK;
  const cx = node.internals.positionAbsolute.x + w / 2;
  const cy = node.internals.positionAbsolute.y + h / 2;

  // 花瓣朝向:正上方(-90°)起,整圈按总数均分。
  const ang = -Math.PI / 2 + (idx * 2 * Math.PI) / n;
  const ux = Math.cos(ang); // 外向单位
  const uy = Math.sin(ang);
  const tx = -uy; // 切向单位(垂直外向)
  const ty = ux;

  const base = edgePoint(cx, cy, w / 2, h / 2, ux, uy); // 花瓣根部
  const footA = { x: base.x + tx * FOOT, y: base.y + ty * FOOT };
  const footB = { x: base.x - tx * FOOT, y: base.y - ty * FOOT }; // 箭头落点
  const c1 = { x: base.x + ux * CTRL + tx * (FOOT + SPLAY), y: base.y + uy * CTRL + ty * (FOOT + SPLAY) };
  const c2 = { x: base.x + ux * CTRL - tx * (FOOT + SPLAY), y: base.y + uy * CTRL - ty * (FOOT + SPLAY) };
  const path = `M ${footA.x},${footA.y} C ${c1.x},${c1.y} ${c2.x},${c2.y} ${footB.x},${footB.y}`;

  // 弧顶(三次贝塞尔 t=0.5):⅛(A+B) + ⅜(c1+c2);标签沿外向再推 LABEL_GAP。
  const apexX = 0.125 * (footA.x + footB.x) + 0.375 * (c1.x + c2.x);
  const apexY = 0.125 * (footA.y + footB.y) + 0.375 * (c1.y + c2.y);
  const labelX = apexX + ux * LABEL_GAP;
  const labelY = apexY + uy * LABEL_GAP;

  const stroke = (style as { stroke?: string } | undefined)?.stroke;
  const opacity = (style as { opacity?: number } | undefined)?.opacity;
  const highlighted = stroke === "var(--color-accent)";
  const dimmed = opacity != null && opacity < 0.5;

  return (
    <>
      <BaseEdge path={path} markerEnd={markerEnd} style={{ ...style, vectorEffect: "non-scaling-stroke" }} />
      <EdgeLabelRenderer>
        <div
          style={{
            position: "absolute",
            transform: `translate(-50%, -50%) translate(${labelX}px, ${labelY}px)`,
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
    </>
  );
}
