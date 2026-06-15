import { createContext, useCallback, useContext, useEffect, useState } from "react";
import type { ReactNode } from "react";

/** 通用看大图(lightbox)。任意缩略图调 useLightbox().open(src, alt) 即弹出大图;
 *  支持 适应窗口 ↔ 放大/缩小、滚动平移、ESC / 点外部 / ✕ 关闭。
 *  以后场景地图、画廊等有图处直接复用同一个组件。 */

type OpenFn = (src: string, alt?: string) => void;
const LightboxCtx = createContext<OpenFn>(() => {});

export const useLightbox = (): OpenFn => useContext(LightboxCtx);

export function LightboxProvider({ children }: { children: ReactNode }) {
  const [item, setItem] = useState<{ src: string; alt?: string } | null>(null);
  const open = useCallback<OpenFn>((src, alt) => setItem({ src, alt }), []);
  const close = useCallback(() => setItem(null), []);

  return (
    <LightboxCtx.Provider value={open}>
      {children}
      {item && <Overlay src={item.src} alt={item.alt} onClose={close} />}
    </LightboxCtx.Provider>
  );
}

const MIN = 0.5;
const MAX = 5;

function Overlay({ src, alt, onClose }: { src: string; alt?: string; onClose: () => void }) {
  // zoom = null → 适应窗口(object-contain);数字 → 以视口宽的倍数显示,可滚动平移。
  const [zoom, setZoom] = useState<number | null>(null);

  useEffect(() => setZoom(null), [src]); // 换图回到适应窗口
  useEffect(() => {
    const h = (e: KeyboardEvent) => {
      if (e.key === "Escape") onClose();
      else if (e.key === "+" || e.key === "=") setZoom((z) => clamp((z ?? 1) * 1.25));
      else if (e.key === "-") setZoom((z) => clamp((z ?? 1) / 1.25));
      else if (e.key === "0") setZoom(null);
    };
    window.addEventListener("keydown", h);
    return () => window.removeEventListener("keydown", h);
  }, [onClose]);

  const fit = zoom === null;

  return (
    <div className="fixed inset-0 z-[60] flex flex-col bg-ink/80 backdrop-blur-[2px]" onClick={onClose}>
      {/* 控制条 */}
      <div
        className="flex items-center gap-2 px-5 py-3 text-paper/90"
        onClick={(e) => e.stopPropagation()}
      >
        <Ctrl onClick={() => setZoom((z) => clamp((z ?? 1) / 1.25))} title="缩小">−</Ctrl>
        <Ctrl onClick={() => setZoom(null)} title="适应窗口">适应</Ctrl>
        <Ctrl onClick={() => setZoom((z) => clamp((z ?? 1) * 1.25))} title="放大">＋</Ctrl>
        <span className="ml-1 font-mono text-[11px] text-paper/60">
          {fit ? "适应窗口" : `${Math.round(zoom * 100)}%`}
        </span>
        <span className="ml-auto truncate font-mono text-[11px] text-paper/50">{alt}</span>
        <Ctrl onClick={onClose} title="关闭(Esc)">关闭 ✕</Ctrl>
      </div>

      {/* 图面:适应=居中 contain;放大=可滚动平移 */}
      <div className="flex min-h-0 flex-1 items-center justify-center overflow-auto p-4">
        <img
          src={src}
          alt={alt}
          onClick={(e) => {
            e.stopPropagation();
            setZoom((z) => (z === null ? 1.5 : null)); // 点图切换 适应 ↔ 放大
          }}
          className={
            fit
              ? "max-h-full max-w-full cursor-zoom-in object-contain"
              : "max-w-none cursor-zoom-out"
          }
          style={fit ? undefined : { width: `${zoom * 100}vw` }}
        />
      </div>
    </div>
  );
}

const clamp = (z: number) => Math.min(MAX, Math.max(MIN, z));

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
