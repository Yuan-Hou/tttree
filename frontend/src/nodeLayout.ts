/** React Flow 节点的手动布局 —— 纯前端本地偏好,只存浏览器 localStorage,不碰后端、不进 fork/delete。
 *
 * 坐标按 `scope`(一个字符串)分桶,桶内按节点 id 索引。调用方决定 scope 的含义:
 *   - 导演工作台:`${storyId}.${turn}` —— 工作台节点按轮动态生成(同一 `draw:0:prompt` 在不同轮指向
 *     不同提案),必须按 (故事, 轮) 分桶才不串位。
 *   - 故事地图:`${storyId}.map` —— 地图是一张随故事整体生长的图,不分轮。
 * 两类 scope 天然不冲突(轮是数字,"map" 不是)。某节点没存过坐标 → 调用方回退到自动布局位置
 * (首开仍是自动布局,拖过的才记住)。 */

export type Pos = { x: number; y: number };
export type NodePositions = Record<string, Pos>;

const bucketKey = (scope: string) => `vore.nodepos.v1.${scope}`;

export function loadPositions(scope: string): NodePositions {
  try {
    const raw = localStorage.getItem(bucketKey(scope));
    if (!raw) return {};
    const o = JSON.parse(raw) as unknown;
    if (!o || typeof o !== "object") return {};
    const out: NodePositions = {};
    for (const [id, v] of Object.entries(o as Record<string, unknown>)) {
      if (v && typeof v === "object" && typeof (v as Pos).x === "number" && typeof (v as Pos).y === "number") {
        out[id] = { x: (v as Pos).x, y: (v as Pos).y };
      }
    }
    return out;
  } catch {
    return {}; // 解析坏数据 / 隐私模式禁用 localStorage → 优雅降级到自动布局
  }
}

export function savePosition(scope: string, nodeId: string, pos: Pos): void {
  try {
    const cur = loadPositions(scope);
    cur[nodeId] = { x: Math.round(pos.x), y: Math.round(pos.y) };
    localStorage.setItem(bucketKey(scope), JSON.stringify(cur));
  } catch {
    /* 配额满 / 禁用 → 忽略,这只是本地偏好 */
  }
}

/** 清掉某 scope 的全部手动坐标 → 回到自动布局。 */
export function clearPositions(scope: string): void {
  try {
    localStorage.removeItem(bucketKey(scope));
  } catch {
    /* 忽略 */
  }
}
