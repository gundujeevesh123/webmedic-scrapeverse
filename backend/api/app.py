"""FastAPI application: control API + dashboard.

Endpoints (JSON unless noted):

    GET  /                       → dashboard HTML (Tailwind CDN, vanilla JS)
    GET  /api/health             → liveness probe
    GET  /api/scrapers           → list all scrapers
    POST /api/scrapers           → register a new scraper (defaults to MetroKart)
    GET  /api/scrapers/{id}      → scraper detail + versions + latest runs + events
    POST /api/scrapers/{id}/run  → fetch → extract → validate → maybe heal
    POST /api/scrapers/{id}/rollback  → roll back to a prior version
    POST /api/scrapers/{id}/switch_fixture → hot-swap the fixture layout served
                                             (demo control; fixture provider only)
    GET  /api/scrapers/{id}/runs
    GET  /api/scrapers/{id}/repair-events
    GET  /api/scrapers/{id}/preview  → last known records (for the dashboard)
"""

from __future__ import annotations

import json
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, JSONResponse
from pydantic import BaseModel

from backend.acquisition.factory import make_acquisition
from backend.acquisition.fixture import FixtureAcquisition
from backend.config import FIXTURE_DIR, settings
from backend.database import store
from backend.scraper.extract import extract
from backend.scraper.strategy import DEFAULT_STRATEGY
from backend.versioning import deploy

REPO_ROOT = Path(__file__).resolve().parents[2]
FRONTEND_DIR = REPO_ROOT / "frontend"
with (FIXTURE_DIR / "golden_dataset.json").open(encoding="utf-8") as _fh:
    GOLDEN = json.load(_fh)["records"]


app = FastAPI(title="WebMedic — Self-Healing Web Scraper", version="0.1.0")


# Per-scraper acquisition instances so the dashboard can flip fixture versions.
_ACQ: dict[int, FixtureAcquisition] = {}


def _acquisition_for(scraper_id: int) -> FixtureAcquisition:
    if scraper_id not in _ACQ:
        acq = make_acquisition(fixture_version="v1_healthy")
        # For dashboard "switch fixture" support we need a FixtureAcquisition.
        if not isinstance(acq, FixtureAcquisition):
            acq = FixtureAcquisition(FIXTURE_DIR / "pages", version="v1_healthy")
        _ACQ[scraper_id] = acq
    return _ACQ[scraper_id]


# --------------------------------------------------------------------------- #
# Request bodies
# --------------------------------------------------------------------------- #


class RegisterBody(BaseModel):
    name: str = "metrokart"
    target_url: str = "http://127.0.0.1:8765/list?page=1"


class RunBody(BaseModel):
    url: str | None = None
    fixture_version: str | None = None       # override the served layout
    expected: int | None = 20


class RollbackBody(BaseModel):
    to_version: int
    reason: str = "manual rollback"


class SwitchFixtureBody(BaseModel):
    version: str


# --------------------------------------------------------------------------- #
# Dashboard
# --------------------------------------------------------------------------- #


@app.get("/", include_in_schema=False)
def dashboard():
    index = FRONTEND_DIR / "index.html"
    if not index.exists():
        return JSONResponse({"error": "frontend not built"}, status_code=500)
    return FileResponse(str(index))


# --------------------------------------------------------------------------- #
# Health check
# --------------------------------------------------------------------------- #


@app.get("/api/health")
def api_health():
    store.init_db()
    return {
        "status": "ok",
        "acquisition_provider": settings.acquisition_provider,
        "brightdata_configured": bool(settings.brightdata_username and settings.brightdata_password),
        "scrapers": len(store.list_scrapers()),
    }


# --------------------------------------------------------------------------- #
# Scrapers
# --------------------------------------------------------------------------- #


@app.get("/api/scrapers")
def list_scrapers():
    store.init_db()
    return store.list_scrapers()


@app.post("/api/scrapers")
def register(body: RegisterBody):
    sid = deploy.register_scraper(body.name, body.target_url, DEFAULT_STRATEGY)
    return {"id": sid, **store.get_scraper(sid)}


@app.get("/api/scrapers/{scraper_id}")
def scraper_detail(scraper_id: int):
    scraper = store.get_scraper(scraper_id)
    if not scraper:
        raise HTTPException(404, "unknown scraper")
    return {
        "scraper": scraper,
        "versions": store.list_versions(scraper_id),
        "runs": store.list_runs(scraper_id, limit=25),
        "repair_events": store.list_repair_events(scraper_id, limit=25),
        "acquisition": {
            "provider": _acquisition_for(scraper_id).provider_name,
            "current_fixture": _acquisition_for(scraper_id).version,
        },
    }


@app.post("/api/scrapers/{scraper_id}/run")
def run_scraper(scraper_id: int, body: RunBody):
    scraper = store.get_scraper(scraper_id)
    if not scraper:
        raise HTTPException(404, "unknown scraper")
    acq = _acquisition_for(scraper_id)
    if body.fixture_version:
        acq.switch_version(body.fixture_version)
    url = body.url or scraper["target_url"]
    hr, decision = deploy.run_once(
        scraper_id=scraper_id,
        url=url,
        fetch=acq,
        expected=body.expected or 20,
        golden=GOLDEN[: body.expected or 20],
    )
    return {
        "health": hr.to_dict(),
        "decision": decision.__dict__,
        "acquisition": acq.provider_name,
        "fixture_version": acq.version,
    }


@app.post("/api/scrapers/{scraper_id}/rollback")
def rollback(scraper_id: int, body: RollbackBody):
    scraper = store.get_scraper(scraper_id)
    if not scraper:
        raise HTTPException(404, "unknown scraper")
    dec = deploy.rollback_to(scraper_id, body.to_version, body.reason)
    return dec.__dict__


@app.post("/api/scrapers/{scraper_id}/switch_fixture")
def switch_fixture(scraper_id: int, body: SwitchFixtureBody):
    acq = _acquisition_for(scraper_id)
    acq.switch_version(body.version)
    return {"switched_to": body.version}


@app.get("/api/scrapers/{scraper_id}/runs")
def scraper_runs(scraper_id: int, limit: int = 50):
    return store.list_runs(scraper_id, limit=limit)


@app.get("/api/scrapers/{scraper_id}/repair-events")
def scraper_repairs(scraper_id: int, limit: int = 50):
    return store.list_repair_events(scraper_id, limit=limit)


@app.get("/api/scrapers/{scraper_id}/repair-events/{event_id}")
def scraper_repair_event_detail(scraper_id: int, event_id: int):
    """Full plan JSON for one repair event: old selector, candidates, winner, scores."""
    with store.connect() as conn:
        row = conn.execute(
            "SELECT id, scraper_id, old_version, new_version, failure_reason, "
            "candidate_count, selected_candidate, confidence, plan, action, timestamp "
            "FROM repair_events WHERE id=? AND scraper_id=?",
            (event_id, scraper_id),
        ).fetchone()
    if not row:
        raise HTTPException(404, "unknown repair event")
    payload = dict(row)
    payload["plan"] = json.loads(payload["plan"]) if payload["plan"] else None
    payload["selected_candidate"] = (
        json.loads(payload["selected_candidate"]) if payload["selected_candidate"] else None
    )
    return payload


@app.get("/api/scrapers/{scraper_id}/preview")
def scraper_preview(scraper_id: int, limit: int = 10):
    """Return the last extraction on the current version + fixture."""
    scraper = store.get_scraper(scraper_id)
    if not scraper:
        raise HTTPException(404, "unknown scraper")
    acq = _acquisition_for(scraper_id)
    strategy = deploy.get_active_strategy(scraper_id)
    snap = acq.fetch(scraper["target_url"])
    result = extract(snap.html, strategy, url=scraper["target_url"])
    return {
        "url": snap.url,
        "provider": snap.provider,
        "fixture_version": acq.version,
        "records": result.records[:limit],
        "count": len(result.records),
    }


@app.get("/api/scrapers/{scraper_id}/versions/{version}")
def scraper_version(scraper_id: int, version: int):
    strategy = store.get_version(scraper_id, version)
    if strategy is None:
        raise HTTPException(404, "unknown version")
    return strategy.to_dict()


@app.get("/api/fixtures")
def list_fixtures():
    return sorted(p.name for p in (FIXTURE_DIR / "pages").iterdir() if p.is_dir())
