"""API integration tests (FastAPI TestClient)."""

import importlib
from pathlib import Path

import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def client(tmp_path, monkeypatch):
    db = tmp_path / "webmedic.sqlite"
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{db}")
    # Reload modules that captured settings at import time
    for name in [
        "backend.config",
        "backend.database.store",
        "backend.versioning.deploy",
        "backend.api.app",
    ]:
        importlib.reload(importlib.import_module(name))
    from backend.api.app import app
    return TestClient(app)


def test_health(client):
    r = client.get("/api/health")
    assert r.status_code == 200
    data = r.json()
    assert data["status"] == "ok"
    assert data["acquisition_provider"] in ("fixture", "brightdata")


def test_register_run_healthy_no_change(client):
    r = client.post(
        "/api/scrapers",
        json={"name": "metrokart", "target_url": "http://127.0.0.1:8765/list?page=1"},
    )
    sid = r.json()["id"]
    r = client.post(f"/api/scrapers/{sid}/run", json={"fixture_version": "v1_healthy", "expected": 10})
    body = r.json()
    assert body["health"]["status"] == "healthy"
    assert body["decision"]["action"] == "no_change"


def test_full_selfheal_cycle(client):
    sid = client.post("/api/scrapers", json={"name": "metrokart", "target_url": "http://127.0.0.1:8765/list?page=1"}).json()["id"]
    # heal on v2_rename_class
    r = client.post(f"/api/scrapers/{sid}/run", json={"fixture_version": "v2_rename_class", "expected": 10})
    body = r.json()
    assert body["health"]["status"] == "repair_required"
    assert body["decision"]["action"] == "promote"
    assert body["decision"]["new_version"] == 2

    detail = client.get(f"/api/scrapers/{sid}").json()
    assert detail["scraper"]["current_version"] == 2
    assert any(ev["action"] == "promote" for ev in detail["repair_events"])


def test_switch_fixture_and_rollback(client):
    sid = client.post("/api/scrapers", json={"name": "metrokart", "target_url": "http://127.0.0.1:8765/list?page=1"}).json()["id"]
    client.post(f"/api/scrapers/{sid}/run", json={"fixture_version": "v3_dataattr", "expected": 10})

    # Rollback to v1
    r = client.post(f"/api/scrapers/{sid}/rollback", json={"to_version": 1, "reason": "test"})
    assert r.status_code == 200
    detail = client.get(f"/api/scrapers/{sid}").json()
    assert detail["scraper"]["current_version"] == 1


def test_list_fixtures_matches_disk(client):
    fixtures = client.get("/api/fixtures").json()
    assert "v1_healthy" in fixtures
    assert "v9_combined" in fixtures
    assert len(fixtures) == 11


def test_dashboard_returns_html(client):
    r = client.get("/")
    assert r.status_code == 200
    assert "WebMedic" in r.text
    # Redesigned dashboard uses "Self-Healing Web Scraper" and "Self-healing web-data platform".
    assert "Self-Healing" in r.text or "Self-healing" in r.text


def test_preview_shows_extracted_records(client):
    sid = client.post("/api/scrapers", json={"name": "metrokart", "target_url": "http://127.0.0.1:8765/list?page=1"}).json()["id"]
    # v3_dataattr baseline extraction returns records where most fields are None
    client.post(f"/api/scrapers/{sid}/switch_fixture", json={"version": "v3_dataattr"})
    preview = client.get(f"/api/scrapers/{sid}/preview?limit=3").json()
    assert preview["count"] > 0
