"""Deduplication tests (guide §4.9)."""

from backend.scraper.dedupe import canonical_url, dedupe, dedupe_key


def _record(**overrides):
    base = {
        "product_id": None,
        "product_name": "Aurora Wireless Headphones",
        "price": 8999.0,
        "currency": "INR",
        "rating": 4.5,
        "review_count": 1284,
        "availability": "In Stock",
        "product_url": "http://127.0.0.1:8765/p/MK-1001",
        "image_url": "http://127.0.0.1:8765/img/MK-1001.jpg",
    }
    base.update(overrides)
    return base


# ----- canonical_url --------------------------------------------------------


def test_canonical_url_strips_tracking_params():
    assert canonical_url("http://x.com/p/1?utm_source=abc&sku=42&gclid=Z") == "http://x.com/p/1?sku=42"


def test_canonical_url_lowercases_host_and_drops_fragment():
    assert canonical_url("HTTP://EXAMPLE.COM/Path#frag") == "http://example.com/Path"


def test_canonical_url_sorts_query():
    assert canonical_url("http://x.com/p?b=2&a=1") == "http://x.com/p?a=1&b=2"


def test_canonical_url_none_and_empty():
    assert canonical_url(None) is None
    assert canonical_url("") is None


# ----- dedupe_key -----------------------------------------------------------


def test_dedupe_key_prefers_product_id():
    assert dedupe_key(_record(product_id="MK-1001")).startswith("id:")


def test_dedupe_key_falls_back_to_canonical_url():
    r = _record(product_id=None, product_url="http://x.com/p?utm_source=z&sku=1")
    key = dedupe_key(r)
    assert key.startswith("url:")
    assert "utm_source" not in key


def test_dedupe_key_hash_fallback_when_no_url_or_id():
    key = dedupe_key(_record(product_id=None, product_url=None))
    assert key.startswith("hash:")


def test_dedupe_key_none_when_nothing_usable():
    assert dedupe_key({"price": 10}) is None


# ----- dedupe main path -----------------------------------------------------


def test_dedupe_removes_id_duplicates_and_merges_missing_fields():
    a = _record(product_id="MK-1", price=100.0, currency=None)
    b = _record(product_id="MK-1", price=None, currency="INR")
    out, report = dedupe([a, b])
    assert len(out) == 1
    # a is more complete on price; b fills currency.
    assert out[0]["price"] == 100.0
    assert out[0]["currency"] == "INR"
    assert report.input_count == 2
    assert report.output_count == 1
    assert report.merged_duplicates == 1
    assert report.keys_by_kind == {"id": 2}


def test_dedupe_drops_records_with_no_key(caplog):
    with caplog.at_level("WARNING"):
        out, report = dedupe([_record(), {"junk": True}])
    assert report.dropped_no_key == 1
    assert report.output_count == 1
    assert any("dropping record with no usable key" in rec.message for rec in caplog.records)


def test_dedupe_preserves_input_order():
    a = _record(product_id="MK-1")
    b = _record(product_id="MK-2")
    c = _record(product_id="MK-3")
    out, _ = dedupe([a, b, c])
    assert [r["product_id"] for r in out] == ["MK-1", "MK-2", "MK-3"]


def test_dedupe_url_canonicalized():
    a = _record(product_id=None, product_url="http://x.com/p/1?utm_source=a")
    b = _record(product_id=None, product_url="http://x.com/p/1?utm_source=b&gclid=Z")
    out, report = dedupe([a, b])
    assert len(out) == 1
    assert report.merged_duplicates == 1
    assert report.keys_by_kind == {"url": 2}


def test_dedupe_empty_input():
    out, report = dedupe([])
    assert out == []
    assert report.input_count == 0
    assert report.output_count == 0
