"""Guard NER-категорий при exact SKU: не подменять direct lookup ошибочным NER-фильтром."""

from contextlib import contextmanager
from unittest.mock import MagicMock

import pytest

from models import PRODUCT_COLS, QueryParseResults, RAWMID_MANUFACTURER_ID

import core


def _product_row(**kw):
    data = {col: None for col in PRODUCT_COLS}
    data.update(kw)
    return tuple(data[col] for col in PRODUCT_COLS)


def _make_fake_database(direct_rows, fts_rows=None, direct_rows_by_sku=None):
    fts_rows = fts_rows or []
    direct_rows_by_sku = direct_rows_by_sku or {}
    cursor = MagicMock()
    fts_calls = []

    def execute(sql, params=None):
        if "products_fts5" in sql:
            fts_calls.append((sql, params))
            cursor.fetchall.return_value = fts_rows
        elif direct_rows_by_sku and params:
            sku_low = (params[0] or "").strip().lower()
            cursor.fetchall.return_value = direct_rows_by_sku.get(sku_low, [])
        else:
            cursor.fetchall.return_value = direct_rows

    cursor.execute.side_effect = execute
    db = MagicMock()
    db.cursor = cursor
    return db, fts_calls


def _patch_find_products_db(
    monkeypatch,
    direct_rows,
    fts_rows=None,
    direct_rows_by_sku=None,
):
    state = {}

    class FakeDatabase:
        def __enter__(self):
            db, fts_calls = _make_fake_database(
                direct_rows,
                fts_rows=fts_rows,
                direct_rows_by_sku=direct_rows_by_sku,
            )
            state["db"] = db
            state["fts_calls"] = fts_calls
            return db

        def __exit__(self, *args):
            return False

    monkeypatch.setattr(core, "Database", FakeDatabase)
    monkeypatch.setattr(
        core,
        "evaluate_manufacturer_bypass",
        lambda *a, **k: (False, ""),
    )
    monkeypatch.setattr(core.dbfuncs, "find_mentioned_products", lambda **k: [])
    return state


def test_ner_category_guard_keeps_direct_sku_over_wrong_ner(monkeypatch, caplog):
    """Кейс 5793: direct RMC-01 + NER «Мультиварки» + шум RMP-04 → остаётся RMC-01."""
    direct_rows = [
        _product_row(
            model="RMC-01",
            name="Кофемашина RMC-01",
            status=1,
            category="Кофемашины",
            price="16900 руб.",
            quantity=1,
            manufacturer_id=RAWMID_MANUFACTURER_ID,
        )
    ]
    fts_rows = [
        _product_row(
            model="RMP-04",
            name="Мультиварка RMP-04",
            status=1,
            category="Мультиварки",
            price="5000 руб.",
            quantity=1,
            manufacturer_id=RAWMID_MANUFACTURER_ID,
        )
    ]
    _patch_find_products_db(monkeypatch, direct_rows, fts_rows=fts_rows)
    monkeypatch.setattr(
        core.llm_funcs,
        "parse_query",
        lambda q, model_name=None: QueryParseResults(
            product_names=["RMC-01", "RMP-04"],
            categories=["Мультиварки"],
            other_products=False,
        ),
    )

    with caplog.at_level("INFO"):
        result = core.find_products("капсулы не прокалываются RMC-01")

    models = [p.model for p in result.products]
    assert "RMC-01" in models
    assert "RMP-04" not in models
    assert "skipped_ner_category_filter_reason=direct_sku" in caplog.text


def test_ner_category_filter_unchanged_without_direct_sku(monkeypatch):
    """Запрос только по категории без SKU — NER-фильтр работает как раньше."""
    vacuum_rows = [
        _product_row(
            model="RMV-02",
            name="Вакууматор RMV-02",
            status=1,
            category="Вакууматоры",
            price="12000 руб.",
            quantity=2,
            manufacturer_id=RAWMID_MANUFACTURER_ID,
        ),
        _product_row(
            model="ACC-01",
            name="Пакеты для вакууматора",
            status=1,
            category="Аксессуары для вакууматоров",
            price="500 руб.",
            quantity=10,
            manufacturer_id=RAWMID_MANUFACTURER_ID,
        ),
    ]
    _patch_find_products_db(monkeypatch, [], fts_rows=vacuum_rows)
    monkeypatch.setattr(
        core.llm_funcs,
        "parse_query",
        lambda q, model_name=None: QueryParseResults(
            product_names=["вакууматор"],
            categories=["Вакууматоры"],
            other_products=False,
        ),
    )

    result = core.find_products("нужен вакууматор")

    models = [p.model for p in result.products]
    assert models == ["RMV-02"]
    assert "ACC-01" not in models


def test_ner_category_guard_keeps_all_requested_skus_in_comparison(monkeypatch, caplog):
    """Кейс 5942: сравни RPB-05 и TM-800AT — оба SKU, не только direct hit."""
    direct_rows_by_sku = {
        "rpb-05": [
            _product_row(
                model="RPB-05",
                name="Блендер RPB-05",
                status=1,
                category="Блендеры",
                price="15000 руб.",
                quantity=1,
                manufacturer_id=RAWMID_MANUFACTURER_ID,
            )
        ],
        "tm-800at": [
            _product_row(
                model="TM-800AT",
                name="Блендер TM-800AT",
                status=1,
                category="Блендеры > Профессиональные блендеры",
                price="45000 руб.",
                quantity=1,
                manufacturer_id=RAWMID_MANUFACTURER_ID,
            )
        ],
    }
    _patch_find_products_db(monkeypatch, [], direct_rows_by_sku=direct_rows_by_sku)
    monkeypatch.setattr(
        core.llm_funcs,
        "parse_query",
        lambda q, model_name=None: QueryParseResults(
            product_names=["RPB-05", "TM-800AT"],
            categories=["Блендеры > Профессиональные блендеры"],
            other_products=False,
        ),
    )

    with caplog.at_level("INFO"):
        result = core.find_products("сравни RPB-05 и TM-800AT")

    models = [p.model for p in result.products]
    assert models == ["RPB-05", "TM-800AT"]
    assert "skipped_ner_category_filter_reason=direct_sku" in caplog.text
