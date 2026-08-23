"""Snapshot preservation tests."""

import json
from pathlib import Path

from backend.acquisition.base import PageSnapshot
from backend.acquisition.fixture import FixtureAcquisition
from backend.config import FIXTURE_DIR


def test_snapshot_written_to_disk(tmp_path):
    acq = FixtureAcquisition(FIXTURE_DIR / "pages", version="v1_healthy", snapshot_dir=tmp_path)
    snap = acq.fetch("http://127.0.0.1:8765/list?page=1")
    assert snap.snapshot_path is not None
    html_file = Path(snap.snapshot_path)
    assert html_file.exists()
    assert html_file.suffix == ".html"
    # sidecar json
    meta = html_file.with_suffix(".json")
    assert meta.exists()
    data = json.loads(meta.read_text())
    assert data["provider"] == "fixture"
    assert data["bytes"] == len(snap.html)
    assert data["content_sha"] == snap.content_sha()


def test_snapshot_disabled_by_default(tmp_path):
    acq = FixtureAcquisition(FIXTURE_DIR / "pages", version="v1_healthy")
    snap = acq.fetch("http://127.0.0.1:8765/list?page=1")
    assert snap.snapshot_path is None
    assert acq.snapshot_dir is None


def test_snapshot_enable_after_construction(tmp_path):
    acq = FixtureAcquisition(FIXTURE_DIR / "pages", version="v1_healthy")
    acq.enable_snapshots(tmp_path / "snaps")
    snap = acq.fetch("http://127.0.0.1:8765/list?page=1")
    assert snap.snapshot_path is not None
    assert (tmp_path / "snaps").exists()


def test_snapshot_partitioned_by_host(tmp_path):
    acq = FixtureAcquisition(FIXTURE_DIR / "pages", version="v1_healthy", snapshot_dir=tmp_path)
    acq.fetch("http://127.0.0.1:8765/list?page=1")
    hosts = [p.name for p in tmp_path.iterdir() if p.is_dir()]
    assert "127.0.0.1_8765" in hosts


def test_page_snapshot_content_sha_is_stable():
    a = PageSnapshot(url="u", html="hello", status_code=200, provider="test")
    b = PageSnapshot(url="u", html="hello", status_code=200, provider="test")
    assert a.content_sha() == b.content_sha()
    c = PageSnapshot(url="u", html="different", status_code=200, provider="test")
    assert a.content_sha() != c.content_sha()
