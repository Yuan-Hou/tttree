"""场景地图(静态,第一版)的纯组装逻辑。

从「最新黑板 + Turn 表」一次性算出三块,不新增任何持久字段、不动写路径:

- nodes:遍历最新黑板 scenes,每个场景一个节点(slug/name/origin_turn/image_paths)。
- solid_edges(实线=每轮转移):遍历 Turn(按 turn_index),每轮一条
  {from, to, turn_index, beat}。to=本轮 blackboard_after 的 current_scene;
  from=上一轮的 current_scene;首轮的 from=虚拟「起点」哨兵 START_SLUG。
  允许 from==to(自环)、允许多条平行边。边数恒等于轮数。
- dashed_edges(虚线=空间相邻):每个场景的 connections 取无向对,去重(A-B==B-A)、
  过滤悬空(指向已不存在场景)与自指。

把它做成纯函数(输入已解析好的 dict),便于直接单测,与 DB / HTTP 解耦。
"""

from __future__ import annotations

# 虚拟「起点」节点的 slug。它不是任何真实场景,只作首轮实线的源点。
# 前端据此合成一个克制的「起点」节点;不混入 nodes(nodes 纯由黑板 scenes 组成)。
START_SLUG = "__start__"


def build_scene_map(blackboard: dict, turns: list[dict], canon_images: list[dict] | None = None) -> dict:
    """组装场景地图。

    blackboard: 最新黑板 dict。
    turns: 已按 turn_index 升序的列表,每项 {turn_index, beat_title, bb_after}。
           bb_after 为该轮 blackboard_after 解析后的 dict(无法解析时传 {})。
    canon_images: 正典图(director_b_proposal)记录
           [{source_turn, scene_slug, output_path, superseded}],按出图先后(id 升序)排列。
           用途有二:① 给每条实线标注「该轮为落点场景出的那张有效图」→ 前端点对话可聚焦并翻到对应图;
           ② 给每个节点的 gallery 标注「这张图属于哪一拍」。被取代(superseded)的图一律从地图
           gallery 过滤掉 —— 地图只呈现「故事当前有效的正典状态时间线」,被覆盖图的展示职责留给
           「场景与画」面板,地图不重复承担历史归档。
    """
    scenes: dict = blackboard.get("scenes") or {}
    current_scene = (blackboard.get("story_meta") or {}).get("current_scene")

    # 被取代的正典图路径:从地图 gallery 过滤掉
    superseded_paths = {
        ci.get("output_path")
        for ci in (canon_images or [])
        if ci.get("superseded") and ci.get("output_path")
    }
    # 路径 → 其产出轮;轮 → beat 标题。供 gallery 标注「第N拍 · beat」
    path_turn: dict[str, int] = {}
    for ci in canon_images or []:
        p, st = ci.get("output_path"), ci.get("source_turn")
        if p and st is not None:
            path_turn[p] = st
    turn_beat: dict[int, str] = {
        t.get("turn_index"): (t.get("beat_title") or "") for t in turns
    }

    # (轮, 场景) → 该轮为该场景出的**有效**正典图路径(跳过被取代的;同键多张取最新一张)。
    # canon_images 已按 id 升序,故同 (轮,场景) 下后写覆盖前写 → 落到最新有效图。
    img_by_turn_scene: dict[tuple[int, str], str] = {}
    for ci in canon_images or []:
        if ci.get("superseded"):
            continue
        st, sc, p = ci.get("source_turn"), ci.get("scene_slug"), ci.get("output_path")
        if st is not None and sc and p:
            img_by_turn_scene[(st, sc)] = p

    # 每场景过滤掉被取代图后的有效图集(image_paths 与 gallery 同序对齐,翻页索引一致)
    valid_by_slug: dict[str, list[str]] = {
        slug: [p for p in (sc.get("image_paths") or []) if p not in superseded_paths]
        for slug, sc in scenes.items()
    }

    nodes = [
        {
            "slug": slug,
            "name": sc.get("name") or slug,
            "origin_turn": sc.get("origin_turn"),
            "image_paths": valid_by_slug[slug],  # 已剔除被取代图;空列表优雅处理
            # gallery:与 image_paths 同序,逐图带「该图属于第几拍 + beat」,翻页标注据此对齐
            "gallery": [
                {"path": p, "turn": path_turn.get(p), "beat": turn_beat.get(path_turn.get(p), "")}
                for p in valid_by_slug[slug]
            ],
        }
        for slug, sc in scenes.items()
    ]
    node_slugs = {n["slug"] for n in nodes}

    # ── 实线:每轮一条,边数恒等于轮数 ──
    solid_edges: list[dict] = []
    prev: str | None = None  # 上一轮落点场景(首轮为 None → 用起点哨兵)
    for t in turns:
        bb_after = t.get("bb_after") or {}
        to_raw = (bb_after.get("story_meta") or {}).get("current_scene")
        src = prev if prev is not None else START_SLUG
        dst = to_raw or src  # current_scene 缺失时优雅退化为自环,边仍有合法端点
        ti = t.get("turn_index")
        # 该轮为落点场景出的有效正典图(且仍在该场景过滤后的有效图集里 → 前端翻页索引有效)
        img = img_by_turn_scene.get((ti, dst))
        if img not in valid_by_slug.get(dst, []):
            img = None
        solid_edges.append(
            {
                "from": src,
                "to": dst,
                "turn_index": ti,
                "beat": t.get("beat_title") or "",
                "image_path": img,
            }
        )
        if to_raw:
            prev = to_raw

    # ── 虚线:无向相邻,去重 + 过滤悬空/自指 ──
    seen: set[tuple[str, str]] = set()
    dashed_edges: list[dict] = []
    for slug, sc in scenes.items():
        for other in sc.get("connections") or []:
            if other == slug or other not in node_slugs:
                continue  # 自指 / 悬空(指向已不存在场景)→ 丢弃
            key = tuple(sorted((slug, other)))
            if key in seen:
                continue  # A-B 与 B-A 只留一条
            seen.add(key)
            dashed_edges.append({"a": key[0], "b": key[1]})

    return {
        "start": START_SLUG,
        "current_scene": current_scene,
        "nodes": nodes,
        "solid_edges": solid_edges,
        "dashed_edges": dashed_edges,
    }
