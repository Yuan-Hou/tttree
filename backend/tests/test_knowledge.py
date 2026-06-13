from app.agents.context import build_messages
from app.db.session import create_all, make_engine, make_session_factory
from app.knowledge.store import clear_knowledge, get_knowledge, set_knowledge

LONG_TEXT = (
    "# 角色设定\n"
    "白子(Shiroko):阿拜多斯高中对策委员会成员,沉默寡言,喜欢摩托与狗。\n"
    "说话简短、克制,偶尔冒出一句让人意外的认真。\n\n"
    "# 世界观\n"
    "基沃托斯,学生自治的庞大都市;学生头顶有光环(Halo)。\n"
    "夏莱(Schale)是协助各社团解决事件的机构。\n\n"
    "# 关系\n"
    "白子 与 对策委员会其他成员(野宫、绿、爱丽丝)是搭档。\n"
)


async def _setup(tmp_path):
    engine = make_engine(f"sqlite+aiosqlite:///{tmp_path / 'kb.db'}")
    await create_all(engine)
    return make_session_factory(engine)


async def test_knowledge_roundtrip_lossless(tmp_path):
    Session = await _setup(tmp_path)
    async with Session() as s:
        # 新故事:无该行,读回空串
        assert await get_knowledge(s, "story-1") == ""
        # 写入含中文、含换行的长文本,读回逐字无损
        await set_knowledge(s, "story-1", LONG_TEXT)
    async with Session() as s:
        assert await get_knowledge(s, "story-1") == LONG_TEXT
    # 整篇覆盖(upsert,不追加)
    async with Session() as s:
        await set_knowledge(s, "story-1", "覆盖后的新内容")
    async with Session() as s:
        assert await get_knowledge(s, "story-1") == "覆盖后的新内容"
    # 清空
    async with Session() as s:
        assert await clear_knowledge(s, "story-1") is True
        assert await clear_knowledge(s, "story-1") is False  # 再清空:本就没有
    async with Session() as s:
        assert await get_knowledge(s, "story-1") == ""


async def test_knowledge_story_isolation(tmp_path):
    Session = await _setup(tmp_path)
    async with Session() as s:
        await set_knowledge(s, "story-A", "A 的专属设定:主角是白子")
        await set_knowledge(s, "story-B", "B 的专属设定:主角是星野")
    async with Session() as s:
        a = await get_knowledge(s, "story-A")
        b = await get_knowledge(s, "story-B")
    assert "白子" in a and "星野" not in a  # A 的设定在 B 里读不到
    assert "星野" in b and "白子" not in b


# ---- 注入隔离:知识库只进 Director-A,Writer / Director-B 完全拿不到 ----

_BB = {"story_meta": {"current_scene": "x"}, "scenes": {}, "characters": {}, "items": {}, "notes": []}
_SECRET = "KNOWLEDGE_SENTINEL_白子摩托"


def test_knowledge_injected_only_into_director():
    director = build_messages("director", history=[], blackboard=_BB, user_action="走进去", knowledge=_SECRET)
    # A 拿到了知识库,且在一条 system 消息里、位于 history(此处为空)之前的稳定前缀位置
    assert any(m["role"] == "system" and _SECRET in m["content"] for m in director)

    # Writer / Director-B 完全拿不到知识库——而且传不传 knowledge,它们的 messages 逐字节相同
    writer_with = build_messages("writer", history=[], blackboard=_BB, user_action="走进去",
                                 writing_brief="wb", knowledge=_SECRET)
    writer_without = build_messages("writer", history=[], blackboard=_BB, user_action="走进去",
                                    writing_brief="wb")
    assert all(_SECRET not in m["content"] for m in writer_with)
    assert writer_with == writer_without  # 缓存完全不受知识库影响

    b_with = build_messages("director_review", history=[], blackboard=_BB, user_action="走进去",
                            narrative="一段叙事", knowledge=_SECRET)
    b_without = build_messages("director_review", history=[], blackboard=_BB, user_action="走进去",
                               narrative="一段叙事")
    assert all(_SECRET not in m["content"] for m in b_with)
    assert b_with == b_without


def test_director_first_system_message_unchanged_by_knowledge():
    """第一条 system(文风圣经)逐字节不受知识库影响,知识库是额外追加的第二条 system。"""
    without = build_messages("director", history=[], blackboard=_BB, user_action="走进去")
    with_kb = build_messages("director", history=[], blackboard=_BB, user_action="走进去", knowledge=_SECRET)
    assert with_kb[0] == without[0]  # 文风圣经那条 system 一致 → 跨 agent 共享前缀不被破坏
    assert with_kb[1]["role"] == "system" and _SECRET in with_kb[1]["content"]
    assert len(with_kb) == len(without) + 1
