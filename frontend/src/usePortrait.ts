import { useEffect, useState } from "react";

/** 订阅竖屏(手机)状态 → 切「地图在上、对话在下」的堆叠布局。
 *  实时应用与导出查看器共用,响应式规则两处一致。 */
export function usePortrait(): boolean {
  const [portrait, setPortrait] = useState(
    () => typeof window !== "undefined" && window.matchMedia("(orientation: portrait)").matches,
  );
  useEffect(() => {
    const mq = window.matchMedia("(orientation: portrait)");
    const on = () => setPortrait(mq.matches);
    mq.addEventListener("change", on);
    return () => mq.removeEventListener("change", on);
  }, []);
  return portrait;
}
