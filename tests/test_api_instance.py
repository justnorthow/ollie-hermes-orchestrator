import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def client(fake_env, monkeypatch):
    import src.api.instance as instance_mod
    calls = []
    monkeypatch.setattr(instance_mod, "bounce_dashboard", lambda: calls.append("bounce"))
    from src.api.main import create_app
    app = create_app()
    c = TestClient(app)
    c.bounce_calls = calls  # type: ignore[attr-defined]
    return c


def _auth():
    return {"Authorization": "Bearer topsecret"}


def _env_text(fake_env):
    return (fake_env["stack"] / ".env").read_text()


def test_set_title_writes_env_and_bounces(client, fake_env):
    r = client.put("/v1/instance/title", json={"title": "JNOW Prod"}, headers=_auth())
    assert r.status_code == 200
    assert r.json() == {"ok": True}
    assert "INSTANCE_TITLE=JNOW Prod\n" in _env_text(fake_env)
    assert client.bounce_calls == ["bounce"]


def test_set_title_trims_and_empty_clears(client, fake_env):
    client.put("/v1/instance/title", json={"title": "  Sandbox  "}, headers=_auth())
    assert "INSTANCE_TITLE=Sandbox\n" in _env_text(fake_env)
    client.put("/v1/instance/title", json={"title": ""}, headers=_auth())
    assert "INSTANCE_TITLE=\n" in _env_text(fake_env)


def test_set_title_rejects_too_long_and_control_chars(client, fake_env):
    r = client.put("/v1/instance/title", json={"title": "x" * 81}, headers=_auth())
    assert r.status_code == 400
    assert r.json()["ok"] is False
    r2 = client.put("/v1/instance/title", json={"title": "a\tb"}, headers=_auth())
    assert r2.status_code == 400
    assert "INSTANCE_TITLE" not in _env_text(fake_env)
    assert client.bounce_calls == []


def test_set_title_requires_admin(client, fake_env, monkeypatch):
    from src.api import authz
    monkeypatch.setattr(authz.roles, "resolve_tier", lambda instance_id, user_id: "member")
    r = client.put("/v1/instance/title", json={"title": "Nope"},
                   headers={**_auth(), "X-Auth-User-Id": "user-123"})
    assert r.status_code == 403
    assert "INSTANCE_TITLE" not in _env_text(fake_env)


def test_set_title_unauthenticated_401(client):
    assert client.put("/v1/instance/title", json={"title": "x"}).status_code == 401


def test_set_title_bounce_failure_swallowed_and_title_persists(client, fake_env, monkeypatch):
    import src.api.instance as instance_mod
    def boom():
        raise RuntimeError("docker down")
    monkeypatch.setattr(instance_mod, "bounce_dashboard", boom)
    # Starlette's TestClient runs BackgroundTasks synchronously as part of
    # the request call, so if _bounce_after_write let the exception escape,
    # this call itself would raise. It must not.
    r = client.put("/v1/instance/title", json={"title": "Durable"}, headers=_auth())
    assert r.status_code == 200
    assert r.json() == {"ok": True}
    assert "INSTANCE_TITLE=Durable\n" in _env_text(fake_env)
