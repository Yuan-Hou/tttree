// 同源调用(开发期由 Vite proxy 转发到 :8000;部署期与 FastAPI 同源)。
import type {
  ContextMessage,
  Draft,
  DrawEvent,
  PickedRef,
  ProposalDraw,
  ProposalsResp,
  Snapshot,
  StoryInfo,
  TurnContexts,
  TurnDraws,
  TurnEvent,
} from "./types";

const json = { "Content-Type": "application/json" };

async function jget<T>(url: string): Promise<T> {
  const r = await fetch(url);
  if (!r.ok) throw new Error(`${r.status} ${await r.text()}`);
  return r.json();
}

/** 生成图/参考图按相对路径(storage/...)存,后端挂在 /storage;前缀一个 / 即可。 */
export const imgUrl = (rel: string): string => "/" + rel.replace(/^\/+/, "");

export const listStories = () => jget<StoryInfo[]>("/stories");

export const createStory = (title: string) =>
  fetch("/stories", { method: "POST", headers: json, body: JSON.stringify({ title }) }).then(
    (r) => r.json() as Promise<StoryInfo>,
  );

export const deleteStory = (id: string) => fetch(`/stories/${id}`, { method: "DELETE" });

export const renameStory = (id: string, title: string) =>
  fetch(`/stories/${id}`, { method: "PATCH", headers: json, body: JSON.stringify({ title }) }).then(
    (r) => r.json() as Promise<StoryInfo>,
  );

export const getSnapshot = (id: string) => jget<Snapshot>(`/story/${id}/snapshot`);

// ── 时间控制 + 节点上下文(M5-B)──
export const getTurnContexts = (id: string, turnIndex: number) =>
  jget<TurnContexts>(`/story/${id}/turn/${turnIndex}/contexts`);

export const getTurnDraws = (id: string, turnIndex: number) =>
  jget<TurnDraws>(`/story/${id}/turn/${turnIndex}/draws`);

export const getStoryProposals = (id: string) => jget<ProposalsResp>(`/story/${id}/proposals`);

// ── 绘图节点拆分:写稿(DeepSeek)/ 画图(gpt-image-2)──
export const getProposalDraw = (id: string, pid: number) =>
  jget<ProposalDraw>(`/story/${id}/draw/proposal/${pid}`);

/** 写稿(重)跑:输出提示词文本。messages 给定=用编辑后的输入重写。 */
export const writeDraft = (id: string, pid: number, messages?: ContextMessage[]) =>
  fetch(`/story/${id}/draw/proposal/${pid}/write`, {
    method: "POST",
    headers: json,
    body: JSON.stringify({ messages: messages ?? null }),
  }).then(async (r) => {
    if (!r.ok) throw new Error(`${r.status} ${await r.text()}`);
    return r.json() as Promise<{ draft_prompt: string; draft_manifest: import("./types").DraftRef[]; draft_messages: ContextMessage[]; warn_redraw_base: boolean }>;
  });

export const saveDraftMessages = (id: string, pid: number, messages: ContextMessage[]) =>
  fetch(`/story/${id}/draw/proposal/${pid}/draft-messages`, {
    method: "PUT",
    headers: json,
    body: JSON.stringify({ messages }),
  }).then((r) => r.json());

/** 画图(重)出图:用确认的提示词 + 自由选择的参考图调 gpt-image-2(短命 SSE,确认即花钱)。 */
export const pictureDraw = (
  id: string,
  pid: number,
  body: { prompt: string; references: PickedRef[] },
  onEvent: (e: DrawEvent) => void,
) => streamSSE<DrawEvent>(`/story/${id}/draw/proposal/${pid}/picture`, body, onEvent);

/** 改写某步的输入记录(直接改 M4.5-B 存的那份;仅最新轮)。 */
export const saveStepContext = (
  id: string,
  turnIndex: number,
  step: "director_a" | "writer" | "director_b",
  messages: ContextMessage[],
) =>
  fetch(`/story/${id}/turn/${turnIndex}/contexts/${step}`, {
    method: "PUT",
    headers: json,
    body: JSON.stringify({ messages }),
  }).then(async (r) => {
    if (!r.ok) throw new Error(`${r.status} ${await r.text()}`);
    return r.json();
  });

export const rollback = (id: string) =>
  fetch(`/story/${id}/rollback`, { method: "POST", headers: json }).then((r) => r.json());

export const retry = (id: string, entry: "director_a" | "writer" | "director_b") =>
  fetch(`/story/${id}/retry`, { method: "POST", headers: json, body: JSON.stringify({ entry }) }).then(
    async (r) => {
      if (!r.ok) throw new Error(`${r.status} ${await r.text()}`);
      return r.json();
    },
  );

export const forkStory = (id: string) =>
  fetch(`/stories/${id}/fork`, { method: "POST", headers: json }).then((r) => r.json() as Promise<StoryInfo>);

export interface DrawOpts {
  proposal_id?: number; // 提案制:kind/截断轮从提案取
  scene?: string; // 临时制:直接画某场景
  source?: string;
  source_turn?: number | null; // 截断/归属轮
}
export const postDraw = (id: string, opts: DrawOpts) =>
  fetch(`/story/${id}/draw`, { method: "POST", headers: json, body: JSON.stringify(opts) }).then(
    (r) => r.json() as Promise<Draft & { detail?: string }>,
  );

/** reuse / skip — 同步 JSON,不出图、不花钱。 */
export const decideDraw = (
  id: string,
  body: { draft_id: string; decision: "reuse" | "skip"; prompt?: string; reuse_image_path?: string },
) => fetch(`/story/${id}/draw/confirm`, { method: "POST", headers: json, body: JSON.stringify(body) }).then((r) => r.json());

/** 通用 SSE:fetch + ReadableStream,逐帧(\n\n 分隔)解析 `data: {json}`。 */
async function streamSSE<E>(url: string, body: unknown, onEvent: (e: E) => void): Promise<void> {
  const resp = await fetch(url, { method: "POST", headers: json, body: JSON.stringify(body) });
  if (!resp.body) throw new Error("无响应流");
  const reader = resp.body.getReader();
  const dec = new TextDecoder();
  let buf = "";
  for (;;) {
    const { done, value } = await reader.read();
    if (done) break;
    buf += dec.decode(value, { stream: true });
    let i: number;
    while ((i = buf.indexOf("\n\n")) >= 0) {
      const frame = buf.slice(0, i);
      buf = buf.slice(i + 2);
      const line = frame.split("\n").find((l) => l.startsWith("data: "));
      if (line) onEvent(JSON.parse(line.slice(6)) as E);
    }
  }
}

export const streamTurn = (id: string, userInput: string, onEvent: (e: TurnEvent) => void) =>
  streamSSE<TurnEvent>(`/story/${id}/turn`, { user_input: userInput }, onEvent);

/** confirm 出图(花钱)→ 短命 SSE 流。confirm 是唯一通往真实出图的路径。 */
export const confirmDraw = (
  id: string,
  body: { draft_id: string; prompt: string },
  onEvent: (e: DrawEvent) => void,
) => streamSSE<DrawEvent>(`/story/${id}/draw/confirm`, { ...body, decision: "confirm" }, onEvent);
