"""Deterministic per-record validation rules (guide §7.2).

Each rule takes a record dict and returns a list of `RuleViolation`s. A record
is *valid* iff every rule returns an empty list. Rules are pure functions so
they can be composed, tested, and reused by the repair scorer.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Callable
from urllib.parse import urlparse

from backend.scraper.schema import REQUIRED_FIELDS, FIELD_TYPES


@dataclass(frozen=True)
class RuleViolation:
    field: str
    code: str
    message: str


Rule = Callable[[dict], list[RuleViolation]]


# --------------------------------------------------------------------------- #
# Field-level rules
# --------------------------------------------------------------------------- #


def required_fields_present(record: dict) -> list[RuleViolation]:
    return [
        RuleViolation(f, "missing", f"{f} is required but missing")
        for f in REQUIRED_FIELDS
        if record.get(f) in (None, "")
    ]


def types_match(record: dict) -> list[RuleViolation]:
    out: list[RuleViolation] = []
    for f, ts in FIELD_TYPES.items():
        v = record.get(f)
        if v is None:
            continue
        if not isinstance(v, ts):
            out.append(
                RuleViolation(f, "wrong_type", f"{f} must be {ts}, got {type(v).__name__}")
            )
    return out


def price_plausible(record: dict) -> list[RuleViolation]:
    price = record.get("price")
    if price is None or not isinstance(price, (int, float)) or isinstance(price, bool):
        # Non-numeric price is caught by `types_match`; don't double-report.
        return []
    if price < 0:
        return [RuleViolation("price", "negative", "price is negative")]
    if price > 10_000_000:
        return [RuleViolation("price", "implausibly_high", f"price {price} exceeds sanity cap")]
    return []


def rating_in_range(record: dict) -> list[RuleViolation]:
    r = record.get("rating")
    if r is None or not isinstance(r, (int, float)) or isinstance(r, bool):
        return []
    if r < 0 or r > 5:
        return [RuleViolation("rating", "out_of_range", f"rating {r} outside [0, 5]")]
    return []


def review_count_non_negative(record: dict) -> list[RuleViolation]:
    rc = record.get("review_count")
    if rc is None or not isinstance(rc, int) or isinstance(rc, bool):
        return []
    if rc < 0:
        return [RuleViolation("review_count", "negative", "review_count is negative")]
    return []


_URL_RE = re.compile(r"^https?://")


def urls_valid(record: dict) -> list[RuleViolation]:
    out: list[RuleViolation] = []
    for f in ("product_url", "image_url"):
        v = record.get(f)
        if v is None:
            continue
        try:
            parsed = urlparse(v)
        except (TypeError, ValueError):
            out.append(RuleViolation(f, "invalid_url", f"{f} is not a URL"))
            continue
        if not parsed.scheme or not parsed.netloc:
            out.append(RuleViolation(f, "invalid_url", f"{f}={v!r} missing scheme/host"))
    return out


_UI_LABEL_BLACKLIST = {"add to cart", "buy now", "wishlist", "compare", "n/a"}
_PLACEHOLDER_TOKENS = {
    "n/a", "na", "null", "undefined", "unknown", "tbd", "todo", "placeholder",
    "sample", "example", "test", "lorem ipsum", "xxx", "???",
}
_HTML_LEAK_RE = re.compile(r"<[a-z/][a-z0-9]*[^>]*>", re.IGNORECASE)


def name_not_ui_label(record: dict) -> list[RuleViolation]:
    name = (record.get("product_name") or "").strip()
    if not name:
        return []
    if name.lower() in _UI_LABEL_BLACKLIST:
        return [
            RuleViolation(
                "product_name", "looks_like_ui", f"product_name={name!r} looks like a UI label"
            )
        ]
    if len(name) < 2:
        return [RuleViolation("product_name", "too_short", "product_name too short")]
    return []


def name_not_placeholder(record: dict) -> list[RuleViolation]:
    """Catch obvious placeholder text ("N/A", "undefined", "TBD", "sample", …)."""
    name = (record.get("product_name") or "").strip().lower()
    if not name:
        return []
    if name in _PLACEHOLDER_TOKENS:
        return [
            RuleViolation(
                "product_name", "placeholder_text", f"product_name={name!r} looks like placeholder text"
            )
        ]
    return []


def price_not_placeholder(record: dict) -> list[RuleViolation]:
    """Reject prices that are almost certainly template defaults (0.00, 0.01, 9999999)."""
    price = record.get("price")
    if price is None or not isinstance(price, (int, float)) or isinstance(price, bool):
        return []
    if price == 0.0:
        return [RuleViolation("price", "placeholder_zero", "price is exactly 0.00 — likely template default")]
    if price == 0.01:
        return [RuleViolation("price", "placeholder_penny", "price is 0.01 — likely stub value")]
    if price >= 9_999_999:
        return [
            RuleViolation("price", "placeholder_sentinel", f"price {price} looks like a placeholder sentinel")
        ]
    return []


def no_html_leakage(record: dict) -> list[RuleViolation]:
    """Any string field containing an HTML tag means the extractor is grabbing markup."""
    out: list[RuleViolation] = []
    for f, v in record.items():
        if isinstance(v, str) and _HTML_LEAK_RE.search(v):
            out.append(RuleViolation(f, "html_leak", f"{f}={v[:40]!r} contains raw HTML"))
    return out


def currency_iso_shape(record: dict) -> list[RuleViolation]:
    cur = record.get("currency")
    if cur is None:
        return []
    if not (isinstance(cur, str) and len(cur) == 3 and cur.isalpha() and cur.isupper()):
        return [
            RuleViolation(
                "currency", "not_iso4217", f"currency={cur!r} is not a 3-letter ISO code"
            )
        ]
    return []


ALL_RULES: tuple[Rule, ...] = (
    required_fields_present,
    types_match,
    price_plausible,
    price_not_placeholder,
    rating_in_range,
    review_count_non_negative,
    urls_valid,
    name_not_ui_label,
    name_not_placeholder,
    no_html_leakage,
    currency_iso_shape,
)


def validate_record(record: dict, rules: tuple[Rule, ...] = ALL_RULES) -> list[RuleViolation]:
    out: list[RuleViolation] = []
    for rule in rules:
        out.extend(rule(record))
    return out
