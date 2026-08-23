"""Interactive demo script (guide §15).

Runs the entire self-heal story in the terminal, no server required:

  1. Start with the v1 healthy layout — show extracted structured records.
  2. Break the layout by switching the fixture to `v3_dataattr`.
  3. Show the traditional strategy failing and the health score collapsing.
  4. Trigger self-healing: candidate generation, testing, scoring, gating.
  5. Show winning candidates and confidence.
  6. Deploy the new version and show restored records + health.
  7. Roll back to v1 and show the DB trail.

Usage:
    python -m backend.api.demo_cli           # runs the whole scripted demo
    python -m backend.api.demo_cli --break v9_combined
"""

from __future__ import annotations

import argparse
import json

from backend.acquisition.fixture import FixtureAcquisition
from backend.config import FIXTURE_DIR
from backend.database import store
from backend.versioning import deploy

with (FIXTURE_DIR / "golden_dataset.json").open(encoding="utf-8") as _fh:
    GOLDEN = json.load(_fh)["records"]


BOLD = "\033[1m"
GREEN = "\033[32m"
YELLOW = "\033[33m"
RED = "\033[31m"
CYAN = "\033[36m"
DIM = "\033[2m"
RESET = "\033[0m"


def _p(header: str, tone: str = CYAN) -> None:
    print(f"\n{tone}{BOLD}▲ {header}{RESET}")


def _kv(k: str, v) -> None:
    print(f"  {DIM}{k:16s}{RESET} {v}")


def _record_line(r: dict) -> str:
    def cell(v, wide: int = 12):
        if v is None:
            return f"{RED}null{RESET}".ljust(wide + len(RED) + len(RESET))
        return str(v)[:wide].ljust(wide)

    return (
        f"    {cell(r.get('product_name'), 24)} "
        f"{cell(r.get('price'), 8)} "
        f"{cell(r.get('currency'), 5)} "
        f"{cell(r.get('rating'), 5)} "
        f"{cell(r.get('review_count'), 7)} "
        f"{cell(r.get('availability'), 12)}"
    )


def _dump_preview(records: list[dict], n: int = 4) -> None:
    print(f"  {DIM}{'name':<24} {'price':<8} {'ccy':<5} {'rate':<5} {'revs':<7} {'stock':<12}{RESET}")
    for r in records[:n]:
        print(_record_line(r))


def run_demo(break_to: str = "v3_dataattr", db_reset: bool = True) -> None:
    if db_reset:
        store.reset_database()
    _p("Registering MetroKart scraper with the v1 healthy layout")
    sid = deploy.register_scraper(
        "metrokart", "http://127.0.0.1:8765/list?page=1"
    )
    acq = FixtureAcquisition(FIXTURE_DIR / "pages", version="v1_healthy")

    _p("Step 1 — Healthy baseline run", GREEN)
    hr, dec = deploy.run_once(
        sid,
        url="http://127.0.0.1:8765/list?page=1",
        fetch=acq,
        expected=10,
        golden=GOLDEN[:10],
    )
    _kv("status", f"{GREEN}{hr.status}{RESET}")
    _kv("health", f"{hr.health_score*100:.1f}%")
    _kv("action", dec.action)
    _dump_preview_from(acq, sid)

    _p(f"Step 2 — Simulating a site change → switching layout to '{break_to}'", YELLOW)
    acq.switch_version(break_to)

    _p("Step 3 — Running the same scraper on the changed layout", RED)
    hr, dec = deploy.run_once(
        sid,
        url="http://127.0.0.1:8765/list?page=1",
        fetch=acq,
        expected=10,
        golden=GOLDEN[:10],
    )
    _kv("status", f"{RED}{hr.status}{RESET}")
    _kv("health", f"{hr.health_score*100:.1f}%")
    _kv("signals", ", ".join(hr.failure_signals) or "—")
    _kv("action", f"{GREEN if dec.action=='promote' else YELLOW}{dec.action}{RESET}")
    _kv("new_version", dec.new_version)
    _kv("confidence", f"{dec.confidence*100:.1f}%")
    _kv("post_health", f"{(dec.post_health or 0)*100:.1f}%")

    _p("Step 4 — Candidates and winners for this repair", CYAN)
    events = store.list_repair_events(sid, limit=1)
    if events:
        # Print the full plan of the latest repair event.
        with store.connect() as conn:
            row = conn.execute("SELECT plan FROM repair_events WHERE id=?", (events[0]["id"],)).fetchone()
        plan = json.loads(row["plan"])
        for f, fr in plan.get("field_repairs", {}).items():
            winner = fr.get("winner") or {}
            print(f"    {BOLD}{f}{RESET}  action={fr['action']}  reason={fr['reason']}")
            if winner:
                sel = winner["selector"]
                print(f"      winner: {GREEN}{sel['kind']}({sel['value']}){RESET} — score {winner['score']['total']*100:.1f}%")
            for c in fr.get("top_candidates", [])[:3]:
                sel = c["selector"]
                print(f"        · {sel['kind']}({sel['value']}) — {c['source']} — {c['score']['total']*100:.1f}%")

    _p("Step 5 — Verifying recovered records on the new version", GREEN)
    _dump_preview_from(acq, sid)

    _p("Step 6 — Version history", CYAN)
    for v in store.list_versions(sid):
        print(f"    v{v['version']:<2}  {v['reason']:60s}  conf={v['confidence']*100:.1f}%")

    _p("Step 7 — Rolling back to v1 for demonstration", YELLOW)
    dec = deploy.rollback_to(sid, to_version=1, reason="demo rollback")
    _kv("action", dec.action)
    _kv("current_version", store.get_scraper(sid)["current_version"])

    _p("Done. AI proposed. Evidence decided. Repair versioned. Rollback available.", GREEN)


def _dump_preview_from(acq: FixtureAcquisition, scraper_id: int) -> None:
    from backend.scraper.extract import extract as _extract

    strategy = deploy.get_active_strategy(scraper_id)
    snap = acq.fetch("http://127.0.0.1:8765/list?page=1")
    result = _extract(snap.html, strategy, url="http://127.0.0.1:8765/list?page=1")
    _dump_preview(result.records, n=4)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--break",
        dest="break_to",
        default="v3_dataattr",
        help="fixture version to simulate the site change (default: v3_dataattr)",
    )
    args = parser.parse_args()
    run_demo(break_to=args.break_to)


if __name__ == "__main__":
    main()
