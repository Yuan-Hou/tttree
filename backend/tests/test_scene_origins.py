import json

from app.db.models import Blackboard, ImageGen
from app.db.session import create_all, make_engine, make_session_factory
from app.state.reducer import reduce_turn
from app.turns.scene_origins import born_in_turn, scenes_born_in_turn

STORY = "st"


def _scene(name: str) -> dict:
    return {"name": name, "base_prompt": "", "visual_anchors": [], "state": "", "connections": [], "image_paths": []}


def _bb(scenes: dict, current: str) -> str:
    """构造一份 Director-B 风格的新黑板 JSON(不含 origin_turn——模拟 B 不回显,由 reducer 打点)。"""
    return json.dumps(
        {"story_meta": {"current_scene": current, "latest_beat": ""}, "scenes": scenes,
         "characters": {}, "items": {}, "notes": []},
        ensure_ascii=False,
    )


async def _setup(tmp_path):
    engine = make_engine(f"sqlite+aiosqlite:///{tmp_path / 'origins.db'}")
    await create_all(engine)
    return make_session_factory(engine)


async def _turn(Session, b_blackboard_str: str):
    async with Session() as s:
        return await reduce_turn(
            story_id=STORY, director_b_new_blackboard_str=b_blackboard_str,
            writer_narrative="n", director_a_json="{}", user_input="u", session=s,
        )


async def _current_bb(Session) -> dict:
    async with Session() as s:
        return json.loads((await s.get(Blackboard, STORY)).json_blob)


async def test_origin_turn_stamped_and_recall_preserves(tmp_path):
    Session = await _setup(tmp_path)
    # 轮1:诞生 entrance
    await _turn(Session, _bb({"entrance": _scene("入口")}, "entrance"))
    # 轮2:recall entrance(B 全量重写不带 origin_turn)+ 新生 cellar
    await _turn(Session, _bb({"entrance": _scene("入口"), "cellar": _scene("地窖")}, "cellar"))
    # 轮3:新生 attic
    await _turn(Session, _bb(
        {"entrance": _scene("入口"), "cellar": _scene("地窖"), "attic": _scene("阁楼")}, "attic"))

    bb = await _current_bb(Session)
    assert bb["scenes"]["entrance"]["origin_turn"] == 1  # 首次被 B 写入的轮
    assert bb["scenes"]["cellar"]["origin_turn"] == 2
    assert bb["scenes"]["attic"]["origin_turn"] == 3

    # 轮4:回访 cellar(current_scene=cellar),诞生点不变
    await _turn(Session, _bb(
        {"entrance": _scene("入口"), "cellar": _scene("地窖"), "attic": _scene("阁楼")}, "cellar"))
    bb = await _current_bb(Session)
    assert bb["scenes"]["cellar"]["origin_turn"] == 2  # recall 不改诞生点
    assert bb["scenes"]["entrance"]["origin_turn"] == 1
    assert bb["scenes"]["attic"]["origin_turn"] == 3


async def test_image_paths_preserved_across_director_b_rewrite(tmp_path):
    """image_paths 由 reducer 权威承袭,不被 Director-B 改名/全量重写丢掉。
    复现真实 bug:某场景出图后,下一轮 B 改名重写该场景且回显 image_paths=[] →
    reducer 必须按 slug 把旧 image_paths 原样带过来,否则图在地图/画廊消失(ImageGen/绘图台仍在)。"""
    Session = await _setup(tmp_path)
    # 轮1:诞生 machine
    await _turn(Session, _bb({"machine": _scene("糖水机")}, "machine"))
    # 模拟出图:record_generation 在 reduce 之后向该场景 image_paths 追加一张
    async with Session() as s:
        row = await s.get(Blackboard, STORY)
        bb = json.loads(row.json_blob)
        bb["scenes"]["machine"]["image_paths"] = ["storage/images/m_t1.png"]
        row.json_blob = json.dumps(bb, ensure_ascii=False)
        await s.commit()
    # 轮2:B 改名(name 变)且回显 image_paths=[](_scene 默认空)→ reducer 应保住旧图
    await _turn(Session, _bb({"machine": _scene("糖水机周围")}, "machine"))
    bb = await _current_bb(Session)
    assert bb["scenes"]["machine"]["name"] == "糖水机周围"  # 改名生效
    assert bb["scenes"]["machine"]["image_paths"] == ["storage/images/m_t1.png"]  # 但图没丢

    # 轮2 再出一张图,轮3 B 再次全量重写 → 两张都在,顺序不乱
    async with Session() as s:
        row = await s.get(Blackboard, STORY)
        bb = json.loads(row.json_blob)
        bb["scenes"]["machine"]["image_paths"].append("storage/images/m_t2.png")
        row.json_blob = json.dumps(bb, ensure_ascii=False)
        await s.commit()
    await _turn(Session, _bb({"machine": _scene("糖水机周围")}, "machine"))
    bb = await _current_bb(Session)
    assert bb["scenes"]["machine"]["image_paths"] == [
        "storage/images/m_t1.png", "storage/images/m_t2.png",
    ]


async def test_reverse_query_scenes_and_images_born_in_turn(tmp_path):
    Session = await _setup(tmp_path)
    await _turn(Session, _bb({"entrance": _scene("入口")}, "entrance"))
    await _turn(Session, _bb({"entrance": _scene("入口"), "cellar": _scene("地窖")}, "cellar"))
    await _turn(Session, _bb(
        {"entrance": _scene("入口"), "cellar": _scene("地窖"), "attic": _scene("阁楼")}, "attic"))

    # 给 cellar(诞生于轮2)挂一张图
    async with Session() as s:
        s.add(ImageGen(story_id=STORY, scene_slug="cellar", kind="new_scene",
                       output_path="storage/images/cellar.png", source_turn=2))
        await s.commit()

    bb = await _current_bb(Session)
    assert scenes_born_in_turn(bb, 2) == ["cellar"]
    assert scenes_born_in_turn(bb, 1) == ["entrance"]
    assert scenes_born_in_turn(bb, 3) == ["attic"]

    async with Session() as s:
        born2 = await born_in_turn(s, STORY, 2)
        born1 = await born_in_turn(s, STORY, 1)
    assert born2["scene_slugs"] == ["cellar"]
    assert [ig.output_path for ig in born2["images"]] == ["storage/images/cellar.png"]
    assert born1["scene_slugs"] == ["entrance"] and born1["images"] == []  # entrance 无图
