"""Deduplication (guide §4.9).

    dedupe_key = product_id                          (best — stable identifier)
    dedupe_key = canonical(product_url)              (fallback — canonical URL)
    dedupe_key = sha1(normalized_name + product_url) (last resort)

`dedupe(records)` picks the strongest available key per record, groups
records by that key, and returns one merged record per key — with the
"most complete" record winning ties (fewest None fields; older record wins
on further ties for stability).

Nothing here raises on missing fields: an empty record without any usable
key is dropped with a warning.
"""

from __future__ import annotations

import hashlib
import logging
import re
from dataclasses import dataclass, asdict, field
from typing import Any, Iterable, Optional
from urllib.parse import urlsplit, urlunsplit

from backend.scraper.schema import REQUIRED_FIELDS

log = logging.getLogger(__name__)


_TRACKING_PARAM_RE = re.compile(
    r"^(utm_[a-z]+|gclid|fbclid|mc_[a-z]+|ref|ref_src|source|sessionid|sid|hsCtaTracking)$",
    re.IGNORECASE,
)


def canonical_url(url: Optional[str]) -> Optional[str]:
    """Strip common tracking parameters, sort query, drop fragments.

    Not a full URL canonicalizer — good enough for grouping duplicates on our
    fixtures + typical e-commerce URLs.
    """
    if not url:
        return None
    try:
        parts = urlsplit(url.strip())
    except ValueError:
        return url
    query = parts.query
    if query:
        kept = []
        for pair in query.split("&"):
            k, _, _ = pair.partition("=")
            if k and not _TRACKING_PARAM_RE.match(k):
                kept.append(pair)
        query = "&".join(sorted(kept))
    canonical = urlunsplit((parts.scheme.lower(), parts.netloc.lower(), parts.path, query, ""))
    return canonical.rstrip("/")


def _normalized_name(name: Optional[str]) -> str:
    if not name:
        return ""
    return re.sub(r"\s+", " ", name.strip().lower())


def dedupe_key(record: dict) -> Optional[str]:
    """Pick the strongest available dedupe key for one record."""
    pid = record.get("product_id")
    if pid:
        return f"id:{pid}"
    url = canonical_url(record.get("product_url"))
    if url:
        return f"url:{url}"
    name = _normalized_name(record.get("product_name"))
    if name:
        seed = f"{name}|{record.get('product_url','')}"
        return f"hash:{hashlib.sha1(seed.encode('utf-8')).hexdigest()[:16]}"
    return None


def _completeness_of(record: dict) -> int:
    """Count non-null required fields — used to pick the winner on collisions."""
    return sum(1 for f in REQUIRED_FIELDS if record.get(f) not in (None, ""))


def _merge_preferring(a: dict, b: dict) -> dict:
    """Merge b into a — a wins on non-null fields; b fills a's gaps."""
    out = dict(a)
    for k, v in b.items():
        if out.get(k) in (None, "") and v not in (None, ""):
            out[k] = v
    return out


@dataclass
class DedupeReport:
    input_count: int = 0
    output_count: int = 0
    dropped_no_key: int = 0
    merged_duplicates: int = 0
    keys_by_kind: dict[str, int] = field(default_factory=dict)

    def to_dict(self) -> dict:
        return asdict(self)


def dedupe(records: Iterable[dict]) -> tuple[list[dict], DedupeReport]:
    """Deduplicate records; return `(unique_records, report)`.

    Duplicate resolution: the record with more non-null fields wins; ties
    resolved by keeping the earlier record (list order preserved).
    """
    report = DedupeReport()
    seen: dict[str, dict] = {}
    order: list[str] = []

    for record in records:
        report.input_count += 1
        key = dedupe_key(record)
        if key is None:
            report.dropped_no_key += 1
            log.warning("dedupe: dropping record with no usable key: %r", record)
            continue
        kind = key.split(":", 1)[0]
        report.keys_by_kind[kind] = report.keys_by_kind.get(kind, 0) + 1

        if key not in seen:
            seen[key] = record
            order.append(key)
            continue

        # Collision: pick the more complete record; merge b into a to keep
        # any non-null fields the other record uniquely carries.
        report.merged_duplicates += 1
        existing = seen[key]
        if _completeness_of(record) > _completeness_of(existing):
            seen[key] = _merge_preferring(record, existing)
        else:
            seen[key] = _merge_preferring(existing, record)

    ordered = [seen[k] for k in order]
    report.output_count = len(ordered)
    return ordered, report
