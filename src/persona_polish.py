"""Optional persona polish step: rewrite a raw soul template into fluent prose
via the agent's local Hermes gateway (OpenAI-compatible chat endpoint).

``polish_persona`` is designed to be *always safe*:
  - Returns the original ``soul_content`` verbatim on any failure.
  - Never raises.
"""
from __future__ import annotations

import json
import logging

import httpx

_logger = logging.getLogger(__name__)

_SYSTEM_PROMPT = (
    "Rewrite the following agent persona as natural, concise second-person prose "
    "that begins 'You are <name>,'. "
    "Keep EVERY fact (personality, mission, communication style, hard rules). "
    "Return ONLY the persona as Markdown, with no preamble, no code fences."
)

_MIN_LENGTH = 20  # chars — sanity check that the LLM actually returned something useful


def polish_persona(
    soul_content: str,
    gateway_port: int,
    gateway_key: str,
    timeout: float = 25.0,
) -> str:
    """Call the gateway's chat-completions endpoint to polish *soul_content*.

    Returns the polished text if the call succeeds and the result is at least
    ``_MIN_LENGTH`` characters.  Falls back to *soul_content* (unchanged) on
    any exception, non-200 response, or an unusably short result.
    """
    if not gateway_key:
        return soul_content

    url = f"http://127.0.0.1:{gateway_port}/v1/chat/completions"
    headers = {
        "Authorization": f"Bearer {gateway_key}",
        "Content-Type": "application/json",
    }
    payload = {
        "messages": [
            {
                "role": "user",
                "content": f"{_SYSTEM_PROMPT}\n\n{soul_content}",
            }
        ],
        "stream": False,
    }

    try:
        resp = httpx.post(url, headers=headers, json=payload, timeout=timeout)
        if resp.status_code != 200:
            _logger.warning(
                "polish_persona: gateway returned %s — using template", resp.status_code
            )
            return soul_content

        data = resp.json()
        text: str = data["choices"][0]["message"]["content"]
        text = text.strip()
        if len(text) > _MIN_LENGTH:
            return text

        _logger.warning(
            "polish_persona: response too short (%d chars) — using template", len(text)
        )
        return soul_content

    except Exception:  # noqa: BLE001
        _logger.warning("polish_persona: failed — using template", exc_info=True)
        return soul_content
