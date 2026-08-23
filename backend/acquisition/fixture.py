"""Local fixture acquisition — deterministic, no network."""

from __future__ import annotations

import logging
import re
from pathlib import Path
from typing import Optional

from .base import Acquisition, PageSnapshot


log = logging.getLogger(__name__)

PAGE_RE = re.compile(r"(?:[?&]page=|/page-|/list/)(\d+)")


class FixtureAcquisition(Acquisition):
    """Serve HTML from `tests/fixtures/pages/{version}/page-{N}.html`.

    The URL scheme is `fixture://{version}/page-{N}` OR
    `http://127.0.0.1:8765/list?page={N}` (in which case `version` is the
    default configured on this instance).
    """

    provider_name = "fixture"

    def __init__(
        self,
        fixture_root: Path,
        version: str = "v1_healthy",
        snapshot_dir: Optional[Path] = None,
    ):
        super().__init__(snapshot_dir=snapshot_dir)
        self.fixture_root = Path(fixture_root)
        self.version = version

    def _resolve(self, url: str) -> tuple[str, int]:
        version = self.version
        if url.startswith("fixture://"):
            # fixture://v3_dataattr/page-1
            rest = url[len("fixture://") :]
            parts = rest.split("/", 1)
            version = parts[0]
            page_str = parts[1] if len(parts) > 1 else "page-1"
            page = int(page_str.replace("page-", ""))
            return version, page
        m = PAGE_RE.search(url)
        page = int(m.group(1)) if m else 1
        return version, page

    def fetch(self, url: str) -> PageSnapshot:
        version, page = self._resolve(url)
        path = self.fixture_root / version / f"page-{page}.html"
        if not path.exists():
            log.warning("fixture: %s missing", path)
            raise FileNotFoundError(f"fixture missing: {path}")
        html = path.read_text(encoding="utf-8")
        snap = PageSnapshot(
            url=url,
            html=html,
            status_code=200,
            provider=self.provider_name,
            provider_meta={"version": version, "page": page, "path": str(path)},
        )
        log.info(
            "fixture: served %s (version=%s page=%d bytes=%d)",
            url, version, page, len(html),
        )
        self._persist_snapshot(snap)
        return snap

    def switch_version(self, version: str) -> None:
        """Change the layout served on the next fetch — used to simulate a website change."""
        log.info("fixture: switching version %s → %s", self.version, version)
        self.version = version
