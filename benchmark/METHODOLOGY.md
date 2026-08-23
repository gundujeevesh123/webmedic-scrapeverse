# Benchmark methodology (frozen)

The benchmark compares a *Traditional* selector-based scraper against
*WebMedic* (self-healing) across a controlled-breakage suite. Every definition
below is what the harness in `benchmark/harness.py` actually computes. If any
number in a published report can't be traced back to this document, the
report is wrong.

---

## 1. Test fixtures

Eleven controlled versions of the same synthetic e-commerce catalog
("MetroKart"), each with two pages of 10 product cards → 20 records total.

Generation is deterministic — `python tests/fixtures/generate_fixtures.py`
regenerates every fixture from the golden dataset bit-for-bit.

Ground-truth labels (used for detection accuracy):

| Fixture             | Real extraction breakage? |
|---------------------|:-------------------------:|
| `v1_healthy`         | no                       |
| `v2_rename_class`    | **yes** (`.price` → `.cost`) |
| `v3_dataattr`        | **yes** (classes → `[data-testid]`) |
| `v4_change_nesting`  | no (descendant selectors keep working) |
| `v5_move_price`      | no |
| `v6_label_change`    | no |
| `v7_decoy`           | no (baseline uses `span.price`, avoids the `<s.price>` decoy) |
| `v8_pagination`      | no (pagination-only change; data still extracted) |
| `v9_combined`        | **yes** (rename + nesting + decoy) |
| `v10_partial`        | **yes** (`div.availability` → `div.stock-status`) |
| `v11_semantic`       | no (normalizer maps "Available" → "In Stock") |

## 2. Golden dataset

`tests/fixtures/golden_dataset.json` — 20 manually-verified records, each
containing every field in the target schema (`product_id`, `product_name`,
`price`, `currency`, `rating`, `review_count`, `availability`,
`product_url`, `image_url`).

The dataset is authoritative — every metric below is scored against it.

---

## 3. Metric definitions

Each metric operates on a set of records produced by one system on one
fixture. Records are always compared against the first N records of the
golden dataset where N = number of records the system produced. This means
"partial extraction" (fewer records than golden) is penalized in
`completeness` and `record_consistency`, not silently accepted.

### 3.1 Field Accuracy

For a record-set of length `N`:

    field_accuracy = (# fields matching golden by str(value)) / (N × |REQUIRED_FIELDS|)

Where `REQUIRED_FIELDS` is the 8-field schema. This is the strictest of the
metrics — a `None` where golden has a value counts against accuracy, and any
extracted value that doesn't string-match golden also counts against.

The benchmark's per-system `avg_field_accuracy` is the arithmetic mean of
per-fixture field accuracies (11 fixtures for each system).

### 3.2 Completeness

For a record-set:

    completeness = (# non-null required fields) / (N × |REQUIRED_FIELDS|)

Bounded to [0, 1]. Independent of golden — measures whether the extractor
returned *anything at all* per field. `avg_completeness` in the summary is
the mean across all 11 fixtures.

### 3.3 Repair Success Rate

Denominator = number of fixtures on which the *Traditional* system was NOT
healthy (i.e. real extraction breakage occurred) AND the *WebMedic* system
attempted a repair.

Numerator = of those, how many post-repair extractions reached status
`healthy`.

    repair_rate = successful_repairs / repair_attempts

Reported as `repair_successes / repair_attempts` for transparency.

### 3.4 False Repairs

Numerator = number of fixtures where the Traditional system WAS healthy
(no real breakage) but WebMedic still triggered a repair.

Denominator = total number of Traditional-healthy fixtures.

A well-behaved self-healer must have `false_repairs = 0`. Any non-zero value
is a bug in either the failure detector or the repair-triggering logic.

### 3.5 Detection Accuracy (Precision / Recall / F1)

Computed by `detect_degradation()` per fixture:

- **TP** — expected breakage, detector fired.
- **TN** — no expected breakage, detector silent.
- **FP** — no expected breakage, detector fired (false alarm).
- **FN** — expected breakage, detector silent (missed regression).

Then:

    precision = TP / (TP + FP)
    recall    = TP / (TP + FN)
    F1        = 2·P·R / (P + R)
    accuracy  = (TP + TN) / (TP + TN + FP + FN)

Ground truth is the "Real extraction breakage?" column in §1.

### 3.6 Recovery Time (MTTR)

MTTR = wall-clock time between the moment the failing health report is
produced and the moment the repair engine returns a `RepairPlan` (with the
proposed strategy scored and gated).

    mttr_ms = mean(elapsed_ms across all successful repairs)

The harness times each repair with `time.time()` before/after
`repair(...)`. Reported in milliseconds.

---

## 4. What the harness does NOT count

- **Cosmetic HTML changes are not repair attempts.** If Traditional stays
  `healthy` on a fixture and WebMedic also stays `healthy`, no repair was
  attempted — that's a true negative for the detector, but does not
  contribute to `repair_attempts`.
- **Pagination-only failures on the first page.** `v8_pagination` breaks
  the next-page follower but data extraction on the first page is fine;
  both systems report healthy on this benchmark because we run per-page.
- **LLM-suggested candidates that score below `min_accept` are recorded but
  not counted as repairs.** A candidate that fails the gate produces a
  `shadow` or `reject` event, not a `promote`.

---

## 5. Why the numbers you see

For the current fixture set (11 fixtures, 20 records each):

- **Traditional avg field accuracy = 89.77%**: 7 fixtures at 100% + 4
  broken fixtures where price/availability/etc. are missing. Mean of
  {1.0, 1.0, 1.0, 1.0, 1.0, 1.0, 1.0, 0.875, 0.25, 0.875, 0.875} is 89.77%.
- **WebMedic avg field accuracy = 100.00%**: All 11 fixtures pass after
  repair (or without needing repair).
- **Repairs 4 / 4**: exactly the four fixtures labeled "yes" in §1.
- **False repairs = 0**: v4, v5, v7 all differ in DOM fingerprint from v1
  but data extraction stays fine — the detector correctly stays silent.
- **Detector precision/recall/F1 = 1.00**: 4 TP + 7 TN, no FP or FN.
- **MTTR ~ 350–450 ms**: fair for a heuristic candidate generator running
  ~50 candidates against 10 records per fixture, on a single Python process.

---

## 6. How to reproduce

Clean state → deterministic result:

    rm -f data/webmedic.sqlite
    python tests/fixtures/generate_fixtures.py
    python -m benchmark.harness --out benchmark/results/latest.json \
                                --md-out benchmark/results/README_snippet.md

The `--json` and `--md` flags print to stdout instead of writing files.

## 7. Independent verification

`tests/integration/test_benchmark_verify.py` re-implements each metric from
scratch (from raw records + fixtures + golden dataset) and asserts the
harness's numbers match — the tests fail loudly if the harness's summary
ever disagrees with an independent recount.

Freeze rule: any change to metric formulas must update this document AND the
`test_benchmark_verify.py` reference implementation in the same commit.
