import { useEffect, useState } from "react";
import { getStoryProposals, imgUrl } from "../api";
import type { ProposalRow, ProposalsResp, SceneMeta } from "../types";
import { useLightbox } from "./Lightbox";
import { PictureNodeEditor } from "./PictureNodeEditor";
import { Eyebrow, Tag } from "./ui";

interface Props {
  storyId: string;
  drawsVersion: number;
  onReload: () => void; // 出图后刷新(bump drawsVersion + 快照)
}

/** 绘图台:导演 B 提案的正典待办,按场景聚合。挑一条 pending/done → 在此走画图节点(写稿若缺→出图,
 *  含参考图自由选择)。门控:variant 无基底灰禁;重绘 new_scene 带警告。
 *  注:用户手动绘图稿不在这里,独立于「手动绘图 · 私人草稿」(ManualDeck)。 */
export function DrawDeck(p: Props) {
  const [data, setData] = useState<ProposalsResp | null>(null);
  const [sel, setSel] = useState<{ id: number; scene: string } | null>(null);

  useEffect(() => {
    let alive = true;
    getStoryProposals(p.storyId).then((d) => alive && setData(d)).catch(() => alive && setData(null));
    return () => {
      alive = false;
    };
  }, [p.storyId, p.drawsVersion]);

  const groups = new Map<string, ProposalRow[]>();
  for (const pr of data?.proposals ?? []) {
    if (!groups.has(pr.scene_slug)) groups.set(pr.scene_slug, []);
    groups.get(pr.scene_slug)!.push(pr);
  }

  return (
    <section className="px-6 py-5">
      <Eyebrow>绘图台 · 按场景(正典)</Eyebrow>

      {/* 选中的待办 → 画图节点(写稿若缺→出图 + 参考图自由选择) */}
      {sel && (
        <div className="mt-3.5 overflow-hidden rounded-xl border border-accent/30 bg-surface">
          <div className="flex items-center justify-between border-b border-line px-3 py-1.5">
            <span className="font-mono text-[11px] text-accent-ink">出图审阅 · {sel.scene}</span>
            <button onClick={() => setSel(null)} className="text-ink-faint hover:text-ink">收起 ✕</button>
          </div>
          <PictureNodeEditor
            key={sel.id}
            storyId={p.storyId}
            proposalId={sel.id}
            canAct
            onDone={p.onReload}
          />
        </div>
      )}

      {groups.size === 0 ? (
        <p className="mt-3 text-[13px] leading-relaxed text-ink-faint">
          还没有绘图待办。推进剧情时导演 B 会提配图建议,积压到这里按场景待画。
        </p>
      ) : (
        <div className="mt-3.5 flex flex-col gap-3.5">
          {[...groups.entries()].map(([slug, rows]) => (
            <SceneGroup key={slug} slug={slug} meta={data!.scenes[slug]} rows={rows} onPick={(id) => setSel({ id, scene: slug })} />
          ))}
        </div>
      )}
    </section>
  );
}

function SceneGroup({ slug, meta, rows, onPick }: { slug: string; meta?: SceneMeta; rows: ProposalRow[]; onPick: (id: number) => void }) {
  return (
    <div className="rounded-xl border border-line bg-paper p-3">
      <div className="flex items-baseline gap-2">
        <span className="text-[13.5px] font-medium text-ink">{meta?.name ?? slug}</span>
        <Tag>{slug}</Tag>
        {meta && !meta.has_new_scene && <span className="font-mono text-[10px] text-ink-faint">无基底</span>}
      </div>
      <div className="mt-2.5 flex flex-col gap-1.5">
        {rows.map((r) => (
          <TodoRow key={r.id} row={r} meta={meta} onPick={onPick} />
        ))}
      </div>
    </div>
  );
}

function TodoRow({ row, meta, onPick }: { row: ProposalRow; meta?: SceneMeta; onPick: (id: number) => void }) {
  const lightbox = useLightbox();
  const gatedVariant = row.kind === "variant" && !(meta?.has_new_scene ?? false);
  const done = row.status === "done";
  return (
    <div className="flex items-center gap-2.5 rounded-lg border border-line bg-surface px-2.5 py-2">
      {done && row.done_image_path ? (
        <img
          src={imgUrl(row.done_image_path)}
          alt=""
          onClick={() => lightbox(imgUrl(row.done_image_path!), row.scene_slug)}
          className="h-9 w-14 shrink-0 cursor-zoom-in rounded-md border border-line object-cover"
        />
      ) : (
        <span className={`h-2 w-2 shrink-0 rounded-full ${done ? "bg-accent" : gatedVariant ? "bg-line-strong" : "border border-line-strong"}`} />
      )}
      <div className="min-w-0 flex-1">
        <div className="flex items-center gap-1.5">
          <Tag tone={row.kind === "new_scene" ? "accent" : "soft"}>{row.kind}</Tag>
          <span className="font-mono text-[10.5px] text-ink-faint">来自第 {row.origin_proposal_turn} 轮</span>
          <span className={`font-mono text-[10.5px] ${done ? "text-accent-ink" : "text-ink-faint"}`}>· {done ? "已画" : "待绘制"}</span>
        </div>
        {gatedVariant && <div className="mt-0.5 text-[10.5px] text-ink-faint">需先绘制 new_scene 基底</div>}
      </div>
      <button
        disabled={gatedVariant}
        onClick={() => onPick(row.id)}
        className="shrink-0 rounded-lg border border-line-strong bg-surface px-2.5 py-1 text-[11.5px] text-ink-soft transition hover:border-accent hover:text-accent-ink disabled:cursor-not-allowed disabled:opacity-40"
        title={gatedVariant ? "需先绘制基底" : done ? "重绘(会触发基底/连贯性提示)" : "去画"}
      >
        {done ? "重绘" : "画"}
      </button>
    </div>
  );
}
