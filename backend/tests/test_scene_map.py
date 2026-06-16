"""场景地图(静态,第一版):从最新黑板 + Turn 表组装 节点/实线/虚线。纯只读路径。

实线=每轮转移(边数恒等于轮数、首轮从虚拟起点出发、允许自环);虚线=空间相邻
(无向去重、悬空连接过滤)。既测纯组装函数,也测一次 HTTP 往返。
"""

import json

import httpx

from app.db.models import Blackboard, Turn
from app.db.session import create_all, make_engine, make_session_factory
from app.stories.scene_map import START_SLUG, build_scene_map
from app.stories.store import create_story


def _bb(scenes: dict, current: str | None = None) -> dict:
    meta = {"title": "T"}
    if current is not None:
        meta["current_scene"] = current
    return {"story_meta": meta, "scenes": scenes, "characters": {}, "items": {}, "notes": []}


# 最新黑板:三场景 A/B/C。A 连 B 与 Z(Z 悬空),B 连 A 与 C,C 连 B。current=C。
LATEST_SCENES = {
    "A": {"name": "门厅", "origin_turn": 1, "image_paths": ["storage/images/a.png"], "connections": ["B", "Z"]},
    "B": {"name": "长廊", "origin_turn": 2, "image_paths": [], "connections": ["A", "C"]},
    "C": {"name": "中庭", "origin_turn": 4, "image_paths": ["storage/images/c.png"], "connections": ["B"]},
}

# 4 轮跨场景:A → B → (停在 B,自环) → C
TURN_SCENES = ["A", "B", "B", "C"]


def _crafted_turns() -> list[dict]:
    return [
        {"turn_index": i + 1, "beat_title": f"beat{i + 1}", "bb_after": _bb(LATEST_SCENES, current=cs)}
        for i, cs in enumerate(TURN_SCENES)
    ]


def test_build_scene_map_pure():
    # 正典图:第1轮为 A 出了 a.png(在 A 现有 image_paths 里);第2轮给 B 出过图但 B 当前无该图 → 应被剔除
    canon = [
        {"source_turn": 1, "scene_slug": "A", "output_path": "storage/images/a.png"},
        {"source_turn": 2, "scene_slug": "B", "output_path": "storage/images/stale.png"},
    ]
    m = build_scene_map(_bb(LATEST_SCENES, current="C"), _crafted_turns(), canon)

    # ── 节点:纯由黑板 scenes 组成,空 image_paths 优雅处理 ──
    assert {n["slug"] for n in m["nodes"]} == {"A", "B", "C"}
    nB = next(n for n in m["nodes"] if n["slug"] == "B")
    assert nB["name"] == "长廊" and nB["origin_turn"] == 2 and nB["image_paths"] == []
    assert m["current_scene"] == "C"
    assert m["start"] == START_SLUG

    # ── 实线:边数==轮数;端点对正确;首轮自起点;自环正确 ──
    se = m["solid_edges"]
    assert len(se) == len(TURN_SCENES)  # 4 条 = 4 轮
    pairs = [(e["from"], e["to"]) for e in se]
    assert pairs == [(START_SLUG, "A"), ("A", "B"), ("B", "B"), ("B", "C")]
    assert se[0]["from"] == START_SLUG  # 首轮从虚拟起点出发
    assert se[2]["from"] == se[2]["to"] == "B"  # 停留 → 自环
    assert [e["turn_index"] for e in se] == [1, 2, 3, 4]
    assert se[1]["beat"] == "beat2"
    # 实线带「该轮落点场景的正典图」:第1轮→A 有效图;第2轮→B 的图不在 B 现有 image_paths → None
    assert se[0]["image_path"] == "storage/images/a.png"
    assert se[1]["image_path"] is None

    # ── 虚线:无向去重、悬空过滤 ──
    dashed = {tuple(sorted((e["a"], e["b"]))) for e in m["dashed_edges"]}
    assert dashed == {("A", "B"), ("B", "C")}  # A-B 只一条;A-Z 悬空被丢
    assert len(m["dashed_edges"]) == 2


def test_solid_edge_count_equals_turns_with_missing_current_scene():
    """优雅退化:某轮 blackboard_after 缺 current_scene 时仍产出一条边(退化为自环),
    边数仍恒等于轮数。"""
    turns = [
        {"turn_index": 1, "beat_title": "起", "bb_after": _bb(LATEST_SCENES, current="A")},
        {"turn_index": 2, "beat_title": "断", "bb_after": _bb(LATEST_SCENES)},  # 无 current_scene
        {"turn_index": 3, "beat_title": "续", "bb_after": _bb(LATEST_SCENES, current="C")},
    ]
    m = build_scene_map(_bb(LATEST_SCENES, current="C"), turns)
    assert len(m["solid_edges"]) == 3  # 边数==轮数
    # 第2轮缺 current → from/to 都退化为上一轮落点 A,边仍有合法端点
    assert m["solid_edges"][1]["from"] == m["solid_edges"][1]["to"] == "A"
    # 第3轮的 from 仍承接最近一次真实落点 A
    assert m["solid_edges"][2]["from"] == "A" and m["solid_edges"][2]["to"] == "C"


async def test_scene_map_http_roundtrip(tmp_path, monkeypatch):
    engine = make_engine(f"sqlite+aiosqlite:///{tmp_path / 'scene_map.db'}")
    await create_all(engine)
    Session = make_session_factory(engine)
    monkeypatch.setattr("app.db.session.async_session", Session)

    async with Session() as s:
        sid = (await create_story(s, title="T")).id
        bb = await s.get(Blackboard, sid)
        bb.json_blob = json.dumps(_bb(LATEST_SCENES, current="C"), ensure_ascii=False)
        for t in _crafted_turns():
            s.add(
                Turn(
                    story_id=sid,
                    turn_index=t["turn_index"],
                    beat_title=t["beat_title"],
                    user_input="u",
                    narrative="n",
                    director_a_json="{}",
                    director_b_json="{}",
                    blackboard_after=json.dumps(t["bb_after"], ensure_ascii=False),
                )
            )
        await s.commit()

    from app.main import app

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://t") as c:
        r = await c.get(f"/story/{sid}/scene-map")
        assert r.status_code == 200
        m = r.json()
        assert {n["slug"] for n in m["nodes"]} == {"A", "B", "C"}
        assert len(m["solid_edges"]) == 4
        assert m["solid_edges"][0]["from"] == START_SLUG
        assert {tuple(sorted((e["a"], e["b"]))) for e in m["dashed_edges"]} == {("A", "B"), ("B", "C")}

        assert (await c.get(f"/story/{sid}/scene-map")).status_code == 200
        assert (await c.get("/story/nope/scene-map")).status_code == 404
    await engine.dispose()
