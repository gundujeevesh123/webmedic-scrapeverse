"""Unit tests for the value normalizer."""

from backend.scraper.normalize import (
    parse_availability,
    parse_currency,
    parse_int_from_text,
    parse_price,
    parse_rating,
    parse_url,
    normalize,
)


def test_price_us_format():
    assert parse_price("$1,299.00") == 1299.0
    assert parse_price("USD 1299") == 1299.0
    assert parse_price("₹8,999.00") == 8999.0


def test_price_eu_format():
    assert parse_price("1.299,00 €") == 1299.0
    assert parse_price("1.299,50 EUR") == 1299.5


def test_price_negative_rejected():
    assert parse_price("-50") is None


def test_price_dirty_text():
    assert parse_price("Buy for ₹ 8,999.00 INR") == 8999.0
    assert parse_price("Free!") is None


def test_currency_symbol_and_iso():
    assert parse_currency("₹8,999.00") == "INR"
    assert parse_currency("$1,299.00") == "USD"
    assert parse_currency("1.299,00 €") == "EUR"
    assert parse_currency("INR") == "INR"


def test_rating_squashes_to_five_scale():
    assert parse_rating("4.5") == 4.5
    assert parse_rating("Rated 4.5/5") == 4.5
    assert parse_rating("8/10") == 4.0
    assert parse_rating("90/100") == 4.5
    assert parse_rating("bogus") is None


def test_int_from_text_handles_thousands():
    assert parse_int_from_text("1,284") == 1284
    assert parse_int_from_text("5,210 reviews") == 5210
    assert parse_int_from_text("no reviews") is None
    assert parse_int_from_text("-5") is None


def test_availability_maps_synonyms():
    assert parse_availability("In Stock") == "In Stock"
    assert parse_availability("Availability: Available") == "In Stock"
    assert parse_availability("Out of Stock") == "Out of Stock"
    assert parse_availability("Unavailable") == "Out of Stock"
    assert parse_availability("") is None


def test_url_absolute_and_relative():
    assert parse_url("/p/1", base_url="http://127.0.0.1:8765") == "http://127.0.0.1:8765/p/1"
    assert (
        parse_url("http://127.0.0.1:8765/p/1", base_url="http://x")
        == "http://127.0.0.1:8765/p/1"
    )


def test_normalize_dispatch_covers_all_transforms():
    assert normalize("price", "price", "₹8,999") == 8999.0
    assert normalize("rating", "rating", "4.5") == 4.5
    assert normalize("review_count", "int_from_text", "1,284 reviews") == 1284
    assert normalize("product_name", "text", "  Aurora  Wireless ") == "Aurora Wireless"
