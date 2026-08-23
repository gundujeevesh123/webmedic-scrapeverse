"""Unit tests for the extraction engine."""

import json
from pathlib import Path

from backend.scraper.extract import extract
from backend.scraper.strategy import DEFAULT_STRATEGY

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures"
GOLDEN = json.load(open(FIXTURES / "golden_dataset.json"))["records"]


def _load(version: str, page: int = 1) -> str:
    return (FIXTURES / "pages" / version / f"page-{page}.html").read_text()


def test_v1_healthy_matches_golden_page1():
    result = extract(_load("v1_healthy", 1), DEFAULT_STRATEGY, url="http://127.0.0.1:8765/list?page=1")
    assert len(result.records) == 10
    for got, expected in zip(result.records, GOLDEN[:10]):
        assert got["product_name"] == expected["product_name"]
        assert got["price"] == expected["price"]
        assert got["currency"] == expected["currency"]
        assert got["rating"] == expected["rating"]
        assert got["review_count"] == expected["review_count"]
        assert got["availability"] == expected["availability"]
        assert got["product_url"] == expected["product_url"]
        assert got["image_url"] == expected["image_url"]


def test_v2_rename_breaks_price_only():
    result = extract(_load("v2_rename_class"), DEFAULT_STRATEGY, url="http://127.0.0.1:8765/list")
    assert len(result.records) == 10
    assert all(r["price"] is None for r in result.records)
    # Other fields still populated
    assert all(r["product_name"] for r in result.records)
    assert all(r["availability"] for r in result.records)


def test_v10_partial_breaks_availability_only():
    result = extract(_load("v10_partial"), DEFAULT_STRATEGY, url="http://127.0.0.1:8765/list")
    assert all(r["price"] for r in result.records)
    assert all(r["availability"] is None for r in result.records)


def test_pagination_next_link_extracted():
    result = extract(_load("v1_healthy", 1), DEFAULT_STRATEGY, url="http://127.0.0.1:8765/list?page=1")
    assert result.next_page_url and result.next_page_url.endswith("page=2")


def test_pagination_broken_when_next_class_disappears():
    result = extract(_load("v8_pagination"), DEFAULT_STRATEGY, url="http://127.0.0.1:8765/list")
    assert result.next_page_url is None
