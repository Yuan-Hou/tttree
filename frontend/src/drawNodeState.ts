import type { DrawItem, StepStatus } from "./types";

/** 显微镜里一条绘图支流(写稿节点 / 画图节点)的状态。
 *
 *  - `writingIds`:正在写稿/重写提示词的 proposal_id → 写稿节点亮「运行中」。
 *  - `generatingIds`:正在出图的 proposal_id → 画图节点亮「运行中」。
 *
 *  关键:两者都**按 proposal_id 索引,而非 scene_slug**。DrawProposal.id 跨轮唯一,所以同一场景
 *  在不同轮次的变体各有独立 id —— 一轮在写稿/出图不会把另一轮同场景的节点也点亮(修复跨轮串)。
 *  运行中优先于「已完成/待运行」,所以对已完成的提案重写/重绘也能看到节点变「运行中」。
 */
export function drawNodeStatuses(
  it: DrawItem,
  writingIds: number[],
  generatingIds: number[],
): { draft: StepStatus; img: StepStatus } {
  const has = (ids: number[]) => it.proposal_id != null && ids.includes(it.proposal_id);
  const draft: StepStatus = has(writingIds) ? "running" : it.status === "done" ? "done" : "pending";
  const img: StepStatus = has(generatingIds) ? "running" : it.status === "done" ? "done" : "pending";
  return { draft, img };
}
