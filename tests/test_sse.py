import json
import pytest
from src.sse import sse_event, sse_stream


def test_sse_event_formats_data_only():
    line = sse_event(event="progress", data={"step": "validate", "ok": True})
    assert line.startswith("event: progress\n")
    assert "data: " in line
    assert line.endswith("\n\n")


async def _gen():
    yield {"step": "a"}
    yield {"step": "b"}


@pytest.mark.asyncio
async def test_sse_stream_emits_each_event():
    out = []
    async for chunk in sse_stream("progress", _gen()):
        out.append(chunk)
    assert len(out) == 2
    payloads = [json.loads(chunk.split("data: ", 1)[1].strip()) for chunk in out]
    assert payloads == [{"step": "a"}, {"step": "b"}]
