"""End-to-end deployment tests: register → run → repair → promote → rollback."""

import json
import os
import tempfile
from pathlib import Path

import pytest

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures"


@pytest.fixture
def tmp_db(tmp_path, monkeypatch):
    """Redirect the DB to a temporary file for each test."""
    db_file = tmp_path / "webmedic.sqlite"
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{db_file}")
    # Reload modules that captured settings at import time.
    import importlib

    import backend.config as cfg
    import backend.database.store as store
    import backend.versioning.deploy as deploy

    importlib.reload(cfg)
    importlib.reload(store)
    importlib.reload(deploy)
    store.reset_database()
    return deploy, store


def _fetcher_from(version: str):
    """Return a callable that maps our fake URL to a fixture file."""

    def fetch(url: str) -> str:
        # url is like "http://127.0.0.1:8765/list?page=1"
        page = 1
        if "page=" in url:
            page = int(url.split("page=")[-1].split("&")[0])
        return (FIXTURES / "pages" / version / f"page-{page}.html").read_text()

    return fetch


def _golden(n: int = 10):
    return json.load(open(FIXTURES / "golden_dataset.json"))["records"][:n]


def test_register_creates_v1(tmp_db):
    deploy, store = tmp_db
    sid = deploy.register_scraper("metrokart", "http://127.0.0.1:8765/list?page=1")
    assert sid > 0
    scraper = store.get_scraper(sid)
    assert scraper["current_version"] == 1
    versions = store.list_versions(sid)
    assert len(versions) == 1


def test_run_healthy_fixture_no_change(tmp_db):
    deploy, store = tmp_db
    sid = deploy.register_scraper("metrokart", "http://127.0.0.1:8765/list?page=1")
    hr, decision = deploy.run_once(
        sid,
        url="http://127.0.0.1:8765/list?page=1",
        fetch=_fetcher_from("v1_healthy"),
        expected=10,
        golden=_golden(10),
    )
    assert hr.status == "healthy"
    assert decision.action == "no_change"


def test_run_broken_v3_triggers_promote_and_recovers(tmp_db):
    deploy, store = tmp_db
    sid = deploy.register_scraper("metrokart", "http://127.0.0.1:8765/list?page=1")
    hr, decision = deploy.run_once(
        sid,
        url="http://127.0.0.1:8765/list?page=1",
        fetch=_fetcher_from("v3_dataattr"),
        expected=10,
        golden=_golden(10),
    )
    assert hr.status == "repair_required"
    assert decision.action == "promote"
    assert decision.new_version == 2
    assert decision.post_health >= 0.95

    # After the repair, running again on the same fixture should be healthy.
    hr2, dec2 = deploy.run_once(
        sid,
        url="http://127.0.0.1:8765/list?page=1",
        fetch=_fetcher_from("v3_dataattr"),
        expected=10,
        golden=_golden(10),
    )
    assert hr2.status == "healthy"
    assert dec2.action == "no_change"
    # And v2 is the active version.
    assert store.get_scraper(sid)["current_version"] == 2


def test_rollback_returns_to_previous_version(tmp_db):
    deploy, store = tmp_db
    sid = deploy.register_scraper("metrokart", "http://127.0.0.1:8765/list?page=1")
    deploy.run_once(
        sid,
        url="http://127.0.0.1:8765/list?page=1",
        fetch=_fetcher_from("v3_dataattr"),
        expected=10,
        golden=_golden(10),
    )
    assert store.get_scraper(sid)["current_version"] == 2

    deploy.rollback_to(sid, to_version=1, reason="regression suspected")
    assert store.get_scraper(sid)["current_version"] == 1

    # Rollback recorded in repair_events
    events = store.list_repair_events(sid)
    assert any(e["action"] == "rollback" for e in events)


def test_repair_events_record_candidate_and_confidence(tmp_db):
    deploy, store = tmp_db
    sid = deploy.register_scraper("metrokart", "http://127.0.0.1:8765/list?page=1")
    deploy.run_once(
        sid,
        url="http://127.0.0.1:8765/list?page=1",
        fetch=_fetcher_from("v2_rename_class"),
        expected=10,
        golden=_golden(10),
    )
    events = store.list_repair_events(sid)
    promoted = [e for e in events if e["action"] == "promote"]
    assert promoted, events
    assert promoted[0]["confidence"] >= 0.90
