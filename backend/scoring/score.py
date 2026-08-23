"""Candidate scoring (guide §8.4).

    Score = 0.25 * SchemaValidity
          + 0.25 * Completeness
          + 0.20 * TypeValidity
          + 0.15 * Structural/SemanticSimilarity
          + 0.15 * HistoricalConsistency

We compute components deterministically from a candidate's extracted values
+ (optionally) a golden dataset for structural comparison + (optionally) a
history of prior successful strategies for consistency.
"""

from __future__ import annotations

from dataclasses import dataclass, asdict
from typing import Iterable, Optional

from backend.scraper.schema import FIELD_TYPES


W_SCHEMA = 0.25
W_COMPLETENESS = 0.25
W_TYPE = 0.20
W_SIMILARITY = 0.15
W_HISTORICAL = 0.15


@dataclass
class ScoreBreakdown:
    schema_validity: float
    completeness: float
    type_validity: float
    similarity: float
    historical_consistency: float
    total: float

    def to_dict(self) -> dict:
        return asdict(self)


def _completeness(values: list) -> float:
    if not values:
        return 0.0
    non_null = sum(1 for v in values if v not in (None, ""))
    return non_null / len(values)


def _schema_validity_from_report(records: list[dict], field: str) -> float:
    """Fraction of records where this field passes shape checks (non-null + right ballpark)."""
    if not records:
        return 0.0
    good = 0
    for r in records:
        v = r.get(field)
        if v in (None, ""):
            continue
        if field == "price" and v <= 0:
            continue
        if field == "rating" and not (0 <= v <= 5):
            continue
        if field == "review_count" and v < 0:
            continue
        if field in ("product_url", "image_url") and not str(v).startswith(("http://", "https://")):
            continue
        if field == "currency" and not (isinstance(v, str) and len(v) == 3 and v.isupper() and v.isalpha()):
            continue
        good += 1
    return good / len(records)


def _type_validity(records: list[dict], field: str) -> float:
    if not records:
        return 0.0
    ts = FIELD_TYPES.get(field, ())
    good = 0
    for r in records:
        v = r.get(field)
        if v is None:
            continue
        if isinstance(v, ts):
            good += 1
    return good / len(records)


def _similarity(
    records: list[dict], golden: Optional[list[dict]], field: str
) -> float:
    """If a golden set is provided, fraction of records matching golden on `field`.

    Otherwise, semantic self-consistency: fraction of records whose value has
    the same value-type distribution as the majority.
    """
    if not records:
        return 0.0
    if golden:
        n = min(len(records), len(golden))
        if n == 0:
            return 0.0
        good = sum(
            1
            for i in range(n)
            if str(records[i].get(field)) == str(golden[i].get(field))
        )
        return good / n
    # Self-consistency: 1 - stdev(len(str(v))) / max_len, clipped to [0,1]
    lengths = [len(str(r.get(field))) for r in records if r.get(field) is not None]
    if not lengths:
        return 0.0
    mean = sum(lengths) / len(lengths)
    if mean == 0:
        return 0.0
    variance = sum((L - mean) ** 2 for L in lengths) / len(lengths)
    stdev = variance ** 0.5
    return max(0.0, min(1.0, 1 - stdev / max(mean, 1)))


def _historical_consistency(
    values: list, historical_values: Optional[list]
) -> float:
    """Overlap fraction with a bag of previously-observed values, if provided."""
    if not values:
        return 0.0
    if not historical_values:
        # No history yet — give a neutral score so we don't punish first repair.
        return 0.5
    hist = set(str(v) for v in historical_values if v is not None)
    hits = sum(1 for v in values if str(v) in hist)
    return hits / len(values)


def score_field(
    records: list[dict],
    field: str,
    golden: Optional[list[dict]] = None,
    historical_values: Optional[list] = None,
) -> ScoreBreakdown:
    values = [r.get(field) for r in records]
    schema = _schema_validity_from_report(records, field)
    completeness = _completeness(values)
    types = _type_validity(records, field)
    similarity = _similarity(records, golden, field)
    historical = _historical_consistency(values, historical_values)
    total = (
        W_SCHEMA * schema
        + W_COMPLETENESS * completeness
        + W_TYPE * types
        + W_SIMILARITY * similarity
        + W_HISTORICAL * historical
    )
    return ScoreBreakdown(
        schema_validity=round(schema, 4),
        completeness=round(completeness, 4),
        type_validity=round(types, 4),
        similarity=round(similarity, 4),
        historical_consistency=round(historical, 4),
        total=round(total, 4),
    )


def score_strategy(
    records: list[dict],
    fields: Iterable[str],
    golden: Optional[list[dict]] = None,
    historical_values: Optional[dict[str, list]] = None,
) -> ScoreBreakdown:
    """Aggregate per-field scores by averaging each component."""
    historical_values = historical_values or {}
    field_list = list(fields)
    if not field_list:
        return ScoreBreakdown(0, 0, 0, 0, 0, 0)
    breakdowns = [
        score_field(
            records, f, golden=golden, historical_values=historical_values.get(f)
        )
        for f in field_list
    ]
    n = len(breakdowns)
    schema = sum(b.schema_validity for b in breakdowns) / n
    comp = sum(b.completeness for b in breakdowns) / n
    types = sum(b.type_validity for b in breakdowns) / n
    sim = sum(b.similarity for b in breakdowns) / n
    hist = sum(b.historical_consistency for b in breakdowns) / n
    total = (
        W_SCHEMA * schema
        + W_COMPLETENESS * comp
        + W_TYPE * types
        + W_SIMILARITY * sim
        + W_HISTORICAL * hist
    )
    return ScoreBreakdown(
        schema_validity=round(schema, 4),
        completeness=round(comp, 4),
        type_validity=round(types, 4),
        similarity=round(sim, 4),
        historical_consistency=round(hist, 4),
        total=round(total, 4),
    )
