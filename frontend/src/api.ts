// 同源调用(开发期由 Vite proxy 转发到 :8000;部署期与 FastAPI 同源)。
import type { Draft, DrawEvent, Snapshot, StoryInfo, TurnEvent } from "./types";

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

export const postDraw = (id: string, scene: string, source: string) =>
  fetch(`/story/${id}/draw`, {
    method: "POST",
    headers: json,
    body: JSON.stringify({ scene, source }),
  }).then((r) => r.json() as Promise<Draft & { detail?: string }>);

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
