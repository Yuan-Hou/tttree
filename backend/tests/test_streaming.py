"""Stage 1:流式基元 —— merge_streams 合流语义 + 各 agent 的 parse/stream 拆分一致性。

真 LLM 留给验证脚本;这里用「直接喂 chunk」的假流验证累积后解析与一次性解析等价。
"""

import asyncio
from collections.abc import AsyncIterator

import pytest

from app.agents.director import parse_director_output, stream_director
from app.agents.director_review import parse_review_output
from app.agents.options import parse_options_output, stream_options
from app.agents.streaming import merge_streams
from app.models.schemas import DirectorOutput, OptionsOutput


async def _agen(items, delay=0.0):
    for x in items:
        if delay:
            await asyncio.sleep(delay)
        yield x


# ── merge_streams ────────────────────────────────────────────────
async def test_merge_interleaves_and_reports_each_done():
    streams = {"a": _agen(["a1", "a2"]), "b": _agen(["b1"])}
    tokens: dict[str, list[str]] = {"a": [], "b": []}
    done: list[str] = []
    async for ev in merge_streams(streams):
        if ev.done:
            assert ev.error is None
            done.append(ev.label)
        else:
            tokens[ev.label].append(ev.delta)
    assert tokens == {"a": ["a1", "a2"], "b": ["b1"]}
    assert sorted(done) == ["a", "b"]  # 两条子流各报一次结束


async def test_merge_one_stream_error_does_not_kill_sibling():
    async def boom() -> AsyncIterator[str]:
        yield "x"
        raise RuntimeError("kaboom")

    streams = {"ok": _agen(["1", "2"], delay=0.01), "bad": boom()}
    ok_tokens: list[str] = []
    errors: dict[str, Exception] = {}
    async for ev in merge_streams(streams):
        if ev.done:
            if ev.error:
                errors[ev.label] = ev.error
        elif ev.label == "ok":
            ok_tokens.append(ev.delta)
    assert ok_tokens == ["1", "2"]                       # 兄弟流照常跑完
    assert isinstance(errors.get("bad"), RuntimeError)   # 失败流以带 error 的 done 上报


# ── parse_* 与 stream_* 累积后等价 ───────────────────────────────
def test_parse_director_output_happy_and_error():
    raw = DirectorOutput(situation="s", beat_points=["b"], writing_brief="wb").model_dump_json()
    assert parse_director_output(raw).writing_brief == "wb"
    from app.agents.director import DirectorOutputError
    with pytest.raises(DirectorOutputError):
        parse_director_output("not json at all {")


def test_parse_options_output_happy_and_error():
    assert parse_options_output('{"options":["甲","乙"]}').options == ["甲", "乙"]
    from app.agents.options import OptionsError
    with pytest.raises(OptionsError):
        parse_options_output("}{")


def test_parse_review_output_returns_dict():
    bb = parse_review_output('{"scenes":{"x":{"name":"X"}}}')
    assert bb["scenes"]["x"]["name"] == "X"


async def test_stream_director_accumulates_to_same_output(monkeypatch):
    """stream_director 逐 chunk 产文 → 累积后 parse 与一次性 run_director 等价。"""
    full = DirectorOutput(situation="s", beat_points=["b1", "b2"], writing_brief="走进门").model_dump_json()
    chunks = [full[i:i + 7] for i in range(0, len(full), 7)]  # 切碎成多 token

    async def fake_stream(model, messages):
        for c in chunks:
            yield c

    monkeypatch.setattr("app.agents.director.chat_json_stream", fake_stream)
    got = []
    async for d in stream_director([], {}, "走进门", messages=[{"role": "user", "content": "x"}]):
        got.append(d)
    assert got == chunks                                   # 原样逐 token 透出
    assert parse_director_output("".join(got)).writing_brief == "走进门"


async def test_stream_options_accumulates_to_same_output(monkeypatch):
    full = '{"options":["往前走","退回去"]}'
    chunks = [full[i:i + 5] for i in range(0, len(full), 5)]

    async def fake_stream(model, messages):
        for c in chunks:
            yield c

    monkeypatch.setattr("app.agents.options.chat_json_stream", fake_stream)
    got = "".join([d async for d in stream_options([], {}, "u", "成稿", messages=[{"role": "user", "content": "x"}])])
    assert parse_options_output(got).options == ["往前走", "退回去"]
