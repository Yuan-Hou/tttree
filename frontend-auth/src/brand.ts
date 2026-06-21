// 品牌名「{Name} Tree」(留空 → 仅「Tree」)。Name 来自后端 /brand(SITE_NAME),同步后本地缓存。
// 与创作台共用 localStorage key "vore_brand_name"。

const KEY = "vore_brand_name";

export function brandTitle(): string {
  const n = (localStorage.getItem(KEY) || "").trim();
  return n ? `${n} Tree` : "Tree";
}

/** 拉取并缓存品牌名;成功返回 true(供调用方触发重渲染)。 */
export async function syncBrand(): Promise<boolean> {
  try {
    const r = await fetch("/brand");
    if (!r.ok) return false;
    const d = await r.json();
    if (d?.name != null) {
      localStorage.setItem(KEY, d.name);
      return true;
    }
  } catch {
    /* 离线/未就绪:用缓存兜底 */
  }
  return false;
}
