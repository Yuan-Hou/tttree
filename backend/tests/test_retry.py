import json

from sqlalchemy import func, select

from app.agents.context import build_messages
from app.db.models import Blackboard, ImageGen, Turn
from app.db.session import create_all, make_engine, make_session_factory
from app.imaging.pipeline import record_generation
from app.models.schemas import DirectorOutput
from app.state.reducer import reduce_turn
from app.stories.store import create_story
from app.turns.retry import retry_turn


def _scene(name, image_paths=None):
    return {"name": name, "base_prompt": "", "visual_anchors": [], "state": "",
            "connections": [], "image_paths": image_paths or []}


def _bb(scenes, current):
    return json.dumps({"story_meta": {"title": "重试", "current_scene": current, "latest_beat": ""},
                       "scenes": scenes, "characters": {}, "items": {}, "notes": []}, ensure_ascii=False)


async def _setup(tmp_path):
    engine = make_engine(f"sqlite+aiosqlite:///{tmp_path / 'retry.db'}")
    await create_all(engine)
    return make_session_factory(engine)


# ---- monkeypatch 帮手:把三个 agent 换成确定性输出(真 LLM 留给验证脚本)----
def _patch_director(mp, a_out, rec):
    async def fake(*a, **k):
        rec.append("A")
        return a_out
    mp.setattr("app.turns.retry.run_director", fake)


def _patch_writer(mp, text, rec):
    async def fake(*a, **k):
        rec.append("W")
        for c in text:
            yield c
    mp.setattr("app.turns.retry.stream_writer", fake)


def _patch_review(mp, bb_out, rec):
    async def fake(*a, **k):
        rec.append("B")
        return bb_out
    mp.setattr("app.turns.retry.run_director_review", fake)


A_ORIG = DirectorOutput(situation="在房间,准备下探", beat_points=["下楼梯", "进地窖"],
                        mood="紧张", writing_brief="第二人称,走进地窖")
NARR_ORIG = "你推开门,走下台阶,进了地窖。"


async def _seed(Session):
    """轮1: room;轮2(原): A=去地窖, Writer=去地窖, B 诞生 cellar。返回 (sid, 存档的 w/b messages)。"""
    async with Session() as s:
        sid = (await create_story(s, title="重试")).id
        await reduce_turn(story_id=sid, director_b_new_blackboard_str=_bb({"room": _scene("房间")}, "room"),
                          writer_narrative="n1", director_a_json="{}", user_input="u1", session=s)
        pre2 = json.loads((await s.get(Blackboard, sid)).json_blob)  # 轮1 blackboard_after = 轮2 之前的黑板
    hist1 = [{"role": "user", "content": "u1"}, {"role": "assistant", "content": "n1"}]
    a_msgs = build_messages("director", history=hist1, blackboard=pre2, user_action="探索", knowledge="")
    w_msgs = build_messages("writer", history=hist1, blackboard=pre2, user_action="探索", writing_brief=A_ORIG.writing_brief)
    b_msgs = build_messages("director_review", history=hist1, blackboard=pre2, user_action="探索",
                            narrative=NARR_ORIG, director_a_plan=A_ORIG.model_dump())
    async with Session() as s:
        await reduce_turn(story_id=sid, director_b_new_blackboard_str=_bb(
            {"room": _scene("房间"), "cellar": _scene("地窖")}, "cellar"),
            writer_narrative=NARR_ORIG, director_a_json=A_ORIG.model_dump_json(), user_input="探索", session=s,
            director_a_messages=json.dumps(a_msgs, ensure_ascii=False),
            writer_messages=json.dumps(w_msgs, ensure_ascii=False),
            director_b_messages=json.dumps(b_msgs, ensure_ascii=False))
    return sid, w_msgs, b_msgs


async def _turn2(Session, sid):
    async with Session() as s:
        return (await s.execute(select(Turn).where(Turn.story_id == sid, Turn.turn_index == 2))).scalar_one()


async def _bb_now(Session, sid):
    async with Session() as s:
        return json.loads((await s.get(Blackboard, sid)).json_blob)


async def test_retry_from_writer_scene_vanishes_and_reborn(tmp_path, monkeypatch):
    """从 Writer 前重试:A 保留、Writer/B 新结果、旧结果丢弃;地窖消失+阁楼新生(资产保留)。"""
    Session = await _setup(tmp_path)
    sid, w_msgs, b_msgs = await _seed(Session)

    # 给地窖画图(资产)
    img = tmp_path / "cellar.png"
    img.write_bytes(b"DATA")
    async with Session() as s:
        await record_generation(s, story_id=sid, scene_slug="cellar", kind="new_scene", final_prompt="",
                                ref_asset_ids=[], ref_image_paths=[], output_path=str(img),
                                origin="user_initiated", source_turn=2)

    rec = []
    _patch_director(monkeypatch, A_ORIG, rec)  # 不应被调用(A 保留)
    _patch_writer(monkeypatch, "你转身上楼,推开阁楼的门。", rec)            # 新 Writer:去阁楼
    _patch_review(monkeypatch, json.loads(_bb({"room": _scene("房间"), "attic": _scene("阁楼")}, "attic")), rec)

    async with Session() as s:
        r = await retry_turn(s, sid, "writer")

    assert r.ok and r.entry == "writer" and r.turn_index == 2
    assert rec == ["W", "B"]  # A 未重走;Writer、B 重走
    t2 = await _turn2(Session, sid)
    # A 保留未变;Writer/B 是新结果(旧结果已丢弃)
    assert json.loads(t2.director_a_json)["writing_brief"] == A_ORIG.writing_brief
    assert t2.narrative == "你转身上楼,推开阁楼的门。"
    bb = await _bb_now(Session, sid)
    assert "cellar" not in bb["scenes"] and "attic" in bb["scenes"]
    # 场景消失+新生
    assert r.invalidated_scene_slugs == ["cellar"]
    assert r.new_scene_slugs == ["attic"]
    assert bb["scenes"]["attic"]["origin_turn"] == 2  # 阁楼 origin_turn 正确打在本轮
    # 地窖图引用解除,但 ImageGen + 文件保留
    assert "cellar" not in json.dumps(bb)
    async with Session() as s:
        n_ig = (await s.execute(select(func.count()).select_from(ImageGen).where(ImageGen.scene_slug == "cellar"))).scalar()
    assert n_ig == 1 and img.exists()
    # 复用证据:Writer 上下文复用存档(A 保留→逐字节不变);B 上下文按新成稿重建
    assert json.loads(t2.writer_messages) == w_msgs
    assert json.loads(t2.director_b_messages) != b_msgs and "阁楼" in t2.director_b_messages


async def test_retry_from_director_a_whole_turn_changes(tmp_path, monkeypatch):
    """从 A 前重试:整轮重来,A/Writer/B 全新。"""
    Session = await _setup(tmp_path)
    sid, _, _ = await _seed(Session)
    new_a = DirectorOutput(situation="改主意", beat_points=["上阁楼"], mood="好奇", writing_brief="第二人称,走上阁楼")
    rec = []
    _patch_director(monkeypatch, new_a, rec)
    _patch_writer(monkeypatch, "你改走楼上,进了阁楼。", rec)
    _patch_review(monkeypatch, json.loads(_bb({"room": _scene("房间"), "attic": _scene("阁楼")}, "attic")), rec)

    async with Session() as s:
        r = await retry_turn(s, sid, "director_a")
    assert r.ok and rec == ["A", "W", "B"]  # 三段全重走
    t2 = await _turn2(Session, sid)
    assert json.loads(t2.director_a_json)["writing_brief"] == "第二人称,走上阁楼"  # A 变了
    assert t2.narrative == "你改走楼上,进了阁楼。"
    bb = await _bb_now(Session, sid)
    assert "cellar" not in bb["scenes"] and bb["scenes"]["attic"]["origin_turn"] == 2
    assert r.invalidated_scene_slugs == ["cellar"] and r.new_scene_slugs == ["attic"]


async def test_retry_from_director_b_only_b_changes(tmp_path, monkeypatch):
    """从 B 前重试:A、Writer 保留,只重走 B。"""
    Session = await _setup(tmp_path)
    sid, w_msgs, b_msgs = await _seed(Session)
    rec = []
    _patch_director(monkeypatch, A_ORIG, rec)   # 不应调用
    _patch_writer(monkeypatch, "不应被调用", rec)  # 不应调用
    # 新 B:同样去地窖,但把场景记成 wine_cellar(换个 slug,体现 B 重判)
    _patch_review(monkeypatch, json.loads(_bb({"room": _scene("房间"), "wine_cellar": _scene("酒窖")}, "wine_cellar")), rec)

    async with Session() as s:
        r = await retry_turn(s, sid, "director_b")
    assert r.ok and rec == ["B"]  # 只重走 B
    t2 = await _turn2(Session, sid)
    assert json.loads(t2.director_a_json)["writing_brief"] == A_ORIG.writing_brief  # A 保留
    assert t2.narrative == NARR_ORIG                                                # Writer 保留
    bb = await _bb_now(Session, sid)
    assert "cellar" not in bb["scenes"] and "wine_cellar" in bb["scenes"]
    assert r.invalidated_scene_slugs == ["cellar"] and r.new_scene_slugs == ["wine_cellar"]
    assert bb["scenes"]["wine_cellar"]["origin_turn"] == 2
    # B 上下文复用存档(A、Writer 都保留 → 逐字节不变)
    assert json.loads(t2.director_b_messages) == b_msgs
    assert json.loads(t2.writer_messages) == w_msgs  # Writer 上下文也原样保留
