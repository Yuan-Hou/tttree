import json

from sqlalchemy import func, select

from app.db.models import Blackboard, Turn
from app.db.session import create_all, make_engine, make_session_factory
from app.state.reducer import reduce_turn

STORY = "story-x"


def without_removed(bb: dict) -> dict:
    """持久化的黑板应剥离顶层 removed;用此构造期望值。"""
    return {k: v for k, v in bb.items() if k != "removed"}

OLD_BB = {
    "story_meta": {"title": "林间迷踪", "current_scene": "forest_edge", "latest_beat": "初入林间"},
    "scenes": {
        "forest_edge": {
            "name": "森林边缘的空地",
            "base_prompt": "黄昏的森林空地。",
            "visual_anchors": ["古树"],
            "state": "树桩上搁着一把铜钥匙。",
            "connections": ["hidden_cabin"],
            "image_paths": [],
        }
    },
    "characters": {
        "旅人": {"location": "forest_edge", "status": "警觉", "inventory": [], "relations": {}, "appearance": "灰斗篷。"}
    },
    "items": {"铜钥匙": {"owner": "scene:forest_edge", "where": "树桩上", "desc": "古旧铜钥匙。"}},
    "notes": [{"content": "旅人失忆。", "since_beat": "初入林间"}],
}

# B 全量重写后的新黑板:进了木屋,钥匙易主
NEW_BB_1 = {
    "story_meta": {"title": "林间迷踪", "current_scene": "hidden_cabin", "latest_beat": "拾匙入屋"},
    "scenes": {
        "forest_edge": OLD_BB["scenes"]["forest_edge"],
        "hidden_cabin": {
            "name": "狭小的木屋",
            "base_prompt": "昏暗木屋。",
            "visual_anchors": ["蒙尘木箱"],
            "state": "尘埃浮动。",
            "connections": ["forest_edge"],
            "image_paths": [],
        },
    },
    "characters": {
        "旅人": {"location": "hidden_cabin", "status": "警觉", "inventory": ["铜钥匙"], "relations": {}, "appearance": "灰斗篷。"}
    },
    "items": {"铜钥匙": {"owner": "character:旅人", "where": "旅人手中", "desc": "古旧铜钥匙。"}},
    "notes": OLD_BB["notes"],
    "removed": [],
}

# 第二轮:微小变化(状态推进),用于验证 turn_index 递增 + 覆盖
NEW_BB_2 = json.loads(json.dumps(NEW_BB_1))
NEW_BB_2["story_meta"]["latest_beat"] = "翻检木箱"
NEW_BB_2["scenes"]["hidden_cabin"]["state"] = "一只木箱已被打开,里面空空如也。"


async def _setup(tmp_path, seed_bb=OLD_BB):
    engine = make_engine(f"sqlite+aiosqlite:///{tmp_path / 'reducer.db'}")
    await create_all(engine)
    Session = make_session_factory(engine)
    async with Session() as s:
        s.add(Blackboard(story_id=STORY, json_blob=json.dumps(seed_bb, ensure_ascii=False)))
        await s.commit()
    return engine, Session


async def test_overwrite_and_turn_index_increments(tmp_path):
    engine, Session = await _setup(tmp_path)

    async with Session() as s:
        r1 = await reduce_turn(
            story_id=STORY,
            director_b_new_blackboard_str=json.dumps(NEW_BB_1, ensure_ascii=False),
            writer_narrative="你拾起钥匙,推门而入……",
            director_a_json=json.dumps({"scene_event": "stay"}, ensure_ascii=False),
            user_input="拾钥匙并推门",
            session=s,
        )
    assert r1.ok is True
    assert r1.turn_index == 1
    assert r1.beat_title == "拾匙入屋"
    assert r1.warnings == []  # NEW_BB_1 自洽,无告警

    async with Session() as s:
        r2 = await reduce_turn(
            story_id=STORY,
            director_b_new_blackboard_str=json.dumps(NEW_BB_2, ensure_ascii=False),
            writer_narrative="你掀开木箱……",
            director_a_json="{}",
            user_input="打开木箱",
            session=s,
        )
    assert r2.ok is True
    assert r2.turn_index == 2  # 代码维护,递增
    # 返回的权威黑板也已剥离 removed
    assert "removed" not in r1.blackboard
    assert "removed" not in r2.blackboard

    # 黑板被覆盖为最新(NEW_BB_2),且持久化时已剥离 removed
    async with Session() as s:
        bb = await s.get(Blackboard, STORY)
        assert "removed" not in json.loads(bb.json_blob)  # removed 不入库
        assert json.loads(bb.json_blob) == without_removed(NEW_BB_2)
        turns = (await s.execute(select(Turn).where(Turn.story_id == STORY).order_by(Turn.turn_index))).scalars().all()
        assert [t.turn_index for t in turns] == [1, 2]
        assert turns[0].beat_title == "拾匙入屋"
        assert json.loads(turns[0].blackboard_after) == without_removed(NEW_BB_1)
        assert turns[1].beat_title == "翻检木箱"
        assert json.loads(turns[1].blackboard_after) == without_removed(NEW_BB_2)
        # director_b_json 保留 B 的原始输出(含 removed),供审计
        assert "removed" in json.loads(turns[0].director_b_json)
        # 存档字段
        assert turns[0].narrative.startswith("你拾起钥匙")
        assert json.loads(turns[0].director_a_json) == {"scene_event": "stay"}

    await engine.dispose()
    print("\n[overwrite] 覆盖写正确;turn_index 1->2 递增;removed 入库前被剥离;raw 仍存于 director_b_json。")


async def test_shrink_warns_but_does_not_block(tmp_path, capsys):
    # 旧黑板用 NEW_BB_1(2 场景/1 角色/1 物品);喂一个缩水的新黑板:删掉 hidden_cabin 和钥匙,且不声明 removed
    engine, Session = await _setup(tmp_path, seed_bb=NEW_BB_1)
    shrunk = {
        "story_meta": {"title": "林间迷踪", "current_scene": "forest_edge", "latest_beat": "凭空缩水"},
        "scenes": {"forest_edge": NEW_BB_1["scenes"]["forest_edge"]},
        "characters": {"旅人": {"location": "forest_edge", "status": "警觉", "inventory": [], "relations": {}, "appearance": "灰斗篷。"}},
        "items": {},
        "notes": [],
        "removed": [],  # 没有解释缩水
    }

    async with Session() as s:
        r = await reduce_turn(
            story_id=STORY,
            director_b_new_blackboard_str=json.dumps(shrunk, ensure_ascii=False),
            writer_narrative="……",
            director_a_json="{}",
            user_input="（异常缩水回合）",
            session=s,
        )

    out = capsys.readouterr().out
    assert r.ok is True  # 不阻断
    assert any("缩水" in w for w in r.warnings)  # 报了缩水告警
    assert "[reducer][WARN]" in out  # 确实打印了
    # 仍然写库(降级=只告警不拦截)
    async with Session() as s:
        bb = await s.get(Blackboard, STORY)
        assert json.loads(bb.json_blob) == without_removed(shrunk)
    await engine.dispose()
    print(f"\n[shrink] 缩水被告警未阻断;告警 {len(r.warnings)} 条,黑板仍被覆盖。")


async def test_removed_stripped_before_persist(tmp_path):
    """B 输出顶层 removed(含删除项)→ 告警/对照用完即丢,不进入持久化黑板;
    确认下一轮的【当前黑板】不再带着上轮的 removed。"""
    engine, Session = await _setup(tmp_path, seed_bb=NEW_BB_1)  # 含铜钥匙
    # B 删掉铜钥匙,并在 removed 显式声明
    after_delete = json.loads(json.dumps(NEW_BB_1))
    del after_delete["items"]["铜钥匙"]
    after_delete["characters"]["旅人"]["inventory"] = []
    after_delete["story_meta"]["latest_beat"] = "钥匙遗失"
    after_delete["removed"] = [{"kind": "item", "key": "铜钥匙", "reason": "被掷入深井,永久失去"}]

    async with Session() as s:
        r = await reduce_turn(
            story_id=STORY,
            director_b_new_blackboard_str=json.dumps(after_delete, ensure_ascii=False),
            writer_narrative="你看着钥匙坠入井中……",
            director_a_json="{}",
            user_input="把钥匙丢进井里",
            session=s,
        )

    assert r.ok is True
    assert "removed" not in r.blackboard  # 返回的权威黑板无 removed

    # 模拟下一轮:从 DB 读回的黑板就是下一轮的【当前黑板】,必须不含 removed
    async with Session() as s:
        next_turn_bb = json.loads((await s.get(Blackboard, STORY)).json_blob)
        assert "removed" not in next_turn_bb
        assert "铜钥匙" not in next_turn_bb["items"]  # 删除本身已落实
        # 但审计存档里 B 的原始 removed 信号仍可追溯
        turn = (await s.execute(select(Turn).where(Turn.story_id == STORY).order_by(Turn.turn_index.desc()))).scalars().first()
        raw = json.loads(turn.director_b_json)
        assert raw["removed"][0]["key"] == "铜钥匙"

    await engine.dispose()
    print("\n[strip-removed] removed 不入库;下一轮黑板不携带上轮 removed;raw 信号仍可审计。")


async def test_draw_proposals_returned_but_not_persisted(tmp_path):
    """B 输出 draw_proposals → reducer 返回它(交人在回路),但剥离出持久黑板。"""
    engine, Session = await _setup(tmp_path, seed_bb=OLD_BB)
    with_props = json.loads(json.dumps(NEW_BB_1))
    with_props["draw_proposals"] = [
        {"scene_slug": "hidden_cabin", "kind": "new_scene", "reason": "首次进入木屋,值得配图"}
    ]

    async with Session() as s:
        r = await reduce_turn(
            story_id=STORY,
            director_b_new_blackboard_str=json.dumps(with_props, ensure_ascii=False),
            writer_narrative="……",
            director_a_json="{}",
            user_input="进入木屋",
            session=s,
        )

    # 返回给人在回路
    assert len(r.draw_proposals) == 1
    assert r.draw_proposals[0]["scene_slug"] == "hidden_cabin"
    # 不进持久黑板,也不在返回的权威黑板里
    assert "draw_proposals" not in r.blackboard
    async with Session() as s:
        bb = json.loads((await s.get(Blackboard, STORY)).json_blob)
        assert "draw_proposals" not in bb
        # 但 raw 输出仍可审计
        turn = (await s.execute(select(Turn).where(Turn.story_id == STORY))).scalar_one()
        assert "draw_proposals" in json.loads(turn.director_b_json)
    await engine.dispose()
    print("\n[draw_proposals] 返回人在回路、剥离出持久黑板、raw 可审计。")


async def test_inconsistency_warnings(tmp_path, capsys):
    engine, Session = await _setup(tmp_path, seed_bb=OLD_BB)
    # 故意制造:旅人 inventory 含「铜钥匙」但 owner 仍是 scene;location 指向不存在的场景
    bad = json.loads(json.dumps(NEW_BB_1))
    bad["items"]["铜钥匙"]["owner"] = "scene:forest_edge"  # 与 inventory 冲突
    bad["characters"]["旅人"]["location"] = "ghost_room"  # 不存在的场景

    async with Session() as s:
        r = await reduce_turn(
            story_id=STORY,
            director_b_new_blackboard_str=json.dumps(bad, ensure_ascii=False),
            writer_narrative="……",
            director_a_json="{}",
            user_input="（不一致回合）",
            session=s,
        )
    assert r.ok is True
    assert any("inventory/owner 不一致" in w for w in r.warnings)
    assert any("location 悬空" in w for w in r.warnings)
    await engine.dispose()
    print(f"\n[consistency] 检出 inventory/owner 与 location 悬空告警:{r.warnings}")


async def test_invalid_json_safe_degrade(tmp_path, capsys):
    engine, Session = await _setup(tmp_path, seed_bb=OLD_BB)

    async with Session() as s:
        r = await reduce_turn(
            story_id=STORY,
            director_b_new_blackboard_str="{ 这不是合法 JSON ,,, ",
            writer_narrative="……",
            director_a_json="{}",
            user_input="（坏 JSON 回合）",
            session=s,
        )

    out = capsys.readouterr().out
    assert r.ok is False
    assert r.error is not None
    assert "[reducer][ERROR]" in out
    assert r.blackboard == OLD_BB  # 返回保留的旧黑板

    # DB 中黑板仍为旧黑板,且没有写入任何 Turn
    async with Session() as s:
        bb = await s.get(Blackboard, STORY)
        assert json.loads(bb.json_blob) == OLD_BB
        turn_count = (await s.execute(select(func.count()).select_from(Turn).where(Turn.story_id == STORY))).scalar()
        assert turn_count == 0
    await engine.dispose()
    print("\n[bad-json] 安全降级:旧黑板保留、未写 Turn、未崩溃。")
