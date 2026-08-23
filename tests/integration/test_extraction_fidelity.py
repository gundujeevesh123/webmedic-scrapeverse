"""End-to-end acquisition + extraction fidelity per fixture.

For every fixture that the baseline strategy is expected to handle, verify
that the extracted records match the golden dataset exactly, that the schema
is fully populated, and that dedup does not accidentally remove real records.

Fixtures known to break the baseline are excluded (they are the repair
engine's job in a later phase).
"""

import json
from pathlib import Path

import pytest

from backend.acquisition.fixture import FixtureAcquisition
from backend.config import FIXTURE_DIR
from backend.scraper.extract import extract
from backend.scraper.schema import REQUIRED_FIELDS
from backend.scraper.strategy import DEFAULT_STRATEGY


GOLDEN = json.load(open(FIXTURE_DIR / "golden_dataset.json"))["records"]


HANDLED_BY_BASELINE = [
    "v1_healthy",
    "v4_change_nesting",
    "v5_move_price",
    "v6_label_change",
    "v7_decoy",
    "v8_pagination",
    "v11_semantic",
]


@pytest.mark.parametrize("version", HANDLED_BY_BASELINE)
def test_baseline_extracts_full_golden_dataset(version):
    acq = FixtureAcquisition(FIXTURE_DIR / "pages", version=version)
    all_records: list[dict] = []
    for page in (1, 2):
        snap = acq.fetch(f"http://127.0.0.1:8765/list?page={page}")
        result = extract(snap.html, DEFAULT_STRATEGY, url=snap.url)
        all_records.extend(result.records)

    assert len(all_records) == len(GOLDEN), f"{version}: got {len(all_records)} records"

    for got, expected in zip(all_records, GOLDEN):
        assert got["product_id"] == expected["product_id"], version
        assert got["product_name"] == expected["product_name"], version
        assert got["price"] == expected["price"], version
        assert got["currency"] == expected["currency"], version
        assert got["rating"] == expected["rating"], version
        assert got["review_count"] == expected["review_count"], version
        assert got["availability"] == expected["availability"], version
        assert got["product_url"] == expected["product_url"], version
        assert got["image_url"] == expected["image_url"], version


def test_extracted_records_match_schema_types():
    acq = FixtureAcquisition(FIXTURE_DIR / "pages", version="v1_healthy")
    snap = acq.fetch("http://127.0.0.1:8765/list?page=1")
    records = extract(snap.html, DEFAULT_STRATEGY, url=snap.url).records
    for r in records:
        for f in REQUIRED_FIELDS:
            assert r.get(f) is not None, f"field {f} missing in {r}"
        assert isinstance(r["price"], float)
        assert isinstance(r["rating"], float)
        assert isinstance(r["review_count"], int)
        assert r["product_url"].startswith("http")
        assert r["image_url"].startswith("http")


def test_extraction_dedupe_populated():
    acq = FixtureAcquisition(FIXTURE_DIR / "pages", version="v1_healthy")
    snap = acq.fetch("http://127.0.0.1:8765/list?page=1")
    result = extract(snap.html, DEFAULT_STRATEGY, url=snap.url)
    assert result.dedupe_report is not None
    assert result.dedupe_report.input_count == 10
    assert result.dedupe_report.output_count == 10
    assert result.dedupe_report.keys_by_kind == {"id": 10}


def test_extraction_across_pages_gives_unique_products():
    acq = FixtureAcquisition(FIXTURE_DIR / "pages", version="v1_healthy")
    all_records = []
    for page in (1, 2):
        snap = acq.fetch(f"http://127.0.0.1:8765/list?page={page}")
        all_records.extend(extract(snap.html, DEFAULT_STRATEGY, url=snap.url).records)
    ids = [r["product_id"] for r in all_records]
    assert len(ids) == len(set(ids)) == 20


def test_extraction_survives_intentionally_duplicated_html():
    """If the same product card appears twice inside a page, dedupe collapses it."""
    import re as _re

    acq = FixtureAcquisition(FIXTURE_DIR / "pages", version="v1_healthy")
    snap = acq.fetch("http://127.0.0.1:8765/list?page=1")
    # Duplicate every <article>…</article> block in place so we have 20 cards.
    dupe_html = _re.sub(
        r"(<article class=\"product-card\".*?</article>)",
        r"\1\1",
        snap.html,
        flags=_re.DOTALL,
    )
    result = extract(dupe_html, DEFAULT_STRATEGY, url=snap.url)
    assert result.dedupe_report is not None
    assert result.dedupe_report.input_count == 20, "duplicated articles did not double the card count"
    assert result.dedupe_report.output_count == 10, "dedupe did not collapse duplicates"
    assert result.dedupe_report.merged_duplicates == 10


def test_missing_fields_do_not_raise():
    """v3_dataattr yields records with mostly null values — extraction must not raise."""
    acq = FixtureAcquisition(FIXTURE_DIR / "pages", version="v3_dataattr")
    snap = acq.fetch("http://127.0.0.1:8765/list?page=1")
    result = extract(snap.html, DEFAULT_STRATEGY, url=snap.url)
    # 10 records still extracted, most fields None — that is fine.
    assert len(result.records) == 10
    # product_id still comes through because it's on the record root, not inside.
    assert all(r["product_id"] for r in result.records)
