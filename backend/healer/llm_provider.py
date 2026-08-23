"""LLM-based candidate provider (pluggable, optional).

The core repair loop never trusts an LLM's answer. An LLM can *only* propose
`Candidate`s; those candidates must pass exactly the same test → score → gate
pipeline as heuristic ones. This module defines the contract and a stub
implementation so a real LLM can be plugged in later without touching the
deployment path.

Contract:

    class CandidateProvider(Protocol):
        def propose(
            self, *, field: str, html: str, strategy, broken_examples: list[dict]
        ) -> list[Candidate]:
            ...

Any provider returning garbage (nonsense selectors, wrong elements, hallucinated
`data-*` attributes) is *safe* because:

  1. Every candidate is executed against the actual HTML — hallucinations that
     don't match return zero records.
  2. Zero records → per-field score components collapse to 0.
  3. `min_accept` (default 0.60) rejects candidates whose score < threshold.
  4. Even at the strategy level, a shadow run against the same HTML must pass
     the healthy threshold before promotion.

So: an adversarial LLM can waste some compute but cannot deploy a bad repair.
"""

from __future__ import annotations

from typing import Iterable, Protocol

from backend.healer.candidates import Candidate
from backend.scraper.strategy import FieldSelector, Strategy


class CandidateProvider(Protocol):
    """Any object that can produce candidates for a broken field."""

    def propose(
        self,
        *,
        field: str,
        html: str,
        strategy: Strategy,
        broken_examples: list[dict] | None = None,
    ) -> list[Candidate]: ...


class NullProvider:
    """No-op provider used when no LLM is configured. Returns nothing."""

    def propose(self, *, field, html, strategy, broken_examples=None):  # noqa: D401
        return []


class StaticProvider:
    """Provider whose candidates come from a hard-coded map — used for tests."""

    def __init__(self, candidates_by_field: dict[str, list[FieldSelector]]):
        self._map = candidates_by_field

    def propose(self, *, field, html, strategy, broken_examples=None) -> list[Candidate]:
        return [
            Candidate(
                field=field,
                selector=sel,
                source="llm",
                rationale="proposed by external LLM (or LLM-shaped provider)",
            )
            for sel in self._map.get(field, [])
        ]
