"""Repair engine tests."""

import json
from pathlib import Path

from backend.healer.candidates import generate_candidates
from backend.healer.repair import broken_fields_from_health, repair
from backend.scoring.score import score_field
from backend.scraper.extract import extract
from backend.scraper.strategy import DEFAULT_STRATEGY
from backend.validator.health import compute_health

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures"
GOLDEN = json.load(open(FIXTURES / "golden_dataset.json"))["records"]


def _load(v: str, page: int = 1):
    return (FIXTURES / "pages" / v / f"page-{page}.html").read_text()


def test_generator_finds_data_testid_for_v3():
    html = _load("v3_dataattr")
    cands = generate_candidates(DEFAULT_STRATEGY, html, broken_fields=["price"])
    values = [c.selector.value for c in cands["price"]]
    assert any("data-testid='price'" in v for v in values)


def test_generator_finds_class_synonym_for_v2():
    html = _load("v2_rename_class")
    cands = generate_candidates(DEFAULT_STRATEGY, html, broken_fields=["price"])
    values = [c.selector.value for c in cands["price"]]
    assert any(v == ".cost" or v.endswith(".cost") for v in values)


def test_broken_fields_derived_from_health():
    html = _load("v3_dataattr")
    result = extract(html, DEFAULT_STRATEGY, url="http://127.0.0.1:8765/list")
    hr = compute_health(result.records, expected=10)
    broken = broken_fields_from_health(hr)
    for f in ("price", "rating", "review_count", "product_name", "availability", "currency"):
        assert f in broken, f"expected {f} in broken={broken}"


def test_repair_v2_rename_promotes_cost_selector():
    html = _load("v2_rename_class")
    plan = repair(
        DEFAULT_STRATEGY,
        html,
        broken_fields=["price"],
        url="http://127.0.0.1:8765/list",
        golden=GOLDEN[:10],
    )
    fr = plan.field_repairs["price"]
    assert fr.action == "promote"
    assert fr.winner.selector.value in (".cost", "span.cost")


def test_repair_v3_dataattr_promotes_all_six_data_testid():
    html = _load("v3_dataattr")
    result = extract(html, DEFAULT_STRATEGY, url="http://127.0.0.1:8765/list")
    hr = compute_health(result.records, expected=10)
    broken = broken_fields_from_health(hr)
    plan = repair(
        DEFAULT_STRATEGY,
        html,
        broken_fields=broken,
        url="http://127.0.0.1:8765/list",
        golden=GOLDEN[:10],
    )
    promoted = [f for f, fr in plan.field_repairs.items() if fr.action == "promote"]
    assert set(promoted) >= {"price", "rating", "review_count", "availability", "product_name"}


def test_repair_recovers_health_after_swap():
    html = _load("v9_combined")
    result = extract(html, DEFAULT_STRATEGY, url="http://127.0.0.1:8765/list")
    hr = compute_health(result.records, expected=10)
    broken = broken_fields_from_health(hr)
    plan = repair(
        DEFAULT_STRATEGY,
        html,
        broken_fields=broken,
        url="http://127.0.0.1:8765/list",
        golden=GOLDEN[:10],
    )
    r2 = extract(html, plan.proposed_strategy, url="http://127.0.0.1:8765/list")
    hr2 = compute_health(r2.records, expected=10)
    assert hr2.status == "healthy"
    assert hr2.health_score >= 0.95


def test_score_field_awards_completeness_and_types():
    good = [dict(price=10.0), dict(price=20.0), dict(price=None)]
    br = score_field(good, "price")
    assert 0 < br.completeness < 1
    # 2 of 3 records have a valid float price → ~0.6667 (rounded)
    assert abs(br.type_validity - 2 / 3) < 0.001


def test_repair_rejects_when_no_signal():
    # Fake situation: nothing broken → no repair
    html = _load("v1_healthy")
    plan = repair(
        DEFAULT_STRATEGY,
        html,
        broken_fields=[],
        url="http://127.0.0.1:8765/list",
        golden=GOLDEN[:10],
    )
    assert plan.field_repairs == {}
    assert plan.proposed_confidence == 0.0
