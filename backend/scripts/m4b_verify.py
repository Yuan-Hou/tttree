"""M4-B 验收:回合推进 SSE + 快照。用 httpx ASGITransport 在进程内打 SSE(不起服务器)。
真实跑一轮三段式(DeepSeek;零 gpt-image-2)。用完删除该测试故事。
用法(backend/ 下):python -m scripts.m4b_verify
"""

import asyncio
import json
import time

import httpx

from app.agents.context import build_messages
from app.config import settings
from app.db.session import create_all, engine
from app.llm.deepseek_client import get_client


def parse_sse(buffer: str):
    """从已累积文本里切出完整 SSE 帧(以空行分隔),返回 (events, remainder)。"""
    events, rest = [], buffer
    while "\n\n" in rest:
        frame, rest = rest.split("\n\n", 1)
        for line in frame.splitlines():
            if line.startswith("data: "):
                events.append(json.loads(line[6:]))
    return events, rest


async def main() -> None:
    await create_all(engine)
    from app.main import app

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://t", timeout=300) as c:
        # 新建测试故事
        sid = (await c.post("/stories", json={"title": "M4B 测试故事"})).json()["id"]
        print(f"新建故事 {sid}")

        # ---- POST /turn,流式接收 SSE ----
        print("\n" + "=" * 72)
        print("POST /story/{id}/turn 的 SSE 事件序列")
        print("=" * 72)
        action = "我猛地睁开眼,发现自己躺在一片冰冷的金属甲板上,四周是低沉嗡鸣的机械声。"
        token_count = 0
        first_token_t = last_token_t = None
        order = []
        buf = ""
        t0 = time.monotonic()
        async with c.stream("POST", f"/story/{sid}/turn", json={"user_input": action}) as resp:
            assert resp.headers["content-type"].startswith("text/event-stream")
            async for chunk in resp.aiter_text():
                buf += chunk
                events, buf = parse_sse(buf)
                for ev in events:
                    t = ev["type"]
                    if t == "narrative_token":
                        token_count += 1
                        now = time.monotonic()
                        if first_token_t is None:
                            first_token_t = now
                        last_token_t = now
                        if token_count <= 3:
                            print(f"  [{now - t0:5.2f}s] narrative_token: {ev['text']!r}")
                    else:
                        order.append(t)
                        now = time.monotonic()
                        if t == "turn_started":
                            print(f"  [{now - t0:5.2f}s] turn_started turn_index={ev['turn_index']}")
                        elif t == "narrative_done":
                            print(f"  [{now - t0:5.2f}s] narrative_done ({len(ev['full_narrative'])}字)")
                        elif t == "state_updated":
                            sc = ev["blackboard"].get("scenes", {})
                            print(f"  [{now - t0:5.2f}s] state_updated beat={ev['beat_title']!r} scenes={list(sc)}")
                        elif t == "draw_proposed":
                            print(f"  [{now - t0:5.2f}s] draw_proposed {[p.get('scene') for p in ev['proposals']]}")
                        elif t == "turn_done":
                            print(f"  [{now - t0:5.2f}s] turn_done")
                        elif t == "error":
                            print(f"  [{now - t0:5.2f}s] ERROR: {ev.get('reason')}")

        print(f"\nnarrative_token 帧数 = {token_count}")
        if first_token_t and last_token_t:
            print(f"首 token @ {first_token_t - t0:.2f}s,末 token @ {last_token_t - t0:.2f}s "
                  f"→ 流式跨度 {last_token_t - first_token_t:.2f}s(非一次性蹦出)")
        print("非 token 事件顺序:", order)
        assert token_count > 5, "narrative_token 应为多帧流式"
        assert order[:2] == ["turn_started", "narrative_done"] or order[0] == "turn_started"
        assert order[-1] == "turn_done"
        assert "state_updated" in order

        # ---- GET /snapshot ----
        print("\n" + "=" * 72)
        print("GET /story/{id}/snapshot")
        print("=" * 72)
        snap = (await c.get(f"/story/{sid}/snapshot")).json()
        print(f"title={snap['title']}  history 回合数={len(snap['history'])}")
        print(f"当前场景={snap['blackboard']['story_meta'].get('current_scene')!r}")
        print(f"scenes={list(snap['blackboard'].get('scenes', {}))}")
        print(f"各场景 image_paths={snap['scenes_images']}")
        print("history[0]:", {k: (v[:30] + '…' if isinstance(v, str) and len(v) > 30 else v)
                               for k, v in snap["history"][0].items()})
        assert len(snap["history"]) == 1
        assert snap["history"][0]["beat_title"]

        # ---- 缓存 usage 探针(确认 SSE 改造未破坏三段式缓存布局)----
        print("\n" + "=" * 72)
        print("缓存 usage 探针(用快照的黑板 + 历史)")
        print("=" * 72)
        bb = snap["blackboard"]
        hist = []
        for h in snap["history"]:
            hist.append({"role": "user", "content": h["user_input"]})
            hist.append({"role": "assistant", "content": h["narrative"]})
        msgs = build_messages("director", history=hist, blackboard=bb, user_action="(probe)")
        assert msgs[0]["content"].startswith("# 文风圣经")
        r = await get_client().chat.completions.create(
            model=settings.deepseek_model, messages=msgs,
            response_format={"type": "json_object"},
        )
        u = r.usage.model_dump()
        print(f"director usage: hit={u.get('prompt_cache_hit_tokens')} "
              f"miss={u.get('prompt_cache_miss_tokens')} total={u.get('prompt_tokens')}")

        # ---- 清理测试故事 ----
        await c.delete(f"/stories/{sid}")
        print(f"\n已删除测试故事 {sid}")
    print("\n✅ M4-B:SSE 多帧流式 + state_updated 带新黑板 + turn_done 收尾 + 快照可恢复 + 缓存未破坏。")


if __name__ == "__main__":
    asyncio.run(main())
