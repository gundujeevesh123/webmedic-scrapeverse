"""Candidate generation.

Given a broken `Strategy` and the current HTML for a page that used to work,
propose N alternative `FieldSelector`s per broken field. The generator is
deliberately heuristic — the AI proposes; the deterministic scorer decides
which candidate wins (guide §8.3, §8.4).

The generator emits candidates from six sources:

  1. Stable-attribute candidates   (`data-testid`, `data-*`, `itemprop`)
  2. Class-name synonyms           (.cost, .amount, .value, .current…)
  3. Tag+role heuristics           (h1..h3 for names; img[src] for image; a[href] for URL)
  4. Text-anchor candidates        (elements whose text matches the field label)
  5. Sibling / neighbor selectors  (relative to a stable class we still find)
  6. Historical strategies         (previously-successful selectors from prior versions)
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable

from bs4 import BeautifulSoup, Tag

from backend.scraper.strategy import FieldSelector, Strategy


# Field-specific synonym vocabulary. Small on purpose — keeps candidate lists tight.
FIELD_KEYWORDS: dict[str, list[str]] = {
    "product_name": ["name", "title", "product-name", "product-title", "prod-title", "heading"],
    "price": ["price", "cost", "amount", "value", "current-price", "final-price", "sale-price", "our-price"],
    "currency": ["currency", "ccy", "curr"],
    "rating": ["rating", "stars", "score", "star-rating", "avg-rating"],
    "review_count": ["review-count", "reviews", "num-reviews", "reviews-count", "ratings"],
    "availability": ["availability", "stock", "stock-status", "in-stock", "out-of-stock"],
    "product_url": ["product-link", "product-url", "detail-link", "item-link"],
    "image_url": ["product-image", "product-img", "main-image", "primary-image"],
}


FIELD_TAGS: dict[str, list[str]] = {
    "product_name": ["h1", "h2", "h3", "h4"],
    "price": ["span", "div", "strong", "b"],
    "currency": ["span", "abbr", "small"],
    "rating": ["span", "div", "meter"],
    "review_count": ["span", "div"],
    "availability": ["div", "span"],
}


FIELD_TRANSFORMS: dict[str, str] = {
    "product_name": "text",
    "price": "price",
    "currency": "text",
    "rating": "rating",
    "review_count": "int_from_text",
    "availability": "text",
    "product_url": "url",
    "image_url": "url",
}


FIELD_ATTRS: dict[str, str | None] = {
    "product_url": "href",
    "image_url": "src",
}


@dataclass
class Candidate:
    field: str
    selector: FieldSelector
    source: str  # "attr" | "class-synonym" | "tag-role" | "text-anchor" | "historical" | ...
    rationale: str


def _classes(el: Tag) -> list[str]:
    return el.get("class") or []


def _all_class_tokens(soup: BeautifulSoup) -> set[str]:
    tokens: set[str] = set()
    for el in soup.find_all(True):
        for c in _classes(el):
            tokens.add(c.lower())
    return tokens


def _all_data_attrs(soup: BeautifulSoup) -> set[str]:
    attrs: set[str] = set()
    for el in soup.find_all(True):
        for attr in el.attrs:
            if attr.startswith("data-"):
                # Include both attr-only and attr=value
                attrs.add(attr)
    return attrs


def generate_for_field(
    field: str,
    root_selector: str,
    soup: BeautifulSoup,
    historical: Iterable[FieldSelector] = (),
) -> list[Candidate]:
    """Return an ordered list of candidate selectors for `field`."""

    kws = FIELD_KEYWORDS.get(field, [])
    tags = FIELD_TAGS.get(field, ["span", "div"])
    attr = FIELD_ATTRS.get(field)
    transform = FIELD_TRANSFORMS.get(field, "text")

    class_tokens = _all_class_tokens(soup)
    data_attrs = _all_data_attrs(soup)

    seen: set[tuple] = set()
    out: list[Candidate] = []

    def _add(cand: Candidate) -> None:
        key = (cand.selector.kind, cand.selector.value, cand.selector.attr)
        if key in seen:
            return
        seen.add(key)
        out.append(cand)

    # --- 1) data-* / itemprop candidates ----------------------------------
    for kw in kws:
        testid = f"[data-testid='{kw}']"
        _add(
            Candidate(
                field,
                FieldSelector(kind="css", value=testid, attr=attr, transform=transform),
                source="attr",
                rationale=f"data-testid matching keyword {kw!r}",
            )
        )
        # data-<kw>
        raw = f"data-{kw}"
        if raw in data_attrs:
            _add(
                Candidate(
                    field,
                    FieldSelector(kind="css", value=f"[{raw}]", attr=attr, transform=transform),
                    source="attr",
                    rationale=f"data-{kw} attribute present in DOM",
                )
            )
        # microdata
        _add(
            Candidate(
                field,
                FieldSelector(
                    kind="css", value=f"[itemprop='{kw}']", attr=attr, transform=transform
                ),
                source="attr",
                rationale=f"itemprop={kw!r}",
            )
        )

    # --- 2) Class-name synonyms actually present in the DOM ----------------
    for kw in kws:
        if kw in class_tokens:
            _add(
                Candidate(
                    field,
                    FieldSelector(
                        kind="css", value=f".{kw}", attr=attr, transform=transform
                    ),
                    source="class-synonym",
                    rationale=f"class .{kw} exists in DOM",
                )
            )
            for tag in tags:
                _add(
                    Candidate(
                        field,
                        FieldSelector(
                            kind="css",
                            value=f"{tag}.{kw}",
                            attr=attr,
                            transform=transform,
                        ),
                        source="class-synonym",
                        rationale=f"{tag}.{kw} found in DOM",
                    )
                )

    # --- 3) Tag + role heuristics -----------------------------------------
    if field == "product_name":
        for tag in ("h1", "h2", "h3", "h4"):
            _add(
                Candidate(
                    field,
                    FieldSelector(kind="css", value=tag, transform="text"),
                    source="tag-role",
                    rationale=f"heading tag {tag} usually holds the product name",
                )
            )
    if field == "image_url":
        _add(
            Candidate(
                field,
                FieldSelector(kind="css", value="img", attr="src", transform="url"),
                source="tag-role",
                rationale="first <img> in the card",
            )
        )
    if field == "product_url":
        _add(
            Candidate(
                field,
                FieldSelector(kind="css", value="a[href]", attr="href", transform="url"),
                source="tag-role",
                rationale="first anchor with href",
            )
        )

    # --- 4) Text-anchor candidates (label → adjacent value) ---------------
    label_words = {
        "price": ["Price", "Buy for", "Cost"],
        "currency": ["Currency"],
        "rating": ["Rated", "Rating"],
        "review_count": ["reviews", "ratings"],
        "availability": ["Availability", "Stock"],
    }
    for label in label_words.get(field, []):
        _add(
            Candidate(
                field,
                FieldSelector(kind="text-anchor", value=label, transform=transform),
                source="text-anchor",
                rationale=f"anchor on label {label!r}",
            )
        )

    # --- 5) Neighbor / DOM-relative candidates ----------------------------
    # A very small set of nth-of-type patterns as a last-resort.
    for tag in tags:
        for n in (1, 2):
            _add(
                Candidate(
                    field,
                    FieldSelector(
                        kind="css", value=f"{tag}:nth-of-type({n})", transform=transform
                    ),
                    source="positional",
                    rationale=f"positional {tag}:nth-of-type({n})",
                )
            )

    # --- 6) Historical: prior known-good selectors we're re-trying --------
    for h in historical:
        _add(
            Candidate(field, h, source="historical", rationale="previously deployed selector")
        )

    return out


def generate_candidates(
    strategy: Strategy,
    html: str,
    broken_fields: Iterable[str],
    historical: dict[str, list[FieldSelector]] | None = None,
) -> dict[str, list[Candidate]]:
    """Generate candidate `FieldSelector`s for every broken field.

    Returns `{field_name: [candidates ranked by heuristic]}`.
    """
    historical = historical or {}
    soup = BeautifulSoup(html, "lxml")
    out: dict[str, list[Candidate]] = {}
    for field in broken_fields:
        out[field] = generate_for_field(
            field,
            strategy.record_selector,
            soup,
            historical=historical.get(field, []),
        )
    return out
