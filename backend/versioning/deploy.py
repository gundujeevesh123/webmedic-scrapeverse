"""Safe-deployment orchestrator.

Ties extraction → validation → healing → versioning together with the
shadow-run + confidence gate from guide §8.5 and §9.

Call flow:

    run_once(scraper_id, url, expected)      # fresh extraction on current version
        └─ if healthy → persist & return
        └─ if repair_required → attempt_repair()

    attempt_repair(...)
        1. Generate candidates for every broken field.
        2. Score them.
        3. If proposed_confidence >= promote_threshold AND shadow run stays
           healthy, promote the new version to production.
        4. Otherwise record a `shadow` repair event and keep the current version.

    rollback(scraper_id, to_version, reason)
        Reverts current_version and records the rollback in `repair_events`.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Callable, Optional

from backend.config import settings
from backend.database import store
from backend.healer.repair import RepairPlan, broken_fields_from_health, repair
from backend.scraper.extract import extract
from backend.scraper.strategy import DEFAULT_STRATEGY, Strategy
from backend.validator.health import HealthReport, compute_health


Fetcher = Callable[[str], str]


@dataclass
class DeploymentDecision:
    scraper_id: int
    old_version: int
    new_version: Optional[int]
    action: str            # "no_change" | "promote" | "shadow" | "reject" | "rollback"
    reason: str
    confidence: float
    pre_health: float
    post_health: Optional[float]


def register_scraper(
    name: str,
    target_url: str,
    strategy: Strategy = DEFAULT_STRATEGY,
    reason: str = "initial registration",
) -> int:
    """Register a scraper and its v1 strategy. Idempotent."""
    store.init_db()
    schema_fields = list(strategy.fields.keys())
    scraper_id = store.upsert_scraper(name, target_url, schema_fields)
    versions = store.list_versions(scraper_id)
    if not versions:
        store.add_version(scraper_id, strategy, reason=reason, confidence=1.0)
        store.set_current_version(scraper_id, 1)
    return scraper_id


def get_active_strategy(scraper_id: int) -> Strategy:
    scraper = store.get_scraper(scraper_id)
    if not scraper:
        raise LookupError(f"scraper {scraper_id} not registered")
    strategy = store.get_version(scraper_id, scraper["current_version"])
    if strategy is None:
        raise LookupError(
            f"missing version {scraper['current_version']} for scraper {scraper_id}"
        )
    return strategy


def run_once(
    scraper_id: int,
    url: str,
    fetch: Fetcher,
    expected: Optional[int] = None,
    golden: Optional[list[dict]] = None,
) -> tuple[HealthReport, DeploymentDecision]:
    """One end-to-end cycle: fetch → extract → validate → maybe heal."""
    scraper = store.get_scraper(scraper_id)
    if scraper is None:
        raise LookupError(f"scraper {scraper_id} not registered")
    current_version = int(scraper["current_version"])
    strategy = store.get_version(scraper_id, current_version)

    html = fetch(url)
    result = extract(html, strategy, url=url)
    hr = compute_health(
        result.records,
        expected=expected,
        strategy_name=strategy.name,
        healthy_threshold=settings.healthy_threshold,
        warning_threshold=settings.warning_threshold,
    )
    store.set_health(scraper_id, hr.health_score, hr.status)
    store.record_run(
        scraper_id=scraper_id,
        version=current_version,
        records_expected=expected,
        records_received=hr.records_received,
        health_score=hr.health_score,
        status=hr.status,
        signals=hr.failure_signals,
        full_report=hr.to_dict(),
    )

    if hr.status == "healthy":
        return hr, DeploymentDecision(
            scraper_id=scraper_id,
            old_version=current_version,
            new_version=current_version,
            action="no_change",
            reason="already healthy",
            confidence=1.0,
            pre_health=hr.health_score,
            post_health=hr.health_score,
        )

    return hr, attempt_repair(
        scraper_id=scraper_id,
        url=url,
        html=html,
        current_health=hr,
        golden=golden,
        expected=expected,
    )


def attempt_repair(
    scraper_id: int,
    url: str,
    html: str,
    current_health: HealthReport,
    golden: Optional[list[dict]] = None,
    expected: Optional[int] = None,
) -> DeploymentDecision:
    """Given a broken run, generate → shadow-run → gate → deploy."""

    scraper = store.get_scraper(scraper_id)
    old_version = int(scraper["current_version"])
    active = store.get_version(scraper_id, old_version)

    broken = broken_fields_from_health(current_health)
    plan: RepairPlan = repair(
        strategy=active,
        html=html,
        broken_fields=broken,
        url=url,
        golden=golden,
    )

    if plan.proposed_strategy is None:
        store.record_repair_event(
            scraper_id=scraper_id,
            old_version=old_version,
            new_version=None,
            failure_reason=",".join(current_health.failure_signals) or "unknown",
            candidate_count=sum(len(fr.top_candidates) for fr in plan.field_repairs.values()),
            selected_candidate=None,
            confidence=0.0,
            plan=plan.to_dict(),
            action="reject",
        )
        return DeploymentDecision(
            scraper_id=scraper_id,
            old_version=old_version,
            new_version=None,
            action="reject",
            reason="no viable candidates",
            confidence=0.0,
            pre_health=current_health.health_score,
            post_health=None,
        )

    # Shadow-run the proposed strategy on the same HTML we already have.
    shadow_result = extract(html, plan.proposed_strategy, url=url)
    shadow_health = compute_health(
        shadow_result.records,
        expected=expected,
        strategy_name=plan.proposed_strategy.name,
        healthy_threshold=settings.healthy_threshold,
        warning_threshold=settings.warning_threshold,
    )

    all_promote = plan.field_repairs and all(
        fr.action == "promote" for fr in plan.field_repairs.values()
    )
    passes_shadow_gate = (
        shadow_health.status == "healthy"
        and shadow_health.health_score >= settings.healthy_threshold
    )

    if all_promote and passes_shadow_gate:
        new_version = store.add_version(
            scraper_id=scraper_id,
            strategy=plan.proposed_strategy,
            reason=",".join(current_health.failure_signals) or "self-heal",
            confidence=plan.proposed_confidence,
        )
        store.set_current_version(scraper_id, new_version)
        store.set_health(scraper_id, shadow_health.health_score, shadow_health.status)
        winning = next(
            (fr.winner.to_dict() for fr in plan.field_repairs.values() if fr.winner),
            None,
        )
        store.record_repair_event(
            scraper_id=scraper_id,
            old_version=old_version,
            new_version=new_version,
            failure_reason=",".join(current_health.failure_signals) or "unknown",
            candidate_count=sum(len(fr.top_candidates) for fr in plan.field_repairs.values()),
            selected_candidate=winning,
            confidence=plan.proposed_confidence,
            plan=plan.to_dict(),
            action="promote",
        )
        return DeploymentDecision(
            scraper_id=scraper_id,
            old_version=old_version,
            new_version=new_version,
            action="promote",
            reason=f"shadow health {shadow_health.health_score:.2f} passes gate",
            confidence=plan.proposed_confidence,
            pre_health=current_health.health_score,
            post_health=shadow_health.health_score,
        )

    # Otherwise the proposal enters shadow-only mode: recorded but not promoted.
    store.record_repair_event(
        scraper_id=scraper_id,
        old_version=old_version,
        new_version=None,
        failure_reason=",".join(current_health.failure_signals) or "unknown",
        candidate_count=sum(len(fr.top_candidates) for fr in plan.field_repairs.values()),
        selected_candidate=None,
        confidence=plan.proposed_confidence,
        plan=plan.to_dict(),
        action="shadow",
    )
    return DeploymentDecision(
        scraper_id=scraper_id,
        old_version=old_version,
        new_version=None,
        action="shadow",
        reason=(
            f"shadow health {shadow_health.health_score:.2f}"
            f" — not promoted (needs >= {settings.healthy_threshold})"
        ),
        confidence=plan.proposed_confidence,
        pre_health=current_health.health_score,
        post_health=shadow_health.health_score,
    )


def rollback_to(scraper_id: int, to_version: int, reason: str = "manual rollback") -> DeploymentDecision:
    scraper = store.get_scraper(scraper_id)
    old_version = int(scraper["current_version"])
    if store.get_version(scraper_id, to_version) is None:
        raise LookupError(f"no such version {to_version} for scraper {scraper_id}")
    store.rollback(scraper_id, to_version, reason)
    return DeploymentDecision(
        scraper_id=scraper_id,
        old_version=old_version,
        new_version=to_version,
        action="rollback",
        reason=reason,
        confidence=1.0,
        pre_health=float(store.get_scraper(scraper_id)["health_score"]),
        post_health=None,
    )
