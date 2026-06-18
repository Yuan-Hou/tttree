import { useEffect, useState } from "react";
import { Handle, Position, type NodeProps } from "@xyflow/react";
import { imgUrl } from "../api";
import { useLightbox } from "./Lightbox";

export interface SceneNodeData {
  variant: "scene" | "start" | "ghost";
  name: string;
  slug?: string;
  originTurn?: number | null;
  images?: string[]; // 正典有效变体图(已剔除被取代),可空
  gallery?: { path: string; turn: number | null; beat: string }[]; // 与 images 同序,逐图带轮次/beat
  current?: boolean; // 当前所在场景 → 高亮
  hl?: boolean; // 动态交互:悬停某实线时,其终点节点高亮
  focused?: boolean; // 点对话聚焦本节点 → 高亮
  pageTo?: number; // 聚焦时令卡片翻到的变体页
  pageNonce?: number; // 每次聚焦请求的标记(变化即应用 pageTo,支持重复聚焦同一节点)
  [k: string]: unknown;
}

/** 场景地图节点。三态:
 *  - start  虚拟「起点」:故事入口,克制的小圆点+标签,只作首轮实线的源。
 *  - scene  真实场景卡:名字 + 变体翻页 gallery(点击走 lightbox)+ 空图占位;当前场景高亮。
 *  - ghost  幽灵场景:实线端点引用了已不在最新黑板里的场景(回退/改写后)→ 淡显占位,不让边凭空消失。
 *  节点可拖动(整卡为拖拽面,翻页箭头 `nodrag` 保证翻页可靠);双击/单击看大图;不可连线。 */
export function SceneNode({ data }: NodeProps) {
  const d = data as SceneNodeData;

  if (d.variant === "start") {
    // 节点框 = 圆本身(标签用 absolute 挂在下方、不计入框),这样 Right 句柄正好落在圆的右侧中点,
    // 实线从圆心高度出发,和圆对齐。
    return (
      <div className="relative flex h-9 w-9 items-center justify-center rounded-full border border-accent bg-accent-soft font-mono text-[15px] text-accent-ink">
        <Handle type="source" position={Position.Right} className="!h-1.5 !w-1.5 !border-0 !bg-accent" />
        ✦
        <span className="absolute left-1/2 top-full mt-1 -translate-x-1/2 whitespace-nowrap font-mono text-[10.5px] tracking-wide text-ink-faint">
          起点
        </span>
      </div>
    );
  }

  if (d.variant === "ghost") {
    return (
      <div className="flex w-[176px] flex-col rounded-xl border border-dashed border-line bg-paper px-3 py-2.5 opacity-60">
        <Handle type="target" position={Position.Left} className="!h-1.5 !w-1.5 !border-0 !bg-line-strong" />
        <Handle type="source" position={Position.Right} className="!h-1.5 !w-1.5 !border-0 !bg-line-strong" />
        <span className="font-serif text-[13px] text-ink-soft">{d.name}</span>
        <span className="mt-0.5 font-mono text-[10px] text-ink-faint">已不在最新黑板</span>
      </div>
    );
  }

  return <SceneCard d={d} />;
}

function SceneCard({ d }: { d: SceneNodeData }) {
  const images = d.images ?? [];
  const gallery = d.gallery ?? [];
  const [i, setI] = useState(0);
  const lightbox = useLightbox();
  const idx = Math.min(i, Math.max(0, images.length - 1));
  const cur = images[idx];
  const cap = gallery[idx]; // 当前页对应的轮次/beat 标注(与 images 同序对齐)

  const openLightbox = () => {
    if (images.length) lightbox(images.map((p) => ({ src: imgUrl(p), alt: d.name })), idx);
  };

  // 点对话聚焦本节点 → 翻到该轮对应的那张图(nonce 变化即应用,支持重复聚焦)
  useEffect(() => {
    if (d.pageNonce != null && d.pageTo != null) setI(d.pageTo);
  }, [d.pageNonce, d.pageTo]);

  const ring =
    d.focused || d.hl
      ? "border-accent ring-2 ring-accent"
      : d.current
        ? "border-accent ring-2 ring-accent-soft"
        : "border-line-strong";

  return (
    <div
      onDoubleClick={openLightbox}
      title={images.length ? "双击看大图(可翻变体)· 拖动可挪位" : "拖动可挪位"}
      className={`flex w-[208px] flex-col overflow-hidden rounded-xl border bg-surface shadow-[0_1px_2px_rgba(28,37,48,0.05)] transition-shadow ${ring}`}
    >
      <Handle type="target" position={Position.Left} className="!h-1.5 !w-1.5 !border-0 !bg-line-strong" />
      <Handle type="source" position={Position.Right} className="!h-1.5 !w-1.5 !border-0 !bg-line-strong" />

      {/* 变体 gallery / 空图占位 */}
      <div className="relative aspect-[4/3] w-full bg-sunken">
        {cur ? (
          <img
            src={imgUrl(cur)}
            alt={d.name}
            onClick={openLightbox}
            className="h-full w-full cursor-zoom-in object-cover"
          />
        ) : (
          <div className="flex h-full w-full items-center justify-center text-[11px] text-ink-faint">
            尚无正典图
          </div>
        )}
        {/* 当前图的轮次/beat 标注:翻页随过滤后的有效图集对齐 */}
        {cur && cap && cap.turn != null && (
          <span className="absolute left-1 top-1 max-w-[85%] truncate rounded bg-ink/55 px-1.5 py-px font-mono text-[9.5px] text-paper">
            第{cap.turn}拍{cap.beat ? ` · ${cap.beat}` : ""}
          </span>
        )}
        {images.length > 1 && (
          <>
            <button
              onClick={(ev) => {
                ev.stopPropagation();
                setI((idx - 1 + images.length) % images.length);
              }}
              onDoubleClick={(ev) => ev.stopPropagation()} // 连点切图不应被卡片判定为双击开大图
              className="nodrag absolute left-1 top-1/2 -translate-y-1/2 rounded-full bg-paper/85 px-1.5 py-0.5 text-[12px] text-ink-soft shadow transition hover:text-accent-ink"
            >
              ‹
            </button>
            <button
              onClick={(ev) => {
                ev.stopPropagation();
                setI((idx + 1) % images.length);
              }}
              onDoubleClick={(ev) => ev.stopPropagation()}
              className="nodrag absolute right-1 top-1/2 -translate-y-1/2 rounded-full bg-paper/85 px-1.5 py-0.5 text-[12px] text-ink-soft shadow transition hover:text-accent-ink"
            >
              ›
            </button>
            <span className="absolute bottom-1 right-1.5 rounded bg-ink/55 px-1.5 py-px font-mono text-[9.5px] text-paper">
              {idx + 1}/{images.length}
            </span>
          </>
        )}
      </div>

      <div className="flex items-center gap-1.5 px-3 py-2">
        <span className="min-w-0 flex-1 truncate font-serif text-[14px] text-ink">{d.name}</span>
        {d.current && (
          <span className="shrink-0 rounded-[5px] bg-accent-soft px-1.5 py-px font-mono text-[9.5px] text-accent-ink">
            当前
          </span>
        )}
        {d.originTurn != null && (
          <span className="shrink-0 font-mono text-[9.5px] text-ink-faint">
            第{d.originTurn}拍生
          </span>
        )}
      </div>
    </div>
  );
}
