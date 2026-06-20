/** 场景地图的「紧凑布局」:力导向(Fruchterman-Reingold)+ 向心引力(求紧凑)+ 矩形去重叠
 *  (大卡片不互相压)。纯函数、无随机、由调用方给的种子坐标起算 → 同一图每次结果一致(可重复「重置到」)。
 *
 *  坐标用节点左上角(与 React Flow node.position 一致):内部换算成中心做力学,末了再换回左上角,
 *  并整体平移到一个体面的画布原点。自环 / 平行边对布局无意义,调用方去重后传入即可(这里也再兜一次)。 */

export interface LayoutNode {
  id: string;
  x: number; // 种子左上角
  y: number;
  w: number; // 节点尺寸(去重叠用)
  h: number;
}
export interface LayoutLink {
  a: string;
  b: string;
}

const K = 240; // 理想边长(≈ 卡片宽 + 小间隙):吸引/排斥在此距离平衡
const REPULSE_MAX = K * 2.4; // 斥力作用半径上限:超出不再相斥 → 折叠后的两端不会互相把团撑开
const ITER = 500; // 力学迭代次数
const GRAVITY = 0.035; // 向心引力强度(越大越紧凑、越聚拢)
const SEED_ASPECT = 1.5; // 蛇形种子的目标宽高比(略宽,贴近视口)
const SEED_GAP = 26; // 蛇形种子格间隙
const OVERLAP_PAD = 24; // 去重叠后卡片之间至少留的空白
const OVERLAP_ITER = 140; // 去重叠迭代上限
const ORIGIN_X = 60; // 归一化后最左卡片的左缘 x
const ORIGIN_Y = 30; // 最上卡片的上缘 y

export function compactLayout(nodes: LayoutNode[], links: LayoutLink[]): Map<string, { x: number; y: number }> {
  const out = new Map<string, { x: number; y: number }>();
  const n = nodes.length;
  if (n === 0) return out;

  // 内部以中心坐标演算
  const p = nodes.map((d) => ({ x: d.x + d.w / 2, y: d.y + d.h / 2, w: d.w, h: d.h }));
  const idx = new Map(nodes.map((d, i) => [d.id, i]));

  // 边去重(无向、去自环、去未知端点)
  const edges: [number, number][] = [];
  const seen = new Set<string>();
  for (const { a, b } of links) {
    const ia = idx.get(a);
    const ib = idx.get(b);
    if (ia == null || ib == null || ia === ib) continue;
    const key = ia < ib ? `${ia}-${ib}` : `${ib}-${ia}`;
    if (seen.has(key)) continue;
    seen.add(key);
    edges.push([ia, ib]);
  }

  // 蛇形种子:按时间线阅读序(种子 x→y)把节点折进一个近方块的网格,折行时蛇形回转 →
  // 链上相邻节点在网格里也相邻(边都短)、整体包围盒小。折叠态本身就是更低能量,力学随后只做
  // 自然松弛而不会把长链重新拉直(这正是「长线形反而不紧凑」的根因修复)。
  const order = [...Array(n).keys()].sort((i, j) => p[i].x - p[j].x || p[i].y - p[j].y);
  const cellW = Math.max(...p.map((d) => d.w)) + SEED_GAP;
  const cellH = Math.max(...p.map((d) => d.h)) + SEED_GAP;
  const cols = Math.max(1, Math.round(Math.sqrt(n * SEED_ASPECT)));
  order.forEach((node, k) => {
    const row = Math.floor(k / cols);
    const inRow = k % cols;
    const col = row % 2 === 0 ? inRow : cols - 1 - inRow; // 偶行→右、奇行←左,蛇形回转
    p[node].x = col * cellW;
    p[node].y = row * cellH;
  });

  const cx0 = p.reduce((s, d) => s + d.x, 0) / n;
  const cy0 = p.reduce((s, d) => s + d.y, 0) / n;
  let temp = K;

  for (let it = 0; it < ITER; it++) {
    const dx = new Float64Array(n);
    const dy = new Float64Array(n);

    // 斥力:所有点对,f = K²/d
    for (let i = 0; i < n; i++) {
      for (let j = i + 1; j < n; j++) {
        let vx = p[i].x - p[j].x;
        let vy = p[i].y - p[j].y;
        const dist = Math.hypot(vx, vy) || 0.01;
        if (dist > REPULSE_MAX) continue; // 超出作用半径:不相斥 → 允许长链折叠回来、团不被远端撑大
        const f = (K * K) / dist;
        vx /= dist;
        vy /= dist;
        dx[i] += vx * f;
        dy[i] += vy * f;
        dx[j] -= vx * f;
        dy[j] -= vy * f;
      }
    }
    // 引力:沿边,f = d²/K
    for (const [a, b] of edges) {
      let vx = p[a].x - p[b].x;
      let vy = p[a].y - p[b].y;
      const dist = Math.hypot(vx, vy) || 0.01;
      const f = (dist * dist) / K;
      vx /= dist;
      vy /= dist;
      dx[a] -= vx * f;
      dy[a] -= vy * f;
      dx[b] += vx * f;
      dy[b] += vy * f;
    }
    // 向心引力:整体往质心收 → 紧凑、且把不连通分量也拢到一处
    for (let i = 0; i < n; i++) {
      dx[i] += (cx0 - p[i].x) * GRAVITY;
      dy[i] += (cy0 - p[i].y) * GRAVITY;
    }
    // 落位(每步位移受温度上限,逐步降温收敛)
    for (let i = 0; i < n; i++) {
      const d = Math.hypot(dx[i], dy[i]) || 0.01;
      const m = Math.min(d, temp);
      p[i].x += (dx[i] / d) * m;
      p[i].y += (dy[i] / d) * m;
    }
    temp = Math.max(temp * 0.985, 1.5);
  }

  // 去重叠:两卡片包围盒(含 PAD)相交则沿重叠较小的轴对推开,迭代到无重叠
  for (let it = 0; it < OVERLAP_ITER; it++) {
    let moved = false;
    for (let i = 0; i < n; i++) {
      for (let j = i + 1; j < n; j++) {
        const ox = (p[i].w + p[j].w) / 2 + OVERLAP_PAD - Math.abs(p[i].x - p[j].x);
        const oy = (p[i].h + p[j].h) / 2 + OVERLAP_PAD - Math.abs(p[i].y - p[j].y);
        if (ox > 0 && oy > 0) {
          moved = true;
          if (ox < oy) {
            const s = ((p[i].x < p[j].x ? -1 : 1) * ox) / 2;
            p[i].x += s;
            p[j].x -= s;
          } else {
            const s = ((p[i].y < p[j].y ? -1 : 1) * oy) / 2;
            p[i].y += s;
            p[j].y -= s;
          }
        }
      }
    }
    if (!moved) break;
  }

  // 归一化:把最小左上角平移到 (ORIGIN_X, ORIGIN_Y),输出回左上角坐标
  let minLeft = Infinity;
  let minTop = Infinity;
  for (const d of p) {
    minLeft = Math.min(minLeft, d.x - d.w / 2);
    minTop = Math.min(minTop, d.y - d.h / 2);
  }
  const offX = ORIGIN_X - minLeft;
  const offY = ORIGIN_Y - minTop;
  nodes.forEach((d, i) => {
    out.set(d.id, { x: p[i].x - d.w / 2 + offX, y: p[i].y - d.h / 2 + offY });
  });
  return out;
}
