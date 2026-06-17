import { imgUrl } from "../api";
import type { Blackboard } from "../types";
import type { PendingImage } from "../useStoryEngine";
import { useLightbox } from "./Lightbox";
import { Button, Eyebrow, Tag } from "./ui";

interface Props {
  blackboard: Blackboard;
  scenesImages: Record<string, string[]>;
  scenesDrafts: Record<string, string[]>; // 手动草稿图(非正式,不进黑板)
  supersededImages?: string[]; // 被取代的正典图路径:仍在画廊,标「被覆盖」
  pending: PendingImage[];
  onDraw: (slug: string) => void;
}

export function ScenesPanel({ blackboard, scenesImages, scenesDrafts, supersededImages, pending, onDraw }: Props) {
  const lightbox = useLightbox();
  const superseded = new Set(supersededImages ?? []);
  const scenes = blackboard.scenes ?? {};
  const slugs = Array.from(
    new Set([...Object.keys(scenes), ...Object.keys(scenesImages), ...Object.keys(scenesDrafts)]),
  );
  const current = blackboard.story_meta?.current_scene;

  return (
    <section className="border-b border-line px-6 py-5">
      <Eyebrow>场景与画</Eyebrow>

      {slugs.length === 0 ? (
        <p className="mt-3 text-[13px] text-ink-faint">尚无场景,先推进剧情。</p>
      ) : (
        <div className="mt-3.5 flex flex-col gap-3">
          {slugs.map((slug) => {
            const sc = scenes[slug] ?? {};
            const imgs = scenesImages[slug] ?? sc.image_paths ?? [];
            const drafts = scenesDrafts[slug] ?? [];
            const waiting = pending.filter((p) => p.scene === slug);
            return (
              <div
                key={slug}
                className={`rounded-xl border bg-paper p-3 ${
                  slug === current ? "border-accent/40" : "border-line"
                }`}
              >
                <div className="flex items-baseline gap-2">
                  <span className="text-[13.5px] font-medium text-ink">{sc.name ?? slug}</span>
                  <Tag>{slug}</Tag>
                  {slug === current && <Tag tone="accent">此刻在此</Tag>}
                </div>
                {sc.state && (
                  <p className="mt-1.5 line-clamp-2 text-[12px] leading-snug text-ink-soft">
                    {sc.state}
                  </p>
                )}

                {(imgs.length > 0 || drafts.length > 0 || waiting.length > 0) && (
                  <div className="mt-2.5 flex flex-col gap-2">
                    {imgs.map((p) =>
                      superseded.has(p) ? (
                        // 被覆盖:同场景同轮被更新的图重绘后,旧图退出正典/候选池,留作历史。灰化以与正典、手动草稿三态可辨。
                        <div key={p} className="relative">
                          <img
                            src={imgUrl(p)}
                            alt={`${sc.name ?? slug}(被覆盖)`}
                            onClick={() => lightbox([{ src: imgUrl(p), alt: `${sc.name ?? slug} · 被覆盖` }], 0)}
                            className="surface-in w-full cursor-zoom-in rounded-[10px] border border-line opacity-55 grayscale"
                          />
                          <span className="absolute left-2 top-2 rounded-[5px] bg-ink-soft/85 px-1.5 py-px font-mono text-[10px] text-paper">
                            被覆盖
                          </span>
                        </div>
                      ) : (
                        <div key={p} className="relative">
                          <img
                            src={imgUrl(p)}
                            alt={sc.name ?? slug}
                            onClick={() => lightbox([{ src: imgUrl(p), alt: sc.name ?? slug }], 0)}
                            className="surface-in w-full cursor-zoom-in rounded-[10px] border border-line"
                          />
                          <span className="absolute left-2 top-2 rounded-[5px] bg-accent/85 px-1.5 py-px font-mono text-[10px] text-white">
                            正典
                          </span>
                        </div>
                      ),
                    )}
                    {waiting.map((w) => (
                      <Placeholder key={w.request_id} pending={w} />
                    ))}
                    {/* 手动草稿:视觉明确「非正式」,与进黑板的正典图区分 */}
                    {drafts.map((p) => (
                      <div key={p} className="relative">
                        <img
                          src={imgUrl(p)}
                          alt={`${sc.name ?? slug}(非正式)`}
                          onClick={() => lightbox([{ src: imgUrl(p), alt: `${sc.name ?? slug} · 手动草稿` }], 0)}
                          className="surface-in w-full cursor-zoom-in rounded-[10px] border border-dashed border-line-strong opacity-90"
                        />
                        <span className="absolute left-2 top-2 rounded-[5px] bg-ink/70 px-1.5 py-px font-mono text-[10px] text-paper">
                          非正式 · 手动草稿
                        </span>
                      </div>
                    ))}
                  </div>
                )}

                <div className="mt-2.5">
                  <Button variant="quiet" onClick={() => onDraw(slug)} className="px-2 py-1 text-[12px]">
                    ✎ 画这个场景
                  </Button>
                </div>
              </div>
            );
          })}
        </div>
      )}
    </section>
  );
}

function Placeholder({ pending }: { pending: PendingImage }) {
  if (pending.status === "failed") {
    return (
      <div className="rounded-[10px] border border-danger/40 bg-danger-soft px-3 py-4 text-center text-[12px] text-danger">
        ✕ 出图失败 · {pending.reason}
      </div>
    );
  }
  return (
    <div className="flex aspect-[3/2] flex-col items-center justify-center gap-1 rounded-[10px] border border-dashed border-accent/40 bg-accent-soft/40">
      <span className="breathe font-mono text-[12px] text-accent-ink">⟳ 生成中</span>
      <span className="font-mono text-[10px] text-ink-faint">约一分钟 · 可继续推进</span>
    </div>
  );
}
