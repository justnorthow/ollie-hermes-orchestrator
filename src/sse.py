import json
from typing import AsyncIterator, Any


def sse_event(*, event: str, data: Any) -> str:
    payload = json.dumps(data, separators=(",", ":"))
    return f"event: {event}\ndata: {payload}\n\n"


async def sse_stream(
    event_name: str,
    source: AsyncIterator[dict],
) -> AsyncIterator[str]:
    async for item in source:
        yield sse_event(event=event_name, data=item)
