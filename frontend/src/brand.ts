// 站点品牌名「{Name} Tree」。Name 来自后端 /brand(部署时由 SITE_NAME 环境变量决定)。
// 同步后写入 localStorage:下次加载直接取缓存、不必等请求(首屏不闪、离线也可用),再后台刷新。
import { useEffect, useState } from "react";

const KEY = "vore_brand_name";

export const getBrandName = (): string => localStorage.getItem(KEY) || "";
/** 品牌标题:有名 → 「{Name} Tree」,留空 → 仅「Tree」。 */
export const titleOf = (name: string): string => (name.trim() ? `${name.trim()} Tree` : "Tree");
export const getBrandTitle = (): string => titleOf(getBrandName());

/** 从后端同步品牌名并落本地缓存。失败/离线 → 沿用缓存。返回当前名。 */
export async function syncBrand(): Promise<string> {
  try {
    const r = await fetch("/brand");
    if (r.ok) {
      const data = (await r.json()) as { name?: string };
      if (data.name) localStorage.setItem(KEY, data.name);
    }
  } catch {
    // 沿用缓存
  }
  return getBrandName();
}

/** 品牌标题「{Name} Tree」:首渲染用本地缓存,挂载后从后端同步并更新。 */
export function useBrandTitle(): string {
  const [name, setName] = useState(getBrandName());
  useEffect(() => {
    let alive = true;
    syncBrand().then((n) => alive && setName(n));
    return () => {
      alive = false;
    };
  }, []);
  return titleOf(name);
}
