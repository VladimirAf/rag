"""Direct SKU lookup: не вызывать FTS5 по product_names, если SQL уже нашёл линейку."""

from contextlib import contextmanager
from unittest.mock import MagicMock

from models import PRODUCT_COLS, QueryParseResults, RAWMID_MANUFACTURER_ID

import core


def _product_row(**kw):
    data = {col: None for col in PRODUCT_COLS}
    data.update(kw)
    return tuple(data[col] for col in PRODUCT_COLS)


def _make_fake_database(direct_rows, fts_rows=None):
    fts_rows = fts_rows or []
    cursor = MagicMock()
    fts_calls = []

    def execute(sql, params=None):
        if "products_fts5" in sql:
            fts_calls.append(sql)
            cursor.fetchall.return_value = fts_rows
        else:
            cursor.fetchall.return_value = direct_rows

    cursor.execute.side_effect = execute
    db = MagicMock()
    db.cursor = cursor
    return db, fts_calls


def test_find_products_skips_fts5_when_direct_sku_matched(monkeypatch):
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
        )
    ]
    state = {}

    class FakeDatabase:
        def __enter__(self):
            db, fts_calls = _make_fake_database(direct_rows, fts_rows=fts_rows)
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
    monkeypatch.setattr(
        core.llm_funcs,
        "parse_query",
        lambda q, model_name=None: QueryParseResults(
            product_names=["RMC-01"],
            categories=["Мультиварки"],
            other_products=False,
        ),
    )
    monkeypatch.setattr(core.dbfuncs, "find_mentioned_products", lambda **k: [])

    result = core.find_products("капсулы не прокалываются RMC-01")

    models = [p.model for p in result.products]
    assert "RMC-01" in models
    assert "RMP-04" not in models
    assert state["fts_calls"] == []


def test_find_products_fts5_when_no_direct_sku_hit(monkeypatch):
    fts_rows = [
        _product_row(
            model="BDS-04",
            name="Блендер BDS-04",
            status=1,
            category="Блендеры",
            price="10000 руб.",
            quantity=1,
            manufacturer_id=RAWMID_MANUFACTURER_ID,
        )
    ]
    state = {}

    class FakeDatabase:
        def __enter__(self):
            db, fts_calls = _make_fake_database([], fts_rows=fts_rows)
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
    monkeypatch.setattr(
        core.llm_funcs,
        "parse_query",
        lambda q, model_name=None: QueryParseResults(
            product_names=["BDS-04"],
            categories=None,
            other_products=None,
        ),
    )
    monkeypatch.setattr(core.dbfuncs, "find_mentioned_products", lambda **k: [])

    result = core.find_products("блендер BDS-04")

    assert any(p.model == "BDS-04" for p in result.products)
    assert state["fts_calls"]
