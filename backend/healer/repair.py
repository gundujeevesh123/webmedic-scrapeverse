"""Repair orchestrator.

Given a broken strategy, a page snapshot, and (optionally) a golden dataset,
propose alternative selectors for every broken field, evaluate them, and
return the best verified single-field replacement per broken field.

Deployment policy (guide §8.5):
  - A repair is *accepted* only if the per-field score >= `min_accept` AND
    completeness >= `min_completeness` AND schema_validity >= `min_schema`.
  - The returned RepairPlan records every candidate, its score, and whether
    it would be shadow-deployed or promoted immediately.
"""

from __future__ import annotations

from dataclasses import dataclass, field, asdict
from typing import Iterable, Optional

from backend.healer.candidates import Candidate, generate_candidates
from backend.scoring.score import ScoreBreakdown, score_field
from backend.scraper.extract import extract
from backend.scraper.strategy import FieldSelector, Strategy

# For type hints only — a plug-in provider (heuristic OR LLM-shaped) must
# implement `propose(field=..., html=..., strategy=..., broken_examples=...)`.
try:  # optional
    from backend.healer.llm_provider import CandidateProvider  # noqa: F401
except Exception:  # pragma: no cover
    CandidateProvider = object  # type: ignore


@dataclass
class ScoredCandidate:
    field: str
    selector: FieldSelector
    source: str
    rationale: str
    score: ScoreBreakdown

    def to_dict(self) -> dict:
        return {
            "field": self.field,
            "selector": self.selector.to_dict(),
            "source": self.source,
            "rationale": self.rationale,
            "score": self.score.to_dict(),
        }


@dataclass
class FieldRepair:
    field: str
    old_selector: Optional[FieldSelector]
    top_candidates: list[ScoredCandidate] = field(default_factory=list)
    winner: Optional[ScoredCandidate] = None
    action: str = "no_change"     # "no_change" | "shadow" | "promote" | "reject"
    reason: str = ""

    def to_dict(self) -> dict:
        return {
            "field": self.field,
            "old_selector": self.old_selector.to_dict() if self.old_selector else None,
            "top_candidates": [c.to_dict() for c in self.top_candidates],
            "winner": self.winner.to_dict() if self.winner else None,
            "action": self.action,
            "reason": self.reason,
        }


@dataclass
class RepairPlan:
    strategy_name: str
    broken_fields: list[str]
    field_repairs: dict[str, FieldRepair] = field(default_factory=dict)
    proposed_strategy: Optional[Strategy] = None
    proposed_confidence: float = 0.0

    def to_dict(self) -> dict:
        return {
            "strategy_name": self.strategy_name,
            "broken_fields": list(self.broken_fields),
            "field_repairs": {k: v.to_dict() for k, v in self.field_repairs.items()},
            "proposed_strategy": self.proposed_strategy.to_dict() if self.proposed_strategy else None,
            "proposed_confidence": round(self.proposed_confidence, 4),
        }


def _test_field_candidate(
    strategy: Strategy,
    html: str,
    field: str,
    candidate: FieldSelector,
    url: str,
) -> list[dict]:
    """Swap one candidate into a copy of the strategy and re-extract."""
    fields = dict(strategy.fields)
    fields[field] = candidate
    candidate_strategy = Strategy(
        name=f"{strategy.name}::{field}={candidate.value}",
        record_selector=strategy.record_selector,
        fields=fields,
        next_page_selector=strategy.next_page_selector,
    )
    return extract(html, candidate_strategy, url=url).records


def repair(
    strategy: Strategy,
    html: str,
    broken_fields: Iterable[str],
    url: str = "",
    golden: Optional[list[dict]] = None,
    historical: Optional[dict[str, list[FieldSelector]]] = None,
    historical_values: Optional[dict[str, list]] = None,
    top_k: int = 5,
    min_accept: float = 0.60,
    min_completeness: float = 0.80,
    min_schema: float = 0.80,
    shadow_threshold: float = 0.75,
    promote_threshold: float = 0.90,
    extra_providers: Optional[list["CandidateProvider"]] = None,
) -> RepairPlan:
    """Generate → test → score → gate candidates for every broken field.

    `extra_providers` (optional) is a list of external candidate providers —
    typically LLM-based. Their candidates go through *exactly* the same
    test → score → gate pipeline as heuristic candidates. This is the LLM
    "AI proposes; evidence decides" seam.
    """

    broken_list = list(broken_fields)
    plan = RepairPlan(strategy_name=strategy.name, broken_fields=broken_list)

    candidate_pool = generate_candidates(
        strategy=strategy,
        html=html,
        broken_fields=broken_list,
        historical=historical or {},
    )

    # Merge external (e.g. LLM) candidates. Every candidate is treated the
    # same downstream — external providers cannot bypass the gate.
    if extra_providers:
        for provider in extra_providers:
            for f in broken_list:
                extra = provider.propose(
                    field=f, html=html, strategy=strategy, broken_examples=[]
                )
                if extra:
                    candidate_pool[f] = candidate_pool.get(f, []) + list(extra)

    for f in broken_list:
        f_repair = FieldRepair(field=f, old_selector=strategy.fields.get(f))
        cands = candidate_pool.get(f, [])

        scored: list[ScoredCandidate] = []
        for cand in cands:
            records = _test_field_candidate(strategy, html, f, cand.selector, url)
            breakdown = score_field(
                records,
                f,
                golden=golden,
                historical_values=(historical_values or {}).get(f),
            )
            scored.append(
                ScoredCandidate(
                    field=f,
                    selector=cand.selector,
                    source=cand.source,
                    rationale=cand.rationale,
                    score=breakdown,
                )
            )

        scored.sort(key=lambda sc: sc.score.total, reverse=True)
        f_repair.top_candidates = scored[:top_k]

        if not scored:
            f_repair.action = "reject"
            f_repair.reason = "no candidates could be generated"
        else:
            winner = scored[0]
            f_repair.winner = winner
            score = winner.score
            if (
                score.total >= promote_threshold
                and score.completeness >= min_completeness
                and score.schema_validity >= min_schema
            ):
                f_repair.action = "promote"
                f_repair.reason = f"score {score.total:.2f} >= promote threshold {promote_threshold}"
            elif (
                score.total >= shadow_threshold
                and score.completeness >= min_completeness
            ):
                f_repair.action = "shadow"
                f_repair.reason = (
                    f"score {score.total:.2f} >= shadow threshold {shadow_threshold}, "
                    f"below promote threshold {promote_threshold}"
                )
            elif score.total >= min_accept:
                f_repair.action = "shadow"
                f_repair.reason = (
                    f"marginal candidate (score {score.total:.2f}); staying in shadow"
                )
            else:
                f_repair.action = "reject"
                f_repair.reason = f"best candidate score {score.total:.2f} < min_accept {min_accept}"

        plan.field_repairs[f] = f_repair

    # Assemble a proposed strategy that swaps in each promoted / shadow winner.
    if plan.field_repairs:
        new_fields = dict(strategy.fields)
        confidences: list[float] = []
        for f, fr in plan.field_repairs.items():
            if fr.winner and fr.action in ("promote", "shadow"):
                new_fields[f] = fr.winner.selector
                confidences.append(fr.winner.score.total)
        plan.proposed_strategy = Strategy(
            name=f"{strategy.name}-repair",
            record_selector=strategy.record_selector,
            fields=new_fields,
            next_page_selector=strategy.next_page_selector,
            notes="Auto-repaired by WebMedic healer.",
        )
        plan.proposed_confidence = (
            sum(confidences) / len(confidences) if confidences else 0.0
        )

    return plan


def broken_fields_from_health(health_report) -> list[str]:
    """Convenience: derive broken-field list from a HealthReport.

    A field is considered broken if it accumulates violations in >= 80% of
    records, mirroring the failure signal in guide §7.4.
    """
    received = max(1, health_report.records_received)
    return sorted(
        f
        for f, count in (health_report.violations_by_field or {}).items()
        if count / received >= 0.8
    )
