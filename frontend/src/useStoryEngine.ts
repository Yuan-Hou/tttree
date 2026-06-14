import { useCallback, useEffect, useRef, useState } from "react";
import * as api from "./api";
import type { Blackboard, Draft, DrawProposal, StoryInfo } from "./types";

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

  const curRef = useRef<string | null>(null);
  curRef.current = curId;

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
      const patch = (fn: (t: TurnView) => TurnView) =>
        setTurns((ts) => ts.map((t) => (t.key === key ? fn(t) : t)));
      try {
        await api.streamTurn(id, text, (ev) => {
          if (ev.type === "turn_started") patch((t) => ({ ...t, turn_index: ev.turn_index }));
          else if (ev.type === "narrative_token")
            patch((t) => ({ ...t, narrative: t.narrative + ev.text }));
          else if (ev.type === "narrative_done")
            patch((t) => ({ ...t, narrative: ev.full_narrative }));
          else if (ev.type === "state_updated") {
            setBlackboard(ev.blackboard);
            setScenesImages((prev) => ({ ...prev, ...scenesImagesOf(ev.blackboard) }));
            patch((t) => ({ ...t, beat_title: ev.beat_title }));
          } else if (ev.type === "draw_proposed")
            setProposals((p) => [...ev.proposals, ...p]);
          else if (ev.type === "error") patch((t) => ({ ...t, streaming: false, error: ev.reason }));
        });
      } catch (e) {
        patch((t) => ({ ...t, error: String(e) }));
      } finally {
        patch((t) => ({ ...t, streaming: false }));
        setTurnStreaming(false);
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
      const draft = await api.postDraw(id, scene, source);
      if ((draft as { detail?: string }).detail) {
        setDrafts((d) =>
          d.map((c) => (c.key === key ? { ...c, status: "failed", note: (draft as { detail?: string }).detail } : c)),
        );
        return;
      }
      setDrafts((d) =>
        d.map((c) => (c.key === key ? { ...c, draft, prompt: draft.prompt_text, status: "review" } : c)),
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

  return {
    stories, curId, title, blackboard, turns, scenesImages, proposals, drafts, pending, turnStreaming,
    refreshStories, selectStory, createStory, removeStory, submitTurn,
    openDraft, editDraftPrompt, dropDraft, confirmDraft, decideDraft, startDraftFromProposal,
  };
}
