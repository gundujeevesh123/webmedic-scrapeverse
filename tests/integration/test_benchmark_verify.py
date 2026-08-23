"""Independent verification of every benchmark metric.

Re-implements each metric from raw fixtures + golden dataset and asserts the
harness's summary numbers agree. If the harness ever drifts from the frozen
methodology in `benchmark/METHODOLOGY.md`, these tests fail loudly.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from backend.acquisition.fixture import FixtureAcquisition
from backend.config import FIXTURE_DIR
from backend.healer.repair import broken_fields_from_health, repair
from backend.scraper.extract import extract
from backend.scraper.schema import REQUIRED_FIELDS
from backend.scraper.strategy import DEFAULT_STRATEGY
from backend.validator.detection import detect_degradation
from backend.validator.health import compute_health

from benchmark.harness import (
    DETECTION_TRUTH,
    detection_accuracy_across_fixtures,
    run_benchmark,
)

FIXTURES = FIXTURE_DIR
GOLDEN = json.load(open(FIXTURES / "golden_dataset.json"))["records"]


# --------------------------------------------------------------------------- #
# Reference (independent) implementations of each metric
# --------------------------------------------------------------------------- #


def _load_pages(version: str) -> list[str]:
    pages_dir = FIXTURES / "pages" / version
    return [
        (pages_dir / p).read_text()
        for p in sorted(x.name for x in pages_dir.iterdir() if x.name.startswith("page-"))
    ]


def _extract_all(version: str, strategy):
    records = []
    representative = _load_pages(version)[0]
    for html in _load_pages(version):
        r = extract(html, strategy, url="http://127.0.0.1:8765/list")
        records.extend(r.records)
    return records, representative


def _traditional_run(version: str):
    records, _ = _extract_all(version, DEFAULT_STRATEGY)
    hr = compute_health(records, expected=len(GOLDEN))
    return records, hr


def _webmedic_run(version: str):
    records, representative = _extract_all(version, DEFAULT_STRATEGY)
    hr = compute_health(records, expected=len(GOLDEN))
    if hr.status == "healthy":
        return records, hr, False, None
    broken = broken_fields_from_health(hr)
    plan = repair(DEFAULT_STRATEGY, representative,
                  broken_fields=broken, url="http://127.0.0.1:8765/list",
                  golden=GOLDEN)
    all_promote = plan.field_repairs and all(
        fr.action == "promote" for fr in plan.field_repairs.values()
    )
    if plan.proposed_strategy and all_promote:
        records2, _ = _extract_all(version, plan.proposed_strategy)
        hr2 = compute_health(records2, expected=len(GOLDEN))
        return records2, hr2, True, plan.proposed_confidence
    return records, hr, False, None


def _field_accuracy(records: list[dict]) -> float:
    if not records:
        return 0.0
    n = min(len(records), len(GOLDEN))
    correct = total = 0
    for i in range(n):
        for f in REQUIRED_FIELDS:
            total += 1
            if str(records[i].get(f)) == str(GOLDEN[i].get(f)):
                correct += 1
    return correct / total if total else 0.0


def _completeness(records: list[dict]) -> float:
    if not records:
        return 0.0
    total = len(records) * len(REQUIRED_FIELDS)
    non_null = sum(
        1 for r in records for f in REQUIRED_FIELDS if r.get(f) not in (None, "")
    )
    return non_null / total


# --------------------------------------------------------------------------- #
# Tests: harness numbers must match the independent recount
# --------------------------------------------------------------------------- #


@pytest.fixture(scope="module")
def report():
    return run_benchmark()


def test_traditional_field_accuracy_matches_independent_recount(report):
    versions = sorted({r.version for r in report.rows})
    ref = {v: _field_accuracy(_traditional_run(v)[0]) for v in versions}
    ref_avg = sum(ref.values()) / len(ref)
    harness_avg = report.summary()["systems"]["Traditional"]["avg_field_accuracy"]
    assert abs(harness_avg - ref_avg) < 1e-4, (harness_avg, ref_avg, ref)


def test_webmedic_field_accuracy_matches_independent_recount(report):
    versions = sorted({r.version for r in report.rows})
    ref = {v: _field_accuracy(_webmedic_run(v)[0]) for v in versions}
    ref_avg = sum(ref.values()) / len(ref)
    harness_avg = report.summary()["systems"]["WebMedic"]["avg_field_accuracy"]
    assert abs(harness_avg - ref_avg) < 1e-4, (harness_avg, ref_avg, ref)


def test_traditional_completeness_matches_independent_recount(report):
    versions = sorted({r.version for r in report.rows})
    ref = {v: _completeness(_traditional_run(v)[0]) for v in versions}
    ref_avg = sum(ref.values()) / len(ref)
    harness_avg = report.summary()["systems"]["Traditional"]["avg_completeness"]
    assert abs(harness_avg - ref_avg) < 1e-4, (harness_avg, ref_avg, ref)


def test_repair_attempts_match_traditional_broken_versions(report):
    """Denominator sanity: repair_attempts equals count of fixtures where
    Traditional was not healthy."""
    traditional_broken = {
        r.version for r in report.rows
        if r.system == "Traditional" and r.status != "healthy"
    }
    webmedic_attempts = {
        r.version for r in report.rows
        if r.system == "WebMedic" and r.repair_attempted
    }
    assert traditional_broken == webmedic_attempts, (traditional_broken, webmedic_attempts)


def test_repair_success_rate_matches_independent_recount(report):
    versions = sorted({r.version for r in report.rows})
    successful = 0
    attempts = 0
    for v in versions:
        _, tr_hr = _traditional_run(v)
        if tr_hr.status == "healthy":
            continue
        attempts += 1
        _, wm_hr, promoted, _ = _webmedic_run(v)
        if promoted and wm_hr.status == "healthy":
            successful += 1
    ref_rate = successful / attempts if attempts else 0.0
    m = report.summary()["systems"]["WebMedic"]
    assert m["repair_attempts"] == attempts, (m["repair_attempts"], attempts)
    assert m["repair_successes"] == successful
    assert abs(m["repair_rate"] - ref_rate) < 1e-4


def test_false_repairs_are_zero_by_construction(report):
    """A false repair = Traditional healthy but WebMedic still attempted repair.

    The benchmark's own gating avoids this — verify explicitly.
    """
    healthy_versions = {
        r.version for r in report.rows
        if r.system == "Traditional" and r.status == "healthy"
    }
    false_repairs = sum(
        1 for r in report.rows
        if r.system == "WebMedic" and r.version in healthy_versions and r.repair_attempted
    )
    assert false_repairs == 0
    assert report.summary()["systems"]["WebMedic"]["false_repairs"] == 0


def test_mttr_is_mean_across_actual_repairs(report):
    repairs = [r for r in report.rows if r.system == "WebMedic" and r.repair_attempted]
    if not repairs:
        assert report.summary()["systems"]["WebMedic"]["mttr_ms"] == 0.0
        return
    ref_mttr = sum(r.mttr_ms for r in repairs) / len(repairs)
    m_mttr = report.summary()["systems"]["WebMedic"]["mttr_ms"]
    # Both harness and reference operate on values rounded to 2 dp per row
    # then averaged — allow a rounding tolerance of half a hundredth ms.
    assert abs(m_mttr - ref_mttr) < 0.05, (m_mttr, ref_mttr)


def test_detection_accuracy_matches_independent_recount(report):
    """Recompute precision/recall from scratch by re-running the detector."""
    versions = sorted(DETECTION_TRUTH.keys())
    baseline_records, baseline_repr = _extract_all("v1_healthy", DEFAULT_STRATEGY)
    baseline_hr = compute_health(baseline_records, expected=len(GOLDEN))
    baseline_fp = extract(baseline_repr, DEFAULT_STRATEGY, url="").fingerprint

    tp = tn = fp = fn = 0
    for v in versions:
        records, representative = _extract_all(v, DEFAULT_STRATEGY)
        hr = compute_health(records, expected=len(GOLDEN))
        fp_here = extract(representative, DEFAULT_STRATEGY, url="").fingerprint
        det = detect_degradation(
            hr, baseline=baseline_hr,
            current_fingerprint=fp_here, baseline_fingerprint=baseline_fp,
        )
        expected = DETECTION_TRUTH[v]
        if expected and det.detected:
            tp += 1
        elif not expected and not det.detected:
            tn += 1
        elif not expected and det.detected:
            fp += 1
        else:
            fn += 1

    ref_precision = tp / (tp + fp) if (tp + fp) else 0.0
    ref_recall = tp / (tp + fn) if (tp + fn) else 0.0
    harness = detection_accuracy_across_fixtures().to_dict()
    assert (tp, tn, fp, fn) == (
        harness["true_positives"], harness["true_negatives"],
        harness["false_positives"], harness["false_negatives"]
    )
    assert abs(harness["precision"] - ref_precision) < 1e-4
    assert abs(harness["recall"] - ref_recall) < 1e-4


def test_no_manufactured_numbers_documented_targets_hold():
    """Sanity guard for the numbers cited in the README.

    If the benchmark ever regresses, these assertions catch it before we ship
    stale figures in the docs.
    """
    r = run_benchmark()
    s = r.summary()
    trad = s["systems"]["Traditional"]
    web = s["systems"]["WebMedic"]
    # Traditional never repairs
    assert trad["repair_attempts"] == 0
    # WebMedic repairs every real breakage
    assert web["repair_rate"] == 1.0
    # No false repairs
    assert web["false_repairs"] == 0
    # WebMedic never drops accuracy below Traditional on any fixture
    by_key = {(r.version, r.system): r for r in r.rows}
    for v in sorted({row.version for row in r.rows}):
        assert by_key[(v, "WebMedic")].field_accuracy >= by_key[(v, "Traditional")].field_accuracy, v
