import { createContext, useCallback, useContext, useEffect, useRef, useState } from "react";
import type { ReactNode } from "react";

/** 通用看大图(lightbox)。任意缩略图调 useLightbox()(images, index) 即弹出大图;
 *  images = [{src, alt}, ...],index 默认 0。支持:
 *    - 多图:左右箭头 / 键盘 ← → 翻页;右下角「当前/总数」(单张时隐藏)
 *    - 点图 / 滚轮 / ＋−键 缩放;放大后可拖动滚动平移看全图;ESC / 点外部 / ✕ 关闭 */

export type LightImage = { src: string; alt?: string };
type OpenFn = (images: LightImage[], index?: number) => void;
const LightboxCtx = createContext<OpenFn>(() => {});

export const useLightbox = (): OpenFn => useContext(LightboxCtx);

export function LightboxProvider({ children }: { children: ReactNode }) {
  const [state, setState] = useState<{ images: LightImage[]; index: number } | null>(null);
  const open = useCallback<OpenFn>((images, index = 0) => {
    if (!images || images.length === 0) return;
    setState({ images, index: Math.min(Math.max(0, index), images.length - 1) });
  }, []);
  const close = useCallback(() => setState(null), []);

  return (
    <LightboxCtx.Provider value={open}>
      {children}
      {state && <Overlay images={state.images} initial={state.index} onClose={close} />}
    </LightboxCtx.Provider>
  );
}

const MIN = 0.5;
const MAX = 12;

function Overlay({ images, initial, onClose }: { images: LightImage[]; initial: number; onClose: () => void }) {
  const [idx, setIdx] = useState(initial);
  // zoom = null → 适应窗口(object-contain);数字 → 以视口宽的倍数显示,可滚动平移。
  const [zoom, setZoom] = useState<number | null>(null);
  const scrollRef = useRef<HTMLDivElement>(null);

  const multi = images.length > 1;
  const i = Math.min(idx, images.length - 1);
  const cur = images[i];
  const go = useCallback(
    (d: number) => setIdx((x) => (x + d + images.length) % images.length),
    [images.length],
  );

  useEffect(() => setZoom(null), [idx]); // 换图回到适应窗口
  useEffect(() => {
    const h = (e: KeyboardEvent) => {
      if (e.key === "Escape") onClose();
      else if (e.key === "ArrowLeft" && multi) go(-1);
      else if (e.key === "ArrowRight" && multi) go(1);
      else if (e.key === "+" || e.key === "=") setZoom((z) => clamp((z ?? 1) * 1.4));
      else if (e.key === "-") setZoom((z) => clamp((z ?? 1) / 1.4));
      else if (e.key === "0") setZoom(null);
    };
    window.addEventListener("keydown", h);
    return () => window.removeEventListener("keydown", h);
  }, [onClose, multi, go]);

  // 滚轮缩放(以非被动监听挂载,才能 preventDefault 拦住页面滚动)
  useEffect(() => {
    const el = scrollRef.current;
    if (!el) return;
    const onWheel = (e: WheelEvent) => {
      e.preventDefault();
      setZoom((z) => clamp((z ?? 1) * (e.deltaY < 0 ? 1.18 : 1 / 1.18)));
    };
    el.addEventListener("wheel", onWheel, { passive: false });
    return () => el.removeEventListener("wheel", onWheel);
  }, []);

  const fit = zoom === null;

  return (
    <div className="fixed inset-0 z-[60] flex flex-col bg-ink/80 backdrop-blur-[2px]" onClick={onClose}>
      {/* 控制条 */}
      <div className="flex items-center gap-2 px-5 py-3 text-paper/90" onClick={(e) => e.stopPropagation()}>
        <Ctrl onClick={() => setZoom((z) => clamp((z ?? 1) / 1.4))} title="缩小">−</Ctrl>
        <Ctrl onClick={() => setZoom(null)} title="适应窗口">适应</Ctrl>
        <Ctrl onClick={() => setZoom((z) => clamp((z ?? 1) * 1.4))} title="放大">＋</Ctrl>
        <span className="ml-1 font-mono text-[11px] text-paper/60">
          {fit ? "适应窗口" : `${Math.round((zoom as number) * 100)}%`}
        </span>
        <span className="ml-auto truncate font-mono text-[11px] text-paper/50">{cur.alt}</span>
        <Ctrl onClick={onClose} title="关闭(Esc)">关闭 ✕</Ctrl>
      </div>

      {/* 图面:外层滚动容器 + 内层 min-w/h-full 居中包裹 —— 放大后左/上溢出也能滚到,看全图。
          点图切换 适应↔放大;翻页只走箭头/键盘,避免与缩放点击冲突。 */}
      <div ref={scrollRef} className="min-h-0 flex-1 overflow-auto" onClick={onClose}>
        <div className="flex min-h-full min-w-full items-center justify-center p-4">
          <img
            src={cur.src}
            alt={cur.alt}
            onClick={(e) => {
              e.stopPropagation();
              setZoom((z) => (z === null ? 2 : null));
            }}
            className={fit ? "max-h-full max-w-full cursor-zoom-in object-contain" : "max-w-none cursor-zoom-out"}
            style={fit ? undefined : { width: `${(zoom as number) * 100}vw` }}
          />
        </div>
      </div>

      {/* 多图:左右箭头 + 当前/总数(锚定视口,不随放大平移) */}
      {multi && (
        <>
          <Arrow side="left" onClick={() => go(-1)} />
          <Arrow side="right" onClick={() => go(1)} />
          <span
            className="absolute bottom-4 right-5 rounded-md bg-ink/55 px-2 py-0.5 font-mono text-[12px] text-paper/85"
            onClick={(e) => e.stopPropagation()}
          >
            {i + 1}/{images.length}
          </span>
        </>
      )}
    </div>
  );
}

const clamp = (z: number) => Math.min(MAX, Math.max(MIN, z));

function Arrow({ side, onClick }: { side: "left" | "right"; onClick: () => void }) {
  return (
    <button
      onClick={(e) => {
        e.stopPropagation();
        onClick();
      }}
      title={side === "left" ? "上一张(←)" : "下一张(→)"}
      className={`absolute top-1/2 -translate-y-1/2 ${
        side === "left" ? "left-4" : "right-4"
      } flex h-11 w-11 items-center justify-center rounded-full border border-paper/20 bg-ink/45 font-mono text-[20px] text-paper/85 transition hover:bg-ink/70`}
    >
      {side === "left" ? "‹" : "›"}
    </button>
  );
}

function Ctrl({ onClick, title, children }: { onClick: () => void; title: string; children: ReactNode }) {
  return (
    <button
      onClick={onClick}
      title={title}
      className="rounded-md border border-paper/20 bg-paper/5 px-2.5 py-1 font-mono text-[11.5px] text-paper/85 transition hover:bg-paper/15"
    >
      {children}
    </button>
  );
}
