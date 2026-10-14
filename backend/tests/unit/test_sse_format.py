"""SSE 帧格式。前端按 "\\n\\n" 切分，少一个换行就会让事件粘连。"""

from __future__ import annotations

import json

from app.api.v1.sse import format_sse, sse_stream


def test_format_is_exact():
    frame = format_sse("token", {"text": "hi"})
    assert frame == 'event: token\ndata: {"text": "hi"}\n\n'


def test_chinese_is_not_escaped():
    frame = format_sse("token", {"text": "你好"})
    # ensure_ascii=False：中文原样输出，curl 里能直接读
    assert "你好" in frame
    assert "\\u" not in frame


def test_frame_ends_with_blank_line():
    assert format_sse("done", {}).endswith("\n\n")


def test_payload_is_valid_json_after_parsing():
    frame = format_sse("message_end", {"finish_reason": "stop", "usage": None})
    data_line = next(line for line in frame.splitlines() if line.startswith("data:"))
    payload = json.loads(data_line[len("data:") :].strip())
    assert payload == {"finish_reason": "stop", "usage": None}


async def test_sse_stream_concatenates_frames():
    async def events():
        yield "message_start", {"user_message_id": 1}
        yield "token", {"text": "a"}
        yield "token", {"text": "b"}

    frames = [frame async for frame in sse_stream(events())]
    assert len(frames) == 3
    assert all(frame.endswith("\n\n") for frame in frames)
    assert frames[0].startswith("event: message_start\n")
