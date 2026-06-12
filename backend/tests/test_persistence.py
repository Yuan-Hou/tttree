import json

from sqlalchemy import select

from app.db.models import Blackboard, Turn
from app.db.session import create_all, make_engine, make_session_factory

# 一份带中文标点 value 的黑板,用于验证往返无损
SAMPLE_BLACKBOARD = {
    "story_meta": {
        "title": "林间迷踪",
        "current_scene": "forest_edge",
        "latest_beat": "初入林间",
    },
    "scenes": {
        "forest_edge": {
            "name": "森林边缘的空地",
            "base_prompt": "黄昏的森林空地,古树环绕,落叶满地。",
            "visual_anchors": ["参天古树", "金色夕照"],
            "state": "宁静、无人,空气里有泥土与青草的气息;门虚掩着。",
            "connections": ["hidden_cabin"],
            "image_paths": [],
        }
    },
    "characters": {
        "主角": {
            "location": "forest_edge",
            "status": "清醒、警觉",
            "inventory": [],
            "relations": {},
            "appearance": "一身风尘仆仆的旅人装束,神情警惕。",
        }
    },
    "items": {},
    "notes": [
        {"content": "主角失忆,不记得自己为何身处此地——需后续呼应。", "since_beat": "初入林间"}
    ],
}


async def test_blackboard_and_turn_roundtrip(tmp_path):
    url = f"sqlite+aiosqlite:///{tmp_path / 'roundtrip.db'}"
    engine = make_engine(url)
    await create_all(engine)
    Session = make_session_factory(engine)

    blob = json.dumps(SAMPLE_BLACKBOARD, ensure_ascii=False)

    async with Session() as s:
        s.add(Blackboard(story_id="story-1", json_blob=blob))
        s.add(
            Turn(
                story_id="story-1",
                turn_index=1,
                beat_title="初入林间",
                user_input="推开那扇虚掩的木门",
                narrative="你伸手推开木门,铰链发出一声沉闷的吱呀……",
                director_a_json="{}",
                director_b_json=blob,
                blackboard_after=blob,
            )
        )
        await s.commit()

    async with Session() as s:
        bb = await s.get(Blackboard, "story-1")
        assert bb is not None
        # JSON 往返无损(结构层面)
        assert json.loads(bb.json_blob) == SAMPLE_BLACKBOARD
        # 中文标点 value 正确存取(字符层面)
        assert "宁静、无人,空气里有泥土与青草的气息;门虚掩着。" in bb.json_blob
        assert bb.updated_at is not None

        turn = (
            await s.execute(select(Turn).where(Turn.story_id == "story-1"))
        ).scalar_one()
        assert turn.turn_index == 1
        assert turn.beat_title == "初入林间"
        assert turn.user_input == "推开那扇虚掩的木门"
        assert json.loads(turn.blackboard_after) == SAMPLE_BLACKBOARD
        assert turn.created_at is not None

    await engine.dispose()
    print("\n[roundtrip] blackboard + turn 往返无损;中文标点 value 完整保留。")
