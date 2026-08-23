"""HTML → structured records.

The extractor takes an HTML string and a Strategy and returns a list of
`Product` dicts (schema-shaped, normalized). It never raises on missing
fields — the validator is what decides whether a run is healthy.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Iterable, Optional

from bs4 import BeautifulSoup, Tag
from lxml import html as lxml_html

from .dedupe import DedupeReport, dedupe
from .normalize import normalize, parse_availability
from .strategy import FieldSelector, Strategy


@dataclass
class ExtractionResult:
    strategy_name: str
    url: str
    records: list[dict]
    fingerprint: str  # DOM structural fingerprint for drift detection
    raw_len: int
    next_page_url: Optional[str] = None
    dedupe_report: Optional[DedupeReport] = None


def _dom_fingerprint(soup: BeautifulSoup) -> str:
    """Cheap DOM fingerprint: histogram of the most common tag+class combos.

    Not cryptographic — just something that changes when a template changes.
    """
    from collections import Counter

    combos: Counter = Counter()
    for el in soup.find_all(True, limit=2000):
        cls = ".".join(sorted(el.get("class", [])))
        combos[f"{el.name}.{cls}" if cls else el.name] += 1
    top = ",".join(f"{k}={v}" for k, v in combos.most_common(20))
    return top


def _to_text(node: Tag | list[Tag] | None) -> str:
    if node is None:
        return ""
    if isinstance(node, list):
        return " ".join(_to_text(n) for n in node)
    return node.get_text(separator=" ", strip=True)


def _select_first(root: Tag, selector: FieldSelector) -> Optional[Any]:
    """Return the first matching *value* (string / node) for `selector` under `root`.

    We look up nodes with BeautifulSoup for CSS and lxml for XPath. Attribute
    extraction takes `element[attr]`; otherwise text is returned.
    """
    if selector.kind == "css":
        node = root.select_one(selector.value)
        if node is None:
            return None
        if selector.attr:
            return node.get(selector.attr)
        return _to_text(node)

    if selector.kind == "xpath":
        # Convert this bs4 subtree to lxml
        tree = lxml_html.fromstring(str(root))
        matches = tree.xpath(selector.value)
        if not matches:
            return None
        first = matches[0]
        if isinstance(first, str):
            return first
        if selector.attr:
            return first.get(selector.attr)
        return first.text_content().strip()

    if selector.kind == "attr-on-self":
        return root.get(selector.attr or selector.value)

    if selector.kind == "text-anchor":
        # Find an element whose text contains `value`, take the *following* text.
        for el in root.find_all(True):
            if selector.value.lower() in _to_text(el).lower():
                # Strip the label out and return what's left of the anchor's parent text.
                sibling_text = "".join(
                    s if isinstance(s, str) else s.get_text(" ", strip=True)
                    for s in el.parent.children
                )
                return sibling_text.replace(selector.value, "").strip(" :·-")
        return None

    if selector.kind == "static":
        return selector.value

    return None


def _base_url_of(url: str) -> str:
    from urllib.parse import urlparse

    parsed = urlparse(url)
    if parsed.scheme and parsed.netloc:
        return f"{parsed.scheme}://{parsed.netloc}"
    return url


def extract(
    html: str, strategy: Strategy, url: str = "", deduplicate: bool = True
) -> ExtractionResult:
    soup = BeautifulSoup(html, "lxml")
    roots = soup.select(strategy.record_selector) if strategy.record_selector else []
    base = _base_url_of(url) if url else ""

    records: list[dict] = []
    for root in roots:
        record: dict[str, Any] = {}
        for field_name, selector in strategy.fields.items():
            raw = _select_first(root, selector)
            record[field_name] = normalize(field_name, selector.transform, raw, base_url=base)
        # Availability requires the categorical normalizer even when transform="text".
        if "availability" in record and record["availability"]:
            record["availability"] = parse_availability(record["availability"])
        records.append(record)

    next_page_url: Optional[str] = None
    if strategy.next_page_selector:
        node = soup.select_one(strategy.next_page_selector)
        if node is not None:
            href = node.get("href") or node.get("data-next")
            if href:
                from urllib.parse import urljoin

                next_page_url = urljoin(url, str(href))

    dedupe_report: Optional[DedupeReport] = None
    if deduplicate and records:
        records, dedupe_report = dedupe(records)

    return ExtractionResult(
        strategy_name=strategy.name,
        url=url,
        records=records,
        fingerprint=_dom_fingerprint(soup),
        raw_len=len(html),
        next_page_url=next_page_url,
        dedupe_report=dedupe_report,
    )


def extract_all_pages(
    fetch: "Any", start_url: str, strategy: Strategy, max_pages: int = 20
) -> ExtractionResult:
    """Iteratively follow `next_page_url` and combine records.

    `fetch(url) -> str` is a callable (from the acquisition layer).
    """
    seen: set[str] = set()
    combined: list[dict] = []
    fingerprint = ""
    url = start_url
    pages = 0
    while url and url not in seen and pages < max_pages:
        seen.add(url)
        html_text = fetch(url)
        result = extract(html_text, strategy, url=url)
        combined.extend(result.records)
        fingerprint = result.fingerprint
        url = result.next_page_url or ""
        pages += 1
    return ExtractionResult(
        strategy_name=strategy.name,
        url=start_url,
        records=combined,
        fingerprint=fingerprint,
        raw_len=sum(1 for _ in combined),
    )
