import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import * as api from "./api";
import type { AgentStep, Blackboard, Draft, DrawProposal, StepStatus, StoryInfo } from "./types";

export type LiveStages = Record<AgentStep, StepStatus>;
const STAGES_AT_SUBMIT: LiveStages = {
  director_a: "running",
  writer: "pending",
  director_b: "pending",
  reducer: "pending",
};

export interface TurnView {
  key: string;
  turn_index?: number;
  user_input: string;
  narrative: string;
  beat_title: string;
  streaming: boolean;
  error?: string;
}

export interface DraftCard {
  key: string;
  draft: Draft;
  prompt: string;
  source: string;
  status: "writing" | "review" | "submitted" | "failed";
  note?: string;
  warn?: boolean; // 重绘 new_scene 警告
  proposalId?: number | null;
}

export interface PendingImage {
  request_id: string;
  scene: string;
  status: "generating" | "failed";
  reason?: string;
}

const scenesImagesOf = (bb: Blackboard): Record<string, string[]> => {
  const out: Record<string, string[]> = {};
  for (const [slug, sc] of Object.entries(bb.scenes ?? {})) out[slug] = sc.image_paths ?? [];
  return out;
};

let seq = 0;
const uid = () => `${Date.now()}-${seq++}`;

export function useStoryEngine() {
  const [stories, setStories] = useState<StoryInfo[]>([]);
  const [curId, setCurId] = useState<string | null>(null);
  const [title, setTitle] = useState("");
  const [blackboard, setBlackboard] = useState<Blackboard>({});
  const [turns, setTurns] = useState<TurnView[]>([]);
  const [scenesImages, setScenesImages] = useState<Record<string, string[]>>({});
  const [proposals, setProposals] = useState<DrawProposal[]>([]);
  const [drafts, setDrafts] = useState<DraftCard[]>([]);
  const [pending, setPending] = useState<PendingImage[]>([]);
  const [turnStreaming, setTurnStreaming] = useState(false);

  // ── 导演工作台(显微镜)+ 时间控制 ──
  const [scopeOpen, setScopeOpen] = useState(false);
  const [scopeTurn, setScopeTurn] = useState<number | null>(null);
  const [liveStages, setLiveStages] = useState<LiveStages | null>(null);
  const [liveTurn, setLiveTurn] = useState<number | null>(null);
  const [retrying, setRetrying] = useState<AgentStep | null>(null);
  const [contextsVersion, setContextsVersion] = useState(0); // 时间操作后递增 → 工作台重取上下文
  const [drawsVersion, setDrawsVersion] = useState(0); // 出图/重试/回退后递增 → 重取绘图支流

  const curRef = useRef<string | null>(null);
  curRef.current = curId;

  const latestTurn = useMemo(() => {
    const idxs = turns.map((t) => t.turn_index).filter((n): n is number => n != null);
    return idxs.length ? Math.max(...idxs) : null;
  }, [turns]);

  const refreshStories = useCallback(async () => {
    setStories(await api.listStories());
  }, []);

  useEffect(() => {
    refreshStories();
  }, [refreshStories]);

  const loadSnapshot = useCallback(async (id: string) => {
    const snap = await api.getSnapshot(id);
    if (curRef.current !== id) return;
    setTitle(snap.title);
    setBlackboard(snap.blackboard ?? {});
    setScenesImages({ ...scenesImagesOf(snap.blackboard ?? {}), ...(snap.scenes_images ?? {}) });
    setTurns(
      (snap.history ?? []).map((t) => ({
        key: `h${t.turn_index}`,
        turn_index: t.turn_index,
        user_input: t.user_input,
        narrative: t.narrative,
        beat_title: t.beat_title,
        streaming: false,
      })),
    );
  }, []);

  const selectStory = useCallback(
    (id: string) => {
      setCurId(id);
      curRef.current = id;
      setProposals([]);
      setDrafts([]);
      setPending([]);
      setScopeTurn(null); // 工作台默认看最新轮
      setLiveStages(null);
      setLiveTurn(null);
      loadSnapshot(id);
    },
    [loadSnapshot],
  );

  const createStory = useCallback(
    async (t: string) => {
      const s = await api.createStory(t);
      await refreshStories();
      selectStory(s.id);
    },
    [refreshStories, selectStory],
  );

  const removeStory = useCallback(
    async (id: string) => {
      await api.deleteStory(id);
      if (curRef.current === id) {
        setCurId(null);
        curRef.current = null;
        setTitle("");
        setBlackboard({});
        setTurns([]);
        setScenesImages({});
        setProposals([]);
        setDrafts([]);
        setPending([]);
      }
      refreshStories();
    },
    [refreshStories],
  );

  // ── 文本线:逐 token 涌现 ──
  const submitTurn = useCallback(
    async (text: string) => {
      const id = curRef.current;
      if (!id || turnStreaming) return;
      const key = uid();
      setTurns((ts) => [
        ...ts,
        { key, user_input: text, narrative: "", beat_title: "", streaming: true },
      ]);
      setTurnStreaming(true);
      // 显微镜实时进度:提交即 A 运行中,后续阶段随 SSE 事件点亮(无需后端补事件)。
      setLiveStages(STAGES_AT_SUBMIT);
      setLiveTurn(null);
      const patch = (fn: (t: TurnView) => TurnView) =>
        setTurns((ts) => ts.map((t) => (t.key === key ? fn(t) : t)));
      try {
        await api.streamTurn(id, text, (ev) => {
          if (ev.type === "turn_started") {
            patch((t) => ({ ...t, turn_index: ev.turn_index }));
            setLiveTurn(ev.turn_index);
            setScopeTurn(ev.turn_index); // 工作台开着时,跟随这一轮
            setLiveStages({ director_a: "done", writer: "running", director_b: "pending", reducer: "pending" });
          } else if (ev.type === "narrative_token")
            patch((t) => ({ ...t, narrative: t.narrative + ev.text }));
          else if (ev.type === "narrative_done") {
            patch((t) => ({ ...t, narrative: ev.full_narrative }));
            setLiveStages({ director_a: "done", writer: "done", director_b: "running", reducer: "pending" });
          } else if (ev.type === "state_updated") {
            setBlackboard(ev.blackboard);
            setScenesImages((prev) => ({ ...prev, ...scenesImagesOf(ev.blackboard) }));
            patch((t) => ({ ...t, beat_title: ev.beat_title }));
            setLiveStages({ director_a: "done", writer: "done", director_b: "done", reducer: "done" });
          } else if (ev.type === "draw_proposed") setProposals((p) => [...ev.proposals, ...p]);
          else if (ev.type === "error") patch((t) => ({ ...t, streaming: false, error: ev.reason }));
        });
      } catch (e) {
        patch((t) => ({ ...t, error: String(e) }));
      } finally {
        patch((t) => ({ ...t, streaming: false }));
        setTurnStreaming(false);
        setLiveStages(null);
        setLiveTurn(null);
        setContextsVersion((v) => v + 1); // 本轮上下文已落盘,可查看
        setDrawsVersion((v) => v + 1); // 本轮新提案已落库 → 绘图台刷新
        refreshStories();
      }
    },
    [turnStreaming, refreshStories],
  );

  // ── 图片线:写稿 → 审阅 → 确认/复用/跳过(独立于文本线,不锁输入)──
  const openDraft = useCallback(async (scene: string, source: string) => {
    const id = curRef.current;
    if (!id) return;
    const key = uid();
    setDrafts((d) => [
      { key, draft: { type: "draft_ready", draft_id: "", scene, kind: "", prompt_text: "", refs: [], history: [] }, prompt: "", source, status: "writing" },
      ...d,
    ]);
    try {
      const draft = await api.postDraw(id, { scene, source, source_turn: latestTurn }); // 归属到当前最新轮
      if ((draft as { detail?: string }).detail) {
        setDrafts((d) =>
          d.map((c) => (c.key === key ? { ...c, status: "failed", note: (draft as { detail?: string }).detail } : c)),
        );
        return;
      }
      setDrafts((d) =>
        d.map((c) => (c.key === key ? { ...c, draft, prompt: draft.prompt_text, status: "review", warn: draft.warn_redraw_base } : c)),
      );
    } catch (e) {
      setDrafts((d) => d.map((c) => (c.key === key ? { ...c, status: "failed", note: String(e) } : c)));
    }
  }, [latestTurn]);

  // 提案制:从绘图台某条待办开稿(kind/截断轮按提案的 origin_turn,后端权威)。
  const openDraftForProposal = useCallback(async (proposalId: number, scene: string) => {
    const id = curRef.current;
    if (!id) return;
    const key = uid();
    setDrafts((d) => [
      { key, draft: { type: "draft_ready", draft_id: "", scene, kind: "", prompt_text: "", refs: [], history: [] }, prompt: "", source: "director_b_proposal", status: "writing", proposalId },
      ...d,
    ]);
    try {
      const draft = await api.postDraw(id, { proposal_id: proposalId, source: "director_b_proposal" });
      if ((draft as { detail?: string }).detail) {
        setDrafts((d) => d.map((c) => (c.key === key ? { ...c, status: "failed", note: (draft as { detail?: string }).detail } : c)));
        return;
      }
      setDrafts((d) =>
        d.map((c) => (c.key === key ? { ...c, draft, prompt: draft.prompt_text, status: "review", warn: draft.warn_redraw_base, proposalId } : c)),
      );
    } catch (e) {
      setDrafts((d) => d.map((c) => (c.key === key ? { ...c, status: "failed", note: String(e) } : c)));
    }
  }, []);

  const editDraftPrompt = useCallback((key: string, prompt: string) => {
    setDrafts((d) => d.map((c) => (c.key === key ? { ...c, prompt } : c)));
  }, []);

  const dropDraft = useCallback((key: string) => {
    setDrafts((d) => d.filter((c) => c.key !== key));
  }, []);

  const silentRefresh = useCallback(async () => {
    const id = curRef.current;
    if (!id) return;
    const snap = await api.getSnapshot(id);
    if (curRef.current !== id) return;
    setBlackboard(snap.blackboard ?? {});
    setScenesImages({ ...scenesImagesOf(snap.blackboard ?? {}), ...(snap.scenes_images ?? {}) });
  }, []);

  const confirmDraft = useCallback(
    async (key: string) => {
      const id = curRef.current;
      const card = drafts.find((c) => c.key === key);
      if (!id || !card || !card.draft.draft_id) return;
      // 卡片转「后台生成中」并最小化;场景画廊放占位。文本线照常可用。
      setDrafts((d) => d.map((c) => (c.key === key ? { ...c, status: "submitted", note: "已提交,后台生成中" } : c)));
      try {
        await api.confirmDraw(id, { draft_id: card.draft.draft_id, prompt: card.prompt }, (ev) => {
          if (ev.type === "image_generating")
            setPending((p) => [...p, { request_id: ev.request_id, scene: ev.scene, status: "generating" }]);
          else if (ev.type === "image_ready") {
            setPending((p) => p.filter((x) => x.request_id !== ev.request_id));
            setScenesImages((prev) => ({
              ...prev,
              [ev.scene]: [...(prev[ev.scene] ?? []).filter((u) => u !== ev.image_path), ev.image_path],
            }));
            silentRefresh();
            setDrawsVersion((v) => v + 1); // 绘图支流刷新
            dropDraft(key);
          } else if (ev.type === "image_failed") {
            setPending((p) =>
              p.map((x) => (x.request_id === ev.request_id ? { ...x, status: "failed", reason: ev.reason } : x)),
            );
            setDrafts((d) => d.map((c) => (c.key === key ? { ...c, status: "failed", note: ev.reason } : c)));
          }
        });
      } catch (e) {
        setDrafts((d) => d.map((c) => (c.key === key ? { ...c, status: "failed", note: String(e) } : c)));
      }
    },
    [drafts, silentRefresh, dropDraft],
  );

  const decideDraft = useCallback(
    async (key: string, decision: "reuse" | "skip") => {
      const id = curRef.current;
      const card = drafts.find((c) => c.key === key);
      if (!id || !card || !card.draft.draft_id) return;
      const body: { draft_id: string; decision: "reuse" | "skip"; prompt: string; reuse_image_path?: string } = {
        draft_id: card.draft.draft_id,
        decision,
        prompt: card.prompt,
      };
      if (decision === "reuse") {
        const ps = blackboard.scenes?.[card.draft.scene]?.image_paths ?? [];
        if (ps.length) body.reuse_image_path = ps[ps.length - 1];
      }
      const res = await api.decideDraw(id, body);
      if (decision === "reuse" && res?.image_path) {
        setScenesImages((prev) => ({
          ...prev,
          [card.draft.scene]: [...(prev[card.draft.scene] ?? []).filter((u) => u !== res.image_path), res.image_path],
        }));
      }
      dropDraft(key);
    },
    [drafts, blackboard, dropDraft],
  );

  const startDraftFromProposal = useCallback(
    (p: DrawProposal) => {
      setProposals((ps) => ps.filter((x) => x !== p));
      openDraft(p.scene_slug, "director_b_proposal");
    },
    [openDraft],
  );

  // 编辑节点输入记录:直接改 M4.5-B 存的那份 messages(仅最新轮)。
  const saveStepContext = useCallback(
    async (turnIndex: number, step: "director_a" | "writer" | "director_b", messages: { role: string; content: string }[]) => {
      const id = curRef.current;
      if (!id) return;
      await api.saveStepContext(id, turnIndex, step, messages);
      setContextsVersion((v) => v + 1); // 重取 → 编辑后内容确为持久化的那份
    },
    [],
  );

  const bumpAll = () => {
    setContextsVersion((v) => v + 1);
    setDrawsVersion((v) => v + 1);
  };

  // ── 时间控制(接 M4.5-C 后端能力)──
  const doRollback = useCallback(async () => {
    const id = curRef.current;
    if (!id || turnStreaming || retrying) return;
    const r = await api.rollback(id);
    if (r?.ok === false || r?.detail) return;
    await loadSnapshot(id);
    setScopeTurn(r.new_latest_turn ?? null); // 跟到回退后的新最新轮(回退掉首轮则为 null)
    bumpAll();
    refreshStories();
  }, [turnStreaming, retrying, loadSnapshot, refreshStories]);

  const doRetry = useCallback(
    async (entry: AgentStep) => {
      const id = curRef.current;
      if (!id || turnStreaming || retrying || entry === "reducer") return;
      setRetrying(entry);
      try {
        await api.retry(id, entry); // 真 LLM 重走(用改后的输入记录),完成后刷新显微镜与叙事
        await loadSnapshot(id);
        setScopeTurn(latestTurn);
        bumpAll();
      } catch {
        /* 失败保持原状;按钮恢复可用 */
      } finally {
        setRetrying(null);
        refreshStories();
      }
    },
    [turnStreaming, retrying, latestTurn, loadSnapshot, refreshStories],
  );

  const reloadScope = useCallback(async () => {
    const id = curRef.current;
    if (!id) return;
    await loadSnapshot(id);
    bumpAll();
  }, [loadSnapshot]);

  const doFork = useCallback(async () => {
    const id = curRef.current;
    if (!id) return;
    await api.forkStory(id); // 副本出现在书架
    refreshStories();
  }, [refreshStories]);

  const openScope = useCallback(() => setScopeOpen(true), []);
  const closeScope = useCallback(() => setScopeOpen(false), []);

  return {
    stories, curId, title, blackboard, turns, scenesImages, proposals, drafts, pending, turnStreaming,
    refreshStories, selectStory, createStory, removeStory, submitTurn,
    openDraft, openDraftForProposal, editDraftPrompt, dropDraft, confirmDraft, decideDraft, startDraftFromProposal,
    // 工作台 + 时间控制
    scopeOpen, scopeTurn, setScopeTurn, openScope, closeScope,
    liveStages, liveTurn, retrying, latestTurn, contextsVersion, drawsVersion,
    doRollback, doRetry, doFork, saveStepContext, reloadScope,
  };
}
