"""Неразмеченные manufacturer_id: нормализация CSV и константы."""

from manufacturer_constants import (
    RAWMID_MANUFACTURER_ID,
    UNMARKED_MANUFACTURER_ID,
    normalize_csv_manufacturer_id,
)


def test_unmarked_constant_is_not_rawmid():
    assert UNMARKED_MANUFACTURER_ID != RAWMID_MANUFACTURER_ID
    assert UNMARKED_MANUFACTURER_ID == 93


def test_normalize_csv_zero_and_empty_to_93():
    assert normalize_csv_manufacturer_id("0") == 93
    assert normalize_csv_manufacturer_id("") == 93
    assert normalize_csv_manufacturer_id("NULL") == 93
    assert normalize_csv_manufacturer_id(None) == 93


def test_normalize_csv_keeps_explicit_ids():
    assert normalize_csv_manufacturer_id("46") == 46
    assert normalize_csv_manufacturer_id("93") == 93
    assert normalize_csv_manufacturer_id("24") == 24
