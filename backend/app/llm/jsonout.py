"""容错解析 LLM 返回的 JSON(子步二)。

OpenAI 兼容 provider 走 response_format={"type":"json_object"},本就是干净 JSON,直解即过。
Claude 无 response_format —— JSON 靠 prompt 强约束保证,但偶发会裹 ```json 围栏或带前后说明,
这里兜底:剥围栏、截取最外层 {...} 再解。仍失败则抛原生 JSONDecodeError,交调用方包装报错。
"""

import json
import re

_FENCE = re.compile(r"^```(?:json)?\s*(.*?)\s*```$", re.DOTALL)


def loads_lenient(raw: str) -> dict:
    s = (raw or "").strip()
    try:
        return json.loads(s)
    except json.JSONDecodeError:
        pass

    m = _FENCE.match(s)
    if m:
        inner = m.group(1).strip()
        try:
            return json.loads(inner)
        except json.JSONDecodeError:
            s = inner  # 围栏剥掉后继续尝试截取花括号

    start, end = s.find("{"), s.rfind("}")
    if start != -1 and end != -1 and end > start:
        return json.loads(s[start : end + 1])

    return json.loads(s)  # 触发原生 JSONDecodeError,交调用方包装
