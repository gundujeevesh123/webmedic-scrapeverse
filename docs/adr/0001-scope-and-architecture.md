# ADR-0001 — Scope and architecture

- Status: Accepted
- Date: 2026-08-19
- Deciders: WebMedic team

## Context

The hackathon prompt asks for a *self-healing* scraper. The guide (§5, §11, §20)
warns against building a large system whose self-healing behavior cannot be
demonstrated. Judges must see the failure-to-recovery loop in under two
minutes.

## Decision

1. **One domain, one schema.** E-commerce product listings (guide §6). Fixed
   schema: `product_name, price, currency, rating, review_count, availability,
   product_url, image_url`.
2. **Deterministic-first architecture.** Four layers per guide §5: Acquisition,
   Extraction, Validation, Recovery. The LLM (or LLM-shaped reasoner) may
   propose candidates; a deterministic validator decides deployment.
3. **Controlled-breakage fixtures + golden dataset.** All demos and benchmarks
   ship with 11 fixture versions of the same catalog (guide §10). The demo
   does *not* depend on any live website.
4. **Bright Data adapter with two providers.** Local fixture provider is the
   default; the Bright Data Web Unlocker / Scraper Studio provider is gated
   by env vars so the repo runs on a clean clone without credentials.
5. **Simple stack, one process.** FastAPI + SQLite + Beautiful Soup +
   vanilla-JS dashboard. No microservices, no queue, no orchestrator (guide §20).
6. **Every repair is versioned.** `scraper_versions` and `repair_events` tables
   per guide §13. Rollback is a single database write.

## Consequences

- Smallest system that convincingly proves self-healing.
- No custom LLM training; the reasoner is a heuristic module with an optional
  LLM plug-in.
- Bright Data is meaningfully integrated (guide requirement) but never a
  single point of failure for the demo.
- Fixtures are versioned in-repo so contributors can reproduce every benchmark
  bit-for-bit.
