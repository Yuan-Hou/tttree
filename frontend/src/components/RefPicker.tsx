import { imgUrl } from "../api";
import type { LibraryAsset, PastImage, PickedRef } from "../types";

interface Props {
  library: LibraryAsset[];
  pastImages: PastImage[];
  value: PickedRef[];
  onChange: (v: PickedRef[]) => void;
}

const same = (a: PickedRef, b: PickedRef) =>
  a.source === b.source &&
  (a.source === "reference_asset" ? a.asset_id === b.asset_id : a.image_path === b.image_path);

/** 参考图自由选择:两类来源(图库 ReferenceAsset + 过往绘制结果 ImageGen.output)。
 *  初始为绘图 Agent 建议的清单,用户可在此增删;过往结果不按轮截断,整故事历史图都可选。 */
export function RefPicker({ library, pastImages, value, onChange }: Props) {
  const has = (r: PickedRef) => value.some((v) => same(v, r));
  const add = (r: PickedRef) => !has(r) && onChange([...value, r]);
  const remove = (r: PickedRef) => onChange(value.filter((v) => !same(v, r)));

  const preview = (r: PickedRef) =>
    r.source === "reference_asset"
      ? library.find((a) => a.asset_id === r.asset_id)?.file_path
      : r.image_path ?? undefined;

  return (
    <div className="flex flex-col gap-2.5">
      {/* 已选 */}
      <div>
        <Label>已选参考图({value.length})</Label>
        {value.length === 0 ? (
          <p className="text-[11.5px] text-ink-faint">无参考图(纯文生图)。下方可从两类来源添加。</p>
        ) : (
          <div className="mt-1 flex flex-wrap gap-2">
            {value.map((r, i) => {
              const src = preview(r);
              return (
                <div key={i} className="relative w-[72px]" title={r.purpose}>
                  {src ? (
                    <img src={imgUrl(src)} alt={r.semantic_name} className="h-[48px] w-[72px] rounded-md border border-accent/40 object-cover" />
                  ) : (
                    <div className="flex h-[48px] w-[72px] items-center justify-center rounded-md border border-dashed border-line-strong text-[10px] text-ink-faint">无图</div>
                  )}
                  <button
                    onClick={() => remove(r)}
                    className="absolute -right-1.5 -top-1.5 flex h-4 w-4 items-center justify-center rounded-full border border-line-strong bg-surface text-[10px] text-ink-soft hover:text-danger"
                    title="移除"
                  >
                    ✕
                  </button>
                  <div className="mt-0.5 truncate text-[10px] text-ink-soft">{r.semantic_name}</div>
                </div>
              );
            })}
          </div>
        )}
      </div>

      {/* 来源一:图库 */}
      <details className="rounded-lg border border-line bg-paper">
        <summary className="cursor-pointer px-3 py-1.5 text-[11px] text-ink-soft">＋ 从参考图库添加({library.length})</summary>
        <div className="flex flex-wrap gap-2 px-3 pb-3 pt-1">
          {library.length === 0 && <span className="text-[11px] text-ink-faint">图库为空。</span>}
          {library.map((a) => {
            const r: PickedRef = { source: "reference_asset", asset_id: a.asset_id, semantic_name: a.label, purpose: a.description || "参考" };
            return <Thumb key={a.asset_id} src={a.file_path} label={a.label} sub={a.category} on={has(r)} onClick={() => (has(r) ? remove(r) : add(r))} />;
          })}
        </div>
      </details>

      {/* 来源二:过往绘制结果(整故事,不按轮截断) */}
      <details className="rounded-lg border border-line bg-paper">
        <summary className="cursor-pointer px-3 py-1.5 text-[11px] text-ink-soft">＋ 从过往绘制结果添加({pastImages.length})</summary>
        <div className="flex flex-wrap gap-2 px-3 pb-3 pt-1">
          {pastImages.length === 0 && <span className="text-[11px] text-ink-faint">还没有画过任何图。</span>}
          {pastImages.map((g) => {
            const r: PickedRef = { source: "history_image", image_path: g.output_path, semantic_name: `${g.scene_slug}·${g.kind}`, purpose: "保持视觉连贯" };
            return <Thumb key={g.imagegen_id} src={g.output_path} label={g.scene_slug} sub={g.kind} on={has(r)} onClick={() => (has(r) ? remove(r) : add(r))} />;
          })}
        </div>
      </details>
    </div>
  );
}

function Thumb({ src, label, sub, on, onClick }: { src: string; label: string; sub: string; on: boolean; onClick: () => void }) {
  return (
    <button onClick={onClick} className={`w-[72px] text-left ${on ? "opacity-100" : "opacity-90 hover:opacity-100"}`} title={on ? "已选,点击移除" : "点击添加"}>
      <div className="relative">
        <img src={imgUrl(src)} alt={label} className={`h-[48px] w-[72px] rounded-md border object-cover ${on ? "border-accent ring-1 ring-accent" : "border-line"}`} />
        {on && <span className="absolute right-0.5 top-0.5 rounded bg-accent px-1 text-[9px] text-white">已选</span>}
      </div>
      <div className="mt-0.5 truncate text-[10px] text-ink-soft">{label}</div>
      <div className="truncate text-[9px] text-ink-faint">{sub}</div>
    </button>
  );
}

const Label = ({ children }: { children: React.ReactNode }) => (
  <div className="font-mono text-[10.5px] uppercase tracking-[0.14em] text-ink-faint">{children}</div>
);
