"""Health scoring for a scraper run (guide §7.3).

Combines completeness, validity, schema consistency, and record-count
consistency into a single score in [0, 1]. Weights and thresholds are
configurable — the constants below are only the guide's starting point.

    H = w1·Completeness + w2·Validity + w3·SchemaConsistency + w4·RecordConsistency
"""

from __future__ import annotations

from dataclasses import dataclass, asdict, field
from typing import Optional

from backend.config import settings
from backend.scraper.schema import REQUIRED_FIELDS

from .rules import RuleViolation, validate_record


# --------------------------------------------------------------------------- #
# Configurable weights + thresholds
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class HealthWeights:
    """Weights for the four health components. Must sum to (approximately) 1.0."""

    completeness: float = 0.30
    validity: float = 0.30
    schema_consistency: float = 0.20
    record_consistency: float = 0.20

    def __post_init__(self) -> None:
        total = self.completeness + self.validity + self.schema_consistency + self.record_consistency
        if not (0.99 <= total <= 1.01):
            raise ValueError(
                f"HealthWeights must sum to ~1.0, got {total:.4f}: {self}"
            )


@dataclass(frozen=True)
class HealthThresholds:
    """Bucket boundaries for `healthy | warning | repair_required`."""

    healthy: float = 0.90        # >= healthy → "healthy"
    warning: float = 0.70        # in [warning, healthy) → "warning"; below → "repair_required"

    # Failure-signal tuning:
    field_broken_frac: float = 0.80        # field is "widely broken" at >= this null rate
    record_collapse_frac: float = 0.50     # received < expected * this → collapse signal
    low_completeness_frac: float = 0.50    # completeness < this → low_completeness signal
    null_spike_delta: float = 0.30         # null rate exceeds baseline+delta → null_spike signal

    def __post_init__(self) -> None:
        if not (0.0 <= self.warning <= self.healthy <= 1.0):
            raise ValueError(f"invalid threshold ordering: {self}")


DEFAULT_WEIGHTS = HealthWeights()
DEFAULT_THRESHOLDS = HealthThresholds(
    healthy=settings.healthy_threshold,
    warning=settings.warning_threshold,
)


# --------------------------------------------------------------------------- #
# Report + main entry point
# --------------------------------------------------------------------------- #


@dataclass
class HealthReport:
    strategy_name: str
    records_expected: Optional[int]
    records_received: int
    completeness: float
    validity: float
    schema_consistency: float
    record_consistency: float
    health_score: float
    status: str
    violations_by_field: dict[str, int] = field(default_factory=dict)
    violations_by_code: dict[str, int] = field(default_factory=dict)
    failure_signals: list[str] = field(default_factory=list)
    null_rates: dict[str, float] = field(default_factory=dict)
    weights: dict[str, float] = field(default_factory=dict)
    thresholds: dict[str, float] = field(default_factory=dict)

    def to_dict(self) -> dict:
        return asdict(self)


def _classify(score: float, thresholds: HealthThresholds) -> str:
    if score >= thresholds.healthy:
        return "healthy"
    if score >= thresholds.warning:
        return "warning"
    return "repair_required"


def compute_null_rates(records: list[dict]) -> dict[str, float]:
    """Fraction of records where each required field is null/empty."""
    n = max(1, len(records))
    return {
        f: sum(1 for r in records if r.get(f) in (None, "")) / n
        for f in REQUIRED_FIELDS
    }


def compute_health(
    records: list[dict],
    expected: Optional[int] = None,
    healthy_threshold: Optional[float] = None,
    warning_threshold: Optional[float] = None,
    strategy_name: str = "",
    weights: Optional[HealthWeights] = None,
    thresholds: Optional[HealthThresholds] = None,
    baseline_null_rates: Optional[dict[str, float]] = None,
) -> HealthReport:
    """Compute a HealthReport for a batch of extracted records.

    `weights`/`thresholds` override the defaults. `baseline_null_rates`, when
    provided, is used to detect a "null_spike" per field vs prior runs.

    `healthy_threshold` / `warning_threshold` are legacy scalar overrides kept
    for backward compatibility.
    """

    weights = weights or DEFAULT_WEIGHTS
    if thresholds is None:
        if healthy_threshold is not None or warning_threshold is not None:
            thresholds = HealthThresholds(
                healthy=healthy_threshold if healthy_threshold is not None else DEFAULT_THRESHOLDS.healthy,
                warning=warning_threshold if warning_threshold is not None else DEFAULT_THRESHOLDS.warning,
            )
        else:
            thresholds = DEFAULT_THRESHOLDS

    received = len(records)
    total_fields = max(1, received * len(REQUIRED_FIELDS))

    per_record_violations: list[list[RuleViolation]] = [validate_record(r) for r in records]
    all_violations = [v for lst in per_record_violations for v in lst]

    violations_by_field: dict[str, int] = {}
    violations_by_code: dict[str, int] = {}
    for v in all_violations:
        violations_by_field[v.field] = violations_by_field.get(v.field, 0) + 1
        violations_by_code[v.code] = violations_by_code.get(v.code, 0) + 1

    # ------- component scores --------------------------------------------

    non_null = sum(
        1
        for r in records
        for f in REQUIRED_FIELDS
        if r.get(f) not in (None, "")
    )
    completeness = min(1.0, non_null / total_fields)

    valid_records = sum(1 for lst in per_record_violations if not lst)
    validity = valid_records / max(1, received)

    wrong_type = sum(1 for v in all_violations if v.code == "wrong_type")
    fields_present = sum(
        1 for r in records for f in REQUIRED_FIELDS if r.get(f) is not None
    )
    schema_consistency = 1.0 if fields_present == 0 else 1.0 - wrong_type / fields_present

    if expected is None or expected == 0:
        record_consistency = 1.0 if received > 0 else 0.0
    else:
        drift = abs(received - expected) / expected
        record_consistency = max(0.0, 1.0 - drift)

    health = (
        weights.completeness * completeness
        + weights.validity * validity
        + weights.schema_consistency * schema_consistency
        + weights.record_consistency * record_consistency
    )
    status = _classify(health, thresholds)

    # ------- diagnostics --------------------------------------------------

    null_rates = compute_null_rates(records) if records else {f: 1.0 for f in REQUIRED_FIELDS}

    signals = _failure_signals(
        received=received,
        expected=expected,
        completeness=completeness,
        violations_by_field=violations_by_field,
        violations_by_code=violations_by_code,
        null_rates=null_rates,
        thresholds=thresholds,
        baseline_null_rates=baseline_null_rates,
    )

    # Add semantic-anomaly signals derived from the record set itself.
    signals.extend(_anomaly_signals(records))

    return HealthReport(
        strategy_name=strategy_name,
        records_expected=expected,
        records_received=received,
        completeness=round(completeness, 4),
        validity=round(validity, 4),
        schema_consistency=round(schema_consistency, 4),
        record_consistency=round(record_consistency, 4),
        health_score=round(health, 4),
        status=status,
        violations_by_field=violations_by_field,
        violations_by_code=violations_by_code,
        failure_signals=signals,
        null_rates={k: round(v, 4) for k, v in null_rates.items()},
        weights={
            "completeness": weights.completeness,
            "validity": weights.validity,
            "schema_consistency": weights.schema_consistency,
            "record_consistency": weights.record_consistency,
        },
        thresholds={
            "healthy": thresholds.healthy,
            "warning": thresholds.warning,
            "field_broken_frac": thresholds.field_broken_frac,
            "record_collapse_frac": thresholds.record_collapse_frac,
            "low_completeness_frac": thresholds.low_completeness_frac,
            "null_spike_delta": thresholds.null_spike_delta,
        },
    )


def _failure_signals(
    *,
    received: int,
    expected: Optional[int],
    completeness: float,
    violations_by_field: dict[str, int],
    violations_by_code: dict[str, int],
    null_rates: dict[str, float],
    thresholds: HealthThresholds,
    baseline_null_rates: Optional[dict[str, float]],
) -> list[str]:
    """The observable signals from guide §7.4."""
    signals: list[str] = []
    if received == 0:
        signals.append("no_records_extracted")
    if expected is not None and expected > 0 and received < expected * thresholds.record_collapse_frac:
        signals.append(f"record_count_collapse:{received}/{expected}")
    if completeness < thresholds.low_completeness_frac:
        signals.append(f"low_completeness:{completeness:.2f}")
    missing_ratio: dict[str, float] = {}
    for field_name, count in violations_by_field.items():
        if received > 0 and count / received >= thresholds.field_broken_frac:
            missing_ratio[field_name] = count / received
    if missing_ratio:
        top = sorted(missing_ratio.items(), key=lambda kv: -kv[1])[0]
        signals.append(f"field_widely_broken:{top[0]}={top[1]:.2f}")
    if violations_by_code.get("wrong_type"):
        signals.append(f"type_drift:{violations_by_code['wrong_type']}")
    if baseline_null_rates:
        for f, cur in null_rates.items():
            baseline = baseline_null_rates.get(f, 0.0)
            if cur - baseline > thresholds.null_spike_delta:
                signals.append(f"null_spike:{f}={baseline:.2f}->{cur:.2f}")
    return signals


def _anomaly_signals(records: list[dict]) -> list[str]:
    """Semantic anomalies derived from the whole record set.

    Currently detects:
      * All prices identical across >1 records (template rendering failure).
      * All product_urls pointing to the same URL (broken relative-URL parse).
    """
    signals: list[str] = []
    if len(records) < 2:
        return signals
    prices = [r.get("price") for r in records if r.get("price") is not None]
    if len(prices) >= 3 and len(set(prices)) == 1:
        signals.append(f"identical_prices:{prices[0]}")
    urls = [r.get("product_url") for r in records if r.get("product_url")]
    if len(urls) >= 3 and len(set(urls)) == 1:
        signals.append("identical_product_urls")
    names = [r.get("product_name") for r in records if r.get("product_name")]
    if len(names) >= 3 and len(set(names)) == 1:
        signals.append(f"identical_product_names:{names[0]!r}")
    return signals
