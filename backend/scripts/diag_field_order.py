"""第一步实测:DeepSeek JSON mode 是否按 schema 字段顺序逐字段生成,
且后字段能依赖前字段已生成的值(字段排序作为思维链的地基)。

测试 schema(有明显先后依赖):
  a = 1..100 的随机整数
  b = a + 10
  c = b * 2

两种 prompt 变体,各跑 N 次:
  [default]  只描述字段,不显式要求顺序  → 测 JSON mode 默认行为是否就按 schema 顺序
  [explicit] 显式要求「按 a→b→c 逐字段思考并输出,后字段依赖前字段的值」

每次检查:① 原始 JSON 里键顺序是否 a,b,c(=token 生成顺序)
         ② b==a+10 且 c==b*2(=后字段确实依赖了前字段的值)

用法(backend/ 下):  python -m scripts.diag_field_order
"""

import asyncio
import json
import re

from app.config import settings
from app.llm.deepseek_client import get_client

N = 8
TEMPERATURE = 1.0  # 拉高温度让 a 充分随机,更能检验「链」在不同取值下是否稳

FIELDS_DESC = (
    '字段:\n'
    '  "a": 1 到 100 之间的一个随机整数\n'
    '  "b": a 的值加 10\n'
    '  "c": b 的值乘 2\n'
    '只输出一个 JSON 对象,三个字段都填整数。'
)

PROMPTS = {
    "default": FIELDS_DESC,
    "explicit": (
        "请按 a → b → c 的顺序**逐字段依次思考并输出**:先定 a,再据 a 算 b,再据 b 算 c,"
        "后面的字段必须使用前面字段已确定的值。\n\n" + FIELDS_DESC
    ),
}


def _key_order_from_raw(raw: str) -> list[str]:
    """从原始 JSON 文本里按出现先后取顶层键序(json.loads 保留文档顺序)。"""
    try:
        return list(json.loads(raw).keys())
    except json.JSONDecodeError:
        return re.findall(r'"(\w+)"\s*:', raw)


async def run_variant(name: str, prompt: str) -> dict:
    client = get_client()
    order_ok = 0
    dep_ok = 0
    samples = []
    for i in range(N):
        resp = await client.chat.completions.create(
            model=settings.deepseek_model,
            messages=[{"role": "user", "content": prompt}],
            temperature=TEMPERATURE,
            response_format={"type": "json_object"},
        )
        raw = (resp.choices[0].message.content or "").strip()
        keys = _key_order_from_raw(raw)
        try:
            d = json.loads(raw)
            a, b, c = d.get("a"), d.get("b"), d.get("c")
            this_order = keys[:3] == ["a", "b", "c"]
            this_dep = (b == a + 10) and (c == b * 2) if all(isinstance(x, int) for x in (a, b, c)) else False
        except Exception:
            a = b = c = None
            this_order = this_dep = False
        order_ok += this_order
        dep_ok += this_dep
        if i < 3:
            samples.append({"raw": raw, "keys": keys, "order_ok": this_order, "dep_ok": this_dep})
    return {"name": name, "order_ok": order_ok, "dep_ok": dep_ok, "samples": samples}


async def main() -> None:
    print(f"model={settings.deepseek_model}  temp={TEMPERATURE}  N={N}/变体\n")
    for name, prompt in PROMPTS.items():
        res = await run_variant(name, prompt)
        print(f"===== 变体 [{name}] =====")
        print(f"  键顺序为 a,b,c : {res['order_ok']}/{N}")
        print(f"  依赖满足(b=a+10,c=b*2): {res['dep_ok']}/{N}")
        for s in res["samples"]:
            print(f"    样本 keys={s['keys']} order={s['order_ok']} dep={s['dep_ok']}  raw={s['raw'][:80]}")
        print()


if __name__ == "__main__":
    asyncio.run(main())
