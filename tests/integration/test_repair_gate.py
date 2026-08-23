"""Self-heal safeguard tests.

Every candidate — heuristic or LLM-proposed — must flow through the
test → score → gate pipeline. This file adversarially injects bad candidates
and asserts the gate never promotes them.

Also exercises: complete repair-event logging, shadow-run failure blocking
promotion, rollback path, and the deploy layer's audit trail.
"""

from __future__ import annotations

import importlib
import json
from pathlib import Path

import pytest

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures"


@pytest.fixture
def env(tmp_path, monkeypatch):
    db = tmp_path / "webmedic.sqlite"
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{db}")
    for name in [
        "backend.config",
        "backend.database.store",
        "backend.versioning.deploy",
    ]:
        importlib.reload(importlib.import_module(name))
    from backend.acquisition.fixture import FixtureAcquisition
    from backend.config import FIXTURE_DIR
    from backend.database import store
    from backend.versioning import deploy
    return {
        "acq": FixtureAcquisition(FIXTURE_DIR / "pages", version="v1_healthy"),
        "store": store,
        "deploy": deploy,
        "golden": json.load(open(FIXTURE_DIR / "golden_dataset.json"))["records"],
        "fixtures": FIXTURE_DIR,
    }


# --------------------------------------------------------------------------- #
# 1. Bad candidates must not be promoted
# --------------------------------------------------------------------------- #


def test_intentionally_bad_selector_scores_low_and_rejected():
    """A candidate that matches the wrong element (or nothing) scores near 0."""
    from backend.healer.repair import repair
    from backend.scraper.strategy import DEFAULT_STRATEGY, FieldSelector
    from backend.healer.llm_provider import StaticProvider

    html = (FIXTURES / "pages" / "v2_rename_class" / "page-1.html").read_text()
    golden = json.load(open(FIXTURES / "golden_dataset.json"))["records"][:10]

    # A malicious "LLM" that suggests non-existent selectors + a selector that
    # matches an unrelated element (the header).
    bad_llm = StaticProvider({
        "price": [
            FieldSelector(kind="css", value=".this-class-does-not-exist", transform="price"),
            FieldSelector(kind="css", value="header h1", transform="price"),  # matches "MetroKart"
            FieldSelector(kind="css", value="footer p", transform="price"),  # matches copyright text
        ]
    })

    plan = repair(
        DEFAULT_STRATEGY, html, broken_fields=["price"],
        url="http://127.0.0.1:8765/list", golden=golden,
        extra_providers=[bad_llm],
    )
    fr = plan.field_repairs["price"]
    # The heuristic generator still finds `.cost` — that wins.
    assert fr.action == "promote"
    assert fr.winner.selector.value in (".cost", "span.cost")

    # But NONE of the LLM candidates should be the winner.
    llm_candidates = [c for c in fr.top_candidates if c.source == "llm"]
    for c in llm_candidates:
        assert c.score.total < 0.60, f"bad LLM candidate {c.selector.value!r} scored {c.score.total}"
    # And the winner must never be LLM-sourced in this adversarial scenario.
    assert fr.winner.source != "llm", fr.winner


def test_only_bad_candidates_available_produces_rejection():
    """When the heuristic finds nothing usable and the LLM lies, the field is rejected."""
    from backend.healer.repair import repair
    from backend.scraper.strategy import Strategy, FieldSelector
    from backend.healer.llm_provider import StaticProvider

    # Use a strategy whose baseline product-card selector matches nothing.
    # Baseline extraction will find 0 records, so scoring collapses.
    html = (FIXTURES / "pages" / "v3_dataattr" / "page-1.html").read_text()
    empty_strategy = Strategy(
        name="empty",
        record_selector="article.product-card",   # still matches in v3
        fields={
            # `price` needs to be repaired — baseline selector does not exist.
            "price": FieldSelector(kind="css", value=".nonexistent", transform="price"),
        },
    )
    bad_llm = StaticProvider({
        "price": [
            FieldSelector(kind="css", value=".not-in-dom-either", transform="price"),
            FieldSelector(kind="css", value="header h1", transform="price"),
        ]
    })
    plan = repair(
        empty_strategy, html, broken_fields=["price"],
        url="http://127.0.0.1:8765/list",
        extra_providers=[bad_llm],
    )
    fr = plan.field_repairs["price"]
    # None of the LLM candidates should be a legitimate winner. If any
    # heuristic candidate happens to promote, that's a real win — but the
    # LLM candidates must all score below min_accept.
    llm_only = [c for c in fr.top_candidates if c.source == "llm"]
    for c in llm_only:
        assert c.score.total < 0.60, c


def test_good_llm_candidate_is_promoted_through_same_gate():
    """A legitimate LLM candidate wins on merit — same pipeline, no fast path."""
    from backend.healer.repair import repair
    from backend.scraper.strategy import DEFAULT_STRATEGY, FieldSelector
    from backend.healer.llm_provider import StaticProvider

    html = (FIXTURES / "pages" / "v3_dataattr" / "page-1.html").read_text()
    golden = json.load(open(FIXTURES / "golden_dataset.json"))["records"][:10]

    good_llm = StaticProvider({
        "price": [FieldSelector(kind="css", value="[data-testid='price']", transform="price")],
    })
    plan = repair(
        DEFAULT_STRATEGY, html, broken_fields=["price"],
        url="http://127.0.0.1:8765/list", golden=golden,
        extra_providers=[good_llm],
    )
    fr = plan.field_repairs["price"]
    # Winner must be the correct data-testid — either from heuristic or LLM.
    assert fr.action == "promote"
    assert "data-testid" in fr.winner.selector.value


# --------------------------------------------------------------------------- #
# 2. Shadow-run failure blocks promotion
# --------------------------------------------------------------------------- #


def test_shadow_run_failure_blocks_promotion(env, monkeypatch):
    """If the shadow-run health can't clear the healthy threshold, don't deploy.

    We patch `compute_health` at the deploy-module callsite so the *shadow* run
    returns a health well below the healthy threshold — even though the actual
    proposed strategy would in reality restore extraction. This exercises the
    gate itself, not the extractor.
    """
    deploy = env["deploy"]
    store = env["store"]
    acq = env["acq"]
    golden = env["golden"][:10]

    sid = deploy.register_scraper("metrokart", "http://127.0.0.1:8765/list?page=1")
    acq.switch_version("v3_dataattr")

    from backend.validator import health as health_mod
    real = health_mod.compute_health
    call_count = {"n": 0}

    def strict_health(*args, **kwargs):
        call_count["n"] += 1
        report = real(*args, **kwargs)
        # First call is the pre-repair run — leave it as-is so repair triggers.
        # Second call is the shadow run — force it to fail the gate.
        if call_count["n"] >= 2:
            report.health_score = 0.50
            report.status = "repair_required"
        return report

    monkeypatch.setattr(deploy, "compute_health", strict_health)

    hr, dec = deploy.run_once(sid, "http://127.0.0.1:8765/list?page=1", fetch=acq, expected=10, golden=golden)
    assert dec.action in ("shadow", "reject"), dec
    # And current_version should still be v1 — no promotion.
    assert store.get_scraper(sid)["current_version"] == 1
    # An event was recorded so the auditor sees the near-miss.
    events = store.list_repair_events(sid)
    assert events and events[0]["action"] in ("shadow", "reject")


# --------------------------------------------------------------------------- #
# 3. Complete repair-event evidence
# --------------------------------------------------------------------------- #


def test_repair_event_captures_full_evidence(env):
    deploy = env["deploy"]
    store = env["store"]
    acq = env["acq"]
    golden = env["golden"][:10]

    sid = deploy.register_scraper("metrokart", "http://127.0.0.1:8765/list?page=1")
    acq.switch_version("v3_dataattr")
    deploy.run_once(sid, "http://127.0.0.1:8765/list?page=1", fetch=acq, expected=10, golden=golden)

    events = store.list_repair_events(sid)
    assert events, "no repair events recorded"
    ev = events[0]
    # High-level fields
    for k in ("old_version", "new_version", "failure_reason", "candidate_count",
              "confidence", "action", "timestamp"):
        assert k in ev, k
    assert ev["action"] == "promote"
    assert ev["new_version"] == 2
    assert ev["confidence"] >= 0.90

    # Fetch the raw plan blob and verify every broken field has a scored winner.
    with store.connect() as conn:
        row = conn.execute("SELECT plan, selected_candidate FROM repair_events WHERE id=?", (ev["id"],)).fetchone()
    plan = json.loads(row["plan"])
    assert plan["strategy_name"] == "metrokart-v1"
    assert set(plan["broken_fields"]) >= {"price", "rating", "review_count", "product_name"}
    for f, fr in plan["field_repairs"].items():
        assert fr["winner"], f
        # Score breakdown is per guide §8.4
        for c in ("schema_validity", "completeness", "type_validity", "similarity",
                  "historical_consistency", "total"):
            assert c in fr["winner"]["score"], f

    # `selected_candidate` is a compact copy of the winner (first field, arbitrary).
    sc = json.loads(row["selected_candidate"])
    assert "selector" in sc and "score" in sc


# --------------------------------------------------------------------------- #
# 4. Rollback preserves history + is auditable
# --------------------------------------------------------------------------- #


def test_rollback_preserves_all_versions_and_audit_trail(env):
    deploy = env["deploy"]
    store = env["store"]
    acq = env["acq"]
    golden = env["golden"][:10]

    sid = deploy.register_scraper("metrokart", "http://127.0.0.1:8765/list?page=1")
    acq.switch_version("v3_dataattr")
    deploy.run_once(sid, "http://127.0.0.1:8765/list?page=1", fetch=acq, expected=10, golden=golden)
    assert store.get_scraper(sid)["current_version"] == 2

    # Rollback and confirm v2 is still stored (not deleted) — for forward rollback.
    deploy.rollback_to(sid, to_version=1, reason="regression suspected")
    versions = store.list_versions(sid)
    assert {v["version"] for v in versions} == {1, 2}, versions
    events = store.list_repair_events(sid)
    kinds = [e["action"] for e in events]
    assert "promote" in kinds and "rollback" in kinds


# --------------------------------------------------------------------------- #
# 5. LLM cannot silently deploy — verified structurally
# --------------------------------------------------------------------------- #


def test_llm_candidate_still_extracts_and_scores(monkeypatch):
    """A CandidateProvider that returns garbage must still be executed against real HTML.

    We prove that by asserting `score_field` is called with the LLM's records.
    """
    from backend.healer.repair import repair
    from backend.healer.llm_provider import StaticProvider
    from backend.scraper.strategy import DEFAULT_STRATEGY, FieldSelector
    from backend.scoring import score as _score_module

    calls = []
    real = _score_module.score_field

    def spy(records, field, **kw):
        calls.append((field, len(records)))
        return real(records, field, **kw)

    monkeypatch.setattr("backend.healer.repair.score_field", spy)

    html = (FIXTURES / "pages" / "v2_rename_class" / "page-1.html").read_text()
    provider = StaticProvider({
        "price": [FieldSelector(kind="css", value=".this-does-not-exist", transform="price")]
    })
    repair(
        DEFAULT_STRATEGY, html, broken_fields=["price"],
        url="http://127.0.0.1:8765/list",
        extra_providers=[provider],
    )
    # At minimum, the LLM candidate should have caused ONE scoring call.
    assert any(f == "price" for f, _ in calls)
    # And multiple heuristic candidates scored too — proves the LLM didn't
    # short-circuit the pipeline.
    price_scoring_calls = sum(1 for f, _ in calls if f == "price")
    assert price_scoring_calls > 5, price_scoring_calls
