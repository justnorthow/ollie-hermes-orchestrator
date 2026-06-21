import base64
import json
import time

import jwt
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from src.api.auth_validate import router as auth_validate_router
from src.auth import require_bearer

SECRET = "test-supabase-secret"


def _make_cookie_value(email="a@b.com", role="agent", exp_delta=60, secret=SECRET):
    now = int(time.time())
    access = jwt.encode(
        {"aud": "authenticated", "sub": "u-1", "email": email,
         "user_role": role, "iat": now, "exp": now + exp_delta},
        secret, algorithm="HS256",
    )
    session = json.dumps({"access_token": access, "token_type": "bearer"})
    return "base64-" + base64.b64encode(session.encode()).decode()


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setenv("SUPABASE_JWT_SECRET", SECRET)
    app = FastAPI()
    app.include_router(auth_validate_router)
    app.dependency_overrides[require_bearer] = lambda: None  # bypass bearer for unit tests
    return TestClient(app)


def test_valid_session_returns_email_and_role(client):
    val = _make_cookie_value(email="broker@x.com", role="compliance")
    r = client.get("/v1/auth/validate", cookies={"sb-abcd-auth-token": val})
    assert r.status_code == 200
    assert r.headers["X-Auth-Email"] == "broker@x.com"
    assert r.headers["X-Auth-Role"] == "compliance"


def test_missing_user_role_defaults_to_agent(client):
    now = int(time.time())
    access = jwt.encode({"aud": "authenticated", "email": "a@b.com",
                         "iat": now, "exp": now + 60}, SECRET, algorithm="HS256")
    val = "base64-" + base64.b64encode(
        json.dumps({"access_token": access}).encode()).decode()
    r = client.get("/v1/auth/validate", cookies={"sb-x-auth-token": val})
    assert r.status_code == 200
    assert r.headers["X-Auth-Role"] == "agent"


def test_chunked_cookie_is_reassembled(client):
    val = _make_cookie_value(email="c@x.com")
    mid = len(val) // 2
    r = client.get("/v1/auth/validate",
                   cookies={"sb-x-auth-token.0": val[:mid], "sb-x-auth-token.1": val[mid:]})
    assert r.status_code == 200
    assert r.headers["X-Auth-Email"] == "c@x.com"


def test_expired_token_is_401(client):
    val = _make_cookie_value(exp_delta=-10)
    r = client.get("/v1/auth/validate", cookies={"sb-x-auth-token": val})
    assert r.status_code == 401


def test_wrong_secret_is_401(client):
    val = _make_cookie_value(secret="not-the-secret")
    r = client.get("/v1/auth/validate", cookies={"sb-x-auth-token": val})
    assert r.status_code == 401


def test_no_cookie_is_401(client):
    r = client.get("/v1/auth/validate")
    assert r.status_code == 401


def test_unconfigured_secret_is_503(monkeypatch):
    monkeypatch.delenv("SUPABASE_JWT_SECRET", raising=False)
    app = FastAPI()
    app.include_router(auth_validate_router)
    app.dependency_overrides[require_bearer] = lambda: None
    r = TestClient(app).get("/v1/auth/validate", cookies={"sb-x-auth-token": "x"})
    assert r.status_code == 503
