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

/** 阅读列视图模型:由 Snapshot.history 映射而来(见 snapshot.ts),
 *  也用于流式进行中的临时轮(turn_index 未定 / streaming / error)。 */
export interface TurnView {
  key: string;
  turn_index?: number;
  user_input: string;
  narrative: string;
  beat_title: string;
  streaming: boolean;
  error?: string;
}

export interface Snapshot {
  story_id: string;
  title: string;
  blackboard: Blackboard;
  scenes_images: Record<string, string[]>; // 正典图(进黑板的提案图)
  scenes_drafts?: Record<string, string[]>; // 用户手动草稿图(origin=user_initiated,不进黑板)
  superseded_images?: string[]; // 被取代的正典图路径:仍在画廊,标「被覆盖」而非「正典」
  latest_options?: string[]; // 最新一轮的下一步可选项(常驻,刷新/切故事后恢复)
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
  past_images: PastImage[]; // 过往生成结果全列(绘图台「替代图片」选图来源)
}
// 手动指定 picker:某轮可画的场景 + 各自 kind / variant 门控
export interface TurnSceneOpt {
  slug: string;
  name: string;
  kind: "new_scene" | "variant";
  variant_gated: boolean;
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

// ── 场景地图(静态,第一版):GET /story/{id}/scene-map ──
export interface SceneMapGalleryItem {
  path: string;
  turn: number | null; // 该图属于第几拍
  beat: string; // 该拍 beat 标题
}
export interface SceneMapNode {
  slug: string;
  name: string;
  origin_turn: number | null;
  image_paths: string[]; // 正典有效图(已剔除被取代图);与 gallery 同序,翻页用
  gallery?: SceneMapGalleryItem[]; // 逐图带轮次/beat 标注,翻页标注据此对齐
}
export interface SceneMapSolidEdge {
  from: string; // 上一轮落点(首轮=start 哨兵)
  to: string;
  turn_index: number;
  beat: string;
  image_path?: string | null; // 该轮为落点场景出的正典图(用于点对话聚焦时翻到对应图)
}
export interface SceneMapDashedEdge {
  a: string; // 无向相邻(a<b,已去重)
  b: string;
}
export interface SceneMap {
  start: string; // 虚拟「起点」节点的 slug 哨兵
  current_scene: string | null;
  nodes: SceneMapNode[];
  solid_edges: SceneMapSolidEdge[]; // 实线=每轮转移(边数==轮数)
  dashed_edges: SceneMapDashedEdge[]; // 虚线=空间相邻(装饰、无标签)
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
  options: StepContext; // Writer 后与 B 并行的叶子:下一步选项
}

export type AgentStep = "director_a" | "writer" | "director_b" | "options" | "reducer";
export type StepStatus = "pending" | "running" | "done" | "error";

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
  | { type: "options_proposed"; options: string[] }
  | { type: "options_failed"; reason: string }
  | { type: "turn_done" }
  | { type: "error"; reason: string };

// ── 图片线 confirm SSE 事件 ──
export type DrawEvent =
  | { type: "image_generating"; scene: string; request_id: string }
  | { type: "image_ready"; scene: string; image_path: string; api_call: string; request_id: string }
  | { type: "image_failed"; scene: string; reason: string; request_id: string };
