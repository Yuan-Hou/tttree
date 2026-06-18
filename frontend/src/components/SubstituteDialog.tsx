import { useEffect, useState } from "react";
import { imgUrl } from "../api";
import type { PastImage } from "../types";
import { Button } from "./ui";

interface Props {
  pastImages: PastImage[];
  busy?: boolean;
  onClose: () => void;
  /** 来源二选一:从过往结果选(imagegenId)或上传新图(file)。 */
  onSubmit: (pick: { imagegenId?: number; file?: File }) => void;
}

/** 替代图片:不调 gpt-image-2,由用户直接指定一张图作为本次出图结果。
 *  两类来源:① 从过往生成结果里选一张(全列,不按轮截断);② 上传一张新图。
 *  选图/上传的动作本身即确认 —— 因为没花钱,无需再过确认闸门。 */
export function SubstituteDialog({ pastImages, busy, onClose, onSubmit }: Props) {
  const [picked, setPicked] = useState<number | null>(null);
  const [file, setFile] = useState<File | null>(null);

  useEffect(() => {
    const h = (e: KeyboardEvent) => e.key === "Escape" && !busy && onClose();
    window.addEventListener("keydown", h);
    return () => window.removeEventListener("keydown", h);
  }, [busy, onClose]);

  const canSubmit = !busy && (picked != null || file != null);
  const submit = () => {
    if (file) onSubmit({ file });
    else if (picked != null) onSubmit({ imagegenId: picked });
  };

  return (
    <div className="fixed inset-0 z-[60] flex items-center justify-center bg-ink/25 p-6 backdrop-blur-[2px]" onClick={() => !busy && onClose()}>
      <div
        className="flex max-h-[80vh] w-[560px] max-w-full flex-col overflow-hidden rounded-2xl border border-line bg-surface shadow-xl"
        onClick={(e) => e.stopPropagation()}
      >
        <div className="border-b border-line px-5 py-3.5">
          <div className="font-serif text-[15px] text-ink">替代图片(不花钱)</div>
          <div className="mt-0.5 text-[11.5px] text-ink-faint">
            直接指定一张图作为本次出图结果,跳过 gpt-image-2。归属、取代规则与真实出图一致;选图/上传即确认。
          </div>
        </div>

        <div className="flex min-h-0 flex-1 flex-col gap-3 overflow-y-auto px-5 py-4">
          {/* 来源二:上传新图(放前面,选中后清空过往选择) */}
          <Label>上传一张新图</Label>
          <label className="flex cursor-pointer items-center gap-2 rounded-lg border border-dashed border-line-strong bg-paper px-3 py-2.5 text-[12px] text-ink-soft hover:border-accent">
            <span className="rounded bg-sunken px-2 py-1 font-mono text-[11px]">选择文件</span>
            <span className="truncate">{file ? file.name : "未选择(PNG / JPG)"}</span>
            <input
              type="file"
              accept="image/*"
              className="hidden"
              disabled={busy}
              onChange={(e) => {
                const f = e.target.files?.[0] ?? null;
                setFile(f);
                if (f) setPicked(null); // 与过往选择互斥
              }}
            />
          </label>

          {/* 来源一:从过往生成结果选 */}
          <Label>或 从过往生成结果选一张({pastImages.length})</Label>
          {pastImages.length === 0 ? (
            <p className="text-[11.5px] text-ink-faint">还没有画过任何图。</p>
          ) : (
            <div className="flex flex-wrap gap-2">
              {pastImages.map((g) => {
                const on = picked === g.imagegen_id;
                return (
                  <button
                    key={g.imagegen_id}
                    disabled={busy}
                    onClick={() => {
                      setPicked(on ? null : g.imagegen_id);
                      if (!on) setFile(null); // 与上传互斥
                    }}
                    className="w-[84px] text-left"
                    title={on ? "已选,点击取消" : "点击选中"}
                  >
                    <img
                      src={imgUrl(g.output_path)}
                      alt={g.scene_slug}
                      className={`h-[56px] w-[84px] rounded-md border object-cover ${on ? "border-accent ring-1 ring-accent" : "border-line"}`}
                    />
                    <div className="mt-0.5 truncate text-[10px] text-ink-soft">{g.scene_slug}</div>
                    <div className="truncate text-[9px] text-ink-faint">{g.kind}</div>
                  </button>
                );
              })}
            </div>
          )}
        </div>

        <div className="flex items-center justify-end gap-2 border-t border-line px-5 py-3">
          <Button variant="quiet" disabled={busy} onClick={onClose}>
            取消
          </Button>
          <Button variant="primary" disabled={!canSubmit} onClick={submit}>
            {busy ? "应用中…" : "▣ 用这张图作为结果"}
          </Button>
        </div>
      </div>
    </div>
  );
}

const Label = ({ children }: { children: React.ReactNode }) => (
  <div className="mt-1 font-mono text-[10.5px] uppercase tracking-[0.14em] text-ink-faint">{children}</div>
);
