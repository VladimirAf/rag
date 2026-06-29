"""Bypass фильтра: probe lookup и should_bypass_rawmid_filter."""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from manufacturer_detect import reload_manufacturer_patterns
from manufacturer_filter import (
    BYPASS_REASON_COMPETITOR_EXPLICIT,
    BYPASS_REASON_SKU_CROSS_BRAND,
    BYPASS_REASON_TWO_BRANDS,
    ProbeHit,
    evaluate_manufacturer_bypass,
    probe_lookup_by_sku,
    should_bypass_rawmid_filter,
    sku_hints_for_probe,
)
from models import QueryParseResults, RAWMID_MANUFACTURER_ID

JTC_ID = 24
EMPTY_PARSE = QueryParseResults(product_names=None, categories=None)


def setup_function():
    reload_manufacturer_patterns()


def test_sku_hints_for_probe_includes_suffix():
    hints = sku_hints_for_probe("сравни RPB-05 и TM-800A")
    assert "RPB-05" in hints
    assert "TM-800A" in hints


def test_probe_lookup_by_sku_real_db():
    """RPB-05 и TM-800A должны резолвиться в разных производителей (46 и 24)."""
    hints = ["RPB-05", "TM-800A"]
    hits = probe_lookup_by_sku(hints)
    mfr_ids = {h.manufacturer_id for h in hits}
    assert RAWMID_MANUFACTURER_ID in mfr_ids
    assert JTC_ID in mfr_ids


def test_should_bypass_sku_cross_brand():
    probe = [
        ProbeHit(model="RPB-05", manufacturer_id=RAWMID_MANUFACTURER_ID),
        ProbeHit(model="TM-800AT", manufacturer_id=JTC_ID),
    ]
    bypass, reason = should_bypass_rawmid_filter(
        "сравни RPB-05 и TM-800A",
        EMPTY_PARSE,
        ["RPB-05", "TM-800A"],
        probe,
        [],
    )
    assert bypass is True
    assert reason == BYPASS_REASON_SKU_CROSS_BRAND


def test_should_bypass_modern_samurai_false():
    bypass, reason = should_bypass_rawmid_filter(
        "сравни Modern и Samurai",
        EMPTY_PARSE,
        [],
        [],
        [],
    )
    assert bypass is False
    assert reason == ""


def test_should_bypass_competitor_explicit():
    probe = [ProbeHit(model="TM-800AT", manufacturer_id=JTC_ID)]
    bypass, reason = should_bypass_rawmid_filter(
        "JTC TM-800A характеристики",
        EMPTY_PARSE,
        ["TM-800A"],
        probe,
        [JTC_ID],
    )
    assert bypass is True
    assert reason == BYPASS_REASON_COMPETITOR_EXPLICIT


def test_should_bypass_blender_recommendation_false():
    bypass, reason = should_bypass_rawmid_filter(
        "посоветуй блендер",
        EMPTY_PARSE,
        [],
        [],
        [],
    )
    assert bypass is False
    assert reason == ""


def test_should_bypass_two_brands_in_query():
    bypass, reason = should_bypass_rawmid_filter(
        "сравни RAWMID и JTC",
        EMPTY_PARSE,
        [],
        [],
        [RAWMID_MANUFACTURER_ID, JTC_ID],
    )
    assert bypass is True
    assert reason == BYPASS_REASON_TWO_BRANDS


def test_cross_brand_ignores_unmarked_manufacturer_id():
    """93 не создаёт ложный кросс-бренд вместе с Rawmid."""
    probe = [
        ProbeHit(model="RPB-05", manufacturer_id=RAWMID_MANUFACTURER_ID),
        ProbeHit(model="unknown-sku", manufacturer_id=93),
    ]
    bypass, reason = should_bypass_rawmid_filter(
        "query",
        EMPTY_PARSE,
        ["RPB-05", "unknown"],
        probe,
        [],
    )
    assert bypass is False
    assert reason == ""


@pytest.mark.parametrize(
    "query,expected_bypass,expected_reason",
    [
        ("сравни RPB-05 и TM-800A", True, BYPASS_REASON_SKU_CROSS_BRAND),
        ("сравни Modern и Samurai", False, ""),
        ("JTC TM-800A характеристики", True, BYPASS_REASON_COMPETITOR_EXPLICIT),
        ("посоветуй блендер", False, ""),
    ],
)
def test_evaluate_manufacturer_bypass_matrix(query, expected_bypass, expected_reason):
    bypass, reason = evaluate_manufacturer_bypass(query)
    assert bypass is expected_bypass
    assert reason == expected_reason


def test_probe_lookup_fixture_sqlite(tmp_path: Path):
    """probe_lookup на изолированной in-memory-подобной БД."""
    db_file = tmp_path / "test.db"
    conn = sqlite3.connect(db_file)
    conn.execute(
        """
        CREATE TABLE products (
            name TEXT, price TEXT, url TEXT, description TEXT, specs TEXT,
            category TEXT, model TEXT, status INTEGER, quantity INTEGER,
            manufacturer_id INTEGER, product_id INTEGER
        )
        """
    )
    conn.execute(
        "INSERT INTO products VALUES (?,?,?,?,?,?,?,?,?,?,?)",
        ("A", "0", "", "", "", "", "SKU-A", 1, 0, 46, 1),
    )
    conn.execute(
        "INSERT INTO products VALUES (?,?,?,?,?,?,?,?,?,?,?)",
        ("B", "0", "", "", "", "", "SKU-B", 1, 0, 24, 2),
    )
    conn.commit()
    conn.close()

    hits = probe_lookup_by_sku(["SKU-A", "SKU-B"], db_path=db_file)
    assert len(hits) == 2
    assert {h.manufacturer_id for h in hits} == {46, 24}
