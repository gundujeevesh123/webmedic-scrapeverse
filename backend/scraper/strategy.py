"""Declarative extraction strategies.

A `Strategy` says how to turn one HTML page into structured `Product`
records. The extractor executes it deterministically. Repair candidates
are just new strategies — the extractor doesn't care where they came from.
"""

from __future__ import annotations

from dataclasses import dataclass, field, asdict
from typing import Any, Literal, Optional


SelectorKind = Literal["css", "xpath", "text-anchor", "attr-on-self", "static"]
Transform = Literal[
    "text", "int", "float", "price", "rating", "int_from_text", "url", "attr", "html"
]


@dataclass
class FieldSelector:
    """How to extract one field from within a record root.

    kind:
      "css"           – CSS selector applied to the record root (soupsieve).
      "xpath"         – XPath expression applied to the record root (lxml).
      "text-anchor"   – Find a label (`value`) and return the sibling / following text.
      "attr-on-self"  – Read `attr` from the record root itself.
      "static"        – Constant literal (`value`).

    attr:
      For "css"/"xpath": if set, take `element[attr]` instead of `.text`.

    transform:
      Value normalizer to apply after raw extraction. See `normalize.py`.
    """

    kind: SelectorKind
    value: str = ""
    attr: Optional[str] = None
    transform: Transform = "text"

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict) -> "FieldSelector":
        return cls(**data)


@dataclass
class Strategy:
    """A full extraction plan for one target page template."""

    name: str
    record_selector: str                       # CSS selector for record roots
    fields: dict[str, FieldSelector] = field(default_factory=dict)
    next_page_selector: Optional[str] = None   # CSS selector for the "next page" link
    notes: str = ""

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "record_selector": self.record_selector,
            "fields": {k: v.to_dict() for k, v in self.fields.items()},
            "next_page_selector": self.next_page_selector,
            "notes": self.notes,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "Strategy":
        return cls(
            name=data["name"],
            record_selector=data["record_selector"],
            fields={k: FieldSelector.from_dict(v) for k, v in data.get("fields", {}).items()},
            next_page_selector=data.get("next_page_selector"),
            notes=data.get("notes", ""),
        )


# --------------------------------------------------------------------------- #
# Default baseline strategy for v1_healthy.
# --------------------------------------------------------------------------- #

DEFAULT_STRATEGY = Strategy(
    name="metrokart-v1",
    record_selector="article.product-card",
    fields={
        # product_id is not part of the schema — it is a stable dedupe key
        # sourced from the record root's `data-product-id` attribute.
        "product_id": FieldSelector(kind="attr-on-self", value="", attr="data-product-id", transform="text"),
        "product_name": FieldSelector(kind="css", value="h3.product-name", transform="text"),
        "price": FieldSelector(kind="css", value="span.price", transform="price"),
        "currency": FieldSelector(kind="css", value="span.currency", transform="text"),
        "rating": FieldSelector(kind="css", value="span.rating", transform="rating"),
        "review_count": FieldSelector(
            kind="css", value="span.review-count", transform="int_from_text"
        ),
        "availability": FieldSelector(kind="css", value="div.availability", transform="text"),
        "product_url": FieldSelector(
            kind="css", value="a.product-link", attr="href", transform="url"
        ),
        "image_url": FieldSelector(
            kind="css", value="img.product-image", attr="src", transform="url"
        ),
    },
    next_page_selector="nav.pagination a.next",
    notes="Baseline strategy for MetroKart v1 layout.",
)
