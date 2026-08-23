"""Failure detection: does the extraction actually need repair?

A page's HTML changing is not, by itself, a reason to alarm — designers move
things around all the time and semantic selectors keep working. What matters
is whether the *extracted data* degraded. This module packages the raw
`HealthReport` + optional baseline into an unambiguous `DetectionReport` that
says one of three things:

  detected=False, severity="none"          — extraction is fine, no repair needed
  detected=True,  severity="warning"       — degradation observed, monitor / stage repair
  detected=True,  severity="critical"      — repair immediately

We compare current-run health against a stored baseline (usually the last
known-good run for the same scraper) so we can catch trends the absolute
thresholds miss (e.g. slow rot). If no baseline is available, we fall back to
absolute thresholds only.

The report carries *evidence* — DOM fingerprint delta, per-field null-rate
deltas, record-count delta, freshly broken vs baseline broken fields — so a
human (or the repair engine) can diagnose the failure without re-fetching.
"""

from __future__ import annotations

from dataclasses import dataclass, field, asdict
from typing import Optional

from backend.scraper.schema import REQUIRED_FIELDS

from .health import DEFAULT_THRESHOLDS, HealthReport, HealthThresholds


@dataclass
class DetectionEvidence:
    """Concrete facts that support the detection verdict."""

    baseline_health: Optional[float] = None
    current_health: float = 0.0
    health_delta: float = 0.0                          # current - baseline (negative = drop)

    baseline_record_count: Optional[int] = None
    current_record_count: int = 0
    record_count_delta: int = 0

    baseline_fingerprint: Optional[str] = None
    current_fingerprint: Optional[str] = None
    fingerprint_changed: bool = False

    baseline_null_rates: dict[str, float] = field(default_factory=dict)
    current_null_rates: dict[str, float] = field(default_factory=dict)
    null_rate_deltas: dict[str, float] = field(default_factory=dict)

    fields_broken_now: list[str] = field(default_factory=list)
    fields_broken_baseline: list[str] = field(default_factory=list)
    newly_broken_fields: list[str] = field(default_factory=list)
    recovered_fields: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class DetectionReport:
    detected: bool
    severity: str                # "none" | "warning" | "critical"
    confidence: float            # 0..1 — how sure we are of the verdict
    reason: str                  # one-line human-readable diagnosis
    signals: list[str] = field(default_factory=list)
    evidence: DetectionEvidence = field(default_factory=DetectionEvidence)
    triggering_rules: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "detected": self.detected,
            "severity": self.severity,
            "confidence": round(self.confidence, 4),
            "reason": self.reason,
            "signals": list(self.signals),
            "triggering_rules": list(self.triggering_rules),
            "evidence": self.evidence.to_dict(),
        }


def _fields_broken(hr: HealthReport, threshold: float = 0.5) -> list[str]:
    """Fields with null rate >= `threshold` in this run."""
    return sorted(f for f, rate in hr.null_rates.items() if rate >= threshold)


def detect_degradation(
    current: HealthReport,
    baseline: Optional[HealthReport] = None,
    thresholds: Optional[HealthThresholds] = None,
    current_fingerprint: Optional[str] = None,
    baseline_fingerprint: Optional[str] = None,
    min_health_drop_for_critical: float = 0.20,
    min_health_drop_for_warning: float = 0.05,
    min_null_spike_for_critical: float = 0.40,
) -> DetectionReport:
    """Produce a DetectionReport.

    Rules (evaluated in order; first "critical" wins):

    Critical:
      * `current.status == "repair_required"` (absolute floor breach)
      * `records_received == 0`
      * baseline exists AND health dropped by >= `min_health_drop_for_critical`
      * baseline exists AND any field's null rate jumped by >= `min_null_spike_for_critical`

    Warning:
      * `current.status == "warning"`
      * baseline exists AND health dropped by >= `min_health_drop_for_warning`
      * Any anomaly signal fired (identical_* / null_spike / type_drift) even if
        the absolute health score is fine.

    None (no alarm):
      * `current.status == "healthy"` AND (no baseline OR health >= baseline)
      * The DOM fingerprint changing on its own is NEVER a reason to alarm —
        we only care whether the extracted data degraded.
    """
    thresholds = thresholds or DEFAULT_THRESHOLDS

    # ------- assemble evidence ------------------------------------------
    ev = DetectionEvidence(
        baseline_health=baseline.health_score if baseline else None,
        current_health=current.health_score,
        health_delta=(current.health_score - baseline.health_score) if baseline else 0.0,
        baseline_record_count=baseline.records_received if baseline else None,
        current_record_count=current.records_received,
        record_count_delta=(current.records_received - baseline.records_received) if baseline else 0,
        baseline_fingerprint=baseline_fingerprint,
        current_fingerprint=current_fingerprint,
        fingerprint_changed=bool(
            baseline_fingerprint and current_fingerprint and baseline_fingerprint != current_fingerprint
        ),
        baseline_null_rates=dict(baseline.null_rates) if baseline else {},
        current_null_rates=dict(current.null_rates),
        null_rate_deltas={
            f: round(current.null_rates.get(f, 0.0) - (baseline.null_rates.get(f, 0.0) if baseline else 0.0), 4)
            for f in REQUIRED_FIELDS
        },
        fields_broken_now=_fields_broken(current),
    )
    if baseline:
        ev.fields_broken_baseline = _fields_broken(baseline)
        ev.newly_broken_fields = sorted(set(ev.fields_broken_now) - set(ev.fields_broken_baseline))
        ev.recovered_fields = sorted(set(ev.fields_broken_baseline) - set(ev.fields_broken_now))
    else:
        ev.fields_broken_baseline = []
        ev.newly_broken_fields = ev.fields_broken_now
        ev.recovered_fields = []

    triggering: list[str] = []

    # ------- CRITICAL rules ---------------------------------------------
    if current.status == "repair_required":
        triggering.append("absolute_status_repair_required")
    if current.records_received == 0:
        triggering.append("no_records_extracted")
    if baseline and -ev.health_delta >= min_health_drop_for_critical:
        triggering.append(f"health_drop:{-ev.health_delta:.2f}>={min_health_drop_for_critical}")
    if baseline:
        max_spike = max((ev.null_rate_deltas.get(f, 0.0) for f in REQUIRED_FIELDS), default=0.0)
        if max_spike >= min_null_spike_for_critical:
            triggering.append(f"null_spike:{max_spike:.2f}>={min_null_spike_for_critical}")

    if triggering:
        severity = "critical"
        # Confidence rises with how many rules triggered and how far below
        # the healthy threshold we are.
        confidence = min(
            1.0,
            0.6
            + 0.1 * len(triggering)
            + max(0.0, thresholds.healthy - current.health_score),
        )
        reason = _summarize(triggering, ev, current)
        return DetectionReport(
            detected=True,
            severity=severity,
            confidence=round(confidence, 4),
            reason=reason,
            signals=list(current.failure_signals),
            evidence=ev,
            triggering_rules=triggering,
        )

    # ------- WARNING rules ----------------------------------------------
    if current.status == "warning":
        triggering.append("absolute_status_warning")
    if baseline and -ev.health_delta >= min_health_drop_for_warning:
        triggering.append(f"health_drop:{-ev.health_delta:.2f}>={min_health_drop_for_warning}")
    for sig in current.failure_signals:
        # Anomaly-family signals are worth reporting even when the score is fine.
        if sig.startswith(("identical_", "null_spike:", "type_drift")):
            triggering.append(f"anomaly:{sig}")

    if triggering:
        confidence = min(1.0, 0.4 + 0.1 * len(triggering))
        return DetectionReport(
            detected=True,
            severity="warning",
            confidence=round(confidence, 4),
            reason=_summarize(triggering, ev, current),
            signals=list(current.failure_signals),
            evidence=ev,
            triggering_rules=triggering,
        )

    # ------- NONE -------------------------------------------------------
    reason = "extraction healthy"
    if ev.fingerprint_changed:
        reason = (
            "DOM fingerprint changed but extracted data is unchanged — cosmetic redesign"
        )
    return DetectionReport(
        detected=False,
        severity="none",
        confidence=1.0 if current.status == "healthy" else 0.8,
        reason=reason,
        signals=list(current.failure_signals),
        evidence=ev,
        triggering_rules=[],
    )


def _summarize(triggering: list[str], ev: DetectionEvidence, hr: HealthReport) -> str:
    """One-line human-readable summary of what went wrong."""
    if ev.newly_broken_fields:
        return (
            f"health={hr.health_score:.2f} ({hr.status}); "
            f"newly broken: {', '.join(ev.newly_broken_fields)}"
        )
    if hr.records_received == 0:
        return "no records extracted"
    parts = []
    if ev.baseline_health is not None:
        parts.append(f"health {ev.baseline_health:.2f}→{hr.current_health if hasattr(hr,'current_health') else hr.health_score:.2f}")
    else:
        parts.append(f"health={hr.health_score:.2f} ({hr.status})")
    if hr.failure_signals:
        parts.append("signals: " + ", ".join(hr.failure_signals[:3]))
    return "; ".join(parts)


# --------------------------------------------------------------------------- #
# Detection-accuracy metrics for the benchmark
# --------------------------------------------------------------------------- #


@dataclass
class DetectionAccuracy:
    true_positives: int = 0
    true_negatives: int = 0
    false_positives: int = 0
    false_negatives: int = 0

    @property
    def total(self) -> int:
        return self.true_positives + self.true_negatives + self.false_positives + self.false_negatives

    @property
    def precision(self) -> float:
        denom = self.true_positives + self.false_positives
        return self.true_positives / denom if denom else 0.0

    @property
    def recall(self) -> float:
        denom = self.true_positives + self.false_negatives
        return self.true_positives / denom if denom else 0.0

    @property
    def f1(self) -> float:
        p, r = self.precision, self.recall
        return 2 * p * r / (p + r) if (p + r) else 0.0

    @property
    def accuracy(self) -> float:
        return (self.true_positives + self.true_negatives) / self.total if self.total else 0.0

    def observe(self, expected_detected: bool, actually_detected: bool) -> None:
        if expected_detected and actually_detected:
            self.true_positives += 1
        elif not expected_detected and not actually_detected:
            self.true_negatives += 1
        elif not expected_detected and actually_detected:
            self.false_positives += 1
        else:
            self.false_negatives += 1

    def to_dict(self) -> dict:
        return {
            "true_positives": self.true_positives,
            "true_negatives": self.true_negatives,
            "false_positives": self.false_positives,
            "false_negatives": self.false_negatives,
            "precision": round(self.precision, 4),
            "recall": round(self.recall, 4),
            "f1": round(self.f1, 4),
            "accuracy": round(self.accuracy, 4),
        }
