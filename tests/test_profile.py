import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from src.api.profile import router as profile_router
from src.auth import require_bearer

URL = "https://core.supabase.co"
KEY = "service-role-key"

ROW = {
    "user_id": "u-1", "role": "broker",
    "market_area": [{"type": "county", "value": "Williamson"}],
    "title": "REALTOR", "brokerage": "Acme", "license_number": "TX-1",
    "phone": "555", "email": "a@b.com", "website": "acme.com",
    "headshot_url": "h.jpg", "logo_url": "l.jpg", "display_name": "Jane",
}


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setenv("SUPABASE_URL", URL)
    monkeypatch.setenv("SUPABASE_SERVICE_ROLE_KEY", KEY)
    app = FastAPI()
    app.include_router(profile_router)
    app.dependency_overrides[require_bearer] = lambda: None
    return TestClient(app)


def test_returns_profile_for_injected_email(client, monkeypatch):
    seen = {}
    def fake(email, url, key):
        seen.update(email=email, url=url, key=key)
        return ROW
    monkeypatch.setattr("src.api.profile._fetch_profile_row", fake)
    r = client.get("/v1/profile", headers={"X-Auth-Email": "a@b.com"})
    assert r.status_code == 200
    body = r.json()
    assert body["brokerage"] == "Acme"
    assert body["market_area"] == [{"type": "county", "value": "Williamson"}]
    assert seen == {"email": "a@b.com", "url": URL, "key": KEY}


def test_empty_defaults_when_no_row(client, monkeypatch):
    monkeypatch.setattr("src.api.profile._fetch_profile_row", lambda *a: None)
    r = client.get("/v1/profile", headers={"X-Auth-Email": "x@y.com"})
    assert r.status_code == 200
    body = r.json()
    assert body["market_area"] == []
    assert body["brokerage"] is None
    assert "display_name" in body


def test_401_without_email(client):
    r = client.get("/v1/profile")
    assert r.status_code == 401


def test_503_without_env(monkeypatch):
    monkeypatch.delenv("SUPABASE_URL", raising=False)
    monkeypatch.delenv("SUPABASE_SERVICE_ROLE_KEY", raising=False)
    app = FastAPI()
    app.include_router(profile_router)
    app.dependency_overrides[require_bearer] = lambda: None
    r = TestClient(app).get("/v1/profile", headers={"X-Auth-Email": "a@b.com"})
    assert r.status_code == 503


def test_502_on_rpc_failure(client, monkeypatch):
    def boom(*a):
        raise RuntimeError("supabase down")
    monkeypatch.setattr("src.api.profile._fetch_profile_row", boom)
    r = client.get("/v1/profile", headers={"X-Auth-Email": "a@b.com"})
    assert r.status_code == 502
