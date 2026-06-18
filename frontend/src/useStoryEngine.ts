import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import * as api from "./api";
import { useToast } from "./components/Toast";
import type { AgentStep, Blackboard, Draft, DraftRef, DrawProposal, PickedRef, StepStatus, StoryInfo } from "./types";

const STEP_LABEL: Record<AgentStep, string> = {
  director_a: "导演 A",
  writer: "写手",
  director_b: "导演 B",
  options: "选项",
  reducer: "落盘",
};

/** 后端 SSE error.reason 形如 "director-a: ...":解析出失败的 agent 步,用于在工作台对应节点展示详情。 */
const parseErrStep = (reason: string): AgentStep | null =>
  reason.startsWith("director-a")
    ? "director_a"
    : reason.startsWith("director-b")
      ? "director_b"
      : reason.startsWith("writer")
        ? "writer"
        : null;

/** 绘图 Agent 建议的引用清单(DraftRef)→ 用户可编辑的选择(PickedRef)。 */
const toPicked = (refs: DraftRef[]): PickedRef[] =>
  refs.map((r) =>
    r.source === "reference_asset"
      ? { source: "reference_asset", asset_id: r.asset_id, semantic_name: r.semantic_name, purpose: r.purpose }
      : { source: "history_image", image_path: r.image_path, semantic_name: r.semantic_name, purpose: r.purpose },
  );

export type LiveStages = Record<AgentStep, StepStatus>;
const STAGES_AT_SUBMIT: LiveStages = {
  director_a: "running",
  writer: "pending",
  director_b: "pending",
  options: "pending",
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
  picked: PickedRef[]; // 用户当前选定的参考图(可编辑;初始为 Agent 建议)
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

// 进站自动恢复上次打开的故事(纯前端本地偏好)。
const LAST_STORY_KEY = "vore.lastStory";
const readLastStory = (): string | null => {
  try {
    return localStorage.getItem(LAST_STORY_KEY);
  } catch {
    return null;
  }
};
const writeLastStory = (id: string | null) => {
  try {
    if (id) localStorage.setItem(LAST_STORY_KEY, id);
    else localStorage.removeItem(LAST_STORY_KEY);
  } catch {
    /* 隐私模式 / 配额 → 忽略 */
  }
};

export function useStoryEngine() {
  const [stories, setStories] = useState<StoryInfo[]>([]);
  const [curId, setCurId] = useState<string | null>(null);
  const [title, setTitle] = useState("");
  const [blackboard, setBlackboard] = useState<Blackboard>({});
  const [turns, setTurns] = useState<TurnView[]>([]);
  const [scenesImages, setScenesImages] = useState<Record<string, string[]>>({});
  const [scenesDrafts, setScenesDrafts] = useState<Record<string, string[]>>({}); // 手动草稿图(非正式)
  const [supersededImages, setSupersededImages] = useState<string[]>([]); // 被取代的正典图路径:仍在画廊,但标「被覆盖」
  const [proposals, setProposals] = useState<DrawProposal[]>([]);
  const [drafts, setDrafts] = useState<DraftCard[]>([]);
  const [pending, setPending] = useState<PendingImage[]>([]);
  const [turnStreaming, setTurnStreaming] = useState(false);
  // 本轮 Options 给出的下一步可选项(输入框上方展示);新一轮/切故事/回退时清空。
  const [options, setOptions] = useState<string[]>([]);
  // 正在写稿/出图的 proposal_id —— 提到引擎层(而非工作台本地),关掉重开工作台/切节点都不丢「运行中」。
  const [writingIds, setWritingIds] = useState<number[]>([]);
  const [generatingIds, setGeneratingIds] = useState<number[]>([]);

  // ── 导演工作台(显微镜)+ 时间控制 ──
  const [scopeOpen, setScopeOpen] = useState(false);
  const [scopeTurn, setScopeTurn] = useState<number | null>(null);
  const [liveStages, setLiveStages] = useState<LiveStages | null>(null);
  const [liveTurn, setLiveTurn] = useState<number | null>(null);
  const [retrying, setRetrying] = useState<AgentStep | null>(null);
  // 最近一次 agent 调用失败:展示在工作台对应节点的报错位 + 触发 toast。新一轮/重试开始时清空。
  const [liveError, setLiveError] = useState<{ step: AgentStep; reason: string } | null>(null);
  const [contextsVersion, setContextsVersion] = useState(0); // 时间操作后递增 → 工作台重取上下文
  const [drawsVersion, setDrawsVersion] = useState(0); // 出图/重试/回退后递增 → 重取绘图支流

  const showToast = useToast();
  const curRef = useRef<string | null>(null);
  curRef.current = curId;
  // 在飞的 SSE 流(出文/出图)。切故事时全部 abort,避免旧故事的流继续写新故事的 state。
  const inflightRef = useRef<Set<AbortController>>(new Set());

  // 写稿/出图「运行中」记账(引擎层,跨工作台开关存活)。SSE 的 finally 无论组件是否卸载都会清掉。
  const onWriting = useCallback(
    (pid: number, on: boolean) =>
      setWritingIds((g) => (on ? [...new Set([...g, pid])] : g.filter((x) => x !== pid))),
    [],
  );
  const onGenerating = useCallback(
    (pid: number, on: boolean) =>
      setGeneratingIds((g) => (on ? [...new Set([...g, pid])] : g.filter((x) => x !== pid))),
    [],
  );

  // 「最新已落盘轮」:排除失败的提交(error 标记)—— 失败的一轮没进库,不能算作 latest,
  // 否则回退/重试会以为它是最新轮,转而误删真正的上一正常轮。
  const latestTurn = useMemo(() => {
    const idxs = turns.filter((t) => !t.error).map((t) => t.turn_index).filter((n): n is number => n != null);
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
    setScenesDrafts(snap.scenes_drafts ?? {});
    setSupersededImages(snap.superseded_images ?? []);
    setOptions(snap.latest_options ?? []); // 常驻:刷新/切故事后恢复最新一轮选项
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
      // 切故事先 abort 旧故事在飞的流,再清掉所有「瞬时/进行中」状态(它们不属于新故事,
      // 也无法从快照重建);已落盘真相由 loadSnapshot 重新对账。
      inflightRef.current.forEach((ac) => ac.abort());
      inflightRef.current.clear();
      setCurId(id);
      curRef.current = id;
      writeLastStory(id); // 记住:进站自动恢复
      setProposals([]);
      setDrafts([]);
      setPending([]);
      setWritingIds([]);
      setGeneratingIds([]);
      setOptions([]);
      setTurnStreaming(false);
      setRetrying(null);
      setLiveError(null);
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
        writeLastStory(null); // 删的是当前故事 → 别再尝试恢复它
        setTitle("");
        setBlackboard({});
        setTurns([]);
        setScenesImages({});
        setScenesDrafts({});
        setSupersededImages([]);
        setProposals([]);
        setDrafts([]);
        setPending([]);
      }
      refreshStories();
    },
    [refreshStories],
  );

  // 进站自动恢复上次打开的故事:首批故事列表到位后跑一次,故事仍在则自动 select;
  // 已不存在(被删/换设备)则优雅留在无选中态。只跑一次,且仅当此刻无选中(不抢已有选择)。
  const restoredRef = useRef(false);
  useEffect(() => {
    if (restoredRef.current || stories.length === 0) return;
    restoredRef.current = true;
    if (curRef.current) return;
    const last = readLastStory();
    if (last && stories.some((s) => s.id === last)) selectStory(last);
  }, [stories, selectStory]);

  // ── 文本线:逐 token 涌现 ──
  const submitTurn = useCallback(
    async (text: string) => {
      const id = curRef.current;
      if (!id || turnStreaming) return;
      const key = uid();
      // 重新提交时先丢掉上一笔失败的尝试(未落盘),只保留正常轮 + 这条新流式轮。
      setTurns((ts) => [
        ...ts.filter((t) => !t.error),
        { key, user_input: text, narrative: "", beat_title: "", streaming: true },
      ]);
      setTurnStreaming(true);
      setLiveError(null); // 新一轮开始,清掉上次的失败详情
      setOptions([]); // 清掉上一轮的选项,本轮 options_proposed 再填
      // 工作台锚到这一笔尝试(latest+1)。若在 turn_started 前就失败(如导演A),它仍能落到
      // 「失败的提交」格(比最新已落盘轮靠后),不会把上一正常轮误当成失败轮。
      setScopeTurn((latestTurn ?? 0) + 1);
      // 显微镜实时进度:提交即 A 运行中,后续阶段随 SSE 事件点亮(无需后端补事件)。
      setLiveStages(STAGES_AT_SUBMIT);
      setLiveTurn(null);
      const patch = (fn: (t: TurnView) => TurnView) =>
        setTurns((ts) => ts.map((t) => (t.key === key ? fn(t) : t)));
      const ac = new AbortController();
      inflightRef.current.add(ac);
      const onStory = () => curRef.current === id; // 仍停在发起这条流的故事?切走了就别再写它的 state
      try {
        await api.streamTurn(id, text, (ev) => {
          if (!onStory()) return; // 切故事后旧流的事件一律丢弃,避免污染新故事
          if (ev.type === "turn_started") {
            patch((t) => ({ ...t, turn_index: ev.turn_index }));
            setLiveTurn(ev.turn_index);
            setScopeTurn(ev.turn_index); // 工作台开着时,跟随这一轮
            setLiveStages({ director_a: "done", writer: "running", director_b: "pending", options: "pending", reducer: "pending" });
          } else if (ev.type === "narrative_token")
            patch((t) => ({ ...t, narrative: t.narrative + ev.text }));
          else if (ev.type === "narrative_done") {
            patch((t) => ({ ...t, narrative: ev.full_narrative }));
            // 成稿后 B 与 Options 并行启动 → 两节点同时点亮"运行中",各自完成各自变 done
            setLiveStages({ director_a: "done", writer: "done", director_b: "running", options: "running", reducer: "pending" });
          } else if (ev.type === "state_updated") {
            setBlackboard(ev.blackboard);
            setScenesImages((prev) => ({ ...prev, ...scenesImagesOf(ev.blackboard) }));
            patch((t) => ({ ...t, beat_title: ev.beat_title }));
            // B+reduce 完成:只点亮 director_b/reducer,保留 options 当前态(它独立完成)
            setLiveStages((s) => (s ? { ...s, director_b: "done", reducer: "done" } : s));
          } else if (ev.type === "options_proposed") {
            setOptions(ev.options);
            setLiveStages((s) => (s ? { ...s, options: "done" } : s));
          } else if (ev.type === "options_failed") {
            setLiveStages((s) => (s ? { ...s, options: "error" } : s));
            setLiveError({ step: "options", reason: ev.reason });
          } else if (ev.type === "draw_proposed") setProposals((p) => [...ev.proposals, ...p]);
          else if (ev.type === "error") {
            const step = parseErrStep(ev.reason);
            if (step) setLiveError({ step, reason: ev.reason });
            showToast(
              step
                ? `出错了(${STEP_LABEL[step]}),详情见导演工作台对应节点`
                : `出错了:${ev.reason}`,
            );
            patch((t) => ({ ...t, streaming: false, error: ev.reason }));
          }
        }, ac.signal);
      } catch (e) {
        if (onStory() && !api.isAbortError(e)) {
          // 主动取消(切故事)不算错;真实错误才提示
          showToast(`出错了:${String(e)}`);
          patch((t) => ({ ...t, error: String(e) }));
        }
      } finally {
        inflightRef.current.delete(ac);
        if (onStory()) {
          // 只有仍在本故事才收尾它的流式状态(切走了由 selectStory 负责重置)
          patch((t) => ({ ...t, streaming: false }));
          setTurnStreaming(false);
          setLiveStages(null);
          setLiveTurn(null);
          setContextsVersion((v) => v + 1); // 本轮上下文已落盘,可查看
          setDrawsVersion((v) => v + 1); // 本轮新提案已落库 → 绘图台刷新
        }
        refreshStories();
      }
    },
    [turnStreaming, refreshStories, showToast, latestTurn],
  );

  // ── 图片线:写稿 → 审阅 → 确认/复用/跳过(独立于文本线,不锁输入)──
  const openDraft = useCallback(async (scene: string, source: string) => {
    const id = curRef.current;
    if (!id) return;
    const key = uid();
    setDrafts((d) => [
      { key, draft: { type: "draft_ready", draft_id: "", scene, kind: "", prompt_text: "", refs: [], history: [] }, prompt: "", picked: [], source, status: "writing" },
      ...d,
    ]);
    try {
      const draft = await api.postDraw(id, { scene, source, source_turn: latestTurn }); // 归属到当前最新轮
      if ((draft as { detail?: string }).detail) {
        const detail = (draft as { detail?: string }).detail;
        setDrafts((d) => d.map((c) => (c.key === key ? { ...c, status: "failed", note: detail } : c)));
        showToast(`绘图写稿出错:${detail}`);
        return;
      }
      setDrafts((d) =>
        d.map((c) => (c.key === key ? { ...c, draft, prompt: draft.prompt_text, picked: toPicked(draft.refs), status: "review", warn: draft.warn_redraw_base } : c)),
      );
    } catch (e) {
      setDrafts((d) => d.map((c) => (c.key === key ? { ...c, status: "failed", note: String(e) } : c)));
      showToast(`绘图写稿出错:${String(e)}`);
    }
  }, [latestTurn, showToast]);

  // 提案制:从绘图台某条待办开稿(kind/截断轮按提案的 origin_turn,后端权威)。
  const openDraftForProposal = useCallback(async (proposalId: number, scene: string) => {
    const id = curRef.current;
    if (!id) return;
    const key = uid();
    setDrafts((d) => [
      { key, draft: { type: "draft_ready", draft_id: "", scene, kind: "", prompt_text: "", refs: [], history: [] }, prompt: "", picked: [], source: "director_b_proposal", status: "writing", proposalId },
      ...d,
    ]);
    try {
      const draft = await api.postDraw(id, { proposal_id: proposalId, source: "director_b_proposal" });
      if ((draft as { detail?: string }).detail) {
        const detail = (draft as { detail?: string }).detail;
        setDrafts((d) => d.map((c) => (c.key === key ? { ...c, status: "failed", note: detail } : c)));
        showToast(`绘图写稿出错:${detail}`);
        return;
      }
      setDrafts((d) =>
        d.map((c) => (c.key === key ? { ...c, draft, prompt: draft.prompt_text, picked: toPicked(draft.refs), status: "review", warn: draft.warn_redraw_base, proposalId } : c)),
      );
    } catch (e) {
      setDrafts((d) => d.map((c) => (c.key === key ? { ...c, status: "failed", note: String(e) } : c)));
      showToast(`绘图写稿出错:${String(e)}`);
    }
  }, [showToast]);

  const editDraftPrompt = useCallback((key: string, prompt: string) => {
    setDrafts((d) => d.map((c) => (c.key === key ? { ...c, prompt } : c)));
  }, []);

  const setDraftRefs = useCallback((key: string, picked: PickedRef[]) => {
    setDrafts((d) => d.map((c) => (c.key === key ? { ...c, picked } : c)));
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
    setScenesDrafts(snap.scenes_drafts ?? {});
    setSupersededImages(snap.superseded_images ?? []);
  }, []);

  const confirmDraft = useCallback(
    async (key: string) => {
      const id = curRef.current;
      const card = drafts.find((c) => c.key === key);
      if (!id || !card || !card.draft.draft_id) return;
      // 卡片转「后台生成中」并最小化;场景画廊放占位。文本线照常可用。
      setDrafts((d) => d.map((c) => (c.key === key ? { ...c, status: "submitted", note: "已提交,后台生成中" } : c)));
      const ac = new AbortController();
      inflightRef.current.add(ac);
      const onStory = () => curRef.current === id;
      try {
        await api.confirmDraw(id, { draft_id: card.draft.draft_id, prompt: card.prompt, references: card.picked }, (ev) => {
          if (!onStory()) return; // 切故事后旧故事的出图事件丢弃,避免串入新故事
          if (ev.type === "image_generating")
            setPending((p) => [...p, { request_id: ev.request_id, scene: ev.scene, status: "generating" }]);
          else if (ev.type === "image_ready") {
            setPending((p) => p.filter((x) => x.request_id !== ev.request_id));
            // 手动图(user_initiated)进「非正式」草稿桶,不混入正典 image_paths;提案图进正典桶。
            const bucket = card.source === "user_initiated" ? setScenesDrafts : setScenesImages;
            bucket((prev) => ({
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
            showToast(`绘图(gpt-image-2)出错:${ev.reason}`);
          }
        }, ac.signal);
      } catch (e) {
        if (onStory() && !api.isAbortError(e)) {
          setDrafts((d) => d.map((c) => (c.key === key ? { ...c, status: "failed", note: String(e) } : c)));
          showToast(`绘图(gpt-image-2)出错:${String(e)}`);
        }
      } finally {
        inflightRef.current.delete(ac);
      }
    },
    [drafts, silentRefresh, dropDraft, showToast],
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

  // 替代图片(旁路):不调 gpt-image-2,直接把指定/上传的图当作本次结果落库。
  // 归属比照真实出图:手动卡(user_initiated)进草稿桶、不进黑板;提案卡进正典桶。
  const substituteDraft = useCallback(
    async (key: string, pick: { imagegenId?: number; file?: File }) => {
      const id = curRef.current;
      const card = drafts.find((c) => c.key === key);
      if (!id || !card) return;
      try {
        const res = await api.substituteDraw(id, {
          scene: card.draft.scene,
          source: card.source,
          sourceTurn: card.draft.draw_turn ?? undefined,
          ...pick,
        });
        const bucket = card.source === "user_initiated" ? setScenesDrafts : setScenesImages;
        bucket((prev) => ({
          ...prev,
          [res.scene]: [...(prev[res.scene] ?? []).filter((u) => u !== res.output_path), res.output_path],
        }));
        silentRefresh();
        setDrawsVersion((v) => v + 1);
        dropDraft(key);
      } catch (e) {
        showToast(`替代图片出错:${String(e)}`);
        throw e;
      }
    },
    [drafts, silentRefresh, dropDraft, showToast],
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
    async (turnIndex: number, step: "director_a" | "writer" | "director_b" | "options", messages: { role: string; content: string }[]) => {
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
    setOptions([]); // 回退后旧选项不再适用
    setScopeTurn(r.new_latest_turn ?? null); // 跟到回退后的新最新轮(回退掉首轮则为 null)
    bumpAll();
    refreshStories();
  }, [turnStreaming, retrying, loadSnapshot, refreshStories]);

  const doRetry = useCallback(
    async (entry: AgentStep) => {
      const id = curRef.current;
      if (!id || turnStreaming || retrying || entry === "reducer") return;
      setRetrying(entry);
      setLiveError(null);
      try {
        await api.retry(id, entry); // 真 LLM 重走(用改后的输入记录),完成后刷新显微镜与叙事
        await loadSnapshot(id);
        setOptions([]); // 时间操作重置临时选项条;重走后的选项见工作台 Options 节点输出
        setScopeTurn(latestTurn);
        bumpAll();
      } catch (e) {
        // 失败保持原状;在工作台对应节点展示详情 + toast(entry 已排除 reducer)
        setLiveError({ step: entry, reason: String(e) });
        showToast(`重试出错(${STEP_LABEL[entry]}),详情见导演工作台对应节点`);
      } finally {
        setRetrying(null);
        refreshStories();
      }
    },
    [turnStreaming, retrying, latestTurn, loadSnapshot, refreshStories, showToast],
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

  // 弃掉「失败的提交」:它没落盘,纯前端清理(清错误详情 + 移除 error 占位 + 工作台回到最新正常轮),
  // 不碰后端、不动正常轮。
  const dismissFailure = useCallback(() => {
    setLiveError(null);
    setTurns((ts) => ts.filter((t) => !t.error));
    setScopeTurn(latestTurn);
  }, [latestTurn]);

  // ── 故事内设置(知识库 / 图库 / 模型,与故事绑定)──
  const [settingsOpen, setSettingsOpen] = useState(false);
  const openSettings = useCallback(() => setSettingsOpen(true), []);
  const closeSettings = useCallback(() => setSettingsOpen(false), []);

  return {
    stories, curId, title, blackboard, turns, scenesImages, scenesDrafts, supersededImages, proposals, drafts, pending, turnStreaming, options,
    refreshStories, selectStory, createStory, removeStory, submitTurn,
    openDraft, openDraftForProposal, editDraftPrompt, setDraftRefs, dropDraft, confirmDraft, decideDraft, substituteDraft, startDraftFromProposal,
    // 工作台 + 时间控制
    scopeOpen, scopeTurn, setScopeTurn, openScope, closeScope,
    liveStages, liveTurn, retrying, latestTurn, contextsVersion, drawsVersion, liveError, dismissFailure,
    writingIds, generatingIds, onWriting, onGenerating,
    doRollback, doRetry, doFork, saveStepContext, reloadScope,
    // 故事内设置
    settingsOpen, openSettings, closeSettings,
  };
}
