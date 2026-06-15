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
  scenes_images: Record<string, string[]>; // 正典图(进黑板的提案图)
  scenes_drafts?: Record<string, string[]>; // 用户手动草稿图(origin=user_initiated,不进黑板)
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
  refs: DraftRef[]; // 绘图 Agent 建议的初始引用清单
  history: { semantic_name: string; image_path: string }[];
  library?: LibraryAsset[]; // 参考图库(RefPicker 来源一)
  past_images?: PastImage[]; // 过往绘制结果全列(RefPicker 来源二,含手动草稿)
  warn_redraw_base?: boolean; // 重绘 new_scene 且场景已有 variant → 需警告
  draw_turn?: number;
  proposal_id?: number | null;
}

// ── 绘图台:按场景聚合的待办(子步三)──
export interface ProposalRow {
  id: number;
  scene_slug: string;
  origin_proposal_turn: number;
  kind: string;
  status: "pending" | "done";
  reason: string;
  done_image_path: string | null;
}
export interface SceneMeta {
  name: string;
  has_new_scene: boolean;
  has_variant: boolean;
  exists: boolean;
}
export interface ProposalsResp {
  proposals: ProposalRow[];
  scenes: Record<string, SceneMeta>;
}

// ── 绘图节点拆分(写稿 / 画图)+ 参考图自由选择 ──
export interface LibraryAsset {
  asset_id: number;
  label: string;
  description: string;
  category: string;
  file_path: string;
}
export interface PastImage {
  imagegen_id: number;
  scene_slug: string;
  kind: string;
  output_path: string;
}
export interface ProposalDraw {
  proposal_id: number;
  scene_slug: string;
  kind: string;
  status: string;
  origin_proposal_turn: number;
  done_image_path: string | null;
  draft_messages: ContextMessage[]; // 写稿输入(可编辑分区)
  draft_prompt: string; // 写稿输出:提示词文本
  draft_manifest: DraftRef[]; // 写稿建议引用清单(初始参考图选择)
  variant_gated: boolean;
  warn_redraw_base: boolean;
  library: LibraryAsset[];
  past_images: PastImage[];
}
/** 用户在画图节点最终选定的一条参考图(两类来源)。 */
export interface PickedRef {
  source: "reference_asset" | "history_image";
  asset_id?: number | null;
  image_path?: string | null;
  semantic_name: string;
  purpose: string;
}

// ── 故事内设置:模型(子步一后端 → 子步四 UI)──
export interface ModelChoice {
  id: string;
  label: string;
  provider: string;
}
export interface StorySettings {
  default_model: string; // 全局默认模型 id
  overrides: Record<string, string>; // agent → 覆盖模型 id("" = 用全局默认)
  effective: Record<string, string>; // agent → 实际生效模型 id
  models: ModelChoice[]; // 可选模型清单
}

// ── 节点上下文(M4.5-B 读取接口 → M5-B HTTP 壳)──
export interface ContextMessage {
  role: string;
  content: string;
}

export interface StepContext {
  messages: ContextMessage[];
  output: unknown;
}

export interface TurnContexts {
  turn_index: number;
  user_input: string;
  beat_title: string;
  director_a: StepContext;
  writer: StepContext;
  director_b: StepContext;
}

export type AgentStep = "director_a" | "writer" | "director_b" | "reducer";
export type StepStatus = "pending" | "running" | "done";

// ── 绘图支流(每个绘图提案 = 写稿 + 绘图 双节点)。与绘图台同源:DrawProposal 表 ──
export interface TurnDrawProposal {
  id: number;
  scene_slug: string;
  kind: string;
  status: "pending" | "done";
  reason: string;
  origin_proposal_turn: number;
  done_image_path: string | null;
}
export interface TurnDraws {
  turn_index: number;
  proposals: TurnDrawProposal[];
}
/** UI 视图:一条绘图提案(pending 待绘制 / done 已画)。live 轮的提案尚未落库 → 无 proposal_id。 */
export interface DrawItem {
  key: string;
  proposal_id?: number;
  scene_slug: string;
  kind: string;
  reason?: string;
  status: "pending" | "done";
  done_image_path?: string | null;
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
