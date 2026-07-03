"""RBAC enforcement on run-proxy + session endpoints (Phase 2a)."""
import json
import types
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

import src.api.runs as runs
import src.api.sessions as sessions_mod
import src.api.authz as authz
from src.api.runs import router as runs_router
from src.api.sessions import router as sessions_router
from src.auth import require_bearer

MEMBER = "mmmmmmmm-0000-0000-0000-000000000001"


@pytest.fixture
def app_with_config(monkeypatch):
    monkeypatch.setenv("HERMES_GATEWAY_URL", "http://gw")
    monkeypatch.setenv("HERMES_GATEWAY_KEY", "k")
    monkeypatch.setenv("HERMES_DASHBOARD_URLS", json.dumps({"pam": "http://127.0.0.1:9121"}))
    app = FastAPI()
    app.state.config = types.SimpleNamespace(instance_id="sandbox", hermes_stack_dir=None)
    app.include_router(runs_router)
    app.include_router(sessions_router)
    app.dependency_overrides[require_bearer] = lambda: None
    # 'pam' is a company agent; force the caller's access check to deny.
    monkeypatch.setattr(authz, "check_agent_access",
                        lambda request, agent, cfg: authz._FORBIDDEN if agent == "pam" else None)
    return TestClient(app)


def test_member_blocked_from_company_agent_run(app_with_config):
    r = app_with_config.post("/v1/runs/pam", content=b'{"input":"hi"}',
                             headers={"X-Auth-User-Id": MEMBER})
    assert r.status_code == 403
    assert r.json() == {"detail": "Forbidden"}


def test_member_blocked_from_company_agent_sessions(app_with_config):
    r = app_with_config.get("/v1/sessions/pam", headers={"X-Auth-User-Id": MEMBER})
    assert r.status_code == 403


def test_allowed_agent_passes_rbac(app_with_config, monkeypatch):
    # 'default' is allowed by the stubbed check; run-proxy proceeds (gateway stubbed)
    monkeypatch.setattr(runs, "_create_run", lambda a, b: (200, b'{"run_id":"r1"}'))
    monkeypatch.setattr(runs, "screen_input", lambda inp, p: {"decision": "allow"})
    r = app_with_config.post("/v1/runs/default", content=b'{"input":"hi"}',
                             headers={"X-Auth-User-Id": MEMBER})
    assert r.status_code == 200
