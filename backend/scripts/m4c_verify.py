"""M4-C 验收:绘图 Web 人在回路 + 异步出图回推。

对着**真实 uvicorn**(非 ASGITransport,后者会缓冲、测不出时序)跑全流程:
  A. /draw 只写稿不出图、不花钱;无 draft 时 confirm 被拒(确认闸门)。
  B. confirm(花 1 张图的钱):confirm 的 SSE 先推 image_generating、晚推 image_ready,
     打印带时间戳的时序证明二者之间有真实异步间隔(不是一次性返回)。
  C. reuse:不调 API、不新增图片文件、image_paths 不重复追加。
  D. skip:不出图、无副作用。
  E. 确认闸门无旁路:execute_image 只被 apply_decision(confirm) 调用(静态核查)。

用法(backend/ 下,需真实 OPENAI key):  python -m scripts.m4c_verify
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
from sqlalchemy import func, select

from app.db.models import Blackboard, ImageGen, Story
from app.db.session import async_session, create_all, engine
from app.storage import BACKEND_ROOT, IMAGES_SUBDIR

STORY = "m4c-verify"
SCENE = "old_classroom"
FIXTURES = Path(__file__).resolve().parent.parent / "fixtures"
IMAGES_DIR = BACKEND_ROOT / IMAGES_SUBDIR


def _free_port() -> int:
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


async def _seed() -> None:
    await create_all(engine)
    bb = json.loads((FIXTURES / "visual_demo_blackboard.json").read_text(encoding="utf-8"))
    async with async_session() as s:
        # 干净起步:清掉同名 story 的旧记录
        old = await s.get(Story, STORY)
        if old:
            await s.delete(old)
        bb_old = await s.get(Blackboard, STORY)
        if bb_old:
            await s.delete(bb_old)
        for ig in (await s.execute(select(ImageGen).where(ImageGen.story_id == STORY))).scalars():
            await s.delete(ig)
        await s.commit()
        s.add(Story(id=STORY, title="M4-C 验收故事"))
        s.add(Blackboard(story_id=STORY, json_blob=json.dumps(bb, ensure_ascii=False)))
        await s.commit()


async def _imagegen_rows() -> list[ImageGen]:
    async with async_session() as s:
        return list(
            (await s.execute(select(ImageGen).where(ImageGen.story_id == STORY).order_by(ImageGen.id))).scalars()
        )


async def _scene_image_paths(client: httpx.AsyncClient, base: str) -> list[str]:
    snap = (await client.get(f"{base}/story/{STORY}/snapshot")).json()
    return snap["blackboard"]["scenes"][SCENE].get("image_paths", [])


def _img_file_count() -> int:
    return len(list(IMAGES_DIR.glob("*.png"))) if IMAGES_DIR.exists() else 0


def _gate_static_check() -> None:
    """静态核查:execute_image 的调用点只有 draw_service.apply_decision。"""
    import re

    app_dir = BACKEND_ROOT / "app"
    callers = []
    for p in app_dir.rglob("*.py"):
        for i, line in enumerate(p.read_text(encoding="utf-8").splitlines(), 1):
            if re.search(r"\bexecute_image\s*\(", line) and "def execute_image" not in line:
                callers.append(f"{p.relative_to(BACKEND_ROOT)}:{i}")
    print("  execute_image 调用点:", callers)
    assert callers and all("draw_service.py" in c for c in callers), (
        f"execute_image 出现在 apply_decision 之外:{callers}"
    )
    print("  ✓ execute_image 只在 draw_service.apply_decision 内被调用(confirm 唯一路径)")


async def run(base: str) -> None:
    async with httpx.AsyncClient(timeout=120.0) as client:
        # ---------- A. /draw 写稿不花钱 + 无 draft 时 confirm 被拒 ----------
        print("\n=== A. /draw 写稿(不出图、不花钱)===")
        igs_before = len(await _imagegen_rows())
        files_before = _img_file_count()
        r = await client.post(f"{base}/story/{STORY}/draw", json={"scene": SCENE, "source": "user_initiated"})
        r.raise_for_status()
        draft = r.json()
        print(f"  draft_ready: draft_id={draft['draft_id'][:8]}… kind={draft['kind']} refs={len(draft['refs'])}")
        print(f"  prompt_text(前80字): {draft['prompt_text'][:80]}…")
        assert draft["type"] == "draft_ready" and draft["draft_id"]
        assert len(await _imagegen_rows()) == igs_before, "写稿阶段不应产生 ImageGen"
        assert _img_file_count() == files_before, "写稿阶段不应产生图片文件"
        print("  ✓ 写稿未出图、未落 ImageGen、未生成文件")

        bogus = await client.post(
            f"{base}/story/{STORY}/draw/confirm", json={"draft_id": "deadbeef", "decision": "confirm"}
        )
        assert bogus.status_code == 404, f"伪 draft_id 应 404,得到 {bogus.status_code}"
        print("  ✓ 闸门:无已审阅 draft 时 confirm 被拒(404),无法绕过 /draw 直接出图")

        # ---------- B. confirm:真出图 + 异步时序 ----------
        print("\n=== B. confirm(真出图,花 1 张图的钱)— SSE 异步时序 ===")
        files_before_b = _img_file_count()
        # 用户编辑后的稿(模拟前端编辑):末尾追加一句,证明 confirm 用的是 body 里的稿
        edited = draft["prompt_text"] + " 画面安静,光线柔和。"
        t0 = time.monotonic()
        timeline: list[tuple[float, dict]] = []
        async with client.stream(
            "POST",
            f"{base}/story/{STORY}/draw/confirm",
            json={"draft_id": draft["draft_id"], "decision": "confirm", "prompt": edited},
        ) as resp:
            assert resp.headers["content-type"].startswith("text/event-stream")
            async for line in resp.aiter_lines():
                if line.startswith("data: "):
                    ev = json.loads(line[len("data: ") :])
                    timeline.append((time.monotonic() - t0, ev))

        print("  SSE 时序(t 相对 confirm 发出,秒):")
        for t, ev in timeline:
            extra = ev.get("image_path") or ev.get("reason") or ev.get("api_call") or ""
            print(f"    t=+{t:6.3f}s  {ev['type']:18s} {extra}")
        types = [ev["type"] for _, ev in timeline]
        assert types[0] == "image_generating", f"首事件应为 image_generating,得到 {types}"
        assert types[-1] == "image_ready", f"末事件应为 image_ready,得到 {types}"
        t_gen = timeline[0][0]
        t_ready = timeline[-1][0]
        gap = t_ready - t_gen
        print(f"  异步间隔 generating→ready = {gap:.3f}s(>0 即证明 generating 先到、出图后置,非一次性返回)")
        assert gap > 0.3, f"间隔过小({gap:.3f}s),疑似阻塞式一次性返回"

        ready = timeline[-1][1]
        img_rel = ready["image_path"]
        assert (BACKEND_ROOT / img_rel).exists(), f"图片文件不存在:{img_rel}"
        assert _img_file_count() == files_before_b + 1, "应恰好新增 1 个图片文件"
        paths = await _scene_image_paths(client, base)
        assert img_rel in paths, "黑板 image_paths 未写入新图"
        igs = await _imagegen_rows()
        assert igs[-1].kind in {"new_scene", "variant"} and igs[-1].output_path == img_rel
        print(f"  ✓ 出图落盘 {img_rel}(api={ready['api_call']});黑板 image_paths 与 ImageGen 已更新")
        print(f"  生成的图:{BACKEND_ROOT / img_rel}")

        # ---------- C. reuse:零花费、不重复追加 ----------
        print("\n=== C. reuse(复用已有图,零花费)===")
        files_before_c = _img_file_count()
        paths_before_c = await _scene_image_paths(client, base)
        igs_before_c = len(await _imagegen_rows())
        r = await client.post(f"{base}/story/{STORY}/draw", json={"scene": SCENE})
        d2 = r.json()
        rr = await client.post(
            f"{base}/story/{STORY}/draw/confirm",
            json={"draft_id": d2["draft_id"], "decision": "reuse", "reuse_image_path": img_rel},
        )
        rr.raise_for_status()
        body = rr.json()
        print(f"  reuse 返回: {body}")
        assert body["action"] == "reuse" and body["image_path"] == img_rel
        assert _img_file_count() == files_before_c, "reuse 不应新增图片文件(未调 API)"
        paths_after_c = await _scene_image_paths(client, base)
        assert paths_after_c == paths_before_c, "reuse 不应向 image_paths 重复追加"
        igs_after_c = await _imagegen_rows()
        assert len(igs_after_c) == igs_before_c + 1 and igs_after_c[-1].kind == "reuse"
        print("  ✓ reuse:未调 API、无新文件、image_paths 未重复追加、ImageGen 记 kind=reuse")

        # ---------- D. skip:无副作用 ----------
        print("\n=== D. skip(跳过,无副作用)===")
        files_before_d = _img_file_count()
        paths_before_d = await _scene_image_paths(client, base)
        igs_before_d = len(await _imagegen_rows())
        r = await client.post(f"{base}/story/{STORY}/draw", json={"scene": SCENE})
        d3 = r.json()
        rs = await client.post(
            f"{base}/story/{STORY}/draw/confirm", json={"draft_id": d3["draft_id"], "decision": "skip"}
        )
        rs.raise_for_status()
        print(f"  skip 返回: {rs.json()}")
        assert rs.json()["action"] == "skip"
        assert _img_file_count() == files_before_d
        assert await _scene_image_paths(client, base) == paths_before_d
        assert len(await _imagegen_rows()) == igs_before_d, "skip 不应产生 ImageGen"
        print("  ✓ skip:无出图、无文件、无 ImageGen、image_paths 不变")


async def main() -> None:
    await _seed()
    print("=== E. 确认闸门静态核查 ===")
    _gate_static_check()

    port = _free_port()
    base = f"http://127.0.0.1:{port}"
    env = dict(os.environ)
    proc = subprocess.Popen(
        [sys.executable, "-m", "uvicorn", "app.main:app", "--host", "127.0.0.1", "--port", str(port), "--log-level", "warning"],
        cwd=str(BACKEND_ROOT),
        env=env,
    )
    try:
        # 等 health 起来
        async with httpx.AsyncClient(timeout=5.0) as c:
            for _ in range(60):
                try:
                    if (await c.get(f"{base}/health")).status_code == 200:
                        break
                except Exception:
                    pass
                await asyncio.sleep(0.5)
            else:
                raise RuntimeError("uvicorn 未在 30s 内就绪")
        await run(base)
        print("\n✅ M4-C 全部验收通过")
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            proc.kill()


if __name__ == "__main__":
    asyncio.run(main())
