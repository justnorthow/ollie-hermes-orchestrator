"""Agent-access authorization (Phase 2a)."""
import types
import pytest
from fastapi import Request

import src.api.authz as authz
import src.api.roles as roles


def _req(user_id: str | None):
    headers = [(b"x-auth-user-id", user_id.encode())] if user_id else []
    scope = {"type": "http", "headers": headers}
    return Request(scope)


class _Entry:
    def __init__(self, id, scope, manager_visible=False):
        self.id = id
        self.scope = scope
        self.manager_visible = manager_visible


@pytest.fixture
def cfg(monkeypatch):
    c = types.SimpleNamespace(instance_id="sandbox", hermes_stack_dir=None)
    monkeypatch.setattr(authz, "read_agents", lambda _p: [
        _Entry("default", "user"),
        _Entry("pam", "company", manager_visible=False),
        _Entry("mkt", "company", manager_visible=True),
    ])
    # read_agents is called with cfg.hermes_stack_dir/'.env'; tolerate None path.
    monkeypatch.setattr(authz, "_env_path", lambda _cfg: "IGNORED")
    return c


def test_can_reach_matrix():
    assert authz.can_reach("member", "user", False) is True
    assert authz.can_reach("member", "company", False) is False
    assert authz.can_reach("manager", "company", False) is False
    assert authz.can_reach("manager", "company", True) is True
    assert authz.can_reach("account_admin", "company", False) is True
    assert authz.can_reach("platform_operator", "company", False) is True


def test_check_access_member_denied_company(cfg, monkeypatch):
    monkeypatch.setattr(roles, "resolve_tier", lambda i, u: "member")
    r = authz.check_agent_access(_req("u1"), "pam", cfg)
    assert r is not None and r.status_code == 403


def test_check_access_member_allowed_user_agent(cfg, monkeypatch):
    monkeypatch.setattr(roles, "resolve_tier", lambda i, u: "member")
    assert authz.check_agent_access(_req("u1"), "default", cfg) is None


def test_check_access_identity_less_allowed(cfg):
    assert authz.check_agent_access(_req(None), "pam", cfg) is None


def test_check_access_unknown_agent_denied(cfg, monkeypatch):
    monkeypatch.setattr(roles, "resolve_tier", lambda i, u: "account_admin")
    r = authz.check_agent_access(_req("u1"), "nope", cfg)
    assert r is not None and r.status_code == 403


def test_reachable_ids_member(cfg, monkeypatch):
    monkeypatch.setattr(roles, "resolve_tier", lambda i, u: "member")
    assert authz.reachable_agent_ids(_req("u1"), cfg) == ["default"]


def test_reachable_ids_manager(cfg, monkeypatch):
    monkeypatch.setattr(roles, "resolve_tier", lambda i, u: "manager")
    assert set(authz.reachable_agent_ids(_req("u1"), cfg)) == {"default", "mkt"}
