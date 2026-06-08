"""Unit tests for src.persona_polish.polish_persona.

All HTTP calls are mocked via pytest monkeypatch so no network traffic is made.
"""
from __future__ import annotations

import json
import pytest
import httpx

from src.persona_polish import polish_persona, _MIN_LENGTH


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_response(content: str, status_code: int = 200) -> httpx.Response:
    """Build a minimal fake httpx.Response that looks like an OpenAI chat response."""
    body = {
        "choices": [
            {
                "message": {
                    "role": "assistant",
                    "content": content,
                }
            }
        ]
    }
    return httpx.Response(
        status_code=status_code,
        content=json.dumps(body).encode(),
        headers={"Content-Type": "application/json"},
    )


# ---------------------------------------------------------------------------
# Happy-path: polish succeeds
# ---------------------------------------------------------------------------

def test_polish_returns_polished_text(monkeypatch):
    polished = "You are Billie, a sharp and witty assistant who never gives up."
    assert len(polished) > _MIN_LENGTH

    def fake_post(url, *, headers, json, timeout):  # noqa: A002
        return _make_response(polished)

    monkeypatch.setattr("src.persona_polish.httpx.post", fake_post)

    result = polish_persona(
        soul_content="You are Billie.\n\n**Personality:** sharp and witty.",
        gateway_port=8642,
        gateway_key="test-key",
    )
    assert result == polished


def test_polish_strips_whitespace_from_response(monkeypatch):
    polished = "You are Billie, a sharp and witty assistant."
    padded = f"\n\n  {polished}  \n"

    def fake_post(url, *, headers, json, timeout):  # noqa: A002
        return _make_response(padded)

    monkeypatch.setattr("src.persona_polish.httpx.post", fake_post)

    result = polish_persona("You are Billie.", gateway_port=8642, gateway_key="k")
    assert result == polished


# ---------------------------------------------------------------------------
# Fallback: various failure modes must return the original soul_content
# ---------------------------------------------------------------------------

def test_polish_falls_back_on_non_200(monkeypatch):
    original = "You are Billie.\n\n**Personality:** sharp."

    def fake_post(url, *, headers, json, timeout):  # noqa: A002
        return _make_response("error body", status_code=503)

    monkeypatch.setattr("src.persona_polish.httpx.post", fake_post)

    result = polish_persona(original, gateway_port=8642, gateway_key="k")
    assert result == original


def test_polish_falls_back_on_network_exception(monkeypatch):
    original = "You are Billie."

    def fake_post(url, *, headers, json, timeout):  # noqa: A002
        raise httpx.ConnectError("refused")

    monkeypatch.setattr("src.persona_polish.httpx.post", fake_post)

    result = polish_persona(original, gateway_port=8642, gateway_key="k")
    assert result == original


def test_polish_falls_back_on_timeout(monkeypatch):
    original = "You are Billie."

    def fake_post(url, *, headers, json, timeout):  # noqa: A002
        raise httpx.TimeoutException("timed out")

    monkeypatch.setattr("src.persona_polish.httpx.post", fake_post)

    result = polish_persona(original, gateway_port=8642, gateway_key="k")
    assert result == original


def test_polish_falls_back_on_malformed_json(monkeypatch):
    original = "You are Billie."

    def fake_post(url, *, headers, json, timeout):  # noqa: A002
        return httpx.Response(
            status_code=200,
            content=b"not-valid-json",
            headers={"Content-Type": "application/json"},
        )

    monkeypatch.setattr("src.persona_polish.httpx.post", fake_post)

    result = polish_persona(original, gateway_port=8642, gateway_key="k")
    assert result == original


def test_polish_falls_back_on_missing_choices_key(monkeypatch):
    original = "You are Billie."

    def fake_post(url, *, headers, json, timeout):  # noqa: A002
        return httpx.Response(
            status_code=200,
            content=b'{"result": "oops"}',
            headers={"Content-Type": "application/json"},
        )

    monkeypatch.setattr("src.persona_polish.httpx.post", fake_post)

    result = polish_persona(original, gateway_port=8642, gateway_key="k")
    assert result == original


def test_polish_falls_back_when_response_too_short(monkeypatch):
    original = "You are Billie.\n\n**Personality:** sharp."
    short = "ok"
    assert len(short) <= _MIN_LENGTH

    def fake_post(url, *, headers, json, timeout):  # noqa: A002
        return _make_response(short)

    monkeypatch.setattr("src.persona_polish.httpx.post", fake_post)

    result = polish_persona(original, gateway_port=8642, gateway_key="k")
    assert result == original


# ---------------------------------------------------------------------------
# Empty gateway key → skip polish entirely (no HTTP call made)
# ---------------------------------------------------------------------------

def test_empty_gateway_key_skips_polish(monkeypatch):
    original = "You are Billie."
    call_count = []

    def fake_post(url, *, headers, json, timeout):  # noqa: A002
        call_count.append(1)
        return _make_response("You are Billie, polished.")

    monkeypatch.setattr("src.persona_polish.httpx.post", fake_post)

    result = polish_persona(original, gateway_port=8642, gateway_key="")
    assert result == original
    assert call_count == [], "HTTP should not have been called with an empty key"


# ---------------------------------------------------------------------------
# Integration-level: set_identity endpoint uses polished content
# ---------------------------------------------------------------------------

def test_set_identity_uses_polished_soul(monkeypatch, fake_env):
    """POST /v1/agents/{id}/identity writes the polished text when polish succeeds."""
    import json as _json
    from fastapi.testclient import TestClient

    polished = "You are Billie, a sharp and witty assistant who never gives up."

    def fake_post(url, *, headers, json, timeout):  # noqa: A002
        return _make_response(polished)

    monkeypatch.setattr("src.persona_polish.httpx.post", fake_post)
    monkeypatch.setenv("HERMES_GATEWAY_KEY", "test-key")

    # Seed the default agent
    default_entry = _json.dumps([{
        "id": "default",
        "name": "Ollie",
        "gatewayUrl": "http://host.docker.internal:8642",
        "dashboardUrl": "http://host.docker.internal:9100",
        "color": "#6366f1",
        "model": "claude-sonnet-4-6",
    }])
    env_path = fake_env["stack"] / ".env"
    env_path.write_text(f"AGENTS_JSON={default_entry}\n")

    hermes_home = fake_env["hermes_home"]
    (hermes_home / "SOUL.md").write_text("<!-- OLLIE-SOUL-DEFAULT -->\n# stub")

    from src.api.main import create_app
    client = TestClient(create_app())

    body = {"displayName": "Billie", "soulContent": "You are Billie.\n\n**Personality:** sharp."}
    r = client.post("/v1/agents/default/identity", json=body,
                    headers={"Authorization": "Bearer topsecret"})
    assert r.status_code == 200
    soul_path = fake_env["hermes_home"] / "SOUL.md"
    assert soul_path.read_text() == polished


def test_set_identity_falls_back_to_template_on_polish_failure(monkeypatch, fake_env):
    """POST /v1/agents/{id}/identity writes the original template when polish fails."""
    import json as _json
    from fastapi.testclient import TestClient

    def fake_post(url, *, headers, json, timeout):  # noqa: A002
        raise httpx.ConnectError("gateway down")

    monkeypatch.setattr("src.persona_polish.httpx.post", fake_post)
    monkeypatch.setenv("HERMES_GATEWAY_KEY", "test-key")

    default_entry = _json.dumps([{
        "id": "default",
        "name": "Ollie",
        "gatewayUrl": "http://host.docker.internal:8642",
        "dashboardUrl": "http://host.docker.internal:9100",
        "color": "#6366f1",
        "model": "claude-sonnet-4-6",
    }])
    env_path = fake_env["stack"] / ".env"
    env_path.write_text(f"AGENTS_JSON={default_entry}\n")

    hermes_home = fake_env["hermes_home"]
    (hermes_home / "SOUL.md").write_text("<!-- OLLIE-SOUL-DEFAULT -->\n# stub")

    from src.api.main import create_app
    client = TestClient(create_app())

    template = "You are Billie.\n\n**Personality:** sharp and witty."
    body = {"displayName": "Billie", "soulContent": template}
    r = client.post("/v1/agents/default/identity", json=body,
                    headers={"Authorization": "Bearer topsecret"})
    assert r.status_code == 200
    soul_path = fake_env["hermes_home"] / "SOUL.md"
    assert soul_path.read_text() == template


def test_set_identity_no_gateway_key_writes_template_verbatim(monkeypatch, fake_env):
    """When HERMES_GATEWAY_KEY is empty, the template is written as-is."""
    import json as _json
    from fastapi.testclient import TestClient

    call_count = []

    def fake_post(url, *, headers, json, timeout):  # noqa: A002
        call_count.append(1)
        return _make_response("You are Billie, polished.")

    monkeypatch.setattr("src.persona_polish.httpx.post", fake_post)

    default_entry = _json.dumps([{
        "id": "default",
        "name": "Ollie",
        "gatewayUrl": "http://host.docker.internal:8642",
        "dashboardUrl": "http://host.docker.internal:9100",
        "color": "#6366f1",
        "model": "claude-sonnet-4-6",
    }])
    env_path = fake_env["stack"] / ".env"
    # Note: HERMES_GATEWAY_KEY is empty string
    env_path.write_text(f"HERMES_GATEWAY_KEY=\nAGENTS_JSON={default_entry}\n")

    hermes_home = fake_env["hermes_home"]
    (hermes_home / "SOUL.md").write_text("<!-- OLLIE-SOUL-DEFAULT -->\n# stub")

    monkeypatch.setenv("HERMES_GATEWAY_KEY", "")
    from src.api.main import create_app
    client = TestClient(create_app())

    template = "You are Billie."
    body = {"displayName": "Billie", "soulContent": template}
    r = client.post("/v1/agents/default/identity", json=body,
                    headers={"Authorization": "Bearer topsecret"})
    assert r.status_code == 200
    soul_path = fake_env["hermes_home"] / "SOUL.md"
    assert soul_path.read_text() == template
    assert call_count == [], "HTTP should not be called when gateway key is empty"
