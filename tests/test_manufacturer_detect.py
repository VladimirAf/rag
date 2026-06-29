"""Детекция производителей в тексте запроса (manufacturer + aliases)."""

from manufacturer_detect import (
    detect_manufacturers_in_query,
    normalize_query_text,
    reload_manufacturer_patterns,
)

JTC_ID = 24
RAWMID_ID = 46


def setup_function():
    reload_manufacturer_patterns()


def test_detect_rawmid_and_jtc():
    result = detect_manufacturers_in_query("сравни RAWMID и JTC")
    assert set(result) == {RAWMID_ID, JTC_ID}


def test_detect_modern_and_samurai_no_brands():
    result = detect_manufacturers_in_query("сравни Modern и Samurai")
    assert result == []


def test_detect_modern_samurai_with_explicit_rawmid():
    result = detect_manufacturers_in_query("сравни RAWMID Modern и Samurai")
    assert set(result) == {RAWMID_ID}


def test_detect_omniblend_alias_maps_to_jtc():
    result = detect_manufacturers_in_query("omniblend TM-800")
    assert JTC_ID in result


def test_detect_rawmid_typos_via_aliases():
    assert RAWMID_ID in detect_manufacturers_in_query("блендер роумид")
    assert RAWMID_ID in detect_manufacturers_in_query("rawmiq blender")


def test_normalize_query_text_yo_to_e():
    assert normalize_query_text("Ёлка") == "елка"


def test_empty_query_returns_empty():
    assert detect_manufacturers_in_query("") == []
    assert detect_manufacturers_in_query("   ") == []
