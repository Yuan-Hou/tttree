import { useEffect, useState } from "react";
import * as api from "../api";
import { imgUrl } from "../api";
import type { DraftRef, PickedRef } from "../types";
import { useProposalDraw } from "../useProposalDraw";
import { useLightbox } from "./Lightbox";
import { RefPicker } from "./RefPicker";
import { Button } from "./ui";

interface Props {
  storyId: string;
  proposalId: number;
  canAct: boolean;
  onDone: () => void; // 出图成功 → 刷新绘图台/显微镜/快照
  onGenerating?: (scene: string, on: boolean) => void; // 点亮显微镜绘图节点
}

const toPicked = (m: DraftRef[]): PickedRef[] =>
  m.map((r) =>
    r.source === "reference_asset"
      ? { source: "reference_asset", asset_id: r.asset_id, semantic_name: r.semantic_name, purpose: r.purpose }
      : { source: "history_image", image_path: r.image_path, semantic_name: r.semantic_name, purpose: r.purpose },
  );

/** 画图节点(gpt-image-2):输入 = 写稿的提示词(可编辑)+ 自由选择的参考图(两类来源);
 *  输出 = 生成的图;重试 = 只用当前提示词+参考图重新出图,不回去重写提示词。 */
export function PictureNodeEditor({ storyId, proposalId, canAct, onDone, onGenerating }: Props) {
  const { data, loading, reload } = useProposalDraw(storyId, proposalId);
  const lightbox = useLightbox();
  const [prompt, setPrompt] = useState("");
  const [refs, setRefs] = useState<PickedRef[]>([]);
  const [busy, setBusy] = useState<null | "writing" | "generating">(null);
  const [error, setError] = useState<string | null>(null);
  const [freshImage, setFreshImage] = useState<string | null>(null);

  // 写稿数据到位后,初始化提示词与参考图选择(以绘图 Agent 建议为起点)
  useEffect(() => {
    if (!data) return;
    setPrompt(data.draft_prompt);
    setRefs(toPicked(data.draft_manifest));
    setFreshImage(null);
    setError(null);
  }, [data?.proposal_id, data?.draft_prompt]);

  if (loading && !data) return <Shell title="画图"><Dim>读取中…</Dim></Shell>;
  if (!data) return <Shell title="画图"><Dim>—</Dim></Shell>;

  const currentImage = freshImage ?? data.done_image_path ?? null;
  const written = Boolean(data.draft_prompt);

  const ensureDraft = async () => {
    setBusy("writing");
    setError(null);
    try {
      await api.writeDraft(storyId, proposalId);
      await reload();
    } catch (e) {
      setError(String(e));
    } finally {
      setBusy(null);
    }
  };

  const picture = async () => {
    setBusy("generating");
    setError(null);
    onGenerating?.(data.scene_slug, true);
    try {
      await api.pictureDraw(storyId, proposalId, { prompt, references: refs }, (ev) => {
        if (ev.type === "image_ready") {
          setFreshImage(ev.image_path);
          onDone();
          reload();
        } else if (ev.type === "image_failed") setError(ev.reason);
      });
    } catch (e) {
      setError(String(e));
    } finally {
      onGenerating?.(data.scene_slug, false);
      setBusy(null);
    }
  };

  return (
    <Shell
      title="画图(gpt-image-2)"
      note="据写稿的提示词 + 自由选择的参考图出图;确认即花钱、无旁路。"
      scene={data.scene_slug}
      kind={data.kind}
    >
      {currentImage && (
        <div>
          <Label>{freshImage ? "刚出的图" : "已出的图"}</Label>
          <img
            src={imgUrl(currentImage)}
            alt={data.scene_slug}
            onClick={() => lightbox(imgUrl(currentImage), data.scene_slug)}
            className="surface-in w-full cursor-zoom-in rounded-[10px] border border-line"
          />
        </div>
      )}

      {data.warn_redraw_base && (
        <div className="rounded-lg border border-danger/40 bg-danger-soft px-3 py-2 text-[12px] leading-snug text-danger">
          ⚠ 重绘基底:本场景已有 variant 变体。重绘 new_scene 会让已有变体的基底改变、可能不连贯;旧变体保留。
        </div>
      )}

      {!written ? (
        <div className="flex flex-col gap-2">
          <p className="rounded-lg border border-dashed border-accent/40 bg-accent-soft/40 px-3 py-2 text-[12px] text-accent-ink">
            还没有提示词稿。画图节点的输入来自写稿节点 —— 先让绘图 Agent 写稿。
          </p>
          <Button variant="ghost" disabled={!canAct || busy !== null} onClick={ensureDraft}>
            {busy === "writing" ? "写稿中…" : "✎ 让绘图 Agent 写稿"}
          </Button>
        </div>
      ) : (
        <>
          <Label>提示词(可编辑)</Label>
          <textarea
            value={prompt}
            onChange={(e) => setPrompt(e.target.value)}
            rows={5}
            readOnly={!canAct}
            spellCheck={false}
            className="w-full resize-y rounded-lg border border-line-strong bg-paper px-3 py-2 font-mono text-[12px] leading-relaxed text-ink focus:border-accent focus:outline-none read-only:text-ink-soft"
          />

          <Label>参考图(自由选择 · 可增删)</Label>
          <RefPicker library={data.library} pastImages={data.past_images} value={refs} onChange={setRefs} />

          {data.variant_gated && (
            <p className="rounded-lg bg-danger-soft px-3 py-2 text-[12px] text-danger">variant 需先绘制该场景的 new_scene 基底,才能变体。</p>
          )}

          {canAct && (
            <div className="flex items-center gap-2">
              <Button variant="primary" disabled={busy !== null || data.variant_gated} onClick={picture}>
                {busy === "generating"
                  ? "出图中(约1分钟)…"
                  : data.warn_redraw_base
                    ? "❖ 我已知晓,重绘(花钱)"
                    : currentImage
                      ? "❖ 重新出图(花钱)"
                      : "❖ 出图(花钱)"}
              </Button>
              <span className="font-mono text-[10px] text-ink-faint">重试只重画,不重写词</span>
            </div>
          )}
        </>
      )}

      {error && <p className="rounded-lg bg-danger-soft px-3 py-2 text-[12px] text-danger">出错:{error}</p>}
    </Shell>
  );
}

function Shell({ title, note, scene, kind, children }: { title: string; note?: string; scene?: string; kind?: string; children: React.ReactNode }) {
  return (
    <div className="flex min-h-0 flex-1 flex-col">
      <div className="border-b border-line px-5 py-3.5">
        <div className="flex items-baseline gap-2">
          <span className="font-serif text-[15px] text-ink">{title}</span>
          {scene && <span className="rounded-[5px] bg-accent-soft px-1.5 py-px font-mono text-[10.5px] text-accent-ink">{scene}</span>}
          {kind && <span className="rounded-[5px] bg-sunken px-1.5 py-px font-mono text-[10.5px] text-ink-soft">{kind}</span>}
        </div>
        {note && <div className="mt-0.5 text-[11.5px] text-ink-faint">{note}</div>}
      </div>
      <div className="flex min-h-0 flex-1 flex-col gap-3 overflow-y-auto px-5 py-4">{children}</div>
    </div>
  );
}
const Label = ({ children }: { children: React.ReactNode }) => (
  <div className="mt-1 font-mono text-[10.5px] uppercase tracking-[0.14em] text-ink-faint">{children}</div>
);
const Dim = ({ children }: { children: React.ReactNode }) => <span className="text-[12.5px] text-ink-faint">{children}</span>;
