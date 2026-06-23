import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from src.api.market_data import router as md_router, build_response
from src.auth import require_bearer

URL = "https://core.supabase.co"
KEY = "service-role-key"

ROW = {
    "region_type": "county", "region_key": "williamson",
    "region_label": "Williamson County, TX",
    "period_end": "2026-05-31", "as_of": "2026-06-01T00:00:00Z",
    "median_sale_price": 389000, "median_sale_price_yoy": -0.023,
    "homes_sold": 1204, "inventory": 2700, "months_of_supply": 4.8, "median_dom": 58,
}
RATE = {"rate30yr": "6.71%", "rateMovement": "down from 6.94%"}


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setenv("SUPABASE_URL", URL)
    monkeypatch.setenv("SUPABASE_SERVICE_ROLE_KEY", KEY)
    monkeypatch.setenv("FRED_API_KEY", "fred-key")
    app = FastAPI()
    app.include_router(md_router)
    app.dependency_overrides[require_bearer] = lambda: None
    return TestClient(app)


def test_build_response_maps_and_formats():
    out = build_response("county", "Williamson", ROW, RATE)
    assert out["month"] == "May 2026"
    assert out["medianSoldPrice"] == "$389,000, down 2.3% from a year ago"
    assert out["inventoryMonths"] == "4.8 months"
    assert out["daysOnMarket"] == "58 days"
    assert out["salesVolume"] == "1,204 closed sales"
    assert out["rate30yr"] == "6.71%"
    assert out["rateMovement"] == "down from 6.94%"
    assert out["unavailable"] == []
    assert any("Redfin" in s for s in out["sources"])
    assert any("FRED" in s for s in out["sources"])
    assert out["warning"] is None


def test_build_response_region_miss_flags_local_fields():
    out = build_response("zip", "00000", None, RATE)
    assert out["medianSoldPrice"] == ""
    assert "medianSoldPrice" in out["unavailable"]
    assert out["rate30yr"] == "6.71%"          # rate still present
    assert out["warning"] and "00000" in out["warning"]


def test_endpoint_returns_mapped_payload(client, monkeypatch):
    monkeypatch.setattr("src.api.market_data._fetch_market_row", lambda rt, rk, u, k: ROW)
    monkeypatch.setattr("src.api.market_data._fetch_rate", lambda key: RATE)
    r = client.get("/v1/market-data", params={"type": "county", "value": "Williamson"},
                   headers={"X-Auth-Email": "a@b.com"})
    assert r.status_code == 200
    assert r.json()["medianSoldPrice"].startswith("$389,000")


def test_endpoint_degrades_when_supabase_errors(client, monkeypatch):
    def boom(*a):
        raise RuntimeError("supabase down")
    monkeypatch.setattr("src.api.market_data._fetch_market_row", boom)
    monkeypatch.setattr("src.api.market_data._fetch_rate", lambda key: RATE)
    r = client.get("/v1/market-data", params={"type": "county", "value": "Williamson"},
                   headers={"X-Auth-Email": "a@b.com"})
    assert r.status_code == 200                 # never 5xx: form stays usable
    body = r.json()
    assert "medianSoldPrice" in body["unavailable"]
    assert body["warning"]


def test_400_on_bad_type(client):
    r = client.get("/v1/market-data", params={"type": "state", "value": "TX"},
                   headers={"X-Auth-Email": "a@b.com"})
    assert r.status_code == 400


def test_401_without_email(client):
    r = client.get("/v1/market-data", params={"type": "county", "value": "Williamson"})
    assert r.status_code == 401


def test_503_without_env(monkeypatch):
    monkeypatch.delenv("SUPABASE_URL", raising=False)
    monkeypatch.delenv("SUPABASE_SERVICE_ROLE_KEY", raising=False)
    app = FastAPI()
    app.include_router(md_router)
    app.dependency_overrides[require_bearer] = lambda: None
    r = TestClient(app).get("/v1/market-data", params={"type": "county", "value": "Williamson"},
                            headers={"X-Auth-Email": "a@b.com"})
    assert r.status_code == 503
