"""Acquisition factory — pick a provider based on config, with safe fallback."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Optional

from backend.config import FIXTURE_DIR, settings

from .base import Acquisition
from .brightdata import BrightDataAcquisition
from .fixture import FixtureAcquisition


log = logging.getLogger(__name__)


def make_acquisition(
    provider: Optional[str] = None, fixture_version: str = "v1_healthy"
) -> Acquisition:
    """Return an Acquisition instance for the requested provider.

    If `provider` is "brightdata" but credentials are missing, we fall back to
    fixture mode with a clearly-logged warning so the app never crashes on a
    clean clone.
    """
    provider = provider or settings.acquisition_provider
    if provider == "brightdata":
        bd = BrightDataAcquisition()
        if not bd.available():
            log.warning(
                "Bright Data credentials missing — falling back to fixture provider."
            )
            return FixtureAcquisition(FIXTURE_DIR / "pages", version=fixture_version)
        return bd
    return FixtureAcquisition(FIXTURE_DIR / "pages", version=fixture_version)
