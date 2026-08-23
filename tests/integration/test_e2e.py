"""Full end-to-end story tests.

These exist so a hackathon judge can run `pytest tests/integration/test_e2e.py`
and see the entire "healthy → break → detect → repair → recover → rollback"
narrative pass in under a second.
"""

import importlib
from pathlib import Path

import pytest


FIXTURES = Path(__file__).resolve().parents[1] / "fixtures"


@pytest.fixture
def env(tmp_path, monkeypatch):
    db = tmp_path / "webmedic.sqlite"
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{db}")
    for name in [
        "backend.config",
        "backend.database.store",
        "backend.versioning.deploy",
    ]:
        importlib.reload(importlib.import_module(name))
    from backend.acquisition.fixture import FixtureAcquisition
    from backend.config import FIXTURE_DIR
    from backend.database import store
    from backend.versioning import deploy
    return {
        "acq": FixtureAcquisition(FIXTURE_DIR / "pages", version="v1_healthy"),
        "store": store,
        "deploy": deploy,
        "golden": __import__("json").load(open(FIXTURE_DIR / "golden_dataset.json"))["records"],
    }


def test_full_selfheal_story(env):
    deploy = env["deploy"]
    store = env["store"]
    acq = env["acq"]
    golden = env["golden"][:10]

    sid = deploy.register_scraper("metrokart", "http://127.0.0.1:8765/list?page=1")

    # 1) Healthy run
    hr, dec = deploy.run_once(sid, "http://127.0.0.1:8765/list?page=1", fetch=acq, expected=10, golden=golden)
    assert hr.status == "healthy" and dec.action == "no_change"

    # 2) Simulate site change to a heavily broken layout
    acq.switch_version("v3_dataattr")

    # 3) Broken run should trigger promote
    hr, dec = deploy.run_once(sid, "http://127.0.0.1:8765/list?page=1", fetch=acq, expected=10, golden=golden)
    assert hr.status == "repair_required"
    assert dec.action == "promote"
    assert dec.new_version == 2

    # 4) The next run on the new version should be healthy
    hr, dec = deploy.run_once(sid, "http://127.0.0.1:8765/list?page=1", fetch=acq, expected=10, golden=golden)
    assert hr.status == "healthy"
    assert dec.action == "no_change"

    # 5) Rollback works and is recorded
    deploy.rollback_to(sid, to_version=1, reason="e2e demo")
    assert store.get_scraper(sid)["current_version"] == 1
    events = store.list_repair_events(sid)
    assert any(e["action"] == "rollback" for e in events)


def test_multiple_layout_changes_in_sequence(env):
    """Ensure the healer copes when the site changes twice back-to-back."""
    deploy = env["deploy"]
    store = env["store"]
    acq = env["acq"]
    golden = env["golden"][:10]

    sid = deploy.register_scraper("metrokart", "http://127.0.0.1:8765/list?page=1")

    versions_walked = ["v1_healthy", "v2_rename_class", "v3_dataattr", "v9_combined", "v1_healthy"]
    for v in versions_walked:
        acq.switch_version(v)
        deploy.run_once(sid, "http://127.0.0.1:8765/list?page=1", fetch=acq, expected=10, golden=golden)

    scraper = store.get_scraper(sid)
    # At least one repair should have been promoted; scraper stays runnable.
    assert scraper["current_version"] >= 1
    events = store.list_repair_events(sid)
    assert any(ev["action"] == "promote" for ev in events)


def test_demo_cli_runs_without_error(tmp_path, monkeypatch, capsys):
    """The scripted demo used at hackathon time must run cleanly."""
    db = tmp_path / "webmedic.sqlite"
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{db}")
    for name in [
        "backend.config",
        "backend.database.store",
        "backend.versioning.deploy",
        "backend.api.demo_cli",
    ]:
        importlib.reload(importlib.import_module(name))
    from backend.api import demo_cli
    demo_cli.run_demo(break_to="v3_dataattr")
    captured = capsys.readouterr().out
    assert "Registering MetroKart" in captured
    assert "Step 1" in captured
    assert "Step 5" in captured
    assert "Rolling back" in captured
