"""Bright Data acquisition adapter.

Uses Bright Data's Web Unlocker / Scraper Studio proxy — you provide credentials
via env vars (`BRIGHTDATA_USERNAME`, `BRIGHTDATA_PASSWORD`, optionally
`BRIGHTDATA_HOST`, `BRIGHTDATA_PORT`, `BRIGHTDATA_ZONE`). When credentials are
absent, `available()` returns False so the app falls back to fixtures without
crashing.

The adapter routes through httpx (session/connection pooling + cookie jar) with
an HTTPS proxy and returns the fetched HTML plus useful metadata (status,
headers) as evidence.

Anti-block defaults (per web-scraping best practice — cf. WebScraperKnowledge
YT-1 "three bosses" and YT-3 sessions + UA rotation):
  * A rotating pool of realistic User-Agent strings (one per fetch).
  * Configurable per-request rate limiting via `request_delay` (default 0.5s).
    Set to 0.0 in tests where determinism is more important than politeness.

Docs — Bright Data Web Unlocker uses HTTP(S) proxy authentication:
  proxy URL = http://{username}:{password}@{host}:{port}
where `username` typically encodes the zone as `brd-customer-<customer_id>-zone-<zone>`.
This module doesn't construct that string automatically; it accepts whatever
`BRIGHTDATA_USERNAME` you pass and uses it as-is.
"""

from __future__ import annotations

import itertools
import logging
import random
import time
from typing import Optional, Sequence

import httpx

from backend.config import settings

from .base import Acquisition, PageSnapshot, ProviderUnavailable


DEFAULT_USER_AGENTS: Sequence[str] = (
    # A small rotating pool of realistic modern browser UAs. Real deployments
    # should pull from a bigger, regularly-refreshed list.
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 "
    "(KHTML, like Gecko) Version/17.4 Safari/605.1.15",
    "Mozilla/5.0 (X11; Linux x86_64; rv:126.0) Gecko/20100101 Firefox/126.0",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:126.0) Gecko/20100101 Firefox/126.0",
    "Mozilla/5.0 (iPhone; CPU iPhone OS 17_4 like Mac OS X) AppleWebKit/605.1.15 "
    "(KHTML, like Gecko) Version/17.4 Mobile/15E148 Safari/604.1",
)


log = logging.getLogger(__name__)


class BrightDataAcquisition(Acquisition):
    provider_name = "brightdata"

    def __init__(
        self,
        username: Optional[str] = None,
        password: Optional[str] = None,
        host: Optional[str] = None,
        port: Optional[int] = None,
        zone: Optional[str] = None,
        timeout: float = 30.0,
        snapshot_dir=None,
        request_delay: float = 0.5,
        user_agents: Optional[Sequence[str]] = None,
    ):
        super().__init__(snapshot_dir=snapshot_dir)
        self.username = username or settings.brightdata_username
        self.password = password or settings.brightdata_password
        self.host = host or settings.brightdata_host
        self.port = port or settings.brightdata_port
        self.zone = zone or settings.brightdata_zone
        self.timeout = timeout
        self.request_delay = max(0.0, float(request_delay))
        self._ua_pool: Sequence[str] = tuple(user_agents) if user_agents else DEFAULT_USER_AGENTS
        # Rotating UA cycle — deterministic order for the test suite, but the
        # starting index is randomized once at construction so successive
        # process runs use different starting UAs.
        self._ua_cycle = itertools.cycle(random.sample(list(self._ua_pool), len(self._ua_pool)))
        self._last_fetch_at: float = 0.0

    def available(self) -> bool:
        return bool(self.username and self.password and self.host and self.port)

    def _proxy_url(self) -> str:
        return f"http://{self.username}:{self.password}@{self.host}:{self.port}"

    def _next_ua(self) -> str:
        return next(self._ua_cycle)

    def _rate_limit(self) -> None:
        """Sleep to enforce at least `request_delay` between successive fetches."""
        if self.request_delay <= 0:
            return
        elapsed = time.time() - self._last_fetch_at
        wait = self.request_delay - elapsed
        if wait > 0:
            log.debug("brightdata: rate limit sleeping %.3fs", wait)
            time.sleep(wait)

    def fetch(self, url: str) -> PageSnapshot:
        if not self.available():
            log.warning("brightdata: credentials missing; refusing to fetch %s", url)
            raise ProviderUnavailable(
                "Bright Data credentials are not configured — set "
                "BRIGHTDATA_USERNAME and BRIGHTDATA_PASSWORD, or use "
                "ACQUISITION_PROVIDER=fixture."
            )
        self._rate_limit()
        proxy = self._proxy_url()
        ua = self._next_ua()
        headers = {
            "User-Agent": ua,
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "en-US,en;q=0.9",
            "Accept-Encoding": "gzip, deflate, br",
        }
        log.info(
            "brightdata: GET %s via %s:%s (zone=%s ua=%s...)",
            url, self.host, self.port, self.zone or "-", ua.split()[0],
        )
        with httpx.Client(
            proxy=proxy,
            timeout=self.timeout,
            follow_redirects=True,
            verify=False,  # Bright Data proxy uses its own TLS chain
            headers=headers,
        ) as client:
            resp = client.get(url)
            resp.raise_for_status()
        self._last_fetch_at = time.time()
        snap = PageSnapshot(
            url=url,
            html=resp.text,
            status_code=resp.status_code,
            provider=self.provider_name,
            provider_meta={
                "final_url": str(resp.url),
                "elapsed_ms": int(resp.elapsed.total_seconds() * 1000),
                "content_type": resp.headers.get("content-type", ""),
                "zone": self.zone or "",
                "user_agent": ua,
            },
        )
        log.info(
            "brightdata: %s → %d (%d bytes in %s ms)",
            url, resp.status_code, len(resp.text),
            snap.provider_meta["elapsed_ms"],
        )
        self._persist_snapshot(snap)
        return snap
