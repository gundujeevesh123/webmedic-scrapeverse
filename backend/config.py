"""Central configuration.

Reads environment variables (with sensible defaults) so the system is
reproducible on a fresh clone. Nothing here reaches out to the network.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = REPO_ROOT / "data"
FIXTURE_DIR = REPO_ROOT / "tests" / "fixtures"
GOLDEN_PATH = FIXTURE_DIR / "golden_dataset.json"


@dataclass(frozen=True)
class Settings:
    acquisition_provider: str = os.getenv("ACQUISITION_PROVIDER", "fixture")

    brightdata_zone: str = os.getenv("BRIGHTDATA_ZONE", "")
    brightdata_username: str = os.getenv("BRIGHTDATA_USERNAME", "")
    brightdata_password: str = os.getenv("BRIGHTDATA_PASSWORD", "")
    brightdata_host: str = os.getenv("BRIGHTDATA_HOST", "brd.superproxy.io")
    brightdata_port: int = int(os.getenv("BRIGHTDATA_PORT", "33335"))

    database_url: str = os.getenv(
        "DATABASE_URL", f"sqlite:///{DATA_DIR / 'webmedic.sqlite'}"
    )
    fixture_base_url: str = os.getenv("FIXTURE_BASE_URL", "http://127.0.0.1:8765")

    healthy_threshold: float = float(os.getenv("HEALTH_HEALTHY_THRESHOLD", "0.90"))
    warning_threshold: float = float(os.getenv("HEALTH_WARNING_THRESHOLD", "0.70"))


settings = Settings()

DATA_DIR.mkdir(parents=True, exist_ok=True)
