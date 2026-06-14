// 后端契约的镜像(见 backend/app/web/*_router.py)。只取前端用得到的字段。

export interface StoryInfo {
  id: string;
  title: string;
  created_at: string;
  last_active_at: string;
  turn_count: number;
}

export interface SceneState {
  name?: string;
  state?: string;
  base_prompt?: string;
  connections?: string[];
  image_paths?: string[];
  origin_turn?: number;
}

export interface CharacterState {
  location?: string;
  status?: string;
  inventory?: string[];
  appearance?: string;
}

export interface ItemState {
  owner?: string;
  where?: string;
  desc?: string;
}

export interface Note {
  content?: string;
  since_beat?: string;
}

export interface Blackboard {
  story_meta?: { title?: string; current_scene?: string; latest_beat?: string };
  scenes?: Record<string, SceneState>;
  characters?: Record<string, CharacterState>;
  items?: Record<string, ItemState>;
  notes?: Note[];
}

export interface HistoryTurn {
  turn_index: number;
  user_input: string;
  narrative: string;
  beat_title: string;
}

export interface Snapshot {
  story_id: string;
  title: string;
  blackboard: Blackboard;
  scenes_images: Record<string, string[]>;
  history: HistoryTurn[];
}

export interface DrawProposal {
  scene_slug: string;
  kind: string;
  reason: string;
}

export interface DraftRef {
  semantic_name: string;
  source: string;
  purpose: string;
  asset_id: number | null;
  image_path: string | null;
  preview_path: string | null;
}

export interface Draft {
  type: "draft_ready";
  draft_id: string;
  scene: string;
  kind: string;
  prompt_text: string;
  refs: DraftRef[];
  history: { semantic_name: string; image_path: string }[];
}

// ── 文本线 SSE 事件 ──
export type TurnEvent =
  | { type: "turn_started"; turn_index: number }
  | { type: "narrative_token"; text: string }
  | { type: "narrative_done"; full_narrative: string }
  | { type: "state_updated"; blackboard: Blackboard; beat_title: string }
  | { type: "draw_proposed"; proposals: DrawProposal[] }
  | { type: "turn_done" }
  | { type: "error"; reason: string };

// ── 图片线 confirm SSE 事件 ──
export type DrawEvent =
  | { type: "image_generating"; scene: string; request_id: string }
  | { type: "image_ready"; scene: string; image_path: string; api_call: string; request_id: string }
  | { type: "image_failed"; scene: string; reason: string; request_id: string };
