import { describe, expect, it } from "vitest";
import { drawNodeStatuses } from "./drawNodeState";
import type { DrawItem } from "./types";

const item = (over: Partial<DrawItem>): DrawItem => ({
  key: "k",
  scene_slug: "X",
  kind: "variant",
  status: "done",
  ...over,
});

describe("drawNodeStatuses", () => {
  it("跨轮同场景不串:只点亮正在出图的那个 proposal,另一轮同场景节点不受影响", () => {
    // 同一场景 X 的两个变体,分属不同历史轮 → proposal_id 不同(5=第二轮, 9=第三轮)。
    const turn2 = item({ proposal_id: 5, scene_slug: "X" });
    const turn3 = item({ proposal_id: 9, scene_slug: "X" });
    const generating = [5]; // 只有第二轮的提案在出图

    expect(drawNodeStatuses(turn2, [], generating).img).toBe("running");
    // 关键断言:第三轮同场景的画图节点不应被点亮(旧实现按 scene_slug 索引会误判 running)
    expect(drawNodeStatuses(turn3, [], generating).img).toBe("done");
  });

  it("未运行时:done→done, pending→pending", () => {
    expect(drawNodeStatuses(item({ proposal_id: 1, status: "done" }), [], []).img).toBe("done");
    expect(drawNodeStatuses(item({ proposal_id: 2, status: "pending" }), [], []).img).toBe("pending");
  });

  it("已完成的提案重绘也能看到画图节点变 running(运行中优先于 done)", () => {
    expect(drawNodeStatuses(item({ proposal_id: 3, status: "done" }), [], [3]).img).toBe("running");
  });

  it("写稿/重写中 → 写稿节点 running(且不影响画图节点)", () => {
    const s = drawNodeStatuses(item({ proposal_id: 7, status: "done" }), [7], []);
    expect(s.draft).toBe("running"); // 已完成的提案重写,写稿节点也变 running
    expect(s.img).toBe("done"); // 画图节点不受写稿影响
  });

  it("live 提案无 proposal_id → 永不判为 running", () => {
    expect(drawNodeStatuses(item({ proposal_id: undefined, status: "pending" }), [5], [5]).img).toBe("pending");
    expect(drawNodeStatuses(item({ proposal_id: undefined, status: "pending" }), [5], [5]).draft).toBe("pending");
  });
});
