"""M4-D 验收:极简测试页 + 整条链路在浏览器层之下的真实贯通。

无头环境无法跑真浏览器,故对**真实 uvicorn** 驱动页面所依赖的同一组端点/SSE,
重点用真实时间戳证明「文本线/图片线解耦」(验收④的核心):
出图(confirm)在后台跑的那~1分钟里,并发发起一个回合,叙事 token **照常流式涌现**,
即 narrative_token 的时间戳落在 image_generating→image_ready 区间内。

覆盖:
  ⓪ GET / 返回测试页 HTML;GET /storage/<图> 能取到生成图(页面据此显示)。
  ① 回合 SSE 逐 token 涌现(token 间有时间间隔,非一次性)。
  ② state_updated 携带新黑板(状态面板据此刷新)。
  ④ 出图期间并发推进剧情:叙事 token 落在出图区间内(解耦,花 1 张图)。
  ⑤ snapshot 恢复:history + scenes_images 完整(刷新即此调用)。
  ⑥ 故事 新建/列出/删除。
  其余:reuse / skip 零花费。

用法(backend/ 下,需真实 DeepSeek + OpenAI key):  python -m scripts.m4d_verify
"""

import asyncio
import json
import os
import socket
import subprocess
import sys
import time
from pathlib import Path

import httpx

from app.storage import BACKEND_ROOT

ACTION1 = "我推开教室的门走进去,环顾四周。"
ACTION2 = "我走到窗边,伸手碰了碰积灰的窗台。"


def _free_port() -> int:
    s = socket.socket(); s.bind(("127.0.0.1", 0)); p = s.getsockname()[1]; s.close(); return p


async def _sse(client, url, body, sink):
    """逐帧读 SSE,把 (相对t, event) 追加进 sink。返回完整 event 列表。"""
    async with client.stream("POST", url, json=body) as resp:
        resp.raise_for_status()
        async for line in resp.aiter_lines():
            if line.startswith("data: "):
                ev = json.loads(line[6:])
                sink.append((time.monotonic(), ev))
    return sink


async def run(base: str) -> None:
    async with httpx.AsyncClient(timeout=180.0) as client:
        # ⓪ 测试页
        print("=== ⓪ 测试页与静态资源 ===")
        page = await client.get(f"{base}/")
        assert page.status_code == 200 and "故事档案" in page.text and "streamSSE" in page.text
        print(f"  ✓ GET / 返回测试页({len(page.text)} 字节,含 故事档案/streamSSE 标记)")

        # ⑥ 新建/列出
        print("\n=== ⑥ 故事档案 新建/列出/删除 ===")
        sid = (await client.post(f"{base}/stories", json={"title": "M4-D 演示"})).json()["id"]
        throwaway = (await client.post(f"{base}/stories", json={"title": "待删除"})).json()["id"]
        names = [s["title"] for s in (await client.get(f"{base}/stories")).json()]
        assert "M4-D 演示" in names and "待删除" in names
        d = await client.delete(f"{base}/stories/{throwaway}")
        assert d.json()["ok"]
        names2 = [s["id"] for s in (await client.get(f"{base}/stories")).json()]
        assert throwaway not in names2 and sid in names2
        print(f"  ✓ 新建 2 个、列出含二者、删除其一后列表只剩演示故事(story_id={sid[:8]}…)")

        # ① 回合①:建立场景 + 证明流式涌现
        print("\n=== ① 回合 SSE 逐 token 涌现 ===")
        t0 = time.monotonic()
        ev1: list = []
        await _sse(client, f"{base}/story/{sid}/turn", {"user_input": ACTION1}, ev1)
        toks = [(t - t0) for t, e in ev1 if e["type"] == "narrative_token"]
        gaps = [b - a for a, b in zip(toks, toks[1:])]
        nonzero = [g for g in gaps if g > 0.001]
        print(f"  narrative_token 数={len(toks)};首 token@+{toks[0]:.2f}s 末@+{toks[-1]:.2f}s;"
              f"token 间隔>1ms 的有 {len(nonzero)}/{len(gaps)} 个(证明逐字涌现非一次性)")
        assert len(toks) > 5 and len(nonzero) > 3, "token 未呈现时间上的逐步涌现"
        # ② 状态更新
        su = [e for _, e in ev1 if e["type"] == "state_updated"]
        assert su and "blackboard" in su[0]
        bb = su[0]["blackboard"]
        scenes = list((bb.get("scenes") or {}).keys())
        assert scenes, "回合后黑板应已建立场景"
        scene = bb["story_meta"].get("current_scene") or scenes[0]
        print(f"  ✓ ② state_updated 携带黑板;当前场景={scene};场景集={scenes}")

        # ⑤ 快照恢复
        print("\n=== ⑤ snapshot 恢复(刷新即此调用)===")
        snap = (await client.get(f"{base}/story/{sid}/snapshot")).json()
        assert snap["history"] and snap["history"][0]["user_input"] == ACTION1
        assert scene in snap["scenes_images"]
        print(f"  ✓ snapshot 含 {len(snap['history'])} 条历史 + scenes_images 键={list(snap['scenes_images'])}")

        # ④ 解耦核心:出图(花 1 张图)期间并发推进剧情
        print("\n=== ④ 文本线/图片线解耦(出图期间并发推进剧情,花 1 张图)===")
        draft = (await client.post(f"{base}/story/{sid}/draw", json={"scene": scene, "source": "user_initiated"})).json()
        print(f"  draft_ready: scene={draft['scene']} kind={draft['kind']}")
        base_t = time.monotonic()
        draw_ev: list = []
        turn_ev: list = []
        # 同时跑:confirm 出图流 ‖ 第二个回合的叙事流
        draw_task = asyncio.create_task(
            _sse(client, f"{base}/story/{sid}/draw/confirm",
                 {"draft_id": draft["draft_id"], "decision": "confirm", "prompt": draft["prompt_text"]}, draw_ev)
        )
        await asyncio.sleep(1.0)  # 让 image_generating 先到、出图进入后台
        turn_task = asyncio.create_task(
            _sse(client, f"{base}/story/{sid}/turn", {"user_input": ACTION2}, turn_ev)
        )
        await asyncio.gather(draw_task, turn_task)

        gen_t = next(t - base_t for t, e in draw_ev if e["type"] == "image_generating")
        ready_t = next(t - base_t for t, e in draw_ev if e["type"] == "image_ready")
        turn_toks = [(t - base_t) for t, e in turn_ev if e["type"] == "narrative_token"]
        during = [t for t in turn_toks if gen_t < t < ready_t]
        print(f"  image_generating @+{gen_t:.2f}s → image_ready @+{ready_t:.2f}s(出图窗口 {ready_t-gen_t:.1f}s)")
        print(f"  第二回合 narrative_token: {len(turn_toks)} 个,首@+{turn_toks[0]:.2f}s 末@+{turn_toks[-1]:.2f}s")
        print(f"  ★ 落在出图窗口内的叙事 token: {len(during)} 个(@+{during[0]:.2f}s … @+{during[-1]:.2f}s)")
        assert len(during) > 5, "出图期间叙事未流动 → 未解耦/被阻塞"
        ready_ev = next(e for _, e in draw_ev if e["type"] == "image_ready")
        print(f"  ✓ 解耦成立:出图后台进行的 {ready_t-gen_t:.0f}s 内,文本线照常逐字涌现;图就绪 {ready_ev['image_path']}")

        # ⓪续:静态图可取
        img_rel = ready_ev["image_path"]
        ir = await client.get(f"{base}/{img_rel}")
        assert ir.status_code == 200 and ir.headers["content-type"].startswith("image/")
        print(f"  ✓ GET /{img_rel} → 200 {ir.headers['content-type']}({len(ir.content)} 字节,页面据此显示图)")

        # reuse / skip 零花费
        print("\n=== reuse / skip(零花费)===")
        d_r = (await client.post(f"{base}/story/{sid}/draw", json={"scene": scene})).json()
        rr = (await client.post(f"{base}/story/{sid}/draw/confirm",
              json={"draft_id": d_r["draft_id"], "decision": "reuse", "reuse_image_path": img_rel})).json()
        assert rr["action"] == "reuse"
        d_s = (await client.post(f"{base}/story/{sid}/draw", json={"scene": scene})).json()
        rs = (await client.post(f"{base}/story/{sid}/draw/confirm",
              json={"draft_id": d_s["draft_id"], "decision": "skip"})).json()
        assert rs["action"] == "skip"
        print(f"  ✓ reuse→{rr['action']}(复用 {rr['image_path'].split('/')[-1]});skip→{rs['action']}")

        print("\n✅ M4-D 全部验收通过")


async def main() -> None:
    port = _free_port()
    base = f"http://127.0.0.1:{port}"
    proc = subprocess.Popen(
        [sys.executable, "-m", "uvicorn", "app.main:app", "--host", "127.0.0.1", "--port", str(port), "--log-level", "warning"],
        cwd=str(BACKEND_ROOT), env=dict(os.environ),
    )
    try:
        async with httpx.AsyncClient(timeout=5.0) as c:
            for _ in range(60):
                try:
                    if (await c.get(f"{base}/health")).status_code == 200:
                        break
                except Exception:
                    pass
                await asyncio.sleep(0.5)
            else:
                raise RuntimeError("uvicorn 未就绪")
        await run(base)
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            proc.kill()


if __name__ == "__main__":
    asyncio.run(main())
