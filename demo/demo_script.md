# Two-Minute Demo Script (guide §15)

> Start with failure, not with a long introduction. Judges must see the core
> innovation immediately.

## Setup (do before the demo)

```bash
uvicorn backend.api.app:app --port 8000    # dashboard
# In another terminal:
python -m backend.api.demo_cli --break v9_combined     # optional terminal-only backup
```

Open `http://127.0.0.1:8000/` in a browser. Click **Register MetroKart**.

## Beat 1 — 0:00 to 0:20 · "This is a healthy scraper"

- Fixture layout: `v1_healthy`.
- Click **Run**.
- Point at the dashboard: **Overview status = healthy, 100.0/100**.
- Point at **Live Preview** — 6 real records with correct prices, ratings, reviews.
- One line to say: *"20 products, structured, normalized, deterministic."*

## Beat 2 — 0:20 to 0:35 · "Now the website redesigns overnight"

- Change **Fixture layout** to `v3_dataattr`.
- Click **Simulate site change**.
- Explain: *"Real e-commerce sites do this in production — classes get
  replaced with data-testid attributes because a designer renamed things."*

## Beat 3 — 0:35 to 0:50 · "A normal scraper silently dies"

- Click **Run**.
- Watch the pill turn red. Health score collapses (e.g. 47.5 / 100).
- Point at **Latest Runs → signals**: `low_completeness`, `field_widely_broken:product_name=1.00`, etc.
- One line: *"A traditional scraper would stop here — and produce garbage."*

## Beat 4 — 0:50 to 1:15 · "WebMedic detects, diagnoses, and repairs"

- Point at **Repair Events**: `v1 → v2 · 6 broken fields · confidence 92 %`.
- Click the event (dashboard optional; use the terminal or JSON viewer if
  time is short).
- Point at the candidate list per field — the winner selector is highlighted
  in green, with its rationale (e.g. *"data-testid matching keyword 'price'"*).
- One line: *"AI proposed the candidates. The deterministic validator picked
  the winners against the golden dataset."*

## Beat 5 — 1:15 to 1:30 · "New version deployed, records recovered"

- Point at **Active Strategy Selectors** — now uses `[data-testid='...']`.
- Point at **Live Preview** — records are back to real prices / ratings.
- Health score climbs back to 100.

## Beat 6 — 1:30 to 1:50 · "Rollback is one write"

- Click **Rollback to v1**.
- Point at **Versions**: v1 initial · v2 auto-heal · current version = 1 again.
- One line: *"Every repair is versioned. Every version is rollback-able."*

## Beat 7 — 1:50 to 2:00 · "This works across a whole benchmark"

- Switch to a terminal and run:

```bash
python -m benchmark.harness
```

- Point at the summary block:
  - Traditional: 7 healthy · 89.77 % accuracy
  - WebMedic: 11 healthy · **100 % accuracy · 4/4 repairs · 0 false repairs · 363 ms MTTR**
- One line: *"This is what self-healing looks like."*

## Closing statement

> "AI proposed. Evidence decided. The scraper broke, and then it repaired
> itself. Bright Data Scraper Studio is the acquisition provider under the
> hood — same code path, same repair engine. All 20 products, structured,
> validated, versioned, rollback-able."

---

Backup plans if the dashboard misbehaves:

- Fall back to `python -m backend.api.demo_cli --break v9_combined`; it
  narrates the same seven beats in the terminal with color.
- The benchmark table (`python -m benchmark.harness`) is a standalone story
  that is enough on its own to prove the point.
