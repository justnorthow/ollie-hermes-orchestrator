import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def client(fake_env):
    from src.api.main import create_app
    return TestClient(create_app())


def test_models_endpoint_lists_models(client):
    r = client.get("/v1/models", headers={"Authorization": "Bearer topsecret"})
    assert r.status_code == 200
    models = r.json()["models"]
    assert len(models) > 0
    assert all("id" in m and "provider" in m for m in models)


def test_skills_endpoint_lists_skills(client):
    r = client.get("/v1/skills", headers={"Authorization": "Bearer topsecret"})
    assert r.status_code == 200
    assert "skills" in r.json()
