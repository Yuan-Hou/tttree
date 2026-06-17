import { BaseEdge, EdgeLabelRenderer, useInternalNode, type EdgeProps } from "@xyflow/react";

const PEAK = 58; // 弧线高出节点顶边的高度
const NODE_W_FALLBACK = 208;

/** 自环边(某轮转移的起点=终点:停留在同一场景)。默认边会从右句柄拉一条直线横穿到左句柄,
 *  看不出是自环。这里改画一道鼓在节点顶边上方的弧线 + 箭头落回节点,一眼可辨「回到自身」。
 *  几何锚在节点实测包围盒(不依赖左右句柄),故不论句柄怎么摆都贴合;轮次+beat 标签置于弧顶。
 *  高亮/降调沿用上层 decorateEdges 改写的 style(BaseEdge 直接消费),标签据 style 同步明暗。 */
export function SelfLoopEdge({ source, markerEnd, style, label }: EdgeProps) {
  const node = useInternalNode(source);
  if (!node) return null;

  const { x, y } = node.internals.positionAbsolute; // 节点左上角(y 即顶边)
  const w = node.measured?.width ?? NODE_W_FALLBACK;
  const topY = y;
  const ax = x + w * 0.62; // 出弧点(偏右)
  const bx = x + w * 0.38; // 入弧点(偏左,箭头落点)
  const peakY = topY - PEAK;
  const path = `M ${ax},${topY} C ${ax + 42},${peakY} ${bx - 42},${peakY} ${bx},${topY}`;

  const stroke = (style as { stroke?: string } | undefined)?.stroke;
  const opacity = (style as { opacity?: number } | undefined)?.opacity;
  const highlighted = stroke === "var(--color-accent)";
  const dimmed = opacity != null && opacity < 0.5;

  const labelX = x + w / 2;
  const labelY = peakY;

  return (
    <>
      <BaseEdge path={path} markerEnd={markerEnd} style={style} />
      <EdgeLabelRenderer>
        <div
          style={{
            position: "absolute",
            transform: `translate(-50%, -100%) translate(${labelX}px, ${labelY - 3}px)`,
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
