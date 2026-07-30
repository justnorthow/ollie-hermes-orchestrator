"""The plugin's local refusal vocabulary.

The plugin used to emit a bare `orchestrator_unreachable` string literal defined
nowhere — an eighth reason in a runbook that says "Every non-grant response
carries a reason" and lists seven. It was also the catch-all for 401/403/404/5xx
from the orchestrator AND client-side timeouts AND connect failures, so a
rotated key, a wrong ORCHESTRATOR_URL and a slow consult all produced the same
undiagnostic string.
"""
import json

import httpx
import pytest

from plugins.dispatch import reasons
from plugins.dispatch.provider import DispatchProvider

URL = "http://127.0.0.1:9123/v1/dispatch/consult"


@pytest.fixture
def provider(monkeypatch):
    monkeypatch.setenv("DISPATCH_MODE", "direct")
    monkeypatch.setenv("DISPATCH_AGENT_ID", "billie")
    p = DispatchProvider()
    p.initialize("sess-1")
    return p


def _status_error(code):
    request = httpx.Request("POST", URL)
    return httpx.HTTPStatusError(
        f"Client error '{code}' for url '{URL}'",
        request=request,
        response=httpx.Response(code, request=request),
    )


def _ask(provider, monkeypatch, exc):
    def boom(path, payload):
        raise exc

    monkeypatch.setattr(provider._client, "post", boom)
    return json.loads(provider.handle_tool_call(
        "ask_teammate", {"teammate": "karl-m", "question": "q"}))


def test_the_plugin_does_not_import_from_src():
    """It runs on a Hermes box where the orchestrator package is not
    importable, so an import from src/ would be an ImportError at plugin load
    — i.e. no dispatch tools at all, on every agent."""
    import pathlib

    root = pathlib.Path(__file__).resolve().parents[1] / "plugins" / "dispatch"
    for path in root.glob("*.py"):
        source = path.read_text(encoding="utf-8")
        assert "from src." not in source, path
        assert "import src" not in source, path


def test_auth_failure_is_its_own_reason(provider, monkeypatch):
    body = _ask(provider, monkeypatch, _status_error(401))

    assert body["ok"] is False
    assert body["reason"] == reasons.ORCHESTRATOR_AUTH_FAILED
    assert "ORCHESTRATOR_KEY" in body["detail"]


def test_a_403_is_also_an_auth_failure(provider, monkeypatch):
    assert _ask(provider, monkeypatch, _status_error(403))["reason"] == \
        reasons.ORCHESTRATOR_AUTH_FAILED


def test_other_http_statuses_are_distinguished_from_auth(provider, monkeypatch):
    for code in (404, 500, 503):
        body = _ask(provider, monkeypatch, _status_error(code))
        assert body["reason"] == reasons.ORCHESTRATOR_ERROR
        assert str(code) in body["detail"]


def test_a_client_timeout_is_its_own_reason(provider, monkeypatch):
    body = _ask(provider, monkeypatch,
                httpx.ReadTimeout("timed out", request=httpx.Request("POST", URL)))

    assert body["reason"] == reasons.ORCHESTRATOR_TIMEOUT


def test_a_connect_failure_is_its_own_reason(provider, monkeypatch):
    body = _ask(provider, monkeypatch,
                httpx.ConnectError("nope", request=httpx.Request("POST", URL)))

    assert body["reason"] == reasons.ORCHESTRATOR_UNREACHABLE
    assert "ORCHESTRATOR_URL" in body["detail"]


def test_the_orchestrator_url_is_never_put_in_front_of_the_model(provider, monkeypatch):
    """str(exc) on an httpx error embeds the full request URL. That text goes
    straight into a tool result the model is about to read and may quote."""
    for exc in (_status_error(401), _status_error(500),
                httpx.ConnectError("nope", request=httpx.Request("POST", URL)),
                httpx.ReadTimeout("t", request=httpx.Request("POST", URL))):
        body = _ask(provider, monkeypatch, exc)
        assert "127.0.0.1" not in str(body)
        assert "9123" not in str(body)


def test_the_reasons_are_distinct_and_disjoint_from_the_servers():
    """An operator reading a log must be able to tell which side refused."""
    local = {v for k, v in vars(reasons).items()
             if k.isupper() and isinstance(v, str)}
    assert len(local) == 4

    from src.dispatch import types

    server = {v for k, v in vars(types).items() if k.startswith("REASON_")}
    assert local & server == set()


def test_list_teammates_uses_the_same_vocabulary(provider, monkeypatch):
    def boom(path, params):
        raise _status_error(401)

    monkeypatch.setattr(provider._client, "get", boom)
    body = json.loads(provider.handle_tool_call("list_teammates", {}))

    assert body["reason"] == reasons.ORCHESTRATOR_AUTH_FAILED
