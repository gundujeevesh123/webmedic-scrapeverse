"""Validator + health-score tests."""

import json
from pathlib import Path

from backend.scraper.extract import extract
from backend.scraper.strategy import DEFAULT_STRATEGY
from backend.validator.health import compute_health
from backend.validator.rules import (
    RuleViolation,
    currency_iso_shape,
    name_not_ui_label,
    price_plausible,
    rating_in_range,
    required_fields_present,
    types_match,
    urls_valid,
    validate_record,
)


GOOD_RECORD = {
    "product_name": "Aurora Wireless Headphones",
    "price": 8999.0,
    "currency": "INR",
    "rating": 4.5,
    "review_count": 1284,
    "availability": "In Stock",
    "product_url": "http://127.0.0.1:8765/p/MK-1001",
    "image_url": "http://127.0.0.1:8765/img/MK-1001.jpg",
}


# ---- individual rules -----------------------------------------------------


def test_good_record_passes_all_rules():
    assert validate_record(GOOD_RECORD) == []


def test_required_fields_flag_missing():
    bad = dict(GOOD_RECORD, price=None)
    violations = required_fields_present(bad)
    assert any(v.field == "price" and v.code == "missing" for v in violations)


def test_types_flag_wrong_type():
    bad = dict(GOOD_RECORD, review_count="1284")  # string instead of int
    violations = types_match(bad)
    assert any(v.code == "wrong_type" for v in violations)


def test_price_negative_flagged():
    assert price_plausible(dict(GOOD_RECORD, price=-1))[0].code == "negative"


def test_rating_out_of_range_flagged():
    assert rating_in_range(dict(GOOD_RECORD, rating=6.0))[0].code == "out_of_range"


def test_urls_missing_scheme_flagged():
    violations = urls_valid(dict(GOOD_RECORD, product_url="/relative"))
    assert any(v.code == "invalid_url" for v in violations)


def test_name_ui_label_flagged():
    violations = name_not_ui_label(dict(GOOD_RECORD, product_name="Add to cart"))
    assert violations and violations[0].code == "looks_like_ui"


def test_currency_iso_shape_flagged():
    violations = currency_iso_shape(dict(GOOD_RECORD, currency="inr"))
    assert violations and violations[0].code == "not_iso4217"


# ---- health report on full runs ------------------------------------------


FIXTURES = Path(__file__).resolve().parents[1] / "fixtures"


def _extract_page(v: str, page: int = 1):
    html = (FIXTURES / "pages" / v / f"page-{page}.html").read_text()
    return extract(html, DEFAULT_STRATEGY, url="http://127.0.0.1:8765/list?page=1")


def test_health_v1_healthy_is_healthy():
    r = _extract_page("v1_healthy")
    report = compute_health(r.records, expected=10, strategy_name=r.strategy_name)
    assert report.status == "healthy"
    assert report.health_score >= 0.95


def test_health_v2_rename_triggers_repair():
    r = _extract_page("v2_rename_class")
    report = compute_health(r.records, expected=10, strategy_name=r.strategy_name)
    # Baseline extractor loses price on this fixture — health should drop.
    assert report.status == "repair_required"
    assert any("price" in s or "field_widely_broken" in s for s in report.failure_signals)


def test_health_v3_dataattr_triggers_repair():
    r = _extract_page("v3_dataattr")
    report = compute_health(r.records, expected=10, strategy_name=r.strategy_name)
    assert report.status == "repair_required"


def test_health_record_count_collapse_signal():
    report = compute_health([GOOD_RECORD], expected=10)
    assert any(s.startswith("record_count_collapse") for s in report.failure_signals)


def test_health_no_records_signal():
    report = compute_health([], expected=10)
    assert "no_records_extracted" in report.failure_signals
    assert report.status == "repair_required"
