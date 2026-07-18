"""Orchestrator compliance/governance/TRAIGA endpoints (service role).

All access goes through the service role; authz is re-enforced here via
_compliance_denied, mirroring the DB's old governance_events RLS + the
frontend RoleRoute OR-gate (compliance tag OR governance_view).
"""
import types
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from src.api import compliance
from src.api import roles
from src.api.compliance import router as compliance_router
from src.auth import require_bearer

HEADERS_ID = {"X-Auth-User-Id": "u1"}


class _Resp:
    def __init__(self, json_data=None, status_code=200):
        self._json = {} if json_data is None else json_data
        self.status_code = status_code

    def raise_for_status(self):
        pass

    def json(self):
        return self._json


@pytest.fixture
def ctx(monkeypatch):
    monkeypatch.setenv("SUPABASE_URL", "http://127.0.0.1:8000")
    monkeypatch.setenv("SUPABASE_SERVICE_ROLE_KEY", "svc-key")
    app = FastAPI()
    app.state.config = types.SimpleNamespace(instance_id="sandbox")
    app.include_router(compliance_router)
    app.dependency_overrides[require_bearer] = lambda: None
    return TestClient(app), monkeypatch


@pytest.fixture
def allowed(ctx):
    """A caller that passes the authz gate via the compliance tag."""
    c, monkeypatch = ctx
    monkeypatch.setattr(roles, "list_user_tags", lambda uid: ["compliance"])
    monkeypatch.setattr(roles, "resolve_governance_view", lambda inst, uid: False)
    return c, monkeypatch


# --- authz gate ---

def test_denied_without_identity(ctx):
    c, _ = ctx
    r = c.get("/v1/governance/events")
    assert r.status_code == 401


def test_denied_non_privileged(ctx):
    c, monkeypatch = ctx
    monkeypatch.setattr(roles, "list_user_tags", lambda uid: [])
    monkeypatch.setattr(roles, "resolve_governance_view", lambda inst, uid: False)
    r = c.get("/v1/governance/events", headers=HEADERS_ID)
    assert r.status_code == 403


def test_allowed_via_compliance_tag(ctx):
    c, monkeypatch = ctx
    monkeypatch.setattr(roles, "list_user_tags", lambda uid: ["compliance"])
    monkeypatch.setattr(roles, "resolve_governance_view", lambda inst, uid: False)
    monkeypatch.setattr(compliance.httpx, "get", lambda *a, **k: _Resp(json_data=[]))
    r = c.get("/v1/governance/events", headers=HEADERS_ID)
    assert r.status_code == 200


def test_allowed_via_governance_view(ctx):
    c, monkeypatch = ctx
    monkeypatch.setattr(roles, "list_user_tags", lambda uid: [])
    monkeypatch.setattr(roles, "resolve_governance_view", lambda inst, uid: True)
    monkeypatch.setattr(compliance.httpx, "get", lambda *a, **k: _Resp(json_data=[]))
    r = c.get("/v1/governance/events", headers=HEADERS_ID)
    assert r.status_code == 200


# --- GET /v1/governance/events ---

def test_governance_events_calls_service_role_and_returns_events(allowed):
    c, monkeypatch = allowed
    calls = []

    def fake_get(url, params=None, headers=None, timeout=None):
        calls.append(dict(url=url, params=params, headers=headers))
        return _Resp(json_data=[{"id": "1"}])

    monkeypatch.setattr(compliance.httpx, "get", fake_get)
    r = c.get("/v1/governance/events", headers=HEADERS_ID)
    assert r.status_code == 200
    assert r.json() == {"events": [{"id": "1"}]}
    assert calls[0]["url"].endswith("/rest/v1/governance_events")
    assert calls[0]["params"]["order"] == "created_at.desc"
    assert calls[0]["params"]["limit"] == "1000"
    assert calls[0]["headers"]["apikey"] == "svc-key"
    assert calls[0]["headers"]["Authorization"] == "Bearer svc-key"


# --- GET /v1/compliance/rules ---

def test_rules_filters_and_capped_true(allowed):
    c, monkeypatch = allowed
    calls = []

    def fake_get(url, params=None, headers=None, timeout=None):
        calls.append(dict(url=url, params=params))
        return _Resp(json_data=[{"rule_key": "x"}] * 1000)

    monkeypatch.setattr(compliance.httpx, "get", fake_get)
    r = c.get("/v1/compliance/rules?status=pending&hub=foo", headers=HEADERS_ID)
    assert r.status_code == 200
    body = r.json()
    assert body["capped"] is True
    assert len(body["rules"]) == 1000
    assert calls[0]["url"].endswith("/rest/v1/compliance_rules")
    assert calls[0]["params"]["status"] == "eq.pending"
    assert calls[0]["params"]["hub"] == "eq.foo"
    assert "confidence" not in calls[0]["params"]
    assert calls[0]["params"]["order"] == "confidence.asc,rule_key.asc"


def test_rules_uncapped_when_under_limit(allowed):
    c, monkeypatch = allowed
    monkeypatch.setattr(compliance.httpx, "get",
                         lambda *a, **k: _Resp(json_data=[{"rule_key": "x"}]))
    r = c.get("/v1/compliance/rules", headers=HEADERS_ID)
    assert r.status_code == 200
    assert r.json()["capped"] is False


# --- GET /v1/compliance/config ---

def test_config_maps_auto_approve(allowed):
    c, monkeypatch = allowed
    monkeypatch.setattr(
        compliance.httpx, "get",
        lambda *a, **k: _Resp(json_data=[{"auto_approve": {"high": True, "medium": False}}]),
    )
    r = c.get("/v1/compliance/config", headers=HEADERS_ID)
    assert r.status_code == 200
    assert r.json() == {"high": True, "medium": False}


def test_config_defaults_when_no_row(allowed):
    c, monkeypatch = allowed
    monkeypatch.setattr(compliance.httpx, "get", lambda *a, **k: _Resp(json_data=[]))
    r = c.get("/v1/compliance/config", headers=HEADERS_ID)
    assert r.status_code == 200
    assert r.json() == {"high": False, "medium": False}


# --- POST /v1/compliance/review ---

def test_review_bad_decision_400(allowed):
    c, _ = allowed
    r = c.post("/v1/compliance/review", json={"ruleKeys": ["a"], "decision": "maybe"},
               headers=HEADERS_ID)
    assert r.status_code == 400


def test_review_empty_rule_keys_no_rpc_call(allowed):
    c, monkeypatch = allowed
    calls = []
    monkeypatch.setattr(compliance.httpx, "post",
                         lambda *a, **k: calls.append(1) or _Resp(json_data=0))
    r = c.post("/v1/compliance/review", json={"ruleKeys": [], "decision": "verified"},
               headers=HEADERS_ID)
    assert r.status_code == 200
    assert r.json() == {"count": 0}
    assert calls == []


def test_review_calls_rpc_with_verified_by(allowed):
    c, monkeypatch = allowed
    calls = []

    def fake_post(url, json=None, headers=None, timeout=None):
        calls.append(dict(url=url, json=json))
        return _Resp(json_data=3)

    monkeypatch.setattr(compliance.httpx, "post", fake_post)
    r = c.post(
        "/v1/compliance/review",
        json={"ruleKeys": ["r1", "r2"], "decision": "verified", "note": "ok"},
        headers={**HEADERS_ID, "X-Auth-Email": "a@b.co"},
    )
    assert r.status_code == 200
    assert r.json() == {"count": 3}
    assert calls[0]["url"].endswith("/rest/v1/rpc/review_rules")
    assert calls[0]["json"] == {
        "p_rule_keys": ["r1", "r2"], "p_decision": "verified",
        "p_note": "ok", "p_verified_by": "a@b.co",
    }


# --- POST /v1/compliance/auto-approve ---

def test_auto_approve_low_tier_400(allowed):
    c, _ = allowed
    r = c.post("/v1/compliance/auto-approve", json={"tier": "low", "enabled": True},
               headers=HEADERS_ID)
    assert r.status_code == 400


def test_auto_approve_high_calls_rpc(allowed):
    c, monkeypatch = allowed
    calls = []

    def fake_post(url, json=None, headers=None, timeout=None):
        calls.append(dict(url=url, json=json))
        return _Resp(json_data=5)

    monkeypatch.setattr(compliance.httpx, "post", fake_post)
    r = c.post(
        "/v1/compliance/auto-approve",
        json={"tier": "high", "enabled": True},
        headers={**HEADERS_ID, "X-Auth-Email": "a@b.co"},
    )
    assert r.status_code == 200
    assert r.json() == {"count": 5}
    assert calls[0]["url"].endswith("/rest/v1/rpc/set_auto_approve")
    assert calls[0]["json"] == {"p_tier": "high", "p_enabled": True, "p_verified_by": "a@b.co"}


# --- GET /v1/traiga/readiness ---

def test_traiga_readiness_missing_params_400(allowed):
    c, _ = allowed
    r = c.get("/v1/traiga/readiness", headers=HEADERS_ID)
    assert r.status_code == 400


def test_traiga_readiness_calls_both_rpcs(allowed):
    c, monkeypatch = allowed
    calls = []

    def fake_post(url, json=None, headers=None, timeout=None):
        calls.append(dict(url=url, json=json))
        if url.endswith("traiga_readiness_counts"):
            return _Resp(json_data=[{"tier": "high", "count": 2}])
        return _Resp(json_data=[{"total": 2, "first_at": "t1", "last_at": "t2"}])

    monkeypatch.setattr(compliance.httpx, "post", fake_post)
    r = c.get("/v1/traiga/readiness?from=2026-01-01&to=2026-02-01", headers=HEADERS_ID)
    assert r.status_code == 200
    body = r.json()
    assert body["counts"] == [{"tier": "high", "count": 2}]
    assert body["window"] == {"total": 2, "first_at": "t1", "last_at": "t2"}
    assert len(calls) == 2
    assert calls[0]["json"] == {"p_from": "2026-01-01", "p_to": "2026-02-01"}
    assert calls[0]["url"].endswith("/rest/v1/rpc/traiga_readiness_counts")
    assert calls[1]["url"].endswith("/rest/v1/rpc/traiga_readiness_window")


def test_traiga_readiness_empty_window_fallback(allowed):
    c, monkeypatch = allowed
    monkeypatch.setattr(compliance.httpx, "post", lambda *a, **k: _Resp(json_data=[]))
    r = c.get("/v1/traiga/readiness?from=2026-01-01&to=2026-02-01", headers=HEADERS_ID)
    assert r.status_code == 200
    assert r.json()["window"] == {"total": 0, "first_at": None, "last_at": None}
