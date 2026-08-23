"""Full self-heal demonstration with evidence.

Prints, for one broken fixture:

  1. The broken state (health report + signals + null rates)
  2. Detection verdict (severity + reason + evidence)
  3. Candidate generation (per broken field, top-K with scores + source + rationale)
  4. Gate decision per field
  5. Shadow-run health
  6. Deployment outcome
  7. Recovered records preview
  8. DB rows (scraper_versions + repair_events)

Usage:
    python scripts/demonstrate_repair.py                  # v3_dataattr, the classic
    python scripts/demonstrate_repair.py --fixture v9_combined
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

from backend.acquisition.fixture import FixtureAcquisition        # noqa: E402
from backend.config import FIXTURE_DIR                             # noqa: E402
from backend.database import store                                 # noqa: E402
from backend.healer.repair import broken_fields_from_health, repair  # noqa: E402
from backend.scraper.extract import extract                        # noqa: E402
from backend.scraper.strategy import DEFAULT_STRATEGY              # noqa: E402
from backend.validator.detection import detect_degradation         # noqa: E402
from backend.validator.health import compute_health                # noqa: E402
from backend.versioning import deploy                              # noqa: E402


BOLD = "\033[1m"
GREEN = "\033[32m"
YELLOW = "\033[33m"
RED = "\033[31m"
CYAN = "\033[36m"
DIM = "\033[2m"
RESET = "\033[0m"


def header(text: str, tone: str = CYAN) -> None:
    print(f"\n{tone}{BOLD}{'━' * 70}\n{text}\n{'━' * 70}{RESET}")


def kv(k: str, v) -> None:
    print(f"  {DIM}{k:24s}{RESET} {v}")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--fixture", default="v3_dataattr")
    args = parser.parse_args()

    store.reset_database()
    golden = json.load(open(FIXTURE_DIR / "golden_dataset.json"))["records"][:10]

    # Register the scraper on v1 (healthy) and record a baseline health.
    acq = FixtureAcquisition(FIXTURE_DIR / "pages", version="v1_healthy")
    sid = deploy.register_scraper("metrokart", "http://127.0.0.1:8765/list?page=1")
    baseline_snap = acq.fetch("http://127.0.0.1:8765/list?page=1")
    baseline_extract = extract(baseline_snap.html, DEFAULT_STRATEGY, url=baseline_snap.url)
    baseline_hr = compute_health(baseline_extract.records, expected=10)

    header("STEP 0 — Baseline (v1_healthy)", GREEN)
    kv("health_score", f"{baseline_hr.health_score:.3f}")
    kv("status", baseline_hr.status)
    kv("records", f"{baseline_hr.records_received}/10")

    # Simulate a website change.
    header(f"STEP 1 — Website change → serving layout {args.fixture!r}", YELLOW)
    acq.switch_version(args.fixture)

    header("STEP 2 — Broken run + failure detection", RED)
    snap = acq.fetch("http://127.0.0.1:8765/list?page=1")
    broken_extract = extract(snap.html, DEFAULT_STRATEGY, url=snap.url)
    broken_hr = compute_health(broken_extract.records, expected=10)
    kv("health_score", f"{RED}{broken_hr.health_score:.3f}{RESET}")
    kv("status", f"{RED}{broken_hr.status}{RESET}")
    kv("failure_signals", ", ".join(broken_hr.failure_signals) or "—")
    kv("null_rates (>0)", {k: v for k, v in broken_hr.null_rates.items() if v > 0})

    det = detect_degradation(
        broken_hr, baseline=baseline_hr,
        current_fingerprint=broken_extract.fingerprint,
        baseline_fingerprint=baseline_extract.fingerprint,
    )
    kv("detection.severity", f"{RED}{det.severity}{RESET}")
    kv("detection.confidence", f"{det.confidence:.2f}")
    kv("detection.reason", det.reason)
    kv("newly_broken_fields", det.evidence.newly_broken_fields)

    header("STEP 3 — Candidate generation (per broken field)", CYAN)
    broken = broken_fields_from_health(broken_hr)
    plan = repair(
        DEFAULT_STRATEGY, snap.html,
        broken_fields=broken, url=snap.url, golden=golden,
    )
    for field, fr in plan.field_repairs.items():
        print(f"  {BOLD}{field}{RESET}  → {len(fr.top_candidates)} candidates evaluated")
        for i, c in enumerate(fr.top_candidates[:5], 1):
            marker = f"{GREEN}★{RESET}" if c is fr.winner else " "
            sel = c.selector
            print(
                f"    {marker} {i}. {sel.kind:11s} {sel.value:<35s} "
                f"[{c.source}]  score={c.score.total*100:5.1f}%"
                f" (schema={c.score.schema_validity:.2f} comp={c.score.completeness:.2f}"
                f" type={c.score.type_validity:.2f} sim={c.score.similarity:.2f})"
            )
        w = fr.winner
        print(f"    {DIM}action={fr.action}  reason={fr.reason}{RESET}")

    header("STEP 4 — Gate decision + shadow run", CYAN)
    hr_after, dec = deploy.run_once(
        sid, "http://127.0.0.1:8765/list?page=1", fetch=acq, expected=10, golden=golden,
    )
    kv("gate action", f"{GREEN if dec.action=='promote' else YELLOW}{dec.action}{RESET}")
    kv("gate reason", dec.reason)
    kv("proposed_confidence", f"{dec.confidence*100:.1f}%")
    kv("pre_health / post_health", f"{dec.pre_health*100:.1f}% → {(dec.post_health or 0)*100:.1f}%")
    kv("new_version", dec.new_version)

    header("STEP 5 — Recovered records (top 5)", GREEN)
    strategy = deploy.get_active_strategy(sid)
    recovered = extract(snap.html, strategy, url=snap.url)
    for r in recovered.records[:5]:
        print(f"    {r['product_name']:30s}  {str(r['price'])+' '+str(r['currency']):15s}  {r['rating']}/5 · {r['review_count']} reviews · {r['availability']}")

    header("STEP 6 — DB evidence", CYAN)
    scraper = store.get_scraper(sid)
    kv("scrapers.current_version", scraper["current_version"])
    kv("scrapers.health_score", f"{scraper['health_score']*100:.1f}%")
    kv("scrapers.status", scraper["status"])
    print(f"  {DIM}scraper_versions:{RESET}")
    for v in store.list_versions(sid):
        print(f"    v{v['version']:<2}  {v['reason']:60s}  conf={v['confidence']*100:.1f}%")
    print(f"  {DIM}repair_events:{RESET}")
    for e in store.list_repair_events(sid):
        print(f"    id={e['id']}  {e['action']:<9s}  v{e['old_version']}→v{e['new_version']}  conf={e['confidence']*100:.1f}%  {e['failure_reason'][:60]}")

    header("DONE — AI proposed. Evidence decided. Deployment versioned. Rollback available.", GREEN)


if __name__ == "__main__":
    main()
