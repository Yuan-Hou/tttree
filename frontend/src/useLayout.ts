import { useCallback, useEffect, useRef, useState } from "react";

/** 主界面四区布局(书架 | 对话 | 地图 | 右坞)的可调宽度 + 书架折叠,全局存 localStorage。
 *
 * 模型:书架、右坞各存一个像素宽;中间「对话 / 地图」两区按比例 midSplit(对话占比)瓜分剩余空间。
 * 三条可拖边界各调一项:书架↔对话 调 shelfW、对话↔地图 调 midSplit、地图↔右坞 调 rightW。
 * 折叠时书架收成窄条(SHELF_COLLAPSED),腾出的空间自然被中间两区按原比例吸收。 */

const KEY = "vore.layout.v1";
const SHELF_MIN = 180,
  SHELF_MAX = 420,
  SHELF_DEFAULT = 248,
  SHELF_COLLAPSED = 48;
const RIGHT_MIN = 288,
  RIGHT_MAX = 560,
  RIGHT_DEFAULT = 344;
const MID_MIN = 300; // 对话 / 地图 各自的最小宽度(防止被拖没)
const DIVIDER = 6; // 每条分隔线的像素宽

export type DragWhich = "shelf" | "mid" | "right";

interface LayoutState {
  shelfW: number;
  rightW: number;
  midSplit: number; // 对话区占「中间两区」的比例 ∈ (0,1)
  collapsed: boolean;
}

const clamp = (v: number, lo: number, hi: number) => Math.min(hi, Math.max(lo, v));
const num = (v: unknown, d: number) => (typeof v === "number" && isFinite(v) ? v : d);

const DEFAULTS: LayoutState = { shelfW: SHELF_DEFAULT, rightW: RIGHT_DEFAULT, midSplit: 0.5, collapsed: false };

function load(): LayoutState {
  try {
    const raw = localStorage.getItem(KEY);
    if (!raw) return DEFAULTS;
    const o = JSON.parse(raw) as Record<string, unknown>;
    return {
      shelfW: clamp(num(o.shelfW, SHELF_DEFAULT), SHELF_MIN, SHELF_MAX),
      rightW: clamp(num(o.rightW, RIGHT_DEFAULT), RIGHT_MIN, RIGHT_MAX),
      midSplit: clamp(num(o.midSplit, 0.5), 0.12, 0.88),
      collapsed: o.collapsed === true,
    };
  } catch {
    return DEFAULTS;
  }
}

export function useLayout() {
  const [st, setSt] = useState<LayoutState>(load);
  const stRef = useRef(st);
  stRef.current = st;
  const containerRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    try {
      localStorage.setItem(KEY, JSON.stringify(st));
    } catch {
      /* 配额满 / 禁用 → 忽略,这只是本地偏好 */
    }
  }, [st]);

  const startDrag = useCallback((which: DragWhich, e: React.PointerEvent) => {
    e.preventDefault();
    const startX = e.clientX;
    const start = stRef.current; // 拖动起点快照:增量都相对它算,避免累积漂移
    const contW = containerRef.current?.clientWidth ?? window.innerWidth;
    const onMove = (ev: PointerEvent) => {
      const dx = ev.clientX - startX;
      setSt((s) => {
        if (which === "shelf") return { ...s, shelfW: clamp(start.shelfW + dx, SHELF_MIN, SHELF_MAX) };
        if (which === "right") return { ...s, rightW: clamp(start.rightW - dx, RIGHT_MIN, RIGHT_MAX) };
        // mid:把像素增量换算成比例增量,并夹住两侧各 ≥ MID_MIN。
        const shelfEff = s.collapsed ? SHELF_COLLAPSED : s.shelfW;
        const dividers = (s.collapsed ? 2 : 3) * DIVIDER;
        const midTotal = contW - shelfEff - s.rightW - dividers;
        if (midTotal <= 2 * MID_MIN) return s;
        const lo = MID_MIN / midTotal;
        const hi = 1 - MID_MIN / midTotal;
        const dialogue = midTotal * start.midSplit + dx;
        return { ...s, midSplit: clamp(dialogue / midTotal, lo, hi) };
      });
    };
    const onUp = () => {
      window.removeEventListener("pointermove", onMove);
      window.removeEventListener("pointerup", onUp);
      document.body.style.cursor = "";
      document.body.style.userSelect = "";
    };
    window.addEventListener("pointermove", onMove);
    window.addEventListener("pointerup", onUp);
    document.body.style.cursor = "col-resize";
    document.body.style.userSelect = "none";
  }, []);

  const toggleCollapsed = useCallback(() => setSt((s) => ({ ...s, collapsed: !s.collapsed })), []);

  return {
    containerRef,
    shelfW: st.collapsed ? SHELF_COLLAPSED : st.shelfW, // 对外给「生效」宽度
    rightW: st.rightW,
    midSplit: st.midSplit,
    collapsed: st.collapsed,
    toggleCollapsed,
    startDrag,
  };
}
