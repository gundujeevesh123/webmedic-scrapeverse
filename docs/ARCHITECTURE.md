# ARCHITECTURE — WebMedic Self-Healing Scraper

> Authoritative architecture document. Produced as the Phase-11 audit deliverable.
> Cross-checked against the guide's required conceptual architecture and the
> current repository surface.

---

## 1. Design principles

The guide (§20) explicitly warns against building a large system whose
self-healing behavior cannot be demonstrated. Every decision below is
governed by these five principles:

1. **Smallest system that convincingly proves the story.** One Python
   process, one SQLite file, one HTML dashboard. No microservices, no queue,
   no orchestrator, no ORM.
2. **Deterministic-first.** Extraction, validation, scoring, gating, and
   rollback are all pure or near-pure functions. AI (heuristic reasoner and,
   optionally, an LLM plug-in) only *proposes candidates* — evidence decides.
3. **Reproducible by construction.** Fixtures + golden dataset ship in the
   repo. Bright Data credentials are optional; the acquisition layer falls
   back to fixture mode with a warning so a clean clone runs the whole
   demo/benchmark.
4. **Versioned + rollback-safe.** Every strategy is a row in
   `scraper_versions`; every deployment or rollback is a row in
   `repair_events`. Nothing is destructive.
5. **Testable end-to-end.** 61 tests spanning unit, integration, and e2e —
   including a benchmark test that pins the "self-heal on 4 of 4 broken
   layouts, 0 false repairs" property.

---

## 2. Conceptual pipeline → module map

The guide's required pipeline is reproduced verbatim below with the
implementing module + file:line reference for every stage.

| # | Stage in guide                     | Module                                    | Entry point                                    | Status |
|---|------------------------------------|-------------------------------------------|------------------------------------------------|:-----:|
| 1 | URL Registry                       | `backend/database/store.py`               | `upsert_scraper()`, `scrapers` table            |  ✅   |
| 2 | Bright Data / Scraper Studio       | `backend/acquisition/brightdata.py`       | `BrightDataAcquisition.fetch()` (proxy via httpx) |  ✅   |
| 3 | Raw Page / Snapshot                | `backend/acquisition/base.py`             | `PageSnapshot` dataclass                        |  ✅   |
| 4 | Extraction Engine                  | `backend/scraper/extract.py`              | `extract(html, strategy, url)`                  |  ✅   |
| 5 | Structured Schema                  | `backend/scraper/schema.py`               | `Product` (Pydantic) + `REQUIRED_FIELDS`        |  ✅   |
| 6 | Validation                         | `backend/validator/rules.py`              | 8 pure `Rule`s + `validate_record()`            |  ✅   |
| 7 | Health Score                       | `backend/validator/health.py`             | `compute_health()` → `HealthReport`             |  ✅   |
| 8 | Failure Detection                  | `backend/validator/health.py` + `healer/repair.py` | `_failure_signals()` + `broken_fields_from_health()` |  ✅   |
| 9 | Repair Engine (candidate gen)      | `backend/healer/candidates.py`            | `generate_candidates()` (6 sources)             |  ✅   |
|10 | Candidate Evaluation               | `backend/scoring/score.py`                | `score_field()` (guide §8.4 formula)            |  ✅   |
|11 | Versioning                         | `backend/database/store.py`               | `scraper_versions` table + `add_version()`      |  ✅   |
|12 | Deployment / Rollback              | `backend/versioning/deploy.py`            | `attempt_repair()`, `rollback_to()`             |  ✅   |
|13 | Monitoring                         | `backend/database/store.py` + `backend/api/app.py` | `runs` table + dashboard + `/api/scrapers/{id}` |  ✅   |

Every conceptual stage has a real implementation. Nothing was cut.

---

## 3. Component diagram

```
    ┌───────────────────────────────────────────────────────────────────┐
    │                        Control plane (FastAPI)                     │
    │   /api/scrapers · /run · /rollback · /switch_fixture · /preview    │
    │   /api/health · /api/fixtures · dashboard at /                     │
    └───────────────┬────────────────────────────────┬───────────────────┘
                    │                                │
                    ▼                                ▼
        ┌───────────────────────┐        ┌────────────────────────┐
        │ Deployment            │        │ Frontend dashboard     │
        │ Orchestrator          │        │ (Tailwind + vanilla JS)│
        │ backend/versioning/   │        │ Overview / Detail /    │
        │ deploy.py             │        │ Repair Events views    │
        └──────┬────────┬───────┘        └────────────────────────┘
               │        │
               │        └──────────────────────────────┐
               ▼                                       ▼
     ┌─────────────────────┐              ┌─────────────────────┐
     │ Acquisition         │              │ Persistence         │
     │ backend/acquisition/│              │ backend/database/   │
     │   base.py           │              │   store.py          │
     │   fixture.py        │              │                     │
     │   brightdata.py     │              │  scrapers           │
     │   factory.py        │              │  scraper_versions   │
     └──────────┬──────────┘              │  runs               │
                │                          │  repair_events     │
                ▼                          └─────────────────────┘
     ┌─────────────────────┐                       ▲
     │ Extraction Engine   │                       │
     │ backend/scraper/    │                       │
     │   strategy.py       │────► records ─────────┘
     │   extract.py        │
     │   normalize.py      │
     │   schema.py         │
     └──────────┬──────────┘
                ▼
     ┌─────────────────────┐          ┌─────────────────────┐
     │ Validator + Health  │──HR──►   │ Repair Engine       │
     │ backend/validator/  │  FAIL    │ backend/healer/     │
     │   rules.py          │          │   candidates.py     │
     │   health.py         │          │   repair.py         │
     └──────────┬──────────┘          │ backend/scoring/    │
                │ PASS                 │   score.py          │
                ▼                      └──────────┬──────────┘
             Store run                            │
                                                  ▼
                                       Shadow-run gate ──► New version ──► Store
                                       (promote / shadow / reject)
```

---

## 4. Module responsibilities (one-liner + entry points)

- **`backend/config.py`** — pure env-driven `Settings` dataclass and the
  fixture / DB path constants. No I/O beyond `mkdir(data)`.

- **`backend/scraper/schema.py`** — `Product` Pydantic model + `REQUIRED_FIELDS`
  + `FIELD_TYPES`. The single source of truth for what a record is.

- **`backend/scraper/strategy.py`** — `FieldSelector` (5 kinds) and `Strategy`
  (record selector + per-field selectors + optional next-page selector).
  `DEFAULT_STRATEGY` is the v1 baseline for MetroKart.

- **`backend/scraper/normalize.py`** — deterministic value normalizers for
  price (US and EU formats), currency (symbol/ISO), rating (0-5/0-10/0-100
  squash), integer-from-text, availability synonyms, URL resolution.

- **`backend/scraper/extract.py`** — `extract(html, strategy, url) →
  ExtractionResult`. Pure function; includes a cheap DOM structural
  fingerprint used later for drift detection and pagination follower.

- **`backend/validator/rules.py`** — 8 pure rules returning `RuleViolation`
  lists: `required_fields_present`, `types_match`, `price_plausible`,
  `rating_in_range`, `review_count_non_negative`, `urls_valid`,
  `name_not_ui_label`, `currency_iso_shape`.

- **`backend/validator/health.py`** — `HealthReport` (per-run summary) +
  `compute_health()` (weighted `0.30·C + 0.30·V + 0.20·S + 0.20·R`) +
  `_failure_signals()` for guide §7.4 signals (no records, count collapse,
  low completeness, widely broken field, type drift).

- **`backend/healer/candidates.py`** — `generate_for_field()` produces
  `Candidate`s from 6 sources: stable attributes, class synonyms, tag+role
  heuristics, text anchors, positional fallbacks, historical selectors.

- **`backend/scoring/score.py`** — `score_field()` computing guide §8.4's
  `0.25·Schema + 0.25·Completeness + 0.20·Type + 0.15·Similarity + 0.15·Historical`.

- **`backend/healer/repair.py`** — `repair()` orchestrator: for every broken
  field, generate → test (swap into strategy, re-extract) → score →
  three-tier gate (`reject`/`shadow`/`promote`). Emits a `RepairPlan`.

- **`backend/database/store.py`** — raw `sqlite3` persistence for the four
  tables. Everything is a small function; no ORM.

- **`backend/versioning/deploy.py`** — the top-level control loop:
  `register_scraper()`, `run_once()`, `attempt_repair()` (with shadow-run
  gate), `rollback_to()`. Records every run + repair event.

- **`backend/acquisition/base.py|fixture.py|brightdata.py|factory.py`** —
  `Acquisition.fetch(url) → PageSnapshot` interface with two providers and a
  factory that respects `ACQUISITION_PROVIDER` env with safe fallback.

- **`backend/api/app.py`** — FastAPI app: health, list/register scrapers,
  scraper detail, run, rollback, switch_fixture (demo control), runs, repair
  events, preview, versions, list fixtures, dashboard HTML. 17 routes total.

- **`backend/api/demo_cli.py`** — colored 7-step terminal narrative for the
  hackathon demo (works with no browser).

- **`benchmark/harness.py`** — Traditional vs WebMedic across all 11
  fixtures, both pages. Reports repair rate, field accuracy, false repairs,
  MTTR.

- **`tests/fixtures/generate_fixtures.py`** — deterministic HTML fixture
  generator that reads `golden_dataset.json` and writes 11 controlled-
  breakage layouts × 2 pages each.

---

## 5. Data schemas

### 5.1 Product schema (extraction output)

Fields, all optional at the type level so a partial extraction never
raises — the validator is what decides completeness:

    product_name : str
    price        : float, ge=0
    currency     : str, len=3         (ISO-4217 shape)
    rating       : float, 0..5
    review_count : int, ge=0
    availability : str
    product_url  : str (absolute URL)
    image_url    : str (absolute URL)

### 5.2 Strategy schema (extraction plan, JSON-serialized in DB)

    Strategy {
        name              : str
        record_selector   : CSS selector for record roots
        fields            : {field_name → FieldSelector}
        next_page_selector: CSS selector, optional
        notes             : str
    }

    FieldSelector {
        kind      : "css" | "xpath" | "text-anchor" | "attr-on-self" | "static"
        value     : selector string / literal
        attr      : optional HTML attribute to read
        transform : "text" | "price" | "rating" | "int_from_text" | "url" | ...
    }

### 5.3 HealthReport (validator output)

    HealthReport {
        strategy_name           : str
        records_expected        : int|None
        records_received        : int
        completeness            : float (0..1)
        validity                : float (0..1)
        schema_consistency      : float (0..1)
        record_consistency      : float (0..1)
        health_score            : float (0..1)     # weighted composite
        status                  : "healthy" | "warning" | "repair_required"
        violations_by_field     : {field → count}
        violations_by_code      : {code → count}
        failure_signals         : [str]            # guide §7.4 signals
    }

### 5.4 RepairPlan (healer output)

    RepairPlan {
        strategy_name        : str
        broken_fields        : [str]
        field_repairs        : {field → FieldRepair}
        proposed_strategy    : Strategy|None
        proposed_confidence  : float (0..1)
    }

    FieldRepair {
        field           : str
        old_selector    : FieldSelector|None
        top_candidates  : [ScoredCandidate]
        winner          : ScoredCandidate|None
        action          : "no_change" | "shadow" | "promote" | "reject"
        reason          : str
    }

---

## 6. Database entities (SQLite)

Tables map 1:1 to the guide §13 recommendation.

    scrapers
    ────────
      id                INTEGER PK
      name              TEXT UNIQUE
      target_url        TEXT
      schema            TEXT (JSON list of field names)
      current_version   INTEGER
      health_score      REAL
      status            TEXT
      created_at, updated_at REAL (epoch seconds)

    scraper_versions
    ────────────────
      id                INTEGER PK
      scraper_id        FK → scrapers.id
      version           INTEGER
      selectors         TEXT (JSON serialized Strategy)
      created_at        REAL
      reason            TEXT
      confidence        REAL
      UNIQUE(scraper_id, version)

    runs
    ────
      id                INTEGER PK
      scraper_id        FK → scrapers.id
      version           INTEGER
      timestamp         REAL
      records_expected  INTEGER
      records_received  INTEGER
      health_score      REAL
      status            TEXT
      signals           TEXT (JSON list)
      report            TEXT (JSON full HealthReport)

    repair_events
    ─────────────
      id                    INTEGER PK
      scraper_id            FK → scrapers.id
      old_version           INTEGER
      new_version           INTEGER (nullable — null when rejected)
      failure_reason        TEXT
      candidate_count       INTEGER
      selected_candidate    TEXT (JSON ScoredCandidate or NULL)
      confidence            REAL
      plan                  TEXT (JSON full RepairPlan)
      action                TEXT ('promote' | 'shadow' | 'reject' | 'rollback')
      timestamp             REAL

Rollback = one write (`UPDATE scrapers SET current_version=?`) + one insert
into `repair_events` with `action='rollback'`.

---

## 7. API boundaries

Every endpoint returns JSON except `GET /` which returns the dashboard HTML.

| Verb | Path                                        | Purpose                                             |
|------|---------------------------------------------|-----------------------------------------------------|
| GET  | `/`                                         | Dashboard SPA                                       |
| GET  | `/api/health`                               | Liveness + acquisition provider + Bright Data status|
| GET  | `/api/fixtures`                             | List fixture layouts on disk                        |
| GET  | `/api/scrapers`                             | List all scrapers                                   |
| POST | `/api/scrapers`                             | Register a scraper (idempotent by name)             |
| GET  | `/api/scrapers/{id}`                        | Scraper + versions + recent runs + repair events    |
| POST | `/api/scrapers/{id}/run`                    | Fetch → extract → validate → maybe heal             |
| POST | `/api/scrapers/{id}/rollback`               | Revert `current_version`                            |
| POST | `/api/scrapers/{id}/switch_fixture`         | Demo control — swap served fixture layout           |
| GET  | `/api/scrapers/{id}/runs`                   | Paginated run history                               |
| GET  | `/api/scrapers/{id}/repair-events`          | Repair event history                                |
| GET  | `/api/scrapers/{id}/preview`                | Extract with the active strategy, top N records     |
| GET  | `/api/scrapers/{id}/versions/{version}`     | Retrieve a specific strategy version JSON           |

Total: 13 unique paths, 17 registered routes (some methods share paths).

---

## 8. Bright Data integration

The acquisition layer is a two-provider interface.

- **`Acquisition.fetch(url) → PageSnapshot`** is the only surface the rest of
  the system sees.
- **`FixtureAcquisition`** serves HTML from `tests/fixtures/pages/…` and
  understands both `fixture://version/page-N` and normal `?page=N` URLs. It
  has a `switch_version()` hook so the dashboard's "simulate site change"
  button can swap the served layout live.
- **`BrightDataAcquisition`** wraps `httpx` with a proxied Web Unlocker
  session. It reads credentials from env:
      BRIGHTDATA_USERNAME  (typically `brd-customer-<cid>-zone-<zone>`)
      BRIGHTDATA_PASSWORD
      BRIGHTDATA_HOST      (default `brd.superproxy.io`)
      BRIGHTDATA_PORT      (default `33335`)
      BRIGHTDATA_ZONE      (metadata only, echoed into `provider_meta`)
  `.available()` returns False when creds are absent, in which case the
  factory logs a WARNING and returns a `FixtureAcquisition` — the demo and
  benchmark therefore run on a clean clone.
- **`factory.make_acquisition()`** switches on `ACQUISITION_PROVIDER` env.

This means Bright Data is *meaningfully integrated* (same code path,
production-ready proxy client) but is *never a single point of failure*.

---

## 9. Controlled failure fixtures

Fixtures are generated deterministically from `golden_dataset.json`:

    python tests/fixtures/generate_fixtures.py

produces 11 fixture layouts × 2 pages each (10 records per page, 20 total):

| Version              | Change                                              | Broken fields in baseline |
|----------------------|-----------------------------------------------------|:--------------------------|
| `v1_healthy`         | baseline                                            | —                         |
| `v2_rename_class`    | `.price` → `.cost`                                  | price                     |
| `v3_dataattr`        | class identifiers replaced with `[data-testid]`     | 6 fields                  |
| `v4_change_nesting`  | extra wrapper divs                                  | — (descendant selectors OK) |
| `v5_move_price`      | price moved to a summary aside                      | —                         |
| `v6_label_change`    | visible labels change                               | —                         |
| `v7_decoy`           | struck-through "was" price                          | — (tag-qualified selector) |
| `v8_pagination`      | next link becomes `.load-more`                      | — (pagination only)       |
| `v9_combined`        | rename + nesting + decoy                            | price                     |
| `v10_partial`        | availability class swapped                          | availability              |
| `v11_semantic`       | availability wording changes                        | — (normalizer maps values) |

Each fixture ships in-repo — the benchmark and demo do not depend on any
live website.

---

## 10. Testing strategy

- **Unit** (`tests/unit/`):
  - `test_normalize.py` — 10 tests. Price (US/EU), currency, rating,
    review_count, availability synonyms, URL resolution.
  - `test_extract.py` — 5 tests. Baseline extracts golden dataset; specific
    fixtures break the right fields.
  - `test_validator.py` — 13 tests. Every rule + full health computation.
  - `test_healer.py` — 8 tests. Candidate sources; broken-field derivation;
    repair promotes correct selectors for v2 / v3 / v9; scoring behavior;
    empty-input safe.
  - `test_acquisition.py` — 6 tests. Fixture routing; scheme parsing;
    version switching; Bright Data unconfigured guard; factory fallback +
    happy path.

- **Integration** (`tests/integration/`):
  - `test_deploy.py` — 5 tests. Register creates v1; healthy no-change;
    v3 broken → promote v2 → next run healthy; rollback reverts and is
    recorded; repair event carries confidence.
  - `test_api.py` — 7 tests. Health, register+run healthy, full self-heal
    cycle, switch+rollback, list fixtures, dashboard HTML, preview.
  - `test_benchmark.py` — 4 tests. Traditional breaks on expected fixtures;
    WebMedic repairs 100 % / 0 false; WebMedic ≥ Traditional per fixture;
    MTTR < 5 s.
  - `test_e2e.py` — 3 tests. Full self-heal story; sequential layout
    changes; demo CLI runs.

Run all 61 tests with `pytest -q` in ~18 seconds.

---

## 11. What is intentionally NOT here (guide §20 anti-patterns)

- **No microservices, queue, or worker.** One FastAPI process is enough.
- **No custom-trained LLM.** The candidate generator is a documented set of
  six heuristics. A future LLM reasoner plugs into the same `Candidate`
  interface without touching the deployment path.
- **No ORM.** `sqlite3` + small pure functions in `store.py`.
- **No Docker.** A `pip install -r requirements.txt` + `pytest` + `uvicorn`
  is faster and more reproducible for a hackathon demo than Docker Compose.
- **No React/Next.js.** A single Tailwind-CDN HTML file with vanilla JS is
  enough and has zero build step.

---

## 12. Cross-check against the guide

| Guide requirement                                          | Where implemented                                 |
|------------------------------------------------------------|---------------------------------------------------|
| Bright Data Scraper Studio meaningful integration          | `backend/acquisition/brightdata.py` + factory     |
| Clear schema + structured output                           | `backend/scraper/schema.py`                       |
| Deterministic validation                                   | `backend/validator/rules.py` (8 pure rules)       |
| Weighted health score                                      | `backend/validator/health.py`                     |
| Automatic repair on failure                                | `backend/healer/repair.py`                        |
| Multiple candidate strategies                              | 6-source generator in `healer/candidates.py`      |
| Candidates tested before deployment                        | `_test_field_candidate()` + shadow run in deploy  |
| Versions + rollback                                        | `scraper_versions` + `rollback_to()`              |
| Golden dataset                                             | `tests/fixtures/golden_dataset.json` (20 records) |
| Controlled benchmark                                       | `benchmark/harness.py`                            |
| Dashboard makes repair visually obvious                    | `frontend/index.html` (3 views)                   |
| Public reproducible repo                                   | This file lives at the top of it                  |
| README complete                                            | `README.md` (problem, arch, quick-start, benchmark, AI disclosure) |
| Demo starts with failure, ends with recovery               | Six-step guided walkthrough in `frontend/index.html` (Register → Healthy → Break → Heal → Inspect → Rollback) |
| AI-use disclosure                                          | `README.md` §"AI-use disclosure"                  |
| Every submitted component explainable                      | This document                                     |

Every item in the guide's "Final Winning Checklist" (§24) is met.

---

## 13. Checkpoint result

    Modules imported cleanly ........ 19 / 19
    Test suite ...................... 61 / 61
    FastAPI routes registered ....... 17
    Dashboard HTML .................. 200 (13,993 bytes)
    Fixtures on disk ................ 11 layouts × 2 pages
    Benchmark last run:
        Traditional : 7 / 11 healthy · 89.77 % accuracy
        WebMedic    : 11 / 11 healthy · 100 % accuracy · 4 / 4 repairs
                      · 0 false repairs · MTTR ≈ 356 ms

Architecture is complete, verified, and ready for further algorithm work.
