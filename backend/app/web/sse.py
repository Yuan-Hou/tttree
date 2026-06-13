import json
from typing import Any


def sse(payload: dict[str, Any]) -> str:
    """格式化一帧 SSE。事件类型放在 payload['type'],前端按 type 分发。"""
    return f"data: {json.dumps(payload, ensure_ascii=False)}\n\n"


SSE_HEADERS = {
    "Cache-Control": "no-cache",
    "Connection": "keep-alive",
    "X-Accel-Buffering": "no",  # 禁止 nginx 等中间层缓冲,保证逐 token flush
}
