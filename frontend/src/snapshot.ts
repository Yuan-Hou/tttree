import type { Snapshot, TurnView } from "./types";

/** Snapshot.history → 阅读列视图模型。
 *  提取为纯函数:实时引擎(useStoryEngine)与导出查看器(viewer)共用同一份映射,
 *  阅读列将来新增的字段在两处自动保持一致。 */
export function snapshotToTurns(snap: Snapshot): TurnView[] {
  return (snap.history ?? []).map((t) => ({
    key: `h${t.turn_index}`,
    turn_index: t.turn_index,
    user_input: t.user_input,
    narrative: t.narrative,
    beat_title: t.beat_title,
    streaming: false,
  }));
}
