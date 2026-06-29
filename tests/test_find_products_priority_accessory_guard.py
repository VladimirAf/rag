"""Ослабление best_priority для аксессуаров при other_products от NER."""

from unittest.mock import MagicMock

import pytest

from models import PRODUCT_COLS, Product, QueryParseResults, RAWMID_MANUFACTURER_ID

import core


def _product_row(**kw):
    data = {col: None for col in PRODUCT_COLS}
    data.update(kw)
    return tuple(data[col] for col in PRODUCT_COLS)


def _patch_find_products_db(monkeypatch, direct_rows):
    class FakeDatabase:
        def __enter__(self):
            cursor = MagicMock()
            cursor.execute.side_effect = lambda sql, params=None: setattr(
                cursor, "fetchall", lambda: direct_rows
            )
            db = MagicMock()
            db.cursor = cursor
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


@pytest.mark.parametrize(
    "response,query,expected",
    [
        (
            QueryParseResults(product_names=["RMC-01"], categories=None, other_products=True),
            "",
            True,
        ),
        (
            QueryParseResults(product_names=["блендер"], categories=["Блендеры"], other_products=False),
            "блендер",
            False,
        ),
        (
            QueryParseResults(
                product_names=["RPB-05", "TM-800AT"],
                categories=["Блендеры"],
                other_products=False,
            ),
            "",
            False,
        ),
        (
            QueryParseResults(
                product_names=["вакууматор"],
                categories=["Вакууматоры"],
                other_products=None,
            ),
            "",
            False,
        ),
        (
            QueryParseResults(
                product_names=["Мешок для орехового молока"],
                categories=["Аксессуары > Соковыжималки"],
                other_products=False,
            ),
            "Мешок для орехового молока аксессуар",
            True,
        ),
        (
            QueryParseResults(
                product_names=["лопатка"],
                categories=None,
                other_products=False,
            ),
            "силиконовая лопатка",
            True,
        ),
        (
            QueryParseResults(
                product_names=None,
                categories=["Запчасти > Блендеры"],
                other_products=False,
            ),
            "штырь в мотор блендера",
            True,
        ),
        (
            QueryParseResults(
                product_names=["BDS-04"],
                categories=["Блендеры"],
                other_products=False,
            ),
            "нож к BDS-04",
            True,
        ),
    ],
)
def test_should_keep_accessory_products(response, query, expected):
    assert core._should_keep_accessory_products(response, query=query) is expected


def test_should_keep_accessory_products_with_schema_lookup():
    response = QueryParseResults(product_names=["BDS-04"], categories=None, other_products=False)
    assert core._should_keep_accessory_products(response, schema_lookup_used=True) is True


def test_best_priority_keeps_accessories_when_other_products(monkeypatch, caplog):
    """«капсулы дешевле RMC-01»: якорь и картриджи не отрезаются best_priority."""
    direct_rows = [
        _product_row(
            model="RMC-01",
            name="Кофемашина RMC-01",
            status=1,
            category="Кофемашины",
            price="16900 руб.",
            quantity=1,
            manufacturer_id=RAWMID_MANUFACTURER_ID,
        ),
        _product_row(
            model="RMC-CAP-10",
            name="Капсулы для RMC-01 (10 шт)",
            status=1,
            category="Аксессуары для кофемашин",
            price="890 руб.",
            quantity=5,
            manufacturer_id=RAWMID_MANUFACTURER_ID,
        ),
    ]
    _patch_find_products_db(monkeypatch, direct_rows)
    monkeypatch.setattr(
        core.llm_funcs,
        "parse_query",
        lambda q, model_name=None: QueryParseResults(
            product_names=["RMC-01"],
            categories=["Кофемашины"],
            other_products=True,
        ),
    )
    monkeypatch.setattr(core.llm_funcs, "find_related_products", lambda *a, **k: [])

    with caplog.at_level("INFO"):
        result = core.find_products("капсулы дешевле RMC-01")

    models = {p.model for p in result.products}
    assert "RMC-01" in models
    assert "RMC-CAP-10" in models
    assert "priority_filter_mode=skip_best_priority" in caplog.text


def test_best_priority_unchanged_without_other_products(monkeypatch, caplog):
    """other_products=false — только основной товар (регрессия)."""
    direct_rows = [
        _product_row(
            model="RMC-01",
            name="Кофемашина RMC-01",
            status=1,
            category="Кофемашины",
            price="16900 руб.",
            quantity=1,
            manufacturer_id=RAWMID_MANUFACTURER_ID,
        ),
        _product_row(
            model="RMC-CAP-10",
            name="Капсулы для RMC-01 (10 шт)",
            status=1,
            category="Аксессуары для кофемашин",
            price="890 руб.",
            quantity=5,
            manufacturer_id=RAWMID_MANUFACTURER_ID,
        ),
    ]
    _patch_find_products_db(monkeypatch, direct_rows)
    monkeypatch.setattr(
        core.llm_funcs,
        "parse_query",
        lambda q, model_name=None: QueryParseResults(
            product_names=["RMC-01"],
            categories=["Кофемашины"],
            other_products=False,
        ),
    )

    with caplog.at_level("INFO"):
        result = core.find_products("кофемашина RMC-01")

    models = [p.model for p in result.products]
    assert models == ["RMC-01"]
    assert "priority_filter_mode=skip_best_priority" not in caplog.text


def test_best_priority_keeps_accessory_product_for_accessory_query(monkeypatch, caplog):
    """rareq #6895: FTS находит мешок и блендер — аксессуар не режется best_priority."""
    fts_rows = [
        _product_row(
            model="Nut Milk Bag",
            name="Мешок для орехового молока",
            status=1,
            category="Аксессуары > Блендеры",
            price="700 руб.",
            quantity=154,
            manufacturer_id=RAWMID_MANUFACTURER_ID,
            product_id=502,
        ),
        _product_row(
            model="RPB-04",
            name="Профессиональный вакуумный блендер PRO RPB-04",
            status=1,
            category="Блендеры",
            price="29900 руб.",
            quantity=233,
            manufacturer_id=RAWMID_MANUFACTURER_ID,
            product_id=3168,
        ),
    ]

    class FakeDatabase:
        def __enter__(self):
            cursor = MagicMock()
            cursor.execute.side_effect = lambda sql, params=None: setattr(
                cursor, "fetchall", lambda: fts_rows
            )
            db = MagicMock()
            db.cursor = cursor
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
    monkeypatch.setattr(
        core.llm_funcs,
        "parse_query",
        lambda q, model_name=None: QueryParseResults(
            product_names=["Мешок для орехового молока RAWMID"],
            categories=["Аксессуары > Соковыжималки"],
            other_products=False,
        ),
    )

    with caplog.at_level("INFO"):
        result = core.find_products("Мешок для орехового молока аксессуар")

    models = {p.model for p in result.products}
    assert "Nut Milk Bag" in models
    assert "priority_filter_mode=skip_best_priority" in caplog.text


def test_best_priority_keeps_part_for_part_query(monkeypatch, caplog):
    """Запчасть в пуле не режется best_priority при part-intent в запросе."""
    fts_rows = [
        _product_row(
            model="BDS-04",
            name="Блендер BDS-04",
            status=1,
            category="Блендеры",
            price="17900 руб.",
            quantity=100,
            manufacturer_id=RAWMID_MANUFACTURER_ID,
            product_id=420,
        ),
        _product_row(
            model=" blender motor pin",
            name="Штырь мотора блендера",
            status=1,
            category="Запчасти > Блендеры",
            price="500 руб.",
            quantity=11,
            manufacturer_id=RAWMID_MANUFACTURER_ID,
            product_id=999,
        ),
    ]

    class FakeDatabase:
        def __enter__(self):
            cursor = MagicMock()
            cursor.execute.side_effect = lambda sql, params=None: setattr(
                cursor, "fetchall", lambda: fts_rows
            )
            db = MagicMock()
            db.cursor = cursor
            return db

        def __exit__(self, *args):
            return False

    monkeypatch.setattr(core, "Database", FakeDatabase)
    monkeypatch.setattr(
        core,
        "evaluate_manufacturer_bypass",
        lambda *a, **k: (False, ""),
    )
    part_products = [Product.from_record(row) for row in fts_rows]
    monkeypatch.setattr(
        core.dbfuncs,
        "find_mentioned_products",
        lambda **k: part_products,
    )
    monkeypatch.setattr(
        core.llm_funcs,
        "parse_query",
        lambda q, model_name=None: QueryParseResults(
            product_names=None,
            categories=["Запчасти > Блендеры"],
            other_products=False,
        ),
    )

    with caplog.at_level("INFO"):
        result = core.find_products("штырь в мотор блендера")

    models = {p.model for p in result.products}
    assert "blender motor pin" in models
    assert "priority_filter_mode=skip_best_priority" in caplog.text
