"""诊断:Director-A / Director-B 偶发 JSON 失败的根因。只观测、不修复。

忠实复刻 run_director / run_director_review 的调用(同 build_messages、同 temperature=0.3、
同 response_format=json_object、同 model),但额外捕获 finish_reason / usage / 解析前 raw,
以便区分:截断 / 非法JSON语法 / Pydantic校验 / 空黑板退化。
用法(backend/ 下):python -m scripts.diag_json
"""

import asyncio
import json
import sqlite3

from pydantic import ValidationError

from app.agents.context import build_messages
from app.config import settings
from app.llm.deepseek_client import get_client
from app.models.schemas import DirectorOutput
from app.stories.store import empty_blackboard

EMPTY = empty_blackboard("诊断故事")
con = sqlite3.connect("vore.db")
_row = con.execute("select json_blob from blackboard where story_id='cli-story'").fetchone()
con.close()
NONEMPTY = json.loads(_row[0]) if _row else None

OPEN_ACTION = "我猛地睁开眼,发现自己躺在一片冰冷的金属甲板上,四周是低沉嗡鸣的机械声。"
NONEMPTY_ACTION = "爱丽丝向前踏出一步,举起武器,空气骤然紧绷。"

failures = []  # 收集失败样本


async def _create(messages):
    r = await get_client().chat.completions.create(
        model=settings.deepseek_model, messages=messages, temperature=0.3,
        response_format={"type": "json_object"},
    )
    ch = r.choices[0]
    return ch.message.content or "", ch.finish_reason, r.usage


def classify(raw, finish_reason, *, validate_pydantic):
    """返回 (ok, category, detail)。"""
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as e:
        cat = "(a)截断" if finish_reason == "length" else "(b)非法JSON语法"
        return False, cat, f"JSONDecodeError: {e}"
    if validate_pydantic:
        try:
            DirectorOutput.model_validate(data)
        except ValidationError as e:
            errs = "; ".join(f"{'.'.join(map(str, x['loc']))}: {x['type']}" for x in e.errors())
            return False, "(c)Pydantic校验", errs
    return True, "ok", ""


async def director_a(blackboard, action):
    raw, fr, usage = await _create(build_messages("director", history=[], blackboard=blackboard, user_action=action))
    ok, cat, detail = classify(raw, fr, validate_pydantic=True)
    return ok, cat, detail, raw, fr, usage.completion_tokens


async def writer_narrative(blackboard, action, brief):
    from app.agents.writer import stream_writer
    chunks = []
    async for tok in stream_writer([], blackboard, action, brief):
        chunks.append(tok)
    return "".join(chunks)


async def director_b(blackboard, action, narrative, a_plan):
    msgs = build_messages("director_review", history=[], blackboard=blackboard, user_action=action,
                          narrative=narrative, director_a_plan=a_plan)
    raw, fr, usage = await _create(msgs)
    ok, cat, detail = classify(raw, fr, validate_pydantic=False)  # B 只做 json.loads
    return ok, cat, detail, raw, fr, usage.completion_tokens


async def run_a_batch(label, blackboard, action, n):
    stats = {"ok": 0, "fail": 0, "by_finish": {}}
    for i in range(n):
        ok, cat, detail, raw, fr, ctoks = await director_a(blackboard, action)
        stats["by_finish"][fr] = stats["by_finish"].get(fr, 0) + 1
        if ok:
            stats["ok"] += 1
        else:
            stats["fail"] += 1
            failures.append(("Director-A", label, cat, detail, fr, ctoks, raw))
            print(f"  [A/{label}] ✗ #{i} {cat} finish={fr} ctoks={ctoks} :: {detail[:120]}")
    print(f"[Director-A / {label}] ok={stats['ok']} fail={stats['fail']} finish分布={stats['by_finish']}")


async def run_pipeline_batch(label, blackboard, action, n):
    """跑完整 A→Writer→B,统计 B 的失败(B 需要真实 narrative)。"""
    stats = {"ok": 0, "fail": 0, "by_finish": {}}
    for i in range(n):
        ok_a, _, _, raw_a, _, _ = await director_a(blackboard, action)
        if not ok_a:
            continue  # A 已失败,跳过(A 的失败单独由 run_a_batch 统计)
        a = DirectorOutput.model_validate(json.loads(raw_a))
        narrative = await writer_narrative(blackboard, action, a.writing_brief)
        ok, cat, detail, raw, fr, ctoks = await director_b(blackboard, action, narrative, a.model_dump())
        stats["by_finish"][fr] = stats["by_finish"].get(fr, 0) + 1
        if ok:
            stats["ok"] += 1
        else:
            stats["fail"] += 1
            failures.append(("Director-B", label, cat, detail, fr, ctoks, raw))
            print(f"  [B/{label}] ✗ #{i} {cat} finish={fr} ctoks={ctoks} :: {detail[:120]}")
    print(f"[Director-B / {label}] ok={stats['ok']} fail={stats['fail']} finish分布={stats['by_finish']}")


async def main():
    print("=" * 72)
    print("Director-A 失败率(空黑板 vs 非空黑板)")
    print("=" * 72)
    await run_a_batch("空黑板", EMPTY, OPEN_ACTION, 16)
    if NONEMPTY:
        await run_a_batch("非空黑板", NONEMPTY, NONEMPTY_ACTION, 16)

    print("\n" + "=" * 72)
    print("Director-B 失败率(完整管线;空黑板 vs 非空黑板)")
    print("=" * 72)
    await run_pipeline_batch("空黑板", EMPTY, OPEN_ACTION, 8)
    if NONEMPTY:
        await run_pipeline_batch("非空黑板", NONEMPTY, NONEMPTY_ACTION, 8)

    print("\n" + "=" * 72)
    print(f"收集到 {len(failures)} 个失败样本,下面 dump 原始内容(最多 3 个)")
    print("=" * 72)
    for who, label, cat, detail, fr, ctoks, raw in failures[:3]:
        print(f"\n########## {who} / {label} / 分类={cat} / finish_reason={fr} / completion_tokens={ctoks} ##########")
        print(f"--- 报错: {detail}")
        print(f"--- raw 长度={len(raw)} 字符;尾部 120 字符(看是否戛然而止):")
        print(repr(raw[-120:]))
        print("--- raw 全文 ---")
        print(raw)


if __name__ == "__main__":
    asyncio.run(main())
