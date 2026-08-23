"""Dashboard-data fidelity tests.

The dashboard is a static HTML file plus JS that consumes the API. We can't
run the browser, but we can prove:

  1. Every displayed number has a source endpoint that returns exactly that
     number (health score, record count, version, confidence, action).
  2. The repair-event drill-down endpoint carries every field the UI renders
     (old selector, top candidates, winner, scores, action, reason).
  3. The frontend JS references those exact keys — a grep-based contract test
     so if we rename an API field we notice.
"""

from __future__ import annotations

import importlib
import json
import re
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

FRONTEND = Path(__file__).resolve().parents[2] / "frontend" / "index.html"


@pytest.fixture
def client(tmp_path, monkeypatch):
    db = tmp_path / "webmedic.sqlite"
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{db}")
    for name in [
        "backend.config",
        "backend.database.store",
        "backend.versioning.deploy",
        "backend.api.app",
    ]:
        importlib.reload(importlib.import_module(name))
    from backend.api.app import app
    return TestClient(app)


def _register_and_heal(client):
    """Bootstrap one scraper, force a broken run so we have a repair event."""
    resp = client.post("/api/scrapers", json={"name": "metrokart", "target_url": "http://127.0.0.1:8765/list?page=1"})
    sid = resp.json()["id"]
    client.post(f"/api/scrapers/{sid}/switch_fixture", json={"version": "v3_dataattr"})
    run = client.post(f"/api/scrapers/{sid}/run", json={"expected": 10}).json()
    return sid, run


# ---- displayed numbers match backend ---------------------------------------


def test_overview_card_numbers_match_scraper_row(client):
    sid, _ = _register_and_heal(client)
    scrapers = client.get("/api/scrapers").json()
    detail = client.get(f"/api/scrapers/{sid}").json()["scraper"]
    row = next(s for s in scrapers if s["id"] == sid)
    assert row["current_version"] == detail["current_version"]
    assert row["health_score"] == detail["health_score"]
    assert row["status"] == detail["status"]


def test_last_run_stats_reflect_stored_health_report(client):
    sid, run = _register_and_heal(client)
    runs = client.get(f"/api/scrapers/{sid}/runs").json()
    # The latest listed run must match what /run just returned.
    latest = runs[0]
    assert latest["records_received"] == run["health"]["records_received"]
    assert latest["health_score"] == run["health"]["health_score"]
    assert latest["status"] == run["health"]["status"]


def test_repair_event_summary_matches_detail(client):
    sid, _ = _register_and_heal(client)
    events = client.get(f"/api/scrapers/{sid}/repair-events").json()
    assert events, "expected one repair event after v3_dataattr run"
    ev = events[0]
    detail = client.get(f"/api/scrapers/{sid}/repair-events/{ev['id']}").json()
    for k in ("action", "old_version", "new_version", "confidence", "candidate_count"):
        assert ev[k] == detail[k], k
    assert detail["plan"] is not None
    assert detail["plan"]["strategy_name"] == "metrokart-v1"


def test_repair_event_detail_carries_ui_required_fields(client):
    """Fields the dashboard renders inside the expanded card — every one must be present."""
    sid, _ = _register_and_heal(client)
    ev_id = client.get(f"/api/scrapers/{sid}/repair-events").json()[0]["id"]
    detail = client.get(f"/api/scrapers/{sid}/repair-events/{ev_id}").json()
    plan = detail["plan"]
    for field, fr in plan["field_repairs"].items():
        # Old selector — this is the "was:" line
        assert "old_selector" in fr, field
        assert fr["old_selector"] is not None, field
        assert "kind" in fr["old_selector"] and "value" in fr["old_selector"]
        # Winner + top candidates
        assert fr["winner"] is not None, field
        for c in fr["top_candidates"]:
            assert "selector" in c and "source" in c and "score" in c
            for score_key in ("schema_validity", "completeness", "type_validity",
                              "similarity", "historical_consistency", "total"):
                assert score_key in c["score"], (field, score_key)
        # Gate action + reason
        assert fr["action"] in ("promote", "shadow", "reject", "no_change")
        assert isinstance(fr["reason"], str)


def test_active_strategy_endpoint_returns_current_selectors(client):
    sid, _ = _register_and_heal(client)
    scraper = client.get(f"/api/scrapers/{sid}").json()["scraper"]
    strat = client.get(f"/api/scrapers/{sid}/versions/{scraper['current_version']}").json()
    assert strat["record_selector"] == "article.product-card"
    assert "price" in strat["fields"]


def test_preview_records_use_active_strategy(client):
    """After heal, preview must reflect records extracted by the CURRENT version."""
    sid, _ = _register_and_heal(client)
    preview = client.get(f"/api/scrapers/{sid}/preview?limit=3").json()
    # The active strategy is now the healed one, so preview should have prices.
    assert preview["count"] > 0
    prices = [r.get("price") for r in preview["records"]]
    assert any(p is not None for p in prices), preview


def test_missing_repair_event_returns_404(client):
    sid, _ = _register_and_heal(client)
    r = client.get(f"/api/scrapers/{sid}/repair-events/9999")
    assert r.status_code == 404


# ---- frontend HTML references the exact API fields it consumes -------------


FRONTEND_TEXT = FRONTEND.read_text() if FRONTEND.exists() else ""


@pytest.mark.parametrize(
    "reference",
    [
        # Data points the dashboard renders — grep-based contract with API
        "current_version",
        "health_score",
        "records_received",
        "records_expected",
        "confidence",
        "old_version",
        "new_version",
        "old_selector",
        "top_candidates",
        "winner",
        "failure_reason",
        "action",
        "field_repairs",
        "selected_candidate",
        "post_health",
        "pre_health",
    ],
)
def test_frontend_references_api_field(reference):
    assert FRONTEND_TEXT, "frontend/index.html not readable"
    assert reference in FRONTEND_TEXT, (
        f"dashboard should reference API field {reference!r} but does not — "
        "either the frontend is missing this data or the field name drifted."
    )


def test_frontend_has_narrative_and_drill_down_hooks():
    """Redesigned dashboard replaces the fixed story panel with a live activity
    feed and a stepped guided walkthrough. Verify the key hooks exist."""
    for hook in (
        "activityFeed",         # live event stream (replaces storyPanel)
        "pushActivity",         # activity-feed emitter
        "renderRepairEventDetail",  # per-event candidate drill-down
        "data-eventid",         # clickable repair-event cards
        "stepCta",              # guided-walkthrough call-to-action
        "renderStep",           # step-by-step renderer
    ):
        assert hook in FRONTEND_TEXT, f"dashboard is missing hook: {hook}"
