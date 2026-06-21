import base64
import hashlib
import hmac
import json

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from src.api.sso import router as sso_router
from src.auth import require_bearer
from src.sso import mint_sso_token

SECRET = "app-sso-secret"


def _decode(token: str, secret: str = SECRET):
    payload_b64, sig_b64 = token.split(".")
    expected = hmac.new(secret.encode(), payload_b64.encode(), hashlib.sha256).digest()
    got = base64.urlsafe_b64decode(sig_b64 + "===")
    assert hmac.compare_digest(expected, got), "signature mismatch"
    return json.loads(base64.urlsafe_b64decode(payload_b64 + "===").decode())


def test_mint_sso_token_carries_email_and_role():
    payload = _decode(mint_sso_token("u@x.com", "marketing", SECRET))
    assert payload["sub"] == "u@x.com"
    assert payload["role"] == "marketing"
    assert payload["exp"] > payload["iat"]
    assert payload["jti"]


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setenv("HIA_SSO_SECRET", SECRET)
    app = FastAPI()
    app.include_router(sso_router)
    app.dependency_overrides[require_bearer] = lambda: None
    return TestClient(app)


def test_app_token_uses_injected_user(client):
    r = client.get("/v1/sso/app-token",
                   headers={"X-Auth-Email": "real@x.com", "X-Auth-Role": "compliance"})
    assert r.status_code == 200
    payload = _decode(r.json()["token"])
    assert payload["sub"] == "real@x.com"
    assert payload["role"] == "compliance"


def test_app_token_defaults_role_to_agent(client):
    r = client.get("/v1/sso/app-token", headers={"X-Auth-Email": "real@x.com"})
    assert r.status_code == 200
    assert _decode(r.json()["token"])["role"] == "agent"


def test_app_token_401_without_user(client):
    r = client.get("/v1/sso/app-token")
    assert r.status_code == 401


def test_app_token_503_without_secret(monkeypatch):
    monkeypatch.delenv("HIA_SSO_SECRET", raising=False)
    app = FastAPI()
    app.include_router(sso_router)
    app.dependency_overrides[require_bearer] = lambda: None
    r = TestClient(app).get("/v1/sso/app-token", headers={"X-Auth-Email": "a@b.com"})
    assert r.status_code == 503
