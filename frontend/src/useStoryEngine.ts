import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import * as api from "./api";
import { snapshotToTurns } from "./snapshot";
import { useToast } from "./components/Toast";
import type { AgentStep, Blackboard, Draft, DraftRef, DrawProposal, PickedRef, StepStatus, StoryInfo, TurnView } from "./types";

export type { TurnView };

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
const ALL_DONE_STAGES: LiveStages = {
  director_a: "done",
  writer: "done",
  director_b: "done",
  options: "done",
  reducer: "done",
};

/** 重试切入点 → 初始节点态:切入点及其下游标「运行中」,上游/旁支保持「完成」。 */
const retryInitialStages = (entry: AgentStep): LiveStages => {
  if (entry === "options") return { ...ALL_DONE_STAGES, options: "running" };
  if (entry === "director_b")
    return { director_a: "done", writer: "done", director_b: "running", options: "done", reducer: "pending" };
  if (entry === "writer")
    return { director_a: "done", writer: "running", director_b: "pending", options: "pending", reducer: "pending" };
  // director_a:整轮重来
  return { director_a: "running", writer: "pending", director_b: "pending", options: "pending", reducer: "pending" };
};

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
  instruction: string; // 用户对绘图写稿 Agent 的「附加指令」(出提示词前填,可改后重新生成)
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
  // 重新加载后检测到「后台仍有作业在跑」(刷新前发起的回合/重走)→ 进入恢复态:禁输入 + 占位提示,
  // 轮询直到作业落盘再对账。activeDraws = 仍在生成的图片数(展示进度,不阻塞输入)。
  const [recovering, setRecovering] = useState<{ kind: "turn" | "retry"; user_input?: string } | null>(null);
  const [activeDraws, setActiveDraws] = useState(0);
  // 正在写稿/出图的 proposal_id —— 提到引擎层(而非工作台本地),关掉重开工作台/切节点都不丢「运行中」。
  const [writingIds, setWritingIds] = useState<number[]>([]);
  const [generatingIds, setGeneratingIds] = useState<number[]>([]);

  // ── 导演工作台(显微镜)+ 时间控制 ──
  const [scopeOpen, setScopeOpen] = useState(false);
  const [scopeTurn, setScopeTurn] = useState<number | null>(null);
  const [liveStages, setLiveStages] = useState<LiveStages | null>(null);
  const [liveTurn, setLiveTurn] = useState<number | null>(null);
  // 本轮/重试中,各 agent 正在逐 token 流入的「原始输出」(A/B/选项是原始 JSON 文本;写手的实时成稿
  // 走叙事本身,不在此)。工作台对应节点据此实时滚动显示。新一轮/重试开始时清空。
  const [liveOutputs, setLiveOutputs] = useState<Partial<Record<AgentStep, string>>>({});
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
  // 恢复轮询的定时器(切故事/卸载时清)。
  const recoverTimer = useRef<number | null>(null);
  // 绘图稿最新值的镜像:供稳定回调(generateDraft)按 key 取卡,不必把 drafts 列进依赖。
  const draftsRef = useRef<DraftCard[]>([]);
  draftsRef.current = drafts;
  // 回合最新值的镜像:供流式重试回调按 turn_index 定位被重走轮(并保存其原叙事用于失败回滚)。
  const turnsRef = useRef<TurnView[]>([]);
  turnsRef.current = turns;

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

  // 「有在飞的请求」:文本流(出文/重试)、出图(gpt-image-2)、绘图写稿、提交中的草稿。
  // 用于刷新/关页前的拦截警告 —— 此刻离开可能丢结果或浪费已花的 API 额度。
  const busy = useMemo(
    () =>
      turnStreaming ||
      retrying !== null ||
      generatingIds.length > 0 ||
      writingIds.length > 0 ||
      pending.some((x) => x.status === "generating") ||
      drafts.some((d) => d.status === "writing" || d.status === "submitted"),
    [turnStreaming, retrying, generatingIds, writingIds, pending, drafts],
  );

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
    setTurns(snapshotToTurns(snap));
  }, []);

  const selectStory = useCallback(
    (id: string) => {
      // 切故事先 abort 旧故事在飞的流,再清掉所有「瞬时/进行中」状态(它们不属于新故事,
      // 也无法从快照重建);已落盘真相由 loadSnapshot 重新对账。
      inflightRef.current.forEach((ac) => ac.abort());
      inflightRef.current.clear();
      if (recoverTimer.current) {
        clearTimeout(recoverTimer.current); // 停掉上个故事的恢复轮询
        recoverTimer.current = null;
      }
      setRecovering(null);
      setActiveDraws(0);
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
      setLiveOutputs({});
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

  // 随时手动改标题:乐观更新当前标题 + 刷新书架(标题只是档案标记,不影响故事/不喂 agent)。
  const renameStory = useCallback(
    async (newTitle: string) => {
      const id = curRef.current;
      const t = newTitle.trim();
      if (!id || !t || t === title) return;
      setTitle(t);
      try {
        await api.renameStory(id, t);
      } catch (err) {
        showToast(`改名失败:${String(err)}`);
      }
      refreshStories();
    },
    [title, refreshStories, showToast],
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
      if (!id || turnStreaming || recovering) return; // 恢复中(后台仍有回合在跑)不许发新回合
      const key = uid();
      // 重新提交时先丢掉上一笔失败的尝试(未落盘),只保留正常轮 + 这条新流式轮。
      setTurns((ts) => [
        ...ts.filter((t) => !t.error),
        { key, user_input: text, narrative: "", beat_title: "", streaming: true },
      ]);
      setTurnStreaming(true);
      setLiveError(null); // 新一轮开始,清掉上次的失败详情
      setOptions([]); // 清掉上一轮的选项,本轮 options_proposed 再填
      setLiveOutputs({}); // 清掉上一轮各 agent 的实时输出缓冲
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
            // turn_started 先于 A 流 → A 运行中(逐 token),后续阶段随事件点亮
            setLiveStages({ director_a: "running", writer: "pending", director_b: "pending", options: "pending", reducer: "pending" });
          } else if (ev.type === "director_a_token")
            setLiveOutputs((o) => ({ ...o, director_a: (o.director_a ?? "") + ev.text }));
          else if (ev.type === "narrative_token") {
            patch((t) => ({ ...t, narrative: t.narrative + ev.text }));
            // 首个叙事 token → A 收尾、写手转运行中(一次性切换,后续 token 不重复 setState)
            setLiveStages((s) => (!s || s.writer === "running" ? s : { ...s, director_a: "done", writer: "running" }));
          } else if (ev.type === "narrative_done") {
            patch((t) => ({ ...t, narrative: ev.full_narrative }));
            // 成稿后 B 与 Options 并行启动 → 两节点同时点亮"运行中",各自完成各自变 done
            setLiveStages({ director_a: "done", writer: "done", director_b: "running", options: "running", reducer: "pending" });
          } else if (ev.type === "director_b_token")
            setLiveOutputs((o) => ({ ...o, director_b: (o.director_b ?? "") + ev.text }));
          else if (ev.type === "options_token")
            setLiveOutputs((o) => ({ ...o, options: (o.options ?? "") + ev.text }));
          else if (ev.type === "state_updated") {
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
          setLiveOutputs({});
          setContextsVersion((v) => v + 1); // 本轮上下文已落盘,可查看
          setDrawsVersion((v) => v + 1); // 本轮新提案已落库 → 绘图台刷新
        }
        refreshStories();
      }
    },
    [turnStreaming, recovering, refreshStories, showToast, latestTurn],
  );

  // ── 图片线:开卡(填附加指令)→ 生成提示词 → 审阅 → 确认/复用/跳过(独立于文本线,不锁输入)──
  // 先开一张「待生成」卡(不立即写稿),让用户在出提示词前先填「附加指令」,再点生成。
  const emptyDraft = (scene: string): Draft => ({ type: "draft_ready", draft_id: "", scene, kind: "", prompt_text: "", refs: [], history: [] });

  const openDraft = useCallback((scene: string, source: string) => {
    const key = uid();
    setDrafts((d) => [
      { key, draft: emptyDraft(scene), prompt: "", picked: [], source, status: "review", instruction: "" },
      ...d,
    ]);
  }, []);

  // 提案制:从绘图台某条待办开稿(kind/截断轮按提案的 origin_turn,后端权威)。
  const openDraftForProposal = useCallback((proposalId: number, scene: string) => {
    const key = uid();
    setDrafts((d) => [
      { key, draft: emptyDraft(scene), prompt: "", picked: [], source: "director_b_proposal", status: "review", instruction: "", proposalId },
      ...d,
    ]);
  }, []);

  const editDraftInstruction = useCallback((key: string, instruction: string) => {
    setDrafts((d) => d.map((c) => (c.key === key ? { ...c, instruction } : c)));
  }, []);

  // (重新)生成提示词:用卡上当前的「附加指令」让绘图写稿 Agent 出/重出稿。每次都是新建上下文。
  const generateDraft = useCallback(async (key: string) => {
    const id = curRef.current;
    if (!id) return;
    const card = draftsRef.current.find((c) => c.key === key);
    if (!card) return;
    const instruction = card.instruction.trim() || undefined;
    setDrafts((d) => d.map((c) => (c.key === key ? { ...c, status: "writing" } : c)));
    try {
      const opts: api.DrawOpts =
        card.proposalId != null
          ? { proposal_id: card.proposalId, source: "director_b_proposal", extra_instruction: instruction }
          : { scene: card.draft.scene, source: card.source, source_turn: latestTurn, extra_instruction: instruction };
      const draft = await api.postDraw(id, opts);
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

  // ── 刷新恢复:重新加载后若后台仍有作业在跑,进入恢复态并轮询,落盘后对账 ──
  const maybeRecover = useCallback(
    (id: string) => {
      let sawActive = false;
      const poll = async () => {
        if (curRef.current !== id) return; // 切走了 → 停
        let active;
        try {
          active = await api.getActive(id);
        } catch {
          recoverTimer.current = window.setTimeout(poll, 2000); // 网络抖动:稍后再试
          return;
        }
        if (curRef.current !== id) return;
        setRecovering(active.turn ? { kind: active.turn.kind, user_input: active.turn.user_input } : null);
        setActiveDraws(active.draws.length);
        if (active.turn != null || active.draws.length > 0) {
          sawActive = true;
          recoverTimer.current = window.setTimeout(poll, 1500); // 还在跑 → 继续轮询
        } else if (sawActive) {
          // 作业刚结束 → 以落盘真相对账(叙事/黑板/图/选项/上下文全刷新)
          await loadSnapshot(id);
          bumpAll();
        }
        // 首检即空(本来就没有活跃作业)→ 什么都不做,正常使用
      };
      poll();
    },
    [loadSnapshot],
  );

  // 选中某故事(快照已在 selectStory 里加载)后,探一次后台作业并按需轮询恢复;切故事/卸载清定时器。
  useEffect(() => {
    if (!curId) return;
    maybeRecover(curId);
    return () => {
      if (recoverTimer.current) {
        clearTimeout(recoverTimer.current);
        recoverTimer.current = null;
      }
    };
  }, [curId, maybeRecover]);

  // ── 时间控制(接 M4.5-C 后端能力)──
  const doRollback = useCallback(async () => {
    const id = curRef.current;
    if (!id || turnStreaming || retrying || recovering) return;
    const r = await api.rollback(id);
    if (r?.ok === false || r?.detail) return;
    await loadSnapshot(id);
    setOptions([]); // 回退后旧选项不再适用
    setScopeTurn(r.new_latest_turn ?? null); // 跟到回退后的新最新轮(回退掉首轮则为 null)
    bumpAll();
    refreshStories();
  }, [turnStreaming, retrying, recovering, loadSnapshot, refreshStories]);

  const doRetry = useCallback(
    async (entry: AgentStep) => {
      const id = curRef.current;
      if (!id || turnStreaming || retrying || recovering || entry === "reducer") return;
      // 定位被重走的轮(=最新已落盘轮)并存下原叙事/小标题,供失败回滚。
      const target = turnsRef.current.find((t) => t.turn_index === latestTurn);
      const targetKey = target?.key ?? null;
      const originalNarrative = target?.narrative ?? "";
      const originalBeat = target?.beat_title ?? "";
      const patchTarget = (fn: (t: TurnView) => TurnView) =>
        setTurns((ts) => ts.map((t) => (t.key === targetKey ? fn(t) : t)));
      // 写手参与的切入点(A/Writer)会重出叙事 → 先清空旧叙事并亮起光标,新 token 干净涌入;
      // 失败时回滚回 originalNarrative。B/Options 切入不动叙事。
      const rewritesNarrative = entry === "director_a" || entry === "writer";

      setRetrying(entry);
      setLiveError(null);
      setLiveOutputs({});
      setLiveStages(retryInitialStages(entry));
      setLiveTurn(latestTurn);
      setScopeTurn(latestTurn);
      if (rewritesNarrative && targetKey) patchTarget((t) => ({ ...t, narrative: "", streaming: true }));

      const ac = new AbortController();
      inflightRef.current.add(ac);
      const onStory = () => curRef.current === id;
      let errored: string | null = null;
      try {
        await api.streamRetry(
          id,
          entry as "director_a" | "writer" | "director_b" | "options",
          (ev) => {
            if (!onStory()) return;
            if (ev.type === "retry_started") {
              setLiveTurn(ev.turn_index);
              setScopeTurn(ev.turn_index);
            } else if (ev.type === "director_a_token")
              setLiveOutputs((o) => ({ ...o, director_a: (o.director_a ?? "") + ev.text }));
            else if (ev.type === "narrative_token") {
              patchTarget((t) => ({ ...t, narrative: t.narrative + ev.text, streaming: true }));
              setLiveStages((s) => (!s || s.writer === "running" ? s : { ...s, director_a: "done", writer: "running" }));
            } else if (ev.type === "narrative_done") {
              patchTarget((t) => ({ ...t, narrative: ev.full_narrative }));
              setLiveStages((s) => (s ? { ...s, director_a: "done", writer: "done", director_b: "running" } : s));
            } else if (ev.type === "director_b_token")
              setLiveOutputs((o) => ({ ...o, director_b: (o.director_b ?? "") + ev.text }));
            else if (ev.type === "options_token") {
              setLiveOutputs((o) => ({ ...o, options: (o.options ?? "") + ev.text }));
              setLiveStages((s) => (s ? { ...s, director_b: "done", options: "running" } : s));
            } else if (ev.type === "options_proposed") {
              setOptions(ev.options);
              setLiveStages((s) => (s ? { ...s, options: "done" } : s));
            } else if (ev.type === "options_failed") {
              setLiveStages((s) => (s ? { ...s, options: "error" } : s));
              setLiveError({ step: "options", reason: ev.reason });
            } else if (ev.type === "state_updated") {
              setBlackboard(ev.blackboard);
              setScenesImages((prev) => ({ ...prev, ...scenesImagesOf(ev.blackboard) }));
              patchTarget((t) => ({ ...t, beat_title: ev.beat_title }));
              setLiveStages((s) => (s ? { ...s, director_b: "done", reducer: "done" } : s));
            } else if (ev.type === "retry_done") {
              patchTarget((t) => ({ ...t, narrative: ev.narrative, streaming: false }));
            } else if (ev.type === "error") {
              errored = ev.reason;
            }
          },
          ac.signal,
        );
        if (!onStory()) return; // 切走了:由 selectStory 负责重置,别再写旧故事 state
        if (errored) {
          // 后端失败前 DB 未改 → 原轮完好;回滚显示到原叙事,在工作台对应节点展示详情。
          if (rewritesNarrative && targetKey)
            patchTarget((t) => ({ ...t, narrative: originalNarrative, beat_title: originalBeat, streaming: false }));
          const step = parseErrStep(errored) ?? entry;
          setLiveError({ step, reason: errored });
          showToast(`重试出错(${STEP_LABEL[step]}),详情见导演工作台对应节点`);
        } else {
          // 成功:以落盘真相对账(叙事/黑板/选项/上下文均刷新)。
          await loadSnapshot(id);
          setScopeTurn(latestTurn);
          bumpAll();
        }
      } catch (e) {
        if (onStory() && !api.isAbortError(e)) {
          if (rewritesNarrative && targetKey)
            patchTarget((t) => ({ ...t, narrative: originalNarrative, beat_title: originalBeat, streaming: false }));
          setLiveError({ step: entry, reason: String(e) });
          showToast(`重试出错(${STEP_LABEL[entry]}),详情见导演工作台对应节点`);
        }
      } finally {
        inflightRef.current.delete(ac);
        if (onStory()) {
          setRetrying(null);
          setLiveStages(null);
          setLiveTurn(null);
          setLiveOutputs({});
        }
        refreshStories();
      }
    },
    [turnStreaming, retrying, recovering, latestTurn, loadSnapshot, refreshStories, showToast],
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
    stories, curId, title, blackboard, turns, scenesImages, scenesDrafts, supersededImages, proposals, drafts, pending, turnStreaming, options, busy, recovering, activeDraws,
    refreshStories, selectStory, createStory, removeStory, renameStory, submitTurn,
    openDraft, openDraftForProposal, generateDraft, editDraftInstruction, editDraftPrompt, setDraftRefs, dropDraft, confirmDraft, decideDraft, substituteDraft, startDraftFromProposal,
    // 工作台 + 时间控制
    scopeOpen, scopeTurn, setScopeTurn, openScope, closeScope,
    liveStages, liveTurn, liveOutputs, retrying, latestTurn, contextsVersion, drawsVersion, liveError, dismissFailure,
    writingIds, generatingIds, onWriting, onGenerating,
    doRollback, doRetry, doFork, saveStepContext, reloadScope,
    // 故事内设置
    settingsOpen, openSettings, closeSettings,
  };
}
