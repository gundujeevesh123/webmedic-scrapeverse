"""Adversarial failure-detection tests.

For every fixture we label whether the detector SHOULD fire (real extraction
degradation) or SHOULD stay silent (cosmetic-only HTML change). Then we run
the detector, assert per-fixture correctness, and finally compute precision
and recall across the whole set.

If any single fixture flips its expected label the suite must fail loudly —
false alarms on cosmetic changes and missed real breakages are equally
dangerous.
"""

from pathlib import Path

import pytest

from backend.acquisition.fixture import FixtureAcquisition
from backend.config import FIXTURE_DIR
from backend.scraper.extract import extract
from backend.scraper.strategy import DEFAULT_STRATEGY
from backend.validator.detection import (
    DetectionAccuracy,
    detect_degradation,
)
from backend.validator.health import compute_health


# The ground truth: "should the detector fire on this fixture?"
#
# HEALTHY (expected_detected=False):
#   v1_healthy       – baseline
#   v4_change_nesting – extra wrapper divs; descendant selectors still work
#   v5_move_price    – price still inside .price inside the card
#   v6_label_change  – labels change, .price/.rating classes intact
#   v7_decoy         – struck-through <s.price>; span.price selector avoids it
#   v8_pagination    – pagination-only change; data extraction unaffected
#   v11_semantic     – availability wording changes; normalizer maps them
#
# BROKEN (expected_detected=True):
#   v2_rename_class  – .price → .cost
#   v3_dataattr      – classes replaced with data-testid
#   v9_combined      – rename + nesting + decoy
#   v10_partial      – availability class swap

EXPECTED = {
    "v1_healthy":        False,
    "v2_rename_class":   True,
    "v3_dataattr":       True,
    "v4_change_nesting": False,
    "v5_move_price":     False,
    "v6_label_change":   False,
    "v7_decoy":          False,
    "v8_pagination":     False,
    "v9_combined":       True,
    "v10_partial":       True,
    "v11_semantic":      False,
}


def _run(version: str):
    acq = FixtureAcquisition(FIXTURE_DIR / "pages", version=version)
    records = []
    fp = ""
    for page in (1, 2):
        snap = acq.fetch(f"http://127.0.0.1:8765/list?page={page}")
        r = extract(snap.html, DEFAULT_STRATEGY, url=snap.url)
        records.extend(r.records)
        fp = r.fingerprint
    hr = compute_health(records, expected=20, strategy_name=DEFAULT_STRATEGY.name)
    return hr, fp


@pytest.fixture(scope="module")
def baseline():
    return _run("v1_healthy")


@pytest.mark.parametrize("version, expected_detected", sorted(EXPECTED.items()))
def test_detector_per_fixture(version, expected_detected, baseline):
    baseline_hr, baseline_fp = baseline
    hr, fp = _run(version)
    det = detect_degradation(
        hr, baseline=baseline_hr,
        current_fingerprint=fp, baseline_fingerprint=baseline_fp,
    )
    assert det.detected == expected_detected, (
        f"{version}: expected detected={expected_detected} but got {det.detected} "
        f"— severity={det.severity} reason={det.reason!r} "
        f"health={hr.health_score:.3f} signals={hr.failure_signals}"
    )


def test_detector_precision_recall_across_all_fixtures(baseline):
    """The whole point of this phase: verify accuracy on the entire suite."""
    baseline_hr, baseline_fp = baseline
    acc = DetectionAccuracy()
    per_fixture: list[tuple[str, bool, bool]] = []
    for version, expected in EXPECTED.items():
        hr, fp = _run(version)
        det = detect_degradation(
            hr, baseline=baseline_hr,
            current_fingerprint=fp, baseline_fingerprint=baseline_fp,
        )
        acc.observe(expected_detected=expected, actually_detected=det.detected)
        per_fixture.append((version, expected, det.detected))

    # Assertions:
    #   * precision == 1.0 → no false alarms on cosmetic changes
    #   * recall    == 1.0 → no missed real breakages
    assert acc.false_positives == 0, [x for x in per_fixture if x[1] is False and x[2] is True]
    assert acc.false_negatives == 0, [x for x in per_fixture if x[1] is True and x[2] is False]
    assert acc.precision == 1.0
    assert acc.recall == 1.0
    assert acc.f1 == 1.0


def test_fingerprint_change_alone_does_not_trigger(baseline):
    """v4/v5/v7 all have very different DOM fingerprints but healthy extraction."""
    baseline_hr, baseline_fp = baseline
    for version in ("v4_change_nesting", "v5_move_price", "v7_decoy"):
        hr, fp = _run(version)
        assert fp != baseline_fp, f"{version} fingerprint should differ from baseline"
        det = detect_degradation(hr, baseline=baseline_hr,
                                 current_fingerprint=fp, baseline_fingerprint=baseline_fp)
        assert not det.detected, f"{version} triggered on fingerprint change alone: {det.reason}"
        # And the human-readable reason should call that out for cosmetic changes.
        if version in ("v4_change_nesting", "v5_move_price", "v7_decoy"):
            assert "cosmetic" in det.reason or det.severity == "none"


def test_evidence_contains_actionable_diagnosis(baseline):
    """The DetectionReport must carry the evidence needed to diagnose the failure."""
    baseline_hr, baseline_fp = baseline
    hr, fp = _run("v3_dataattr")
    det = detect_degradation(hr, baseline=baseline_hr,
                             current_fingerprint=fp, baseline_fingerprint=baseline_fp)
    assert det.detected and det.severity == "critical"
    ev = det.evidence
    assert ev.current_health < ev.baseline_health
    assert ev.health_delta < 0
    # Newly-broken fields identify exactly which columns to repair
    assert set(ev.newly_broken_fields) >= {"price", "rating", "review_count", "availability"}
    # Null-rate deltas > 0 for the same fields
    for f in ("price", "rating"):
        assert ev.null_rate_deltas.get(f, 0.0) > 0.5
    # And the triggering rules are stable and named
    assert any(r.startswith("absolute_status_repair_required") for r in det.triggering_rules)


def test_serialize_report_round_trip(baseline):
    baseline_hr, baseline_fp = baseline
    hr, fp = _run("v2_rename_class")
    det = detect_degradation(hr, baseline=baseline_hr,
                             current_fingerprint=fp, baseline_fingerprint=baseline_fp)
    d = det.to_dict()
    assert d["detected"] is True
    assert d["severity"] == "critical"
    assert isinstance(d["evidence"], dict)
    assert "null_rate_deltas" in d["evidence"]
