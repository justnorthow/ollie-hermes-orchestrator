import json
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from src.api.market_datasets import router as ds_router
from src.api.market_data import router as md_router
from src.auth import require_bearer

URL = "https://core.supabase.co"
KEY = "service-role-key"
UID = "7bde10cf-a74c-41e8-ab30-f2c5ba19f069"

DRAFT = {"label": "Teravista", "period_label": "June 2026", "period_end": "2026-06-30",
         "source_label": "Unlock MLS — June 2026 Market Report",
         "figures": {"medianSoldPrice": "$450,000", "inventoryMonths": "3.1 months",
                     "daysOnMarket": "42 days", "salesVolume": "87 closed sales"},
         "warnings": []}


class _FakeEntry:
    id = "real-estate"
    gateway_port = 8642


@pytest.fixture
def client(monkeypatch, tmp_path):
    monkeypatch.setenv("SUPABASE_URL", URL)
    monkeypatch.setenv("SUPABASE_SERVICE_ROLE_KEY", KEY)
    app = FastAPI()
    app.include_router(ds_router)
    app.include_router(md_router)
    app.dependency_overrides[require_bearer] = lambda: None

    class _Cfg:
        hermes_stack_dir = tmp_path
        instance_id = "sandbox"
    app.state.config = _Cfg()
    app.state.hermes_gateway_key = "gw-key"
    monkeypatch.setattr("src.api.market_datasets._agent_entry",
                        lambda request, agent_id: _FakeEntry())
    return TestClient(app)


AUTH = {"X-Auth-Email": "jb@jnow.io", "X-Auth-User-Id": UID}


def test_parse_happy_path(client, monkeypatch):
    monkeypatch.setattr("src.api.market_datasets.call_gateway_parse",
                        lambda content, port, key: json.dumps(DRAFT))
    r = client.post("/v1/market-datasets/parse?filename=stats.csv",
                    content=b"region,median\nTeravista,450000\n", headers=AUTH)
    assert r.status_code == 200
    assert r.json()["label"] == "Teravista"


def test_parse_retries_once_then_422(client, monkeypatch):
    calls = {"n": 0}
    def bad(content, port, key):
        calls["n"] += 1
        return "sorry, no data"
    monkeypatch.setattr("src.api.market_datasets.call_gateway_parse", bad)
    r = client.post("/v1/market-datasets/parse?filename=stats.csv",
                    content=b"x", headers=AUTH)
    assert r.status_code == 422
    assert calls["n"] == 2


def test_parse_rejects_unsupported_and_oversize(client):
    r = client.post("/v1/market-datasets/parse?filename=doc.docx",
                    content=b"x", headers=AUTH)
    assert r.status_code == 415
    r = client.post("/v1/market-datasets/parse?filename=big.csv",
                    content=b"x" * (10 * 1024 * 1024 + 1), headers=AUTH)
    assert r.status_code == 413


def test_parse_requires_identity(client):
    r = client.post("/v1/market-datasets/parse?filename=a.csv", content=b"x")
    assert r.status_code == 401


def test_save_inserts_confirmed_values(client, monkeypatch):
    captured = {}
    def fake_post(url, **kw):
        captured["url"] = url
        captured["json"] = kw.get("json")
        class R:
            status_code = 201
            def raise_for_status(self): pass
        return R()
    monkeypatch.setattr("src.api.market_datasets.httpx.post", fake_post)
    body = {"label": "Teravista", "period_label": "June 2026",
            "period_end": "2026-06-30", "linked_area": None,
            "figures": DRAFT["figures"], "source_label": DRAFT["source_label"]}
    r = client.post("/v1/market-datasets", json=body, headers=AUTH)
    assert r.status_code == 200
    assert r.json()["ok"] is True
    assert captured["json"]["uploaded_by"] == UID          # identity from header, not body
    assert "/rest/v1/market_datasets" in captured["url"]


def test_list_returns_datasets(client, monkeypatch):
    rows = [{"id": "d1", "label": "Teravista", "period_label": "June 2026"}]
    def fake_get(url, **kw):
        class R:
            status_code = 200
            def raise_for_status(self): pass
            def json(self): return rows
        return R()
    monkeypatch.setattr("src.api.market_datasets.httpx.get", fake_get)
    r = client.get("/v1/market-datasets", headers=AUTH)
    assert r.status_code == 200
    assert r.json()["datasets"][0]["label"] == "Teravista"


def test_delete_forbidden_for_non_uploader_member(client, monkeypatch):
    monkeypatch.setattr("src.api.market_datasets._fetch_uploader",
                        lambda ds_id, url, key: "someone-else")
    monkeypatch.setattr("src.api.market_datasets.resolve_tier",
                        lambda iid, uid: "member")
    r = client.delete("/v1/market-datasets/d1", headers=AUTH)
    assert r.status_code == 403


def test_delete_allowed_for_manager(client, monkeypatch):
    monkeypatch.setattr("src.api.market_datasets._fetch_uploader",
                        lambda ds_id, url, key: "someone-else")
    monkeypatch.setattr("src.api.market_datasets.resolve_tier",
                        lambda iid, uid: "manager")
    deleted = {}
    def fake_delete(url, **kw):
        deleted["url"] = url
        class R:
            status_code = 204
            def raise_for_status(self): pass
        return R()
    monkeypatch.setattr("src.api.market_datasets.httpx.delete", fake_delete)
    r = client.delete("/v1/market-datasets/d1", headers=AUTH)
    assert r.status_code == 200
    assert "market_datasets" in deleted["url"]


def test_rates_endpoint(client, monkeypatch):
    monkeypatch.setenv("FRED_API_KEY", "fred")
    monkeypatch.setattr("src.api.market_data._fetch_rate",
                        lambda key: {"rate30yr": "6.55%", "rateMovement": "up from 6.49%"})
    r = client.get("/v1/rates", headers=AUTH)
    assert r.status_code == 200
    assert r.json() == {"rate30yr": "6.55%", "rateMovement": "up from 6.49%"}
