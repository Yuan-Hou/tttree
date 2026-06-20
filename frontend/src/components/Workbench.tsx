import { useEffect, useMemo, useRef, useState } from "react";
import * as api from "../api";
import type {
  AgentStep,
  ContextMessage,
  DrawItem,
  DrawProposal,
  SettingsSection,
  StepStatus,
  TurnContexts,
  TurnDraws,
} from "../types";
import type { LiveStages, TurnView } from "../useStoryEngine";
import { AgentFlow } from "./AgentFlow";
import { NodeEditor } from "./NodeEditor";
import { WriteNodeEditor } from "./WriteNodeEditor";
import { PictureNodeEditor } from "./PictureNodeEditor";
import { Button } from "./ui";

interface Props {
  onClose: () => void;
  storyId: string;
  title: string;
  turns: TurnView[];
  scopeTurn: number | null;
  setScopeTurn: (n: number) => void;
  latestTurn: number | null;
  liveStages: LiveStages | null;
  liveTurn: number | null;
  liveOutputs: Partial<Record<AgentStep, string>>; // 各 agent 进行中的实时原始输出(逐 token)
  turnStreaming: boolean;
  retrying: AgentStep | null;
  contextsVersion: number;
  drawsVersion: number;
  liveError: { step: AgentStep; reason: string } | null; // 最近一次 agent 调用失败 → 对应节点展示详情
  dismissFailure: () => void; // 弃掉「失败的提交」(纯前端,不碰后端)
  // 写稿/出图「运行中」记账(引擎层 → 关掉重开工作台/切节点不丢「运行中」)
  writingIds: number[];
  generatingIds: number[];
  onWriting: (proposalId: number, on: boolean) => void;
  onGenerating: (proposalId: number, on: boolean) => void;
  proposals: DrawProposal[];
  onRetry: (s: AgentStep) => void;
  onRollback: () => void;
  onFork: () => void;
  saveStepContext: (turnIndex: number, step: Exclude<AgentStep, "reducer">, messages: ContextMessage[]) => Promise<void>;
  reloadScope: () => Promise<void>;
  onOpenSettings: (section: SettingsSection) => void; // 「数据源」节点 → 打开对应设置分区
}

// 线性主轴(不含 options —— options 是 Writer 后与 B 并行的叶子,单独处理)
const ORDER: AgentStep[] = ["director_a", "writer", "director_b", "reducer"];
const ALL_DONE: LiveStages = { director_a: "done", writer: "done", director_b: "done", options: "done", reducer: "done" };

type Sel =
  | { kind: "step"; step: AgentStep }
  | { kind: "draw"; i: number; part: "prompt" | "image" }
  | null;

function parseSel(id: string | null): Sel {
  if (!id) return null;
  if (id.startsWith("ds:")) return null; // 数据源节点不进编辑区:它靠自身按钮开设置,无 agent 上下文
  if (id.startsWith("draw:")) {
    const [, i, part] = id.split(":");
    return { kind: "draw", i: Number(i), part: part as "prompt" | "image" };
  }
  return { kind: "step", step: id as AgentStep };
}

function toDrawItems(d: TurnDraws): DrawItem[] {
  // 与绘图台同源:DrawProposal 按本轮过滤。pending 待绘制 / done 已画(带缩略图)。
  return d.proposals.map((pr) => ({
    key: `p${pr.id}`,
    proposal_id: pr.id,
    scene_slug: pr.scene_slug,
    kind: pr.kind,
    reason: pr.reason,
    status: pr.status,
    done_image_path: pr.done_image_path,
  }));
}

export function Workbench(p: Props) {
  const [selId, setSelId] = useState<string | null>(null);
  const [contexts, setContexts] = useState<TurnContexts | null>(null);
  const [loading, setLoading] = useState(false);
  const [draws, setDraws] = useState<TurnDraws | null>(null);

  const effTurn = p.scopeTurn ?? p.latestTurn;
  const isLive = p.liveStages !== null && p.liveTurn === effTurn;
  // 本轮是否已落盘:新提交看的是 latest+1(未落盘);重试看的是 latest(已落盘,只是 agent 在重走)。
  // 据此区分「进行中且无落盘绘图支流」(只新提交满足)与「重试(绘图支流仍取落盘那份)」。
  const turnPersisted = effTurn != null && effTurn <= (p.latestTurn ?? -1);
  const liveUnpersisted = isLive && !turnPersisted; // 仅新提交:提案未落库,用实时提案预览
  // 「失败的提交」视图:有失败详情、非进行中/重走中,且当前停在比最新已落盘轮更靠后的那一格
  // (= 失败尝试 latestTurn+1)。这一格不是真实落盘轮,故没有上下文/绘图,也不能回退/重试。
  const failedView =
    p.liveError != null && !isLive && p.retrying === null && effTurn != null && effTurn > (p.latestTurn ?? 0);
  const isLatest = effTurn != null && effTurn === p.latestTurn;
  const idle = !p.turnStreaming && p.retrying === null;
  const editable = isLatest && idle && !isLive && !failedView;
  const noPersisted = isLive || failedView; // 无落盘上下文(进行中/重试都看实时输出,不取存档输入)
  const drawsSkip = liveUnpersisted || failedView; // 绘图支流:仅新提交(未落库)/失败提交 不取落盘

  // 节点状态:进行中/重走中→实时点亮(两者都走 liveStages);失败→失败步 error、其后 pending;否则静态全完成。
  // 重试也驱动 liveStages + liveTurn(见 useStoryEngine.doRetry),故同样落在 isLive 分支,无需单列重走逻辑。
  let stages: LiveStages = ALL_DONE;
  if (isLive && p.liveStages) stages = p.liveStages;
  else if (failedView && p.liveError) {
    const fi = ORDER.indexOf(p.liveError.step);
    stages = ORDER.reduce((acc, s, i) => {
      acc[s] = (i < fi ? "done" : i === fi ? "error" : "pending") as StepStatus;
      return acc;
    }, {} as LiveStages);
    stages.options = "pending"; // 失败的提交未走到/未落 Options
  } else if (p.liveError?.step === "options" && isLatest && !isLive && p.retrying === null) {
    // Options 失败不阻断落盘 → 该轮照常完成,但 options 节点标红展示其失败
    stages = { ...ALL_DONE, options: "error" };
  }

  // 绘图支流:进行中的轮用本轮的实时提案;否则用落盘后取回的 draws。
  const drawItems: DrawItem[] = useMemo(() => {
    // 新提交进行中:提案尚未落库 → 无 proposal_id,只作"待绘制"预览,出图等落库后(turn_done 刷新)。
    // 重试不属此列(turn 已落盘)→ 仍用取回的落盘 draws。
    if (liveUnpersisted)
      return p.proposals.map((pr, i) => ({
        key: `lp${i}`, scene_slug: pr.scene_slug, kind: pr.kind, reason: pr.reason, status: "pending" as const,
      }));
    return draws ? toDrawItems(draws) : [];
  }, [liveUnpersisted, p.proposals, draws]);

  const liveNarrative = p.turns.find((t) => t.turn_index === effTurn)?.narrative ?? "";

  useEffect(() => {
    const h = (e: KeyboardEvent) => e.key === "Escape" && p.onClose();
    window.addEventListener("keydown", h);
    return () => window.removeEventListener("keydown", h);
  }, [p]);

  // 取该轮上下文(进行中 / 失败的提交 都没落盘 → 不取)
  useEffect(() => {
    if (effTurn == null || noPersisted) {
      setContexts(null);
      return;
    }
    let alive = true;
    setLoading(true);
    api
      .getTurnContexts(p.storyId, effTurn)
      .then((c) => alive && setContexts(c))
      .catch(() => alive && setContexts(null))
      .finally(() => alive && setLoading(false));
    return () => {
      alive = false;
    };
  }, [p.storyId, effTurn, noPersisted, p.contextsVersion]);

  // 取该轮绘图支流。换轮(或换故事)先清空旧数据,避免短暂显示上一轮的绘图节点(跨轮串);
  // 同轮内的 drawsVersion 刷新不清空,避免无谓闪烁。
  const prevDrawsKey = useRef<string | null>(null);
  useEffect(() => {
    if (effTurn == null || drawsSkip) {
      setDraws(null);
      return;
    }
    const key = `${p.storyId}:${effTurn}`;
    if (prevDrawsKey.current !== key) {
      setDraws(null);
      prevDrawsKey.current = key;
    }
    let alive = true;
    api.getTurnDraws(p.storyId, effTurn).then((d) => alive && setDraws(d)).catch(() => alive && setDraws(null));
    return () => {
      alive = false;
    };
  }, [p.storyId, effTurn, drawsSkip, p.drawsVersion]);

  const sel = parseSel(selId);
  // 在对应节点展示失败详情:失败的提交(failedView)整体,或在最新正常轮上「从这里重试」失败时
  const showNodeError = p.liveError != null && !isLive && p.retrying === null && (failedView || isLatest);

  return (
    <div className="fixed inset-0 z-50 flex flex-col bg-ink/20 p-5 backdrop-blur-[2px]" onClick={p.onClose}>
      <div
        className="flex min-h-0 flex-1 flex-col overflow-hidden rounded-2xl border border-line-strong bg-paper shadow-[0_24px_60px_-20px_rgba(28,37,48,0.35)]"
        onClick={(e) => e.stopPropagation()}
      >
        <header className="flex items-center gap-4 border-b border-line bg-surface px-6 py-3.5">
          <div className="flex items-baseline gap-2.5">
            <span className="font-serif text-[16px] text-accent-ink">
              <span className="text-accent">❡</span> 导演工作台
            </span>
            <span className="font-serif text-[14px] text-ink-soft">{p.title}</span>
          </div>

          {effTurn != null ? (
            <div className="ml-2 flex items-center gap-1.5">
              <RoundBtn disabled={effTurn <= 1} onClick={() => p.setScopeTurn(effTurn - 1)}>‹</RoundBtn>
              <span className="font-mono text-[12px] text-ink">
                {failedView ? (
                  "失败的提交"
                ) : (
                  <>
                    第 {effTurn} 轮<span className="text-ink-faint"> / 共 {p.latestTurn}</span>
                  </>
                )}
              </span>
              <RoundBtn disabled={failedView || p.latestTurn == null || effTurn >= p.latestTurn} onClick={() => p.setScopeTurn(effTurn + 1)}>›</RoundBtn>
              <span
                className={`ml-1.5 rounded-[5px] px-1.5 py-px font-mono text-[10px] ${
                  failedView
                    ? "bg-danger-soft text-danger"
                    : isLive
                      ? "bg-accent-soft text-accent-ink"
                      : isLatest
                        ? "bg-sunken text-ink-soft"
                        : "bg-sunken text-ink-faint"
                }`}
              >
                {failedView ? "未计入故事 · 重新输入以重试" : isLive ? "进行中" : isLatest ? "最新轮 · 可操作" : "历史轮 · 只读"}
              </span>
            </div>
          ) : (
            <span className="ml-2 font-mono text-[12px] text-ink-faint">尚无回合</span>
          )}

          <div className="ml-auto flex items-center gap-2">
            {failedView && (
              <Button variant="ghost" onClick={p.dismissFailure} title="失败的提交未落盘,弃掉它(不影响已有正常轮)">
                弃掉这次失败
              </Button>
            )}
            <Button variant="ghost" onClick={p.onFork} title="完整克隆当前故事,作主动后悔药">建副本</Button>
            <Button
              variant="ghost"
              disabled={failedView || p.latestTurn == null || !idle}
              onClick={p.onRollback}
              title={failedView ? "失败的提交没进库,无需回退;回退是删最新的正常轮" : "回退最新一轮(可连续)"}
            >
              ↩ 回退最新轮
            </Button>
            <Button variant="quiet" onClick={p.onClose}>关闭 ✕</Button>
          </div>
        </header>

        {effTurn == null ? (
          <div className="flex flex-1 items-center justify-center text-[13px] text-ink-faint">
            还没有回合 —— 先在阅读区推进一轮,再来摊开它的内部流。
          </div>
        ) : (
          <div className="flex min-h-0 flex-1">
            <div className="relative min-w-0 flex-1">
              <AgentFlow
                storyId={p.storyId}
                turn={effTurn}
                stages={stages}
                draws={drawItems}
                writingIds={p.writingIds}
                generatingIds={p.generatingIds}
                selectedId={selId}
                onSelectNode={setSelId}
                onOpenSettings={p.onOpenSettings}
              />
              {!isLatest && !isLive && (
                <div className="pointer-events-none absolute bottom-4 left-1/2 -translate-x-1/2 rounded-full border border-line bg-surface/90 px-3 py-1 font-mono text-[10.5px] text-ink-faint">
                  历史轮只读 · 要改这一轮,先回退到此
                </div>
              )}
            </div>

            <div className="flex w-[392px] shrink-0 flex-col border-l border-line bg-surface">
              {sel === null ? (
                <div className="flex flex-1 items-center justify-center px-8 text-center text-[12.5px] leading-relaxed text-ink-faint">
                  点任一节点打开编辑区:看完整输入(可就地编辑)+ 输出,从编辑区"从这里重试"。
                </div>
              ) : sel.kind === "step" ? (
                <NodeEditor
                  step={sel.step}
                  contexts={contexts}
                  loading={loading}
                  live={isLive}
                  liveNarrative={liveNarrative}
                  liveOutput={sel.step !== "reducer" ? p.liveOutputs[sel.step] : undefined}
                  editable={editable}
                  retrying={p.retrying !== null}
                  error={showNodeError && p.liveError?.step === sel.step ? p.liveError.reason : undefined}
                  onSave={(step, msgs) => p.saveStepContext(effTurn, step, msgs)}
                  onRetry={p.onRetry}
                />
              ) : drawItems[sel.i] ? (
                drawItems[sel.i].proposal_id == null ? (
                  <div className="flex flex-1 items-center justify-center px-8 text-center text-[12.5px] text-ink-faint">
                    本轮进行中,提案落库后即可绘制。
                  </div>
                ) : sel.part === "prompt" ? (
                  <WriteNodeEditor
                    storyId={p.storyId}
                    proposalId={drawItems[sel.i].proposal_id!}
                    canAct={idle}
                    onChanged={p.reloadScope}
                    onWriting={p.onWriting}
                  />
                ) : (
                  <PictureNodeEditor
                    storyId={p.storyId}
                    proposalId={drawItems[sel.i].proposal_id!}
                    canAct={idle}
                    onWriting={p.onWriting}
                    onGenerating={p.onGenerating}
                    onDone={p.reloadScope}
                  />
                )
              ) : (
                <div className="flex flex-1 items-center justify-center text-[12.5px] text-ink-faint">该绘图节点已变化,请重新选择。</div>
              )}
            </div>
          </div>
        )}
      </div>
    </div>
  );
}

function RoundBtn({ disabled, onClick, children }: { disabled: boolean; onClick: () => void; children: React.ReactNode }) {
  return (
    <button
      disabled={disabled}
      onClick={onClick}
      className="flex h-6 w-6 items-center justify-center rounded-md border border-line-strong bg-surface text-ink-soft transition hover:bg-sunken disabled:opacity-30"
    >
      {children}
    </button>
  );
}
