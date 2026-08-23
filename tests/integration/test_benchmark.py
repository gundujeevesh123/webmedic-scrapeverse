"""Benchmark harness tests — locks in the demo numbers."""

from benchmark.harness import run_benchmark


def test_benchmark_traditional_baseline_breaks_on_expected_versions():
    report = run_benchmark()
    broken = report.summary()["broken_versions"]
    # The four fixtures that should always break the traditional strategy.
    assert set(broken) >= {"v2_rename_class", "v3_dataattr", "v9_combined", "v10_partial"}


def test_benchmark_webmedic_repairs_all_breakages():
    report = run_benchmark()
    summary = report.summary()["systems"]["WebMedic"]
    assert summary["healthy_runs"] == summary["runs"]
    assert summary["repair_rate"] == 1.0
    assert summary["false_repairs"] == 0
    assert summary["avg_field_accuracy"] >= 0.95


def test_benchmark_webmedic_is_never_worse_than_traditional():
    report = run_benchmark()
    trad_rows = {r.version: r for r in report.rows if r.system == "Traditional"}
    wm_rows = {r.version: r for r in report.rows if r.system == "WebMedic"}
    for v in trad_rows:
        assert wm_rows[v].field_accuracy >= trad_rows[v].field_accuracy, v


def test_benchmark_mttr_reasonable():
    report = run_benchmark()
    summary = report.summary()["systems"]["WebMedic"]
    # Repairs should be sub-5 seconds on a laptop-class machine.
    assert 0 < summary["mttr_ms"] < 5000
