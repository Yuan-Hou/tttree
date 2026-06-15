import { useEffect, useRef, useState } from "react";
import { addReference, deleteReference, imgUrl, listReferences, updateReference } from "../api";
import type { LibraryAsset } from "../types";
import { useLightbox } from "./Lightbox";
import { Button, Tag } from "./ui";

const CATEGORIES = ["角色", "物品", "场景氛围", "其他"] as const;

/** 图库编辑(故事内设置 · 子步二):列出该故事参考图、上传新图、改说明/类别、删除。
 *  复用 M4.5-E 的 CRUD 接口。参考图是绘图 Agent 锚定角色/物品跨图一致性的素材。 */
export function GalleryEditor({ storyId }: { storyId: string }) {
  const [assets, setAssets] = useState<LibraryAsset[] | null>(null);
  const [err, setErr] = useState<string | null>(null);

  const reload = () =>
    listReferences(storyId)
      .then(setAssets)
      .catch((e) => setErr(String(e)));

  useEffect(() => {
    setAssets(null);
    setErr(null);
    let alive = true;
    listReferences(storyId)
      .then((a) => alive && setAssets(a))
      .catch((e) => alive && setErr(String(e)));
    return () => {
      alive = false;
    };
  }, [storyId]);

  return (
    <div className="flex min-h-0 flex-1 flex-col gap-4">
      <UploadForm storyId={storyId} onAdded={reload} />

      {err && <p className="rounded-lg bg-danger-soft px-3 py-2 text-[12px] text-danger">出错:{err}</p>}

      <div className="min-h-0 flex-1 overflow-y-auto pr-1">
        {assets === null ? (
          <div className="py-8 text-center text-[13px] text-ink-faint">载入中…</div>
        ) : assets.length === 0 ? (
          <div className="py-8 text-center text-[13px] text-ink-faint">
            还没有参考图。上传角色立绘、物品造型或场景氛围图,绘图时 AI 会据「语义名 + 说明」决定何时引用。
          </div>
        ) : (
          <div className="flex flex-col gap-2.5">
            {assets.map((a) => (
              <RefCard key={a.asset_id} storyId={storyId} asset={a} onChanged={reload} />
            ))}
          </div>
        )}
      </div>
    </div>
  );
}

function UploadForm({ storyId, onAdded }: { storyId: string; onAdded: () => void }) {
  const [file, setFile] = useState<File | null>(null);
  const [label, setLabel] = useState("");
  const [category, setCategory] = useState<string>("角色");
  const [description, setDescription] = useState("");
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const fileRef = useRef<HTMLInputElement>(null);
  const preview = file ? URL.createObjectURL(file) : null;

  const reset = () => {
    setFile(null);
    setLabel("");
    setDescription("");
    setCategory("角色");
    if (fileRef.current) fileRef.current.value = "";
  };

  const submit = async () => {
    if (!file || !label.trim()) return;
    setBusy(true);
    setErr(null);
    try {
      await addReference(storyId, { file, label: label.trim(), description: description.trim(), category });
      reset();
      onAdded();
    } catch (e) {
      setErr(String(e));
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="rounded-xl border border-line bg-paper p-3.5">
      <div className="font-mono text-[10.5px] uppercase tracking-[0.14em] text-ink-faint">上传参考图</div>
      <div className="mt-2.5 flex gap-3.5">
        <label className="group flex h-[88px] w-[120px] shrink-0 cursor-pointer items-center justify-center overflow-hidden rounded-lg border border-dashed border-line-strong bg-surface transition hover:border-accent">
          {preview ? (
            <img src={preview} alt="" className="h-full w-full object-cover" />
          ) : (
            <span className="text-[12px] text-ink-faint group-hover:text-accent-ink">选择图片</span>
          )}
          <input
            ref={fileRef}
            type="file"
            accept="image/*"
            className="hidden"
            onChange={(e) => setFile(e.target.files?.[0] ?? null)}
          />
        </label>

        <div className="flex min-w-0 flex-1 flex-col gap-2">
          <div className="flex gap-2">
            <input
              value={label}
              onChange={(e) => setLabel(e.target.value)}
              placeholder="语义名(如「主角立绘」)"
              className="min-w-0 flex-1 rounded-lg border border-line-strong bg-surface px-2.5 py-1.5 text-[13px] text-ink focus:border-accent focus:outline-none"
            />
            <select
              value={category}
              onChange={(e) => setCategory(e.target.value)}
              className="rounded-lg border border-line-strong bg-surface px-2 py-1.5 text-[12.5px] text-ink-soft focus:border-accent focus:outline-none"
            >
              {CATEGORIES.map((c) => (
                <option key={c} value={c}>
                  {c}
                </option>
              ))}
            </select>
          </div>
          <input
            value={description}
            onChange={(e) => setDescription(e.target.value)}
            placeholder="说明 — 供绘图 AI 判断何时引用此图(如「蓝发束发、左眉疤」)"
            className="rounded-lg border border-line-strong bg-surface px-2.5 py-1.5 text-[12.5px] text-ink focus:border-accent focus:outline-none"
          />
        </div>
      </div>
      {err && <p className="mt-2 rounded-lg bg-danger-soft px-3 py-1.5 text-[12px] text-danger">{err}</p>}
      <div className="mt-2.5 flex items-center gap-3">
        <Button variant="primary" disabled={!file || !label.trim() || busy} onClick={submit}>
          {busy ? "上传中…" : "上传"}
        </Button>
        {file && (
          <button onClick={reset} className="text-[12px] text-ink-faint hover:text-ink">
            清除
          </button>
        )}
      </div>
    </div>
  );
}

function RefCard({ storyId, asset, onChanged }: { storyId: string; asset: LibraryAsset; onChanged: () => void }) {
  const lightbox = useLightbox();
  const [label, setLabel] = useState(asset.label);
  const [description, setDescription] = useState(asset.description);
  const [category, setCategory] = useState(asset.category);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);

  const dirty = label !== asset.label || description !== asset.description || category !== asset.category;

  const save = async () => {
    setBusy(true);
    setErr(null);
    try {
      await updateReference(storyId, asset.asset_id, { label: label.trim(), description, category });
      onChanged();
    } catch (e) {
      setErr(String(e));
    } finally {
      setBusy(false);
    }
  };

  const remove = async () => {
    if (!confirm(`删除参考图「${asset.label}」?此操作不可撤销。`)) return;
    setBusy(true);
    try {
      await deleteReference(storyId, asset.asset_id);
      onChanged();
    } catch (e) {
      setErr(String(e));
      setBusy(false);
    }
  };

  return (
    <div className="flex gap-3 rounded-xl border border-line bg-surface p-2.5">
      <img
        src={imgUrl(asset.file_path)}
        alt={asset.label}
        onClick={() => lightbox(imgUrl(asset.file_path), asset.label)}
        className="h-[76px] w-[104px] shrink-0 cursor-zoom-in rounded-lg border border-line object-cover"
      />
      <div className="flex min-w-0 flex-1 flex-col gap-1.5">
        <div className="flex gap-2">
          <input
            value={label}
            onChange={(e) => setLabel(e.target.value)}
            className="min-w-0 flex-1 rounded-md border border-transparent bg-paper px-2 py-1 text-[13px] font-medium text-ink hover:border-line focus:border-accent focus:outline-none"
          />
          <select
            value={category}
            onChange={(e) => setCategory(e.target.value)}
            className="rounded-md border border-transparent bg-paper px-1.5 py-1 text-[11.5px] text-ink-soft hover:border-line focus:border-accent focus:outline-none"
          >
            {CATEGORIES.map((c) => (
              <option key={c} value={c}>
                {c}
              </option>
            ))}
          </select>
        </div>
        <textarea
          value={description}
          onChange={(e) => setDescription(e.target.value)}
          rows={2}
          placeholder="说明(供 AI 判断何时引用)"
          className="resize-none rounded-md border border-transparent bg-paper px-2 py-1 text-[12px] leading-snug text-ink-soft hover:border-line focus:border-accent focus:outline-none"
        />
        {err && <p className="text-[11.5px] text-danger">{err}</p>}
        <div className="flex items-center gap-2">
          <Tag>#{asset.asset_id}</Tag>
          {dirty && (
            <Button variant="ghost" className="px-2 py-1 text-[11.5px]" disabled={busy} onClick={save}>
              {busy ? "保存中…" : "保存改动"}
            </Button>
          )}
          <button
            onClick={remove}
            disabled={busy}
            className="ml-auto rounded-md px-2 py-1 text-[11.5px] text-ink-faint transition hover:bg-danger-soft hover:text-danger disabled:opacity-40"
          >
            删除
          </button>
        </div>
      </div>
    </div>
  );
}
