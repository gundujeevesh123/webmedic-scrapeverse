# Known limitations

An honest list of things WebMedic does and does not do. Every item here is
either an explicit design choice (with rationale) or a known gap we would
close before production.

## Scope

- **One domain, one schema.** MetroKart-shaped product listings only. Adding
  a second domain means writing a new baseline `Strategy` and (optionally)
  a new fixture set. The extraction engine and repair pipeline are
  domain-agnostic; only the strategy + fixtures are domain-specific.
- **Synthetic demo storefront.** All benchmarks and tests run against
  fixtures generated deterministically from `golden_dataset.json`. No live
  websites are scraped by any test or the demo. Bright Data is meaningfully
  integrated as an acquisition provider but the demo does not depend on it.

## Detection

- **Detector requires ≥ 3 records to fire semantic anomaly signals.**
  `identical_prices` / `identical_urls` / `identical_names` need at least
  3 records to distinguish "one product" from "template broken".
- **Cosmetic HTML changes do not lower the health score by design.** A
  fingerprint change alone never alarms. We rely on the extracted-data
  signals (completeness, validity, null rates) — this is what avoids false
  alarms on v4/v5/v7. It also means a very slow field-value drift that
  never crosses a threshold would go undetected until it does.

## Repair

- **Heuristic candidate generator, not a trained model.** The 6-source
  generator covers rename, attribute swap, nesting change, decoy, label
  change, and partial break. It will not repair layouts where the target
  data literally moved out of the record root (only a re-scoping of the
  record selector would fix that).
- **`extra_providers` (LLM) is a documented seam, not wired to a real
  LLM.** A real Claude/OpenAI provider is a ~50-line adapter that returns
  `list[Candidate]` — see `backend/healer/llm_provider.py`. All safeguards
  still apply: LLM candidates flow through the same test → score → gate.
- **Historical scoring gives 0.5 on first repair.** Neutral by design so
  we don't punish first fixes; converges to a real signal after the third
  repair.
- **No cross-run learning.** Winning selectors are stored per version but
  are only re-tried as `historical` candidates when the *same* field
  breaks again. Cross-field pattern learning would be future work.

## Deployment / storage

- **Single-process SQLite.** Fine for the hackathon and moderate loads.
  Concurrency > 1 process needs Postgres. The store module is intentionally
  thin (raw `sqlite3` + parameterized queries) so swapping is one file.
- **Snapshots are stored on the container's disk** unless the operator
  wires the `snapshot_dir` to a persistent volume. Snapshots survive across
  restarts but not container recycling.
- **No pruning of old runs / repair events.** Both tables grow unbounded.
  A weekly TRUNCATE job (or LIMIT on the queries used by the dashboard) is
  the recommended mitigation.

## Acquisition

- **Bright Data credentials are user-provided.** No auto-provisioning. The
  factory logs and falls back to fixtures when creds are missing, which is
  correct for local development but silent-failure in production. Recommend
  a startup healthcheck endpoint verify the provider before serving.
- **httpx TLS verify=False for the Bright Data proxy.** Bright Data uses
  its own TLS chain; this is expected but worth documenting for security
  reviewers.

## Dashboard

- **Tailwind loaded from CDN.** Zero build step, but the dashboard needs
  internet at *page load time* for the theme. Vendoring Tailwind is a
  10-line change if offline demo is required.
- **No auth on the FastAPI endpoints.** Fine for `127.0.0.1` demo; bind to
  an internal interface or add a reverse proxy for anything else.

## Testing

- **169 tests, ~40 s runtime.** No CI wired up; a `pytest -q` per push is
  the recommended pre-commit hook.
- **MTTR is wall-clock and machine-dependent.** Absolute numbers will
  differ across hardware; the ordering (Traditional had no repair time,
  WebMedic ≈ 400 ms) is what matters. The `test_benchmark_verify.py`
  assertions are relative or use loose tolerances (0.05 ms) accordingly.

## Ethics / scope-of-use

- Public data only. The Bright Data guide requires it and we ship no
  workflow that requires logged-in access or private information.
- The synthetic MetroKart storefront is fictional. Do not reuse the fake
  product IDs to test against any real e-commerce site.

## Golden Rule from the practitioner community

**Do not scrape if a public API is available.** Every web-scraping
practitioner interviewed in the WebScraperKnowledge notes (7 videos)
converges on the same point: if a site provides an official developer
API, use it. It is cleaner, faster, more stable, and won't get blocked.
Web scraping is the right call only when no API exists or the API omits
data you need — and in that case, respect robots.txt, rate limits, and
the site's terms of service. WebMedic's self-healing loop is designed
for pages you *have* to scrape; it is not a license to scrape recklessly.

## Anti-block practices already implemented

- **Snapshots preserved on every fetch** (opt-in via `Acquisition.enable_snapshots`).
  This means a broken run can always be re-diagnosed offline without re-hitting
  the site — the #1 web-scraping best practice.
- **Session pooling + cookies** via `httpx.Client` (keeps connections warm and
  cookies alive across requests).
- **User-Agent rotation** — Bright Data adapter cycles through a pool of 5
  realistic browser UAs. Real deployments should extend the pool.
- **Configurable rate limiting** — `BrightDataAcquisition(request_delay=0.5)`
  by default. Tests pass `0.0` for determinism.
- **Residential proxy routing** — Bright Data Web Unlocker with rotating IPs.
