"""Acquisition layer — how WebMedic gets HTML.

Guide §5 Layer 1: obtain the public page or rendered page. We expose one
`Acquisition` interface with two concrete implementations:

  FixtureAcquisition  – deterministic, reads from `tests/fixtures/pages/*`.
                        Used by tests, the CLI demo, and the benchmark.
  BrightDataAcquisition – routes requests through Bright Data's Web Unlocker
                          proxy so JS-rendered / anti-bot pages resolve.
                          Falls back gracefully when credentials are absent.

Every acquisition returns a `PageSnapshot` — raw HTML + provider metadata that
becomes evidence in the repair loop (guide §8.1 "collect evidence").

Optional snapshot preservation: any Acquisition can be given a `snapshot_dir`.
When set, every successful fetch is dumped to `<snapshot_dir>/<host>/<ts>_<sha8>.html`
alongside a JSON sidecar with `PageSnapshot` metadata. This is what powers the
"collect evidence" step of the repair loop — a broken run can always be
re-diagnosed offline from the saved snapshot.
"""

from __future__ import annotations

import hashlib
import json
import logging
import re
import time
from abc import ABC, abstractmethod
from dataclasses import asdict, dataclass, field
from pathlib import Path
from urllib.parse import urlparse

log = logging.getLogger(__name__)

_SAFE_HOST_RE = re.compile(r"[^A-Za-z0-9._-]")


@dataclass
class PageSnapshot:
    url: str
    html: str
    status_code: int
    provider: str                       # "fixture" | "brightdata"
    provider_meta: dict = field(default_factory=dict)
    fetched_at: float = field(default_factory=time.time)
    snapshot_path: str | None = None  # populated when preservation is enabled

    def __len__(self) -> int:
        return len(self.html)

    def content_sha(self) -> str:
        return hashlib.sha256(self.html.encode("utf-8")).hexdigest()[:16]

    def to_meta_dict(self) -> dict:
        """Everything about this snapshot EXCEPT the html body — for the sidecar file."""
        d = asdict(self)
        d.pop("html", None)
        d["content_sha"] = self.content_sha()
        d["bytes"] = len(self.html)
        return d


class Acquisition(ABC):
    provider_name: str = "abstract"

    def __init__(self, snapshot_dir: Path | None = None):
        self.snapshot_dir: Path | None = Path(snapshot_dir) if snapshot_dir else None

    @abstractmethod
    def fetch(self, url: str) -> PageSnapshot: ...

    def __call__(self, url: str) -> str:
        """Adapter so an Acquisition can be used anywhere a `Fetcher` is expected."""
        return self.fetch(url).html

    # ------------------------------------------------------------------ #
    # Snapshot preservation (opt-in evidence trail for the repair loop)
    # ------------------------------------------------------------------ #

    def enable_snapshots(self, snapshot_dir: Path) -> None:
        self.snapshot_dir = Path(snapshot_dir)
        self.snapshot_dir.mkdir(parents=True, exist_ok=True)
        log.info("snapshots: enabled at %s", self.snapshot_dir)

    def _persist_snapshot(self, snap: PageSnapshot) -> None:
        if not self.snapshot_dir:
            return
        try:
            host = _SAFE_HOST_RE.sub("_", urlparse(snap.url).netloc or "unknown") or "unknown"
            host_dir = self.snapshot_dir / host
            host_dir.mkdir(parents=True, exist_ok=True)
            stem = f"{int(snap.fetched_at)}_{snap.content_sha()}"
            html_path = host_dir / f"{stem}.html"
            meta_path = host_dir / f"{stem}.json"
            html_path.write_text(snap.html, encoding="utf-8")
            snap.snapshot_path = str(html_path)
            meta_path.write_text(json.dumps(snap.to_meta_dict(), indent=2), encoding="utf-8")
            log.info(
                "snapshots: wrote %s (%d bytes, provider=%s)",
                html_path, len(snap.html), snap.provider,
            )
        except OSError as exc:
            log.warning("snapshots: failed to persist %s → %s", snap.url, exc)


class ProviderUnavailable(RuntimeError):
    """Raised when a provider is asked to fetch but is not configured.

    Kept inheriting from :class:`RuntimeError` for backward compatibility
    with every existing ``except ProviderUnavailable`` /
    ``except RuntimeError`` site. Also re-exported from
    :mod:`backend.errors` so callers importing the unified error set can
    catch it from one place.
    """

