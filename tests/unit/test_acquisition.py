"""Acquisition adapter tests."""

import pytest

from backend.acquisition.base import PageSnapshot, ProviderUnavailable
from backend.acquisition.brightdata import BrightDataAcquisition
from backend.acquisition.factory import make_acquisition
from backend.acquisition.fixture import FixtureAcquisition
from backend.config import FIXTURE_DIR


def test_fixture_resolves_version_and_page():
    fx = FixtureAcquisition(FIXTURE_DIR / "pages", version="v1_healthy")
    snap = fx.fetch("http://127.0.0.1:8765/list?page=2")
    assert isinstance(snap, PageSnapshot)
    assert snap.status_code == 200
    assert "product-card" in snap.html
    assert snap.provider == "fixture"
    assert snap.provider_meta["page"] == 2


def test_fixture_url_scheme_selects_version():
    fx = FixtureAcquisition(FIXTURE_DIR / "pages", version="v1_healthy")
    snap = fx.fetch("fixture://v3_dataattr/page-1")
    assert 'data-testid="price"' in snap.html


def test_fixture_switch_version_changes_layout():
    fx = FixtureAcquisition(FIXTURE_DIR / "pages", version="v1_healthy")
    fx.switch_version("v2_rename_class")
    snap = fx.fetch("http://127.0.0.1:8765/list?page=1")
    assert 'class="cost"' in snap.html


def test_brightdata_raises_when_credentials_missing(monkeypatch):
    monkeypatch.setenv("BRIGHTDATA_USERNAME", "")
    monkeypatch.setenv("BRIGHTDATA_PASSWORD", "")
    bd = BrightDataAcquisition(username="", password="")
    assert not bd.available()
    with pytest.raises(ProviderUnavailable):
        bd.fetch("http://example.com")


def test_factory_falls_back_when_brightdata_unconfigured(monkeypatch, caplog):
    monkeypatch.setenv("BRIGHTDATA_USERNAME", "")
    monkeypatch.setenv("BRIGHTDATA_PASSWORD", "")
    # Reload config so the new env is visible.
    import importlib

    import backend.config as cfg
    importlib.reload(cfg)
    import backend.acquisition.factory as fac
    importlib.reload(fac)

    with caplog.at_level("WARNING"):
        acq = fac.make_acquisition(provider="brightdata")
    assert isinstance(acq, FixtureAcquisition)
    assert any("Bright Data credentials missing" in rec.message for rec in caplog.records)


def test_factory_returns_brightdata_when_configured(monkeypatch):
    monkeypatch.setenv("BRIGHTDATA_USERNAME", "u")
    monkeypatch.setenv("BRIGHTDATA_PASSWORD", "p")
    import importlib

    import backend.config as cfg
    importlib.reload(cfg)
    # brightdata module captured `settings` at import time — reload after cfg.
    import backend.acquisition.brightdata as bd
    importlib.reload(bd)
    import backend.acquisition.factory as fac
    importlib.reload(fac)

    acq = fac.make_acquisition(provider="brightdata")
    assert acq.provider_name == "brightdata"
