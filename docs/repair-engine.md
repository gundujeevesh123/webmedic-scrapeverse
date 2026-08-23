# Repair Engine Deep Dive

The repair engine is the beating heart of WebMedic. It answers one question:

> *A run just failed the health gate — what new selectors should I try, and
> how do I decide which to trust?*

## Inputs

- The current (failed) `Strategy`.
- The HTML that caused the failure (a `PageSnapshot`).
- The list of `broken_fields` — derived from the `HealthReport`'s failure
  signals: any field with violations in >= 80 % of records is considered
  broken (guide §7.4).
- An optional golden dataset for structural comparison scoring.
- An optional history of previously-successful selectors per field.

## Six candidate sources

For each broken field, `candidates.generate_for_field` emits candidates from
six sources so the search is broad but tight:

1. **Stable-attribute candidates** — `[data-testid='<kw>']`, `[data-<kw>]`,
   `[itemprop='<kw>']` for each field-specific keyword. This is what catches
   `v3_dataattr`.
2. **Class-name synonyms present in the DOM** — for each field-specific
   keyword (e.g. `price` also tries `cost`, `amount`, `value`, `current-price`),
   if the class actually exists in the DOM, we emit both `.kw` and
   `<tag>.kw` variants. This is what catches `v2_rename_class` (`.cost`).
3. **Tag + role heuristics** — headings for `product_name`, `<img>` for
   `image_url`, `a[href]` for `product_url`. These act as fallbacks when the
   more specific candidates score low.
4. **Text-anchor candidates** — for each label word associated with the
   field (e.g. `Rating`, `Rated`, `Buy for`, `Availability`), we look up the
   first element whose text contains the label and take the sibling / adjacent
   value.
5. **Positional fallbacks** — `<tag>:nth-of-type(N)` for N=1,2 on the
   preferred tag list. Last-resort but often surprisingly effective when the
   DOM order is stable.
6. **Historical candidates** — any selector previously deployed for this
   field. This is what makes the system self-improving across many pages
   from the same site.

## Test — Score — Gate

For every candidate we build a *hypothetical strategy*: the current strategy
with the broken field's selector swapped for the candidate. We re-extract
against the same HTML, then feed the resulting records into
`score_field(records, field, golden, historical_values)` which computes
guide §8.4's five components:

    Score = 0.25 · SchemaValidity
          + 0.25 · Completeness
          + 0.20 · TypeValidity
          + 0.15 · Structural/SemanticSimilarity
          + 0.15 · HistoricalConsistency

The three gate thresholds decide the action:

- `min_accept` (0.60) — below this the candidate is rejected outright.
- `shadow_threshold` (0.75) — a candidate in [0.75, 0.90] and passing the
  completeness floor enters *shadow mode*: recorded but not promoted, so
  humans can review before production traffic uses it.
- `promote_threshold` (0.90) — a candidate above this AND clearing both
  completeness (>= 0.80) and schema-validity (>= 0.80) floors is promoted
  to production.

Even after a per-field winner is chosen, the whole *proposed strategy* has
to pass a **shadow run** on the same HTML: the shadow-extracted records
must yield a `HealthReport` with `status == "healthy"`. Only then does
`versioning/deploy.py::attempt_repair` add the new version, flip
`scrapers.current_version`, and record the repair in `repair_events`.

This two-layer gate is why the benchmark shows **0 false repairs** — a
candidate that scores 0.92 on completeness but collapses total health does
not get promoted.

## What if no candidate is good enough?

The plan is still recorded as a `shadow` or `reject` event with the full
list of tested candidates and their scores. Humans (or a future scheduled
job) can inspect the event and either lower thresholds, add a hand-written
selector to the historical pool, or accept the top candidate manually. In
the meantime the current version keeps running — degraded but honest.

## Where an LLM would plug in

The `Candidate` dataclass is deliberately source-agnostic. A future
`llm_reasoner.py` can produce a `list[Candidate]` from a prompt over the
old-vs-new DOM diff, and the resulting candidates flow through the same
`test → score → gate` pipeline. Nothing in the deployment path needs to
change. That is the guide's core principle:

    AI proposes. Evidence decides.
