"""Extra validator tests: semantic rules, configurable weights, null-spike detection.

These tests intentionally introduce every category of degradation the health
layer is supposed to detect and assert that each produces the right signal /
violation code.
"""

import pytest

from backend.validator.health import (
    DEFAULT_THRESHOLDS,
    HealthThresholds,
    HealthWeights,
    compute_health,
    compute_null_rates,
)
from backend.validator.rules import (
    name_not_placeholder,
    no_html_leakage,
    price_not_placeholder,
    validate_record,
)


GOOD = {
    "product_name": "Aurora Wireless Headphones",
    "price": 8999.0,
    "currency": "INR",
    "rating": 4.5,
    "review_count": 1284,
    "availability": "In Stock",
    "product_url": "http://127.0.0.1:8765/p/MK-1001",
    "image_url": "http://127.0.0.1:8765/img/MK-1001.jpg",
}


# ---- new rule-level tests -------------------------------------------------


def test_name_placeholder_flagged():
    for placeholder in ("N/A", "undefined", "TBD", "sample", "test", "lorem ipsum"):
        v = name_not_placeholder({**GOOD, "product_name": placeholder})
        assert v and v[0].code == "placeholder_text", placeholder


def test_name_placeholder_ignores_real_names():
    assert name_not_placeholder(GOOD) == []


def test_price_placeholder_zero_flagged():
    v = price_not_placeholder({**GOOD, "price": 0.0})
    assert v and v[0].code == "placeholder_zero"


def test_price_placeholder_penny_flagged():
    v = price_not_placeholder({**GOOD, "price": 0.01})
    assert v and v[0].code == "placeholder_penny"


def test_price_placeholder_sentinel_flagged():
    v = price_not_placeholder({**GOOD, "price": 9_999_999.0})
    assert v and v[0].code == "placeholder_sentinel"


def test_html_leakage_flagged():
    v = no_html_leakage({**GOOD, "product_name": "Aurora <span>Wireless</span> Headphones"})
    assert v and v[0].code == "html_leak"


def test_html_leakage_ignores_clean_text():
    assert no_html_leakage(GOOD) == []


def test_good_record_still_passes_all_rules_after_additions():
    assert validate_record(GOOD) == []


# ---- configurable weights + thresholds -----------------------------------


def test_weights_must_sum_to_one():
    with pytest.raises(ValueError):
        HealthWeights(completeness=0.5, validity=0.5, schema_consistency=0.5, record_consistency=0.5)


def test_thresholds_ordering_enforced():
    with pytest.raises(ValueError):
        HealthThresholds(healthy=0.5, warning=0.9)


def test_custom_weights_change_score():
    # One record has a null price → completeness < 1, validity < 1 for that record.
    records = [GOOD] * 4 + [{**GOOD, "price": None}]
    base = compute_health(records, expected=5)
    validity_heavy = compute_health(
        records,
        expected=5,
        weights=HealthWeights(
            completeness=0.10, validity=0.70, schema_consistency=0.10, record_consistency=0.10
        ),
    )
    completeness_heavy = compute_health(
        records,
        expected=5,
        weights=HealthWeights(
            completeness=0.70, validity=0.10, schema_consistency=0.10, record_consistency=0.10
        ),
    )
    # All three should differ: the imperfect record penalizes different weights differently.
    scores = {base.health_score, validity_heavy.health_score, completeness_heavy.health_score}
    assert len(scores) == 3, scores


def test_custom_thresholds_change_status():
    # Give records that score ~0.85 so we can move the healthy bar to demote them.
    records = [GOOD] * 4 + [{**GOOD, "price": None, "rating": None, "review_count": None, "availability": None}]
    base = compute_health(records, expected=5)
    assert base.status in ("warning", "healthy")  # actual score is around 0.87
    strict = compute_health(
        records,
        expected=5,
        thresholds=HealthThresholds(healthy=0.99, warning=0.98),
    )
    assert strict.status == "repair_required"


def test_legacy_scalar_thresholds_still_work():
    records = [GOOD] * 5
    report = compute_health(records, expected=5, healthy_threshold=0.5, warning_threshold=0.4)
    assert report.status == "healthy"
    assert report.thresholds["healthy"] == 0.5


# ---- null-spike detection --------------------------------------------------


def test_null_rates_computed_per_field():
    records = [GOOD, {**GOOD, "price": None}, {**GOOD, "price": None}]
    rates = compute_null_rates(records)
    assert rates["price"] == pytest.approx(2 / 3)
    assert rates["product_name"] == 0.0


def test_null_spike_signal_fires_when_baseline_exceeded():
    records = [{**GOOD, "price": None} for _ in range(5)]
    baseline = {"price": 0.0}
    report = compute_health(records, expected=5, baseline_null_rates=baseline)
    assert any(s.startswith("null_spike:price=") for s in report.failure_signals)


def test_null_spike_signal_silent_when_within_delta():
    records = [{**GOOD, "price": None if i < 1 else 8999.0} for i in range(5)]
    baseline = {"price": 0.0}
    report = compute_health(records, expected=5, baseline_null_rates=baseline)
    # 20% null on a baseline of 0% → below default 0.30 delta → no signal
    assert not any(s.startswith("null_spike:") for s in report.failure_signals)


# ---- semantic anomalies ----------------------------------------------------


def test_identical_prices_signal():
    records = [{**GOOD, "product_name": f"P{i}"} for i in range(5)]  # all price=8999
    report = compute_health(records, expected=5)
    assert any(s.startswith("identical_prices:") for s in report.failure_signals)


def test_identical_urls_signal():
    records = [{**GOOD, "product_name": f"P{i}", "product_url": "http://x.com/p/1"} for i in range(5)]
    report = compute_health(records, expected=5)
    assert "identical_product_urls" in report.failure_signals


def test_no_identical_anomaly_when_diverse():
    records = [
        {**GOOD, "product_name": f"P{i}", "price": 100.0 + i, "product_url": f"http://x.com/p/{i}"}
        for i in range(5)
    ]
    report = compute_health(records, expected=5)
    assert not any(s.startswith("identical_") for s in report.failure_signals)


# ---- healthy vs degraded end-to-end ---------------------------------------


def test_healthy_records_yield_healthy_status_and_no_signals():
    records = [{**GOOD, "product_name": f"P{i}", "price": 100.0 + i, "product_url": f"http://x.com/p/{i}"} for i in range(10)]
    report = compute_health(records, expected=10)
    assert report.status == "healthy"
    assert report.failure_signals == []
    assert report.health_score >= 0.90
    for f, rate in report.null_rates.items():
        assert rate == 0.0, f


def test_degraded_records_yield_repair_required_and_specific_signals():
    # 10 records where price is dropped in 9 of them → widely broken + low completeness
    records = [
        {**GOOD, "product_name": f"P{i}", "product_url": f"http://x.com/p/{i}",
         "price": None if i > 0 else 100.0}
        for i in range(10)
    ]
    report = compute_health(records, expected=10)
    assert report.status == "repair_required"
    assert any(s.startswith("field_widely_broken:price") for s in report.failure_signals)


def test_string_numeric_fields_do_not_crash_validator():
    """Regression guard: strings in numeric fields must not crash comparisons.

    A prior version of `review_count_non_negative` did `rc < 0` directly and
    exploded with TypeError when the extractor happened to leave `review_count`
    as a string. That crash is worse than a validation failure — it prevents
    us from even scoring the run.
    """
    dirty = [
        {**GOOD, "product_name": f"P{i}",
         "product_url": f"http://x.com/p/{i}",
         "review_count": str(1000 + i),   # string instead of int
         "price": "cheap",                # string instead of float
         "rating": "five stars"}          # string instead of float
        for i in range(5)
    ]
    # Must not raise:
    report = compute_health(dirty, expected=5)
    assert report.status in ("warning", "repair_required")
    assert "wrong_type" in report.violations_by_code


def test_report_carries_weights_and_thresholds_used():
    records = [GOOD] * 3
    weights = HealthWeights(completeness=0.4, validity=0.3, schema_consistency=0.2, record_consistency=0.1)
    thresholds = HealthThresholds(healthy=0.85, warning=0.5, field_broken_frac=0.7)
    report = compute_health(records, expected=3, weights=weights, thresholds=thresholds)
    assert report.weights["completeness"] == 0.4
    assert report.thresholds["field_broken_frac"] == 0.7
