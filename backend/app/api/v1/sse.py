"""SSE 事件编码。

事件名与载荷结构见 docs/API-SPEC.md：
    message_start / token / source / tool_status / message_end / error

format_sse 的输出必须是**完整一帧**（以空行结尾），因为前端是按 "\\n\\n"
切分的；少一个换行会让两个事件粘在一起。
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator, Mapping
from typing import Any


def format_sse(event: str, data: Mapping[str, Any]) -> str:
    # ensure_ascii=False：中文直接以 UTF-8 输出，不转成 \uXXXX，便于 curl 直接读。
    payload = json.dumps(data, ensure_ascii=False, default=str)
    return f"event: {event}\ndata: {payload}\n\n"


async def sse_stream(events: AsyncIterator[tuple[str, Mapping[str, Any]]]) -> AsyncIterator[str]:
    """把 (事件名, 载荷) 的异步迭代器转成 SSE 文本流。

    StreamingResponse 每 yield 一次就 flush 一次，所以逐 token 显示是天然的，
    不需要额外做缓冲控制。
    """
    async for event, data in events:
        yield format_sse(event, data)
