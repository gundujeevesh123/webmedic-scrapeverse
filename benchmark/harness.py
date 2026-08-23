"""Controlled-breakage benchmark harness (guide §10).

Runs two systems against every fixture version and reports:

    * repair_rate       - fraction of broken fixtures where WebMedic promotes a fix
    * field_accuracy    - fraction of (record × field) pairs matching the golden set
    * false_repairs     - repairs performed on already-healthy fixtures (should be 0)
    * mttr_ms           - mean wall-clock time from failure detection to repair

Systems compared:

    Traditional  - the DEFAULT_STRATEGY, no self-healing.
    WebMedic     - DEFAULT_STRATEGY + candidate generation + shadow-gated promotion.

Usage:
    python -m benchmark.harness            # prints a JSON+ASCII report
    python -m benchmark.harness --json     # JSON only
"""

from __future__ import annotations

import argparse
import json
import time
from dataclasses import dataclass, asdict, field
from pathlib import Path

from backend.healer.repair import broken_fields_from_health, repair
from backend.scraper.extract import extract
from backend.scraper.schema import REQUIRED_FIELDS
from backend.scraper.strategy import DEFAULT_STRATEGY
from backend.validator.detection import DetectionAccuracy, detect_degradation
from backend.validator.health import compute_health


# Ground truth for which fixtures represent real extraction degradation.
DETECTION_TRUTH = {
    "v1_healthy":        False,
    "v2_rename_class":   True,
    "v3_dataattr":       True,
    "v4_change_nesting": False,
    "v5_move_price":     False,
    "v6_label_change":   False,
    "v7_decoy":          False,
    "v8_pagination":     False,
    "v9_combined":       True,
    "v10_partial":       True,
    "v11_semantic":      False,
}


FIXTURES = Path(__file__).resolve().parents[1] / "tests" / "fixtures"
PAGES = FIXTURES / "pages"
GOLDEN = json.load(open(FIXTURES / "golden_dataset.json"))["records"]


@dataclass
class Row:
    version: str
    system: str
    records: int
    completeness: float
    validity: float
    field_accuracy: float
    health: float
    status: str
    repair_attempted: bool = False
    repair_confidence: float = 0.0
    mttr_ms: float = 0.0


@dataclass
class Report:
    rows: list[Row] = field(default_factory=list)

    def summary(self) -> dict:
        by_system: dict[str, list[Row]] = {}
        for r in self.rows:
            by_system.setdefault(r.system, []).append(r)

        broken_versions = {
            r.version for r in self.rows if r.system == "Traditional" and r.status != "healthy"
        }
        healthy_versions = {
            r.version for r in self.rows if r.system == "Traditional" and r.status == "healthy"
        }

        def _system_summary(system: str) -> dict:
            rows = by_system.get(system, [])
            repairs_on_broken = [
                r for r in rows if r.version in broken_versions and r.repair_attempted
            ]
            successes = [r for r in repairs_on_broken if r.status == "healthy"]
            false_reps = [
                r
                for r in rows
                if r.version in healthy_versions and r.repair_attempted
            ]
            mttr = (
                sum(r.mttr_ms for r in repairs_on_broken) / len(repairs_on_broken)
                if repairs_on_broken
                else 0.0
            )
            avg_accuracy = (
                sum(r.field_accuracy for r in rows) / len(rows) if rows else 0.0
            )
            avg_completeness = (
                sum(r.completeness for r in rows) / len(rows) if rows else 0.0
            )
            return {
                "runs": len(rows),
                "avg_field_accuracy": round(avg_accuracy, 4),
                "avg_completeness": round(avg_completeness, 4),
                "healthy_runs": sum(1 for r in rows if r.status == "healthy"),
                "repair_attempts": len(repairs_on_broken),
                "repair_successes": len(successes),
                "repair_rate": round(
                    len(successes) / len(repairs_on_broken), 4
                ) if repairs_on_broken else 0.0,
                "false_repairs": len(false_reps),
                "mttr_ms": round(mttr, 2),
            }

        return {
            "systems": {s: _system_summary(s) for s in by_system},
            "broken_versions": sorted(broken_versions),
            "healthy_versions": sorted(healthy_versions),
        }

    def to_dict(self) -> dict:
        return {
            "rows": [asdict(r) for r in self.rows],
            "summary": self.summary(),
            "detection": detection_accuracy_across_fixtures().to_dict(),
        }


def field_accuracy(records: list[dict], golden: list[dict]) -> float:
    if not records or not golden:
        return 0.0
    n = min(len(records), len(golden))
    if n == 0:
        return 0.0
    correct = 0
    total = 0
    for i in range(n):
        for f in REQUIRED_FIELDS:
            total += 1
            if str(records[i].get(f)) == str(golden[i].get(f)):
                correct += 1
    return correct / total if total else 0.0


def _load_pages(version: str) -> list[str]:
    """Return every page's HTML as separate strings (parse independently)."""
    return [
        (PAGES / version / p).read_text()
        for p in sorted(x.name for x in (PAGES / version).iterdir() if x.name.startswith("page-"))
    ]


def _extract_all(version: str, strategy) -> tuple[list[dict], str]:
    """Extract records from every page of a fixture and return (records, combined_html).

    We keep the combined HTML around because the repair engine needs a single
    representative snapshot to reason about.
    """
    all_records: list[dict] = []
    for html in _load_pages(version):
        r = extract(html, strategy, url="http://127.0.0.1:8765/list")
        all_records.extend(r.records)
    # Use page-1 as the representative snapshot for repair candidate generation.
    representative = _load_pages(version)[0]
    return all_records, representative


def run_traditional(version: str) -> Row:
    records, _ = _extract_all(version, DEFAULT_STRATEGY)
    hr = compute_health(records, expected=len(GOLDEN))
    return Row(
        version=version,
        system="Traditional",
        records=len(records),
        completeness=hr.completeness,
        validity=hr.validity,
        field_accuracy=round(field_accuracy(records, GOLDEN), 4),
        health=hr.health_score,
        status=hr.status,
    )


def run_webmedic(version: str) -> Row:
    records, representative = _extract_all(version, DEFAULT_STRATEGY)
    hr = compute_health(records, expected=len(GOLDEN))
    row = Row(
        version=version,
        system="WebMedic",
        records=len(records),
        completeness=hr.completeness,
        validity=hr.validity,
        field_accuracy=round(field_accuracy(records, GOLDEN), 4),
        health=hr.health_score,
        status=hr.status,
    )
    if hr.status != "healthy":
        broken = broken_fields_from_health(hr)
        start = time.time()
        plan = repair(
            strategy=DEFAULT_STRATEGY,
            html=representative,
            broken_fields=broken,
            url="http://127.0.0.1:8765/list",
            golden=GOLDEN,
        )
        elapsed_ms = (time.time() - start) * 1000

        row.repair_attempted = True
        row.repair_confidence = round(plan.proposed_confidence, 4)
        row.mttr_ms = round(elapsed_ms, 2)

        all_promote = plan.field_repairs and all(
            fr.action == "promote" for fr in plan.field_repairs.values()
        )
        if plan.proposed_strategy and all_promote:
            records2, _ = _extract_all(version, plan.proposed_strategy)
            hr2 = compute_health(records2, expected=len(GOLDEN))
            row.records = len(records2)
            row.completeness = hr2.completeness
            row.validity = hr2.validity
            row.field_accuracy = round(field_accuracy(records2, GOLDEN), 4)
            row.health = hr2.health_score
            row.status = hr2.status
    return row


def run_benchmark() -> Report:
    versions = sorted(p.name for p in PAGES.iterdir() if p.is_dir())
    report = Report()
    for v in versions:
        report.rows.append(run_traditional(v))
        report.rows.append(run_webmedic(v))
    return report


def detection_accuracy_across_fixtures() -> DetectionAccuracy:
    """Score the failure detector on every fixture against the ground-truth labels."""
    acc = DetectionAccuracy()
    baseline_records, baseline_repr = _extract_all("v1_healthy", DEFAULT_STRATEGY)
    baseline_hr = compute_health(baseline_records, expected=len(GOLDEN))
    from backend.scraper.extract import extract as _ex

    baseline_fp = _ex(baseline_repr, DEFAULT_STRATEGY, url="").fingerprint

    for version, expected in DETECTION_TRUTH.items():
        records, representative = _extract_all(version, DEFAULT_STRATEGY)
        hr = compute_health(records, expected=len(GOLDEN))
        fp = _ex(representative, DEFAULT_STRATEGY, url="").fingerprint
        det = detect_degradation(
            hr, baseline=baseline_hr,
            current_fingerprint=fp, baseline_fingerprint=baseline_fp,
        )
        acc.observe(expected_detected=expected, actually_detected=det.detected)
    return acc


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #


def _fmt_row(r: Row) -> str:
    ind = " " if r.system == "Traditional" else "*"
    return (
        f"  {ind} {r.version:20s} {r.system:11s} "
        f"records={r.records:2d}/20  acc={r.field_accuracy*100:5.1f}%  "
        f"health={r.health*100:5.1f}%  status={r.status:16s}"
        + (f"  repair={r.repair_confidence*100:5.1f}%  mttr={r.mttr_ms:5.0f}ms"
           if r.repair_attempted else "")
    )


def render_markdown(report: "Report") -> str:
    """Produce a README-ready Markdown summary of the benchmark."""
    summary = report.summary()
    det = detection_accuracy_across_fixtures().to_dict()
    lines: list[str] = []
    lines.append("## Benchmark - controlled breakage across 11 fixture layouts, 20 records each")
    lines.append("")
    lines.append(
        "|  System      | Runs | Healthy | Field accuracy | Completeness | Repairs | False repairs | MTTR   |"
    )
    lines.append(
        "| ------------ | :--: | :-----: | :------------: | :----------: | :-----: | :-----------: | :----: |"
    )
    for sys_name, m in summary["systems"].items():
        acc = f"{m['avg_field_accuracy']*100:.2f}%"
        comp = f"{m['avg_completeness']*100:.2f}%"
        healthy = f"{m['healthy_runs']} / {m['runs']}"
        repairs = f"{m['repair_successes']} / {m['repair_attempts']}" if m['repair_attempts'] else "-"
        false_r = str(m['false_repairs'])
        mttr = f"{m['mttr_ms']:.0f} ms" if m['mttr_ms'] else "-"
        lines.append(
            f"| **{sys_name}** | {m['runs']} | {healthy} | {acc} | {comp} | {repairs} | {false_r} | {mttr} |"
        )
    lines.append("")
    lines.append(f"**Detector precision:** {det['precision']:.2f} · "
                 f"**recall:** {det['recall']:.2f} · "
                 f"**F1:** {det['f1']:.2f} · "
                 f"({det['true_positives']} TP · {det['true_negatives']} TN · "
                 f"{det['false_positives']} FP · {det['false_negatives']} FN)")
    lines.append("")
    lines.append("### Per-fixture detail")
    lines.append("")
    lines.append(
        "| Fixture | Traditional health / status | WebMedic health / status | Repair | Confidence | MTTR |"
    )
    lines.append(
        "| ------- | -------------------------- | ------------------------ | :----: | :--------: | :--: |"
    )
    versions = sorted({r.version for r in report.rows})
    by_key = {(r.version, r.system): r for r in report.rows}
    for v in versions:
        t = by_key[(v, "Traditional")]
        w = by_key[(v, "WebMedic")]
        repair = "yes" if w.repair_attempted else "no"
        conf = f"{w.repair_confidence*100:.1f}%" if w.repair_attempted else "-"
        mttr = f"{w.mttr_ms:.0f} ms" if w.repair_attempted else "-"
        lines.append(
            f"| `{v}` | {t.health*100:.1f}% {t.status} | "
            f"{w.health*100:.1f}% {w.status} | {repair} | {conf} | {mttr} |"
        )
    lines.append("")
    return "\n".join(lines)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--json", action="store_true", help="only emit JSON")
    parser.add_argument("--md", action="store_true", help="emit README-ready Markdown")
    parser.add_argument("--out", type=Path, default=None, help="write JSON report to file")
    parser.add_argument("--md-out", type=Path, default=None, help="write Markdown report to file")
    args = parser.parse_args()

    report = run_benchmark()
    if args.out:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(json.dumps(report.to_dict(), indent=2), encoding="utf-8")
    if args.md_out:
        args.md_out.parent.mkdir(parents=True, exist_ok=True)
        args.md_out.write_text(render_markdown(report), encoding="utf-8")

    if args.json:
        print(json.dumps(report.to_dict(), indent=2))
        return
    if args.md:
        print(render_markdown(report))
        return

    print("Benchmark: controlled-breakage suite")
    print("=" * 70)
    for row in report.rows:
        print(_fmt_row(row))
    print("-" * 70)
    print("Summary:")
    print(json.dumps(report.summary(), indent=2))
    print("Detection accuracy (should we alarm?):")
    print(json.dumps(detection_accuracy_across_fixtures().to_dict(), indent=2))


if __name__ == "__main__":
    main()
