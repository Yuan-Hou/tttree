import { useEffect, useRef, useState } from "react";
import * as api from "../api";
import { getStoryProposals, imgUrl } from "../api";
import type { ProposalRow, ProposalsResp, SceneMeta, TurnSceneOpt } from "../types";
import { useLightbox } from "./Lightbox";
import { PictureNodeEditor } from "./PictureNodeEditor";
import { SubstituteDialog } from "./SubstituteDialog";
import { useToast } from "./Toast";
import { Eyebrow, Tag } from "./ui";

interface Props {
  storyId: string;
  drawsVersion: number;
  latestTurn: number | null; // 手动指定 picker 的轮次上界
  defaultTurn: number | null; // 默认轮次=对话流当前滚动位置(视口中央那一轮)
  onReload: () => void; // 出图后刷新(bump drawsVersion + 快照)
  onWriting?: (proposalId: number, on: boolean) => void; // 提升到 engine 的共享写稿态,工作台节点据此点亮
  onGenerating?: (proposalId: number, on: boolean) => void; // 同上,绘图态(按 proposal_id 索引)
}

/** 绘图台:导演 B 提案的正典待办,按场景聚合。挑一条 pending/done → 在此走画图节点(写稿若缺→出图,
 *  含参考图自由选择)。门控:variant 无基底灰禁;重绘 new_scene 带警告。
 *  注:用户手动绘图稿不在这里,独立于「手动绘图 · 私人草稿」(ManualDeck)。 */
export function DrawDeck(p: Props) {
  const [data, setData] = useState<ProposalsResp | null>(null);
  const [sel, setSel] = useState<{ id: number; scene: string } | null>(null);
  const [sub, setSub] = useState<{ id: number; scene: string } | null>(null); // 替代图片对话框(按 proposal)
  const [subBusy, setSubBusy] = useState(false);
  const [manualOpen, setManualOpen] = useState(false); // 手动指定:为任意场景×任意轮自建提案
  const toast = useToast();
  const panelRef = useRef<HTMLDivElement>(null); // 出图审阅面板:一出现就滚到它

  // 选中某待办 → 出图审阅面板浮现 → 自动滚到面板(改选另一条也重滚)。
  useEffect(() => {
    if (sel) panelRef.current?.scrollIntoView({ behavior: "smooth", block: "start" });
  }, [sel?.id]);

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
      <div className="flex items-center justify-between">
        <Eyebrow>绘图台 · 按场景(正典)</Eyebrow>
        {/* 手动指定:不靠导演 B 提案,自己挑「某场景 × 某轮」自建一条待办(进正典、同一条管线) */}
        <button
          onClick={() => setManualOpen((v) => !v)}
          disabled={p.latestTurn == null}
          className="shrink-0 rounded-lg border border-line-strong bg-surface px-2.5 py-1 text-[11.5px] text-ink-soft transition hover:border-accent hover:text-accent-ink disabled:cursor-not-allowed disabled:opacity-40"
          title={p.latestTurn == null ? "故事尚无回合" : "手动指定:为某场景在某一轮自建绘图待办"}
        >
          {manualOpen ? "收起 ✕" : "+ 手动指定"}
        </button>
      </div>

      {manualOpen && p.latestTurn != null && (
        <ManualDesignate
          storyId={p.storyId}
          latestTurn={p.latestTurn}
          defaultTurn={p.defaultTurn}
          onCreated={() => {
            setManualOpen(false);
            p.onReload();
          }}
        />
      )}

      {/* 选中的待办 → 画图节点(写稿若缺→出图 + 参考图自由选择) */}
      {sel && (
        <div ref={panelRef} className="mt-3.5 scroll-mt-2 overflow-hidden rounded-xl border border-accent/30 bg-surface">
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
            onWriting={p.onWriting}
            onGenerating={p.onGenerating}
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
            <SceneGroup
              key={slug}
              slug={slug}
              meta={data!.scenes[slug]}
              rows={rows}
              onPick={(id) => setSel({ id, scene: slug })}
              onSubstitute={(id) => setSub({ id, scene: slug })}
            />
          ))}
        </div>
      )}

      {sub && (
        <SubstituteDialog
          pastImages={data?.past_images ?? []}
          busy={subBusy}
          onClose={() => setSub(null)}
          onSubmit={async (pick) => {
            setSubBusy(true);
            try {
              await api.substituteDraw(p.storyId, { proposalId: sub.id, ...pick });
              setSub(null);
              p.onReload();
              toast("已用替代图片作为该提案结果(未花钱)");
            } catch (e) {
              toast(`替代图片出错:${String(e)}`);
            } finally {
              setSubBusy(false);
            }
          }}
        />
      )}
    </section>
  );
}

/** 手动指定面板:先定轮(默认=对话流当前滚动位置,可改 1..最新轮)→ 列该轮可画场景 → 指定即自建提案。
 *  自建的提案与导演 B 的提案完全同一条管线(随后出现在下方待办、该轮工作台绘图分支、画完进黑板/场景地图)。 */
function ManualDesignate({
  storyId,
  latestTurn,
  defaultTurn,
  onCreated,
}: {
  storyId: string;
  latestTurn: number;
  defaultTurn: number | null;
  onCreated: () => void;
}) {
  const clamp = (n: number) => Math.max(1, Math.min(latestTurn, n));
  const [turn, setTurn] = useState<number>(() => clamp(defaultTurn ?? latestTurn));
  const [scenes, setScenes] = useState<TurnSceneOpt[] | null>(null);
  const [loading, setLoading] = useState(false);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const toast = useToast();

  useEffect(() => {
    let alive = true;
    setLoading(true);
    setErr(null);
    api
      .getTurnScenes(storyId, turn)
      .then((d) => alive && setScenes(d.scenes))
      .catch((e) => alive && (setScenes([]), setErr(String(e))))
      .finally(() => alive && setLoading(false));
    return () => {
      alive = false;
    };
  }, [storyId, turn]);

  const designate = async (scene: string) => {
    setBusy(true);
    try {
      await api.createProposal(storyId, { scene, turn });
      toast(`已为「${scene}」在第 ${turn} 轮自建绘图待办`);
      onCreated();
    } catch (e) {
      toast(`手动指定出错:${String(e)}`);
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="mt-3 rounded-xl border border-accent/30 bg-surface p-3">
      <div className="flex items-center gap-2">
        <span className="text-[12px] text-ink-soft">在第</span>
        <div className="flex items-center gap-1">
          <button
            onClick={() => setTurn((t) => clamp(t - 1))}
            disabled={turn <= 1}
            className="h-6 w-6 rounded-md border border-line-strong text-ink-soft transition hover:border-accent disabled:opacity-30"
          >
            −
          </button>
          <input
            type="number"
            min={1}
            max={latestTurn}
            value={turn}
            onChange={(ev) => setTurn(clamp(Number(ev.target.value) || 1))}
            className="w-14 rounded-md border border-line-strong bg-paper px-2 py-0.5 text-center text-[13px] text-ink focus:border-accent focus:outline-none"
          />
          <button
            onClick={() => setTurn((t) => clamp(t + 1))}
            disabled={turn >= latestTurn}
            className="h-6 w-6 rounded-md border border-line-strong text-ink-soft transition hover:border-accent disabled:opacity-30"
          >
            +
          </button>
        </div>
        <span className="text-[12px] text-ink-soft">轮绘图 · 选一个场景(上下文定格那一轮)</span>
        <span className="ml-auto font-mono text-[10.5px] text-ink-faint">最新 {latestTurn} 轮</span>
      </div>

      <div className="mt-2.5 flex flex-col gap-1.5">
        {loading ? (
          <p className="text-[12px] text-ink-faint">载入第 {turn} 轮场景…</p>
        ) : err ? (
          <p className="text-[12px] text-danger">{err}</p>
        ) : scenes && scenes.length > 0 ? (
          scenes.map((sc) => (
            <div key={sc.slug} className="flex items-center gap-2.5 rounded-lg border border-line bg-paper px-2.5 py-2">
              <div className="min-w-0 flex-1">
                <div className="flex items-center gap-1.5">
                  <span className="text-[13px] text-ink">{sc.name}</span>
                  <Tag>{sc.slug}</Tag>
                  <Tag tone={sc.kind === "new_scene" ? "accent" : "soft"}>{sc.kind}</Tag>
                </div>
                {sc.variant_gated && <div className="mt-0.5 text-[10.5px] text-ink-faint">需先绘制 new_scene 基底</div>}
              </div>
              <button
                onClick={() => designate(sc.slug)}
                disabled={busy || sc.variant_gated}
                className="shrink-0 rounded-lg border border-line-strong bg-surface px-2.5 py-1 text-[11.5px] text-ink-soft transition hover:border-accent hover:text-accent-ink disabled:cursor-not-allowed disabled:opacity-40"
                title={sc.variant_gated ? "需先绘制基底" : "为该场景在此轮自建绘图待办"}
              >
                指定
              </button>
            </div>
          ))
        ) : (
          <p className="text-[12px] text-ink-faint">第 {turn} 轮没有可画的场景。</p>
        )}
      </div>
    </div>
  );
}

function SceneGroup({ slug, meta, rows, onPick, onSubstitute }: { slug: string; meta?: SceneMeta; rows: ProposalRow[]; onPick: (id: number) => void; onSubstitute: (id: number) => void }) {
  return (
    <div className="rounded-xl border border-line bg-paper p-3">
      <div className="flex items-baseline gap-2">
        <span className="text-[13.5px] font-medium text-ink">{meta?.name ?? slug}</span>
        <Tag>{slug}</Tag>
        {meta && !meta.has_new_scene && <span className="font-mono text-[10px] text-ink-faint">无基底</span>}
      </div>
      <div className="mt-2.5 flex flex-col gap-1.5">
        {rows.map((r) => (
          <TodoRow key={r.id} row={r} meta={meta} onPick={onPick} onSubstitute={onSubstitute} />
        ))}
      </div>
    </div>
  );
}

function TodoRow({ row, meta, onPick, onSubstitute }: { row: ProposalRow; meta?: SceneMeta; onPick: (id: number) => void; onSubstitute: (id: number) => void }) {
  const lightbox = useLightbox();
  const gatedVariant = row.kind === "variant" && !(meta?.has_new_scene ?? false);
  const done = row.status === "done";
  return (
    <div className="flex items-center gap-2.5 rounded-lg border border-line bg-surface px-2.5 py-2">
      {done && row.done_image_path ? (
        <img
          src={imgUrl(row.done_image_path)}
          alt=""
          onClick={() => lightbox([{ src: imgUrl(row.done_image_path!), alt: row.scene_slug }], 0)}
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
      {/* 替代图片:不写提示词、不调 gpt-image-2,直接指定/上传一张图作结果 */}
      <button
        disabled={gatedVariant}
        onClick={() => onSubstitute(row.id)}
        className="shrink-0 rounded-lg border border-line bg-surface px-2.5 py-1 text-[11.5px] text-ink-faint transition hover:border-accent hover:text-accent-ink disabled:cursor-not-allowed disabled:opacity-40"
        title={gatedVariant ? "需先绘制基底" : "替代图片(指定/上传,不花钱)"}
      >
        ▣ 替代
      </button>
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
