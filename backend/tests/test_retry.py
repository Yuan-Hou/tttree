import json

from sqlalchemy import func, select

from app.agents.context import build_messages
from app.db.models import Blackboard, ImageGen, Turn
from app.db.session import create_all, make_engine, make_session_factory
from app.imaging.pipeline import record_generation
from app.models.schemas import DirectorOutput, OptionsOutput
from app.state.reducer import reduce_turn
from app.stories.store import create_story
from app.turns.retry import retry_turn, stream_retry_turn


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


# ---- monkeypatch 帮手:把四个 agent 换成确定性「流式」桩(逐 token 产原始 JSON / 文本;真 LLM 留给验证脚本)----
def _patch_director(mp, a_out, rec):
    async def fake(*a, **k):
        rec.append("A")
        yield a_out.model_dump_json()
    mp.setattr("app.turns.retry.stream_director", fake)


def _patch_writer(mp, text, rec):
    async def fake(*a, **k):
        rec.append("W")
        for c in text:
            yield c
    mp.setattr("app.turns.retry.stream_writer", fake)


def _patch_review(mp, bb_out, rec):
    async def fake(*a, **k):
        rec.append("B")
        yield json.dumps(bb_out, ensure_ascii=False)
    mp.setattr("app.turns.retry.stream_director_review", fake)


def _patch_options(mp, opts_out, rec):
    async def fake(*a, **k):
        rec.append("O")
        yield opts_out.model_dump_json()
    mp.setattr("app.turns.retry.stream_options", fake)


A_ORIG = DirectorOutput(situation="在房间,准备下探", beat_points=["下楼梯", "进地窖"],
                        mood="紧张", writing_brief="第二人称,走进地窖")
NARR_ORIG = "你推开门,走下台阶,进了地窖。"
OPTS_ORIG = OptionsOutput(options=["点亮油灯", "原路退回"])


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
    o_msgs = build_messages("options", history=hist1, blackboard=pre2, user_action="探索", narrative=NARR_ORIG)
    async with Session() as s:
        await reduce_turn(story_id=sid, director_b_new_blackboard_str=_bb(
            {"room": _scene("房间"), "cellar": _scene("地窖")}, "cellar"),
            writer_narrative=NARR_ORIG, director_a_json=A_ORIG.model_dump_json(), user_input="探索", session=s,
            director_a_messages=json.dumps(a_msgs, ensure_ascii=False),
            writer_messages=json.dumps(w_msgs, ensure_ascii=False),
            director_b_messages=json.dumps(b_msgs, ensure_ascii=False),
            options_json=OPTS_ORIG.model_dump_json(),
            options_messages=json.dumps(o_msgs, ensure_ascii=False))
    return sid, w_msgs, b_msgs, o_msgs


async def _turn2(Session, sid):
    async with Session() as s:
        return (await s.execute(select(Turn).where(Turn.story_id == sid, Turn.turn_index == 2))).scalar_one()


async def _bb_now(Session, sid):
    async with Session() as s:
        return json.loads((await s.get(Blackboard, sid)).json_blob)


async def test_retry_from_writer_scene_vanishes_and_reborn(tmp_path, monkeypatch):
    """从 Writer 前重试:A 保留、Writer/B 新结果、旧结果丢弃;地窖消失+阁楼新生(资产保留)。"""
    Session = await _setup(tmp_path)
    sid, w_msgs, b_msgs, o_msgs = await _seed(Session)

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
    _patch_options(monkeypatch, OptionsOutput(options=["进阁楼深处"]), rec)  # Writer 变 → 连带重跑 Options

    async with Session() as s:
        r = await retry_turn(s, sid, "writer")

    assert r.ok and r.entry == "writer" and r.turn_index == 2
    assert rec == ["W", "B", "O"]  # A 未重走;Writer、B 重走;Options 连带重跑
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
    # Options 连带重跑 → 新选项落库(覆盖原选项)
    assert json.loads(t2.options_json)["options"] == ["进阁楼深处"]


async def test_retry_from_director_a_whole_turn_changes(tmp_path, monkeypatch):
    """从 A 前重试:整轮重来,A/Writer/B 全新。"""
    Session = await _setup(tmp_path)
    sid, _, _, _ = await _seed(Session)
    new_a = DirectorOutput(situation="改主意", beat_points=["上阁楼"], mood="好奇", writing_brief="第二人称,走上阁楼")
    rec = []
    _patch_director(monkeypatch, new_a, rec)
    _patch_writer(monkeypatch, "你改走楼上,进了阁楼。", rec)
    _patch_review(monkeypatch, json.loads(_bb({"room": _scene("房间"), "attic": _scene("阁楼")}, "attic")), rec)
    _patch_options(monkeypatch, OptionsOutput(options=["环顾阁楼"]), rec)  # A 变 → 连带重跑 Options

    async with Session() as s:
        r = await retry_turn(s, sid, "director_a")
    assert r.ok and rec == ["A", "W", "B", "O"]  # 全重走 + Options 连带
    t2 = await _turn2(Session, sid)
    assert json.loads(t2.director_a_json)["writing_brief"] == "第二人称,走上阁楼"  # A 变了
    assert t2.narrative == "你改走楼上,进了阁楼。"
    bb = await _bb_now(Session, sid)
    assert "cellar" not in bb["scenes"] and bb["scenes"]["attic"]["origin_turn"] == 2
    assert r.invalidated_scene_slugs == ["cellar"] and r.new_scene_slugs == ["attic"]


async def test_retry_from_director_b_only_b_changes(tmp_path, monkeypatch):
    """从 B 前重试:A、Writer 保留,只重走 B。"""
    Session = await _setup(tmp_path)
    sid, w_msgs, b_msgs, o_msgs = await _seed(Session)
    rec = []
    _patch_director(monkeypatch, A_ORIG, rec)   # 不应调用
    _patch_writer(monkeypatch, "不应被调用", rec)  # 不应调用
    # 新 B:同样去地窖,但把场景记成 wine_cellar(换个 slug,体现 B 重判)
    _patch_review(monkeypatch, json.loads(_bb({"room": _scene("房间"), "wine_cellar": _scene("酒窖")}, "wine_cellar")), rec)
    _patch_options(monkeypatch, OptionsOutput(options=["不应被调用"]), rec)  # B 与 Options 并行兄弟 → 不应被调用

    async with Session() as s:
        r = await retry_turn(s, sid, "director_b")
    assert r.ok and rec == ["B"]  # 只重走 B;Options 未重跑(保留)
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
    # Options 原样保留(B 重走不影响并行兄弟)
    assert json.loads(t2.options_json)["options"] == OPTS_ORIG.options
    assert json.loads(t2.options_messages) == o_msgs


async def test_retry_from_options_leaf_only(tmp_path, monkeypatch):
    """从 Options 重试:叶子自重试,只重跑 Options;B/Writer/A/场景/黑板全不动。"""
    Session = await _setup(tmp_path)
    sid, w_msgs, b_msgs, o_msgs = await _seed(Session)
    bb_before = await _bb_now(Session, sid)
    rec = []
    _patch_director(monkeypatch, A_ORIG, rec)            # 不应调用
    _patch_writer(monkeypatch, "不应被调用", rec)          # 不应调用
    _patch_review(monkeypatch, json.loads(_bb({}, "")), rec)  # 不应调用
    _patch_options(monkeypatch, OptionsOutput(options=["新选项A", "新选项B"]), rec)

    async with Session() as s:
        r = await retry_turn(s, sid, "options")

    assert r.ok and r.entry == "options" and r.turn_index == 2
    assert rec == ["O"]  # 只重跑 Options,不碰 A/Writer/B
    t2 = await _turn2(Session, sid)
    # Options 输出被就地覆写;复用存档 messages(上游未变 → 逐字节不变,缓存命中)
    assert json.loads(t2.options_json)["options"] == ["新选项A", "新选项B"]
    assert json.loads(t2.options_messages) == o_msgs
    # B/Writer/A 的产物与黑板全未变(不 rollback、不 reduce)
    assert t2.narrative == NARR_ORIG
    assert json.loads(t2.director_a_json)["writing_brief"] == A_ORIG.writing_brief
    assert json.loads(t2.director_b_messages) == b_msgs
    assert await _bb_now(Session, sid) == bb_before  # 黑板逐字节不变
    assert r.invalidated_scene_slugs == [] and r.new_scene_slugs == []


# ── 流式重试:逐 token 事件 + 失败安全(DB 不变,可恢复)──────────────
async def test_stream_retry_writer_emits_tokens_then_done(tmp_path, monkeypatch):
    """从 Writer 前流式重试:依次产出 narrative_token / director_b_token / options_token,
    最后 state_updated + retry_done;新叙事由逐 token 累积而成。"""
    Session = await _setup(tmp_path)
    sid, *_ = await _seed(Session)
    rec = []
    _patch_writer(monkeypatch, "你转身上楼。", rec)
    _patch_review(monkeypatch, json.loads(_bb({"room": _scene("房间"), "attic": _scene("阁楼")}, "attic")), rec)
    _patch_options(monkeypatch, OptionsOutput(options=["进阁楼"]), rec)

    evs = []
    async with Session() as s:
        async for ev in stream_retry_turn(s, sid, "writer"):
            evs.append(ev)
    types = [e["type"] for e in evs]
    assert types[0] == "retry_started"
    # 逐 token:写手成稿由 narrative_token 累积;B、Options 各有 token 事件
    narrative = "".join(e["text"] for e in evs if e["type"] == "narrative_token")
    assert narrative == "你转身上楼。"
    assert any(e["type"] == "director_b_token" for e in evs)
    assert any(e["type"] == "options_token" for e in evs)
    assert any(e["type"] == "options_proposed" and e["options"] == ["进阁楼"] for e in evs)
    # 收尾:state_updated 在 retry_done 之前
    assert types.index("state_updated") < types.index("retry_done")
    done = next(e for e in evs if e["type"] == "retry_done")
    assert done["narrative"] == "你转身上楼。" and done["new_scene_slugs"] == ["attic"]
    # 真落盘
    t2 = await _turn2(Session, sid)
    assert t2.narrative == "你转身上楼。"


async def test_stream_retry_writer_failure_leaves_turn_intact(tmp_path, monkeypatch):
    """重试中途写手失败 → 只产 error,DB 完全不变(原叙事/黑板/选项都在),前端据此恢复。"""
    Session = await _setup(tmp_path)
    sid, _, _, o_msgs = await _seed(Session)
    bb_before = await _bb_now(Session, sid)

    async def boom_writer(*a, **k):
        yield "半截"  # 先吐一点,再炸 —— 模拟流到一半失败
        raise RuntimeError("writer LLM down")

    monkeypatch.setattr("app.turns.retry.stream_writer", boom_writer)
    # B / Options 不应被触及(写手已失败提前返回)
    _patch_review(monkeypatch, json.loads(_bb({}, "")), [])
    _patch_options(monkeypatch, OptionsOutput(options=["x"]), [])

    evs = []
    async with Session() as s:
        async for ev in stream_retry_turn(s, sid, "writer"):
            evs.append(ev)
    assert evs[-1]["type"] == "error" and "writer" in evs[-1]["reason"]
    assert not any(e["type"] in ("state_updated", "retry_done") for e in evs)
    # DB 完好:原叙事、黑板、选项一字未动
    t2 = await _turn2(Session, sid)
    assert t2.narrative == NARR_ORIG
    assert json.loads(t2.options_json)["options"] == OPTS_ORIG.options
    assert json.loads(t2.options_messages) == o_msgs
    assert await _bb_now(Session, sid) == bb_before
