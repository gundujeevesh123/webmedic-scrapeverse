"""Value normalization.

Websites express the same concept many ways. The normalizer is the *only*
place we tolerate messy input. Everything downstream (validator, scoring,
persistence) operates on normalized values.
"""

from __future__ import annotations

import re
from typing import Any, Optional
from urllib.parse import urljoin


# Ordered by specificity: symbol → ISO code (broad first for our fixtures).
CURRENCY_SYMBOLS: dict[str, str] = {
    "₹": "INR",
    "Rs.": "INR",
    "INR": "INR",
    "$": "USD",
    "US$": "USD",
    "USD": "USD",
    "€": "EUR",
    "EUR": "EUR",
    "£": "GBP",
    "GBP": "GBP",
    "¥": "JPY",
    "JPY": "JPY",
}


AVAILABILITY_MAP: dict[str, str] = {
    "in stock": "In Stock",
    "available": "In Stock",
    "yes": "In Stock",
    "out of stock": "Out of Stock",
    "unavailable": "Out of Stock",
    "no": "Out of Stock",
    "preorder": "Preorder",
    "backorder": "Backorder",
}


_NUMERIC_RE = re.compile(r"[-+]?\d[\d,\.]*")


def _clean(text: Any) -> str:
    if text is None:
        return ""
    if not isinstance(text, str):
        text = str(text)
    return re.sub(r"\s+", " ", text).strip()


def parse_price(raw: Any) -> Optional[float]:
    """Return the first plausible price in `raw` as a float, or None.

    Handles $1,299.00 · 1.299,00 € · ₹8,999.00 · "Buy for ₹ 8,999.00 INR".
    """
    if raw is None:
        return None
    text = _clean(raw)
    if not text:
        return None
    match = _NUMERIC_RE.search(text)
    if not match:
        return None
    n = match.group(0)
    # Detect European format: "1.299,00" -> "1299.00"
    if "," in n and "." in n:
        if n.rfind(",") > n.rfind("."):
            n = n.replace(".", "").replace(",", ".")
        else:
            n = n.replace(",", "")
    elif "," in n:
        # ambiguous: "1,299" (thousands) vs "1,29" (decimal, EU)
        if re.match(r"^\d{1,3},\d{2}$", n):
            n = n.replace(",", ".")
        else:
            n = n.replace(",", "")
    try:
        value = float(n)
    except ValueError:
        return None
    if value < 0:
        return None
    return value


def parse_currency(raw: Any) -> Optional[str]:
    """Return an ISO-4217-ish three-letter code, or None."""
    text = _clean(raw)
    if not text:
        return None
    upper = text.upper()
    for token, iso in CURRENCY_SYMBOLS.items():
        if token.upper() in upper:
            return iso
    if len(text) == 3 and text.isalpha():
        return text.upper()
    return None


def parse_rating(raw: Any) -> Optional[float]:
    """Return a float in [0, 5] or None."""
    if raw is None:
        return None
    text = _clean(raw)
    m = re.search(r"\d+(?:\.\d+)?", text)
    if not m:
        return None
    try:
        value = float(m.group(0))
    except ValueError:
        return None
    # Some sites use 0–10 or 0–100 — squash to 0–5.
    if value > 5 and value <= 10:
        value = value / 2.0
    if value > 10 and value <= 100:
        value = value / 20.0
    if 0 <= value <= 5:
        return value
    return None


def parse_int_from_text(raw: Any) -> Optional[int]:
    """Grab the first integer from text, allowing thousands separators."""
    text = _clean(raw)
    m = _NUMERIC_RE.search(text)
    if not m:
        return None
    n = m.group(0).replace(",", "").split(".", 1)[0]
    try:
        value = int(n)
    except ValueError:
        return None
    if value < 0:
        return None
    return value


def parse_availability(raw: Any) -> Optional[str]:
    """Return a normalized availability label ("In Stock" / "Out of Stock" / ...)."""
    text = _clean(raw)
    if not text:
        return None
    lowered = text.lower()
    # Strip leading label ("Availability: In Stock")
    if ":" in lowered:
        lowered = lowered.split(":", 1)[1].strip()
    # Longer keys first so "unavailable" isn't caught by "available".
    for key in sorted(AVAILABILITY_MAP, key=len, reverse=True):
        if key in lowered:
            return AVAILABILITY_MAP[key]
    return text  # keep original if we can't classify


def parse_url(raw: Any, base_url: str = "") -> Optional[str]:
    if raw is None:
        return None
    text = _clean(raw)
    if not text:
        return None
    if base_url and not (text.startswith("http://") or text.startswith("https://")):
        return urljoin(base_url, text)
    return text


def parse_text(raw: Any) -> Optional[str]:
    text = _clean(raw)
    return text or None


def normalize(field_name: str, transform: str, raw: Any, base_url: str = "") -> Any:
    """Dispatch table used by the extractor."""
    if transform == "text":
        return parse_text(raw)
    if transform == "int" or transform == "int_from_text":
        return parse_int_from_text(raw)
    if transform == "float":
        try:
            return float(_clean(raw))
        except (TypeError, ValueError):
            return None
    if transform == "price":
        return parse_price(raw)
    if transform == "rating":
        return parse_rating(raw)
    if transform == "url":
        return parse_url(raw, base_url=base_url)
    if transform == "attr":
        return parse_text(raw)
    if transform == "html":
        return raw  # keep as-is
    if field_name == "availability":
        return parse_availability(raw)
    return parse_text(raw)
