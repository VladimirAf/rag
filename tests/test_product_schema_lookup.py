"""Unit-тесты product_schema lookup и интеграция с find_products."""

from __future__ import annotations

import sqlite3
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from models import PRODUCT_COLS, Product, QueryParseResults, RAWMID_MANUFACTURER_ID

import core
import product_schema_lookup as psl


CSV_PATH = Path(__file__).resolve().parents[2] / "data" / "product_schema.csv"


def _product_row(**kw):
    data = {col: None for col in PRODUCT_COLS}
    data.update(kw)
    return tuple(data[col] for col in PRODUCT_COLS)


def _init_schema_db(db_file: Path) -> sqlite3.Connection:
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
    conn.executescript(psl.PRODUCT_SCHEMA_DDL)
    return conn


def _seed_schema_db(
    conn: sqlite3.Connection,
    products: list[tuple],
    schema_rows: list[tuple],
) -> None:
    conn.executemany("INSERT INTO products VALUES (?,?,?,?,?,?,?,?,?,?,?)", products)
    conn.executemany(
        """
        INSERT INTO product_schema (product_id, related_product_id, schema_num, link_type)
        VALUES (?, ?, ?, ?)
        """,
        schema_rows,
    )
    conn.commit()


GOLDEN1_QUERY = "Есть ли в наличии подшипник для ножа блендера BDS-04?"
GOLDEN2_QUERY = "Нужны воронка и лопатка к блендеру BDS-04 — дай ссылки"
GOLDEN3_QUERY = "Какая крышка подходит к блендеру BDC-03 и есть ли она в наличии?"
GOLDEN4_QUERY = "У кофемашины rmc-01 сломался картридж под капсулы — что заказать?"
GOLDEN5_QUERY = "BDC-03 — цена, наличие и основные характеристики"

GOLDEN_QUERIES = (
    GOLDEN1_QUERY,
    GOLDEN2_QUERY,
    GOLDEN3_QUERY,
    GOLDEN4_QUERY,
    GOLDEN5_QUERY,
)


@pytest.fixture
def golden1_schema_db(tmp_path: Path):
    """Golden #1: много шумных запчастей BDS-04, целевые — bearings."""
    db_file = tmp_path / "golden1.db"
    conn = _init_schema_db(db_file)
    main_id = 420
    noise_specs = [
        (501, "BD power cable", "Кабель питания блендера BD", 1),
        (502, "BD toggle switch", "Тумблер включения BD", 2),
        (503, "BD screw M4", "Винт крепежный M4", 3),
        (504, "BD screw M5", "Винт крепежный M5", 4),
        (505, "BD motor brush", "Щетки двигателя BD", 6),
        (506, "BD jar lid generic", "Универсальная крышка", 8),
        (507, "BD gasket kit", "Набор прокладок BD", 9),
        (508, "BD impeller", "Импеллер BD", 10),
        (509, "BD base plate", "Основание корпуса BD", 11),
        (510, "BD fan", "Вентилятор охлаждения", 12),
        (511, "BD PCB", "Плата управления BD", 13),
        (512, "BD fuse", "Предохранитель BD", 14),
    ]
    target_specs = [
        (
            1540,
            "BD knife bearings rubber",
            "Резиновое кольцо на блок подшипников ножа блендера (внешнее)",
            5,
        ),
        (1541, "RAWMID BD bears", "Подшипники ножа блендера BD", 4),
    ]
    products = [
        _product_row(
            product_id=main_id,
            model="BDS-04",
            name="Блендер RAWMID BDS-04",
            status=1,
            category="Блендеры",
            price="100 руб.",
            quantity=10,
            manufacturer_id=RAWMID_MANUFACTURER_ID,
        ),
    ]
    schema_rows: list[tuple] = []
    for pid, model, name, schema_num in target_specs + noise_specs:
        products.append(
            _product_row(
                product_id=pid,
                model=model,
                name=name,
                status=1,
                category="Запчасти для блендеров",
                price="200 руб.",
                quantity=3,
                manufacturer_id=RAWMID_MANUFACTURER_ID,
            )
        )
        schema_rows.append((main_id, pid, schema_num, "part"))
    _seed_schema_db(conn, products, schema_rows)
    conn.close()
    return db_file


@pytest.fixture
def golden3_schema_db(tmp_path: Path):
    """Golden #3: много запчастей BDC-03, целевая — cover gasket."""
    db_file = tmp_path / "golden3.db"
    conn = _init_schema_db(db_file)
    main_id = 310
    target = (
        3200,
        "blender jar 600 cover gasket",
        "Прокладка/крышка кувшина блендера 600мл",
        2,
    )
    noise_specs = [
        (3201, "BDC knife", "Нож блендера BDC", 1),
        (3202, "BDC motor", "Двигатель BDC", 3),
        (3203, "BDC cable", "Кабель BDC", 4),
        (3204, "BDC jar 1L", "Кувшин 1л BDC", 5),
        (3205, "BDC base", "Основание BDC", 6),
        (3206, "BDC screw", "Винт BDC", 7),
        (3207, "BDC gasket set", "Набор прокладок BDC", 8),
        (3208, "BDC impeller", "Импеллер BDC", 9),
        (3209, "BDC PCB", "Плата BDC", 10),
        (3210, "BDC fan", "Вентилятор BDC", 11),
        (3211, "BDC toggle", "Тумблер BDC", 12),
        (3212, "BDC bearing", "Подшипник BDC", 13),
    ]
    products = [
        _product_row(
            product_id=main_id,
            model="BDC-03",
            name="Блендер RAWMID BDC-03",
            status=1,
            category="Блендеры",
            price="100 руб.",
            quantity=10,
            manufacturer_id=RAWMID_MANUFACTURER_ID,
        ),
    ]
    schema_rows: list[tuple] = []
    for pid, model, name, schema_num in [target] + noise_specs:
        products.append(
            _product_row(
                product_id=pid,
                model=model,
                name=name,
                status=1,
                category="Запчасти для блендеров",
                price="150 руб.",
                quantity=2,
                manufacturer_id=RAWMID_MANUFACTURER_ID,
            )
        )
        schema_rows.append((main_id, pid, schema_num, "part"))
    _seed_schema_db(conn, products, schema_rows)
    conn.close()
    return db_file


@pytest.fixture
def golden2_schema_db(tmp_path: Path):
    """Golden #2: воронка и лопатка к BDS-04 + шумные jar/accessory в schema."""
    db_file = tmp_path / "golden2.db"
    conn = _init_schema_db(db_file)
    main_id = 420
    target_specs = [
        (601, " BD funnel", "Воронка для блендера  BD", 1),
        (602, " BD spatula", "Лопатка для блендера  BD", 2),
    ]
    noise_specs = [
        (603, "Nut Milk Bag", "Мешок для орехового молока", 0, "accessory"),
        (604, "BDM-07 jar", "Кувшин BDM-07", 3, "part"),
        (605, "RPB-03 jar", "Кувшин RPB-03", 4, "part"),
        (606, "BD knife rubber", "Резиновое кольцо ножа", 5, "part"),
        (607, "BD power cable", "Кабель питания BD", 6, "part"),
        (608, "BD toggle switch", "Тумблер BD", 7, "part"),
        (609, "BD screw M4", "Винт M4", 8, "part"),
        (610, "BD motor brush", "Щетки двигателя", 9, "part"),
    ]
    products = [
        _product_row(
            product_id=main_id,
            model="BDS-04",
            name="Блендер RAWMID BDS-04",
            status=1,
            category="Блендеры",
            price="100 руб.",
            quantity=10,
            manufacturer_id=RAWMID_MANUFACTURER_ID,
        ),
    ]
    schema_rows: list[tuple] = []
    for pid, model, name, schema_num in target_specs:
        products.append(
            _product_row(
                product_id=pid,
                model=model,
                name=name,
                status=1,
                category="Аксессуары для блендеров",
                price="300 руб.",
                quantity=4,
                manufacturer_id=RAWMID_MANUFACTURER_ID,
            )
        )
        schema_rows.append((main_id, pid, schema_num, "accessory"))
    for pid, model, name, schema_num, link_type in noise_specs:
        products.append(
            _product_row(
                product_id=pid,
                model=model,
                name=name,
                status=1,
                category="Запчасти для блендеров",
                price="200 руб.",
                quantity=2,
                manufacturer_id=RAWMID_MANUFACTURER_ID,
            )
        )
        schema_rows.append((main_id, pid, schema_num, link_type))
    _seed_schema_db(conn, products, schema_rows)
    conn.close()
    return db_file


@pytest.fixture
def schema_db(tmp_path: Path):
    db_file = tmp_path / "schema.db"
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
    conn.executescript(psl.PRODUCT_SCHEMA_DDL)
    rows = [
        _product_row(
            product_id=420,
            model="BDS-04",
            name="Блендер RAWMID BDS-04",
            status=1,
            category="Блендеры",
            price="100 руб.",
            quantity=10,
            manufacturer_id=RAWMID_MANUFACTURER_ID,
        ),
        _product_row(
            product_id=502,
            model="Nut Milk Bag",
            name="Мешок для орехового молока",
            status=1,
            category="Аксессуары для блендеров",
            price="500 руб.",
            quantity=5,
            manufacturer_id=RAWMID_MANUFACTURER_ID,
        ),
        _product_row(
            product_id=1540,
            model="BD knife rubber",
            name="Резиновое кольцо на блок подшипников ножа блендера (внешнее)",
            status=1,
            category="Запчасти для блендеров",
            price="200 руб.",
            quantity=3,
            manufacturer_id=RAWMID_MANUFACTURER_ID,
        ),
        _product_row(
            product_id=1855,
            model=" BD knife base 4hp",
            name="База для ножей блендеров  4ЛС",
            status=1,
            category="Запчасти для блендеров",
            price="300 руб.",
            quantity=2,
            manufacturer_id=RAWMID_MANUFACTURER_ID,
        ),
    ]
    conn.executemany(
        "INSERT INTO products VALUES (?,?,?,?,?,?,?,?,?,?,?)",
        rows,
    )
    conn.executemany(
        """
        INSERT INTO product_schema (product_id, related_product_id, schema_num, link_type)
        VALUES (?, ?, ?, ?)
        """,
        [
            (420, 502, 0, "accessory"),
            (420, 1540, 5, "part"),
            (420, 1855, 7, "part"),
        ],
    )
    conn.commit()
    conn.close()
    return db_file


@pytest.fixture
def rmc_schema_db(tmp_path: Path):
    db_file = tmp_path / "rmc_schema.db"
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
    conn.executescript(psl.PRODUCT_SCHEMA_DDL)
    rows = [
        _product_row(
            product_id=900,
            model="RMC-01",
            name="Кофемашина RMC-01",
            status=1,
            category="Кофемашины",
            price="15000 руб.",
            quantity=5,
            manufacturer_id=RAWMID_MANUFACTURER_ID,
        ),
        _product_row(
            product_id=901,
            model="RMC-01 nespresso cartridge",
            name="Картридж Nespresso для RMC-01",
            status=1,
            category="Запчасти для кофемашин",
            price="800 руб.",
            quantity=10,
            manufacturer_id=RAWMID_MANUFACTURER_ID,
        ),
        _product_row(
            product_id=902,
            model="RMC-01 Dolce cartridge",
            name="Картридж Dolce Gusto для RMC-01",
            status=1,
            category="Запчасти для кофемашин",
            price="900 руб.",
            quantity=8,
            manufacturer_id=RAWMID_MANUFACTURER_ID,
        ),
        _product_row(
            product_id=903,
            model="RMC-01 water filter",
            name="Фильтр для воды RMC-01",
            status=1,
            category="Аксессуары для кофемашин",
            price="400 руб.",
            quantity=3,
            manufacturer_id=RAWMID_MANUFACTURER_ID,
        ),
    ]
    conn.executemany(
        "INSERT INTO products VALUES (?,?,?,?,?,?,?,?,?,?,?)",
        rows,
    )
    conn.executemany(
        """
        INSERT INTO product_schema (product_id, related_product_id, schema_num, link_type)
        VALUES (?, ?, ?, ?)
        """,
        [
            (900, 901, 3, "part"),
            (900, 902, 4, "part"),
            (900, 903, 0, "accessory"),
        ],
    )
    conn.commit()
    conn.close()
    return db_file


_LLM_NOISE_PRODUCTS = [
    Product(
        name="Кувшин BDM-07",
        price="",
        url="",
        description="",
        specs="",
        model="BDM-07 jar extra",
        category="Запчасти для блендеров",
        status=1,
        quantity=99,
        manufacturer_id=RAWMID_MANUFACTURER_ID,
        product_id=9901,
    ),
    Product(
        name="Кувшин RPB-03",
        price="",
        url="",
        description="",
        specs="",
        model="RPB-03 jar extra",
        category="Запчасти для блендеров",
        status=1,
        quantity=88,
        manufacturer_id=RAWMID_MANUFACTURER_ID,
        product_id=9902,
    ),
]


@pytest.fixture
def golden_schema_db_by_case(request):
    """Indirect fixture: имя pytest-fixture с mock-БД для golden set."""
    return request.getfixturevalue(request.param)


_GOLDEN_SCHEMA_HIT_CASES = [
    pytest.param(
        "golden1_schema_db",
        "BDS-04",
        GOLDEN1_QUERY,
        5,
        frozenset({" BD bears", "BD knife bearings rubber"}),
        frozenset(),
        id="1-bearing-bds04",
    ),
    pytest.param(
        "golden2_schema_db",
        "BDS-04",
        GOLDEN2_QUERY,
        8,
        frozenset({" BD funnel", " BD spatula"}),
        frozenset({"BDM-", "RPB-"}),
        id="2-funnel-spatula-bds04",
    ),
    pytest.param(
        "golden3_schema_db",
        "BDC-03",
        GOLDEN3_QUERY,
        8,
        frozenset({"blender jar 600 cover gasket"}),
        frozenset(),
        id="3-cover-bdc03",
    ),
    pytest.param(
        "rmc_schema_db",
        "rmc-01",
        GOLDEN4_QUERY,
        8,
        frozenset({"RMC-01 nespresso cartridge", "RMC-01 Dolce cartridge"}),
        frozenset(),
        id="4-cartridge-rmc01",
    ),
]


@pytest.mark.parametrize(
    "golden_schema_db_by_case,model,query,max_hits,required_models,forbidden_substrings",
    _GOLDEN_SCHEMA_HIT_CASES,
    indirect=["golden_schema_db_by_case"],
)
def test_golden_schema_context_noise_schema_hits(
    golden_schema_db_by_case: Path,
    model: str,
    query: str,
    max_hits: int,
    required_models: frozenset[str],
    forbidden_substrings: frozenset[str],
):
    """Golden set #1–#4: schema lookup без шума (mock БД)."""
    hits = psl.find_related_for_model_sku(
        model,
        query=query,
        db_path=golden_schema_db_by_case,
    )
    models = {p.model for p in hits}
    assert len(hits) <= max_hits
    assert required_models <= models
    for sub in forbidden_substrings:
        assert not any(sub in (m or "") for m in models)


_GOLDEN_FIND_PRODUCTS_CASES = [
    pytest.param(
        "golden2_schema_db",
        420,
        "BDS-04",
        GOLDEN2_QUERY,
        8,
        frozenset({" BD funnel", " BD spatula"}),
        frozenset({"BDM-", "RPB-"}),
        True,
        id="2-find-products-funnel-spatula",
    ),
    pytest.param(
        "golden3_schema_db",
        310,
        "BDC-03",
        GOLDEN3_QUERY,
        15,
        frozenset({"blender jar 600 cover gasket"}),
        frozenset(),
        True,
        id="3-find-products-cover-bdc03",
    ),
    pytest.param(
        "golden3_schema_db",
        310,
        "BDC-03",
        GOLDEN5_QUERY,
        1,
        frozenset({"BDC-03"}),
        frozenset(),
        False,
        id="5-find-products-plain-sku",
    ),
]


@pytest.mark.parametrize(
    "golden_schema_db_by_case,main_product_id,model,query,max_products,required_models,forbidden_substrings,other_products",
    _GOLDEN_FIND_PRODUCTS_CASES,
    indirect=["golden_schema_db_by_case"],
)
def test_golden_schema_context_noise_find_products(
    monkeypatch,
    golden_schema_db_by_case: Path,
    main_product_id: int,
    model: str,
    query: str,
    max_products: int,
    required_models: frozenset[str],
    forbidden_substrings: frozenset[str],
    other_products: bool,
):
    """Golden set #2, #3, #5: find_products с mock БД и без LLM-related шума."""
    direct_rows = [
        _product_row(
            product_id=main_product_id,
            model=model,
            name=f"Product {model}",
            status=1,
            category="Блендеры",
            price="100 руб.",
            quantity=10,
            manufacturer_id=RAWMID_MANUFACTURER_ID,
        ),
    ]
    _patch_find_products_db(monkeypatch, direct_rows, golden_schema_db_by_case)
    monkeypatch.setattr(
        core.llm_funcs,
        "find_related_products",
        lambda *a, **k: list(_LLM_NOISE_PRODUCTS),
    )
    monkeypatch.setattr(
        core.llm_funcs,
        "parse_query",
        lambda q, model_name=None: QueryParseResults(
            product_names=[model],
            categories=["Блендеры"],
            other_products=other_products,
        ),
    )

    result = core.find_products(query)
    models = {p.model for p in result.products}

    assert len(result.products) <= max_products
    assert required_models <= models
    for sub in forbidden_substrings:
        assert not any(sub in (m or "") for m in models)


def test_golden_schema_context_noise_case5_skips_schema_lookup():
    """Golden #5: plain SKU — schema lookup не включается."""
    response = QueryParseResults(
        product_names=["BDC-03"],
        categories=["Блендеры"],
        other_products=False,
    )
    assert not psl.query_requests_schema_lookup(
        GOLDEN5_QUERY,
        response,
        direct_sku_hits=["BDC-03"],
    )


def test_import_product_schema_from_csv_idempotent(tmp_path: Path):
  if not CSV_PATH.is_file():
      pytest.skip("product_schema.csv not available")
  db_file = tmp_path / "import.db"
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
  conn.commit()
  conn.close()

  count1 = psl.import_product_schema_from_csv(CSV_PATH, db_path=db_file)
  count2 = psl.import_product_schema_from_csv(CSV_PATH, db_path=db_file)
  assert count1 > 0
  assert count1 == count2

  conn = sqlite3.connect(db_file)
  rows = conn.execute("SELECT COUNT(*) FROM product_schema").fetchone()[0]
  dupes = conn.execute(
      """
      SELECT product_id, related_product_id, COUNT(*)
      FROM product_schema
      GROUP BY product_id, related_product_id
      HAVING COUNT(*) > 1
      """
  ).fetchall()
  conn.close()
  assert rows == count1
  assert dupes == []


def test_resolve_storefront_product_id(schema_db: Path):
    assert psl.resolve_storefront_product_id("BDS-04", db_path=schema_db) == 420
    assert psl.resolve_storefront_product_id("UNKNOWN", db_path=schema_db) is None


def test_find_parts_for_model_filters_by_mention(schema_db: Path):
    hits = psl.find_parts_for_model("BDS-04", mention="нож к BDS-04", db_path=schema_db)
    models = {p.model for p in hits}
    assert models
    assert all("knife" in (p.model or "").lower() or "нож" in (p.name or "").lower() for p in hits)


def test_find_accessories_for_model_nut_milk_bag(schema_db: Path):
    hits = psl.find_accessories_for_model(
        "BDS-04",
        mention="мешок для орехового молока к BDS-04",
        db_path=schema_db,
    )
    assert len(hits) == 1
    assert hits[0].model == "Nut Milk Bag"


def test_query_has_accessory_intent():
    assert psl.query_has_accessory_intent("Мешок для орехового молока  аксессуар")
    assert psl.query_has_accessory_intent("силиконовая лопатка к блендеру")
    assert not psl.query_has_accessory_intent("блендер BDS-04")
    assert not psl.query_has_accessory_intent("штырь в мотор блендера")


def test_query_has_part_intent():
    assert psl.query_has_part_intent("штырь в мотор блендера")
    assert psl.query_has_part_intent("нож к BDS-04")
    assert psl.query_has_part_intent("есть ли запчасти для блендера")
    assert not psl.query_has_part_intent("мешок для орехового молока")
    assert not psl.query_has_part_intent("блендер BDS-04")


def test_query_requests_schema_lookup():
    response = QueryParseResults(product_names=["BDS-04"], categories=None, other_products=False)
    assert psl.query_requests_schema_lookup("нож к BDS-04", response, direct_sku_hits=["BDS-04"])
    assert not psl.query_requests_schema_lookup("BDS-04", response, direct_sku_hits=["BDS-04"])

    response_other = QueryParseResults(product_names=["BDS-04"], categories=None, other_products=True)
    assert psl.query_requests_schema_lookup("BDS-04", response_other, direct_sku_hits=["BDS-04"])


@pytest.mark.parametrize(
    ("query", "expected_token"),
    [
        ("Есть ли в наличии подшипник для ножа блендера BDS-04?", "подшипник"),
        ("Нужны воронка и лопатка к блендеру BDS-04 — дай ссылки", "воронка"),
        ("Нужны воронка и лопатка к блендеру BDS-04 — дай ссылки", "лопатка"),
        ("Какая крышка подходит к блендеру BDC-03 и есть ли она в наличии?", "крышка"),
        (
            "У кофемашины rmc-01 сломался картридж под капсулы — что заказать?",
            "картридж",
        ),
    ],
)
def test_extract_mention_tokens_cyrillic_golden_set(query: str, expected_token: str):
    tokens = psl.extract_mention_tokens(query, sku_hints=["BDS-04", "BDC-03", "rmc-01"])
    assert expected_token in tokens


def test_extract_mention_tokens_cyrillic_not_skipped_by_empty_compact():
    tokens = psl.extract_mention_tokens("подшипник для ножа BDS-04", sku_hints=["BDS-04"])
    assert "подшипник" in tokens
    assert "ножа" in tokens


def test_normalize_token_compact_keeps_cyrillic_and_latin():
    assert psl._normalize_token_compact("Подшипник") == "подшипник"
    assert psl._normalize_token_compact("BDS-04") == "bds04"


@pytest.mark.parametrize(
    ("query", "expected"),
    [
        (
            "У кофемашины rmc-01 сломался картридж под капсулы — что заказать?",
            ("part", "accessory"),
        ),
        ("капсулы дешевле RMC-01", ("part", "accessory")),
        ("мешок для орехового молока к BDS-04", "accessory"),
        ("нож к BDS-04", "part"),
        (
            "BDS-04 дай ссылку на нож и дай еще мешок для орехового молока",
            ("part", "accessory"),
        ),
        ("нож и лопатка к BDS-04", ("part", "accessory")),
        ("BDS-04 цена", None),
    ],
)
def test_infer_link_type_from_query_cartridge_dual_mode(query: str, expected):
    assert psl._infer_link_type_from_query(query) == expected


def test_find_related_for_model_sku_cartridge_rmc01(rmc_schema_db: Path):
    query = "У кофемашины rmc-01 сломался картридж под капсулы — что заказать?"
    hits = psl.find_related_for_model_sku("rmc-01", query=query, db_path=rmc_schema_db)
    models = {p.model for p in hits}
    assert models & {"RMC-01 nespresso cartridge", "RMC-01 Dolce cartridge"}


def test_find_related_for_model_sku_accessory_only_without_cartridge_stems(
    schema_db: Path,
):
    hits = psl.find_related_for_model_sku(
        "BDS-04",
        query="мешок для орехового молока к BDS-04",
        db_path=schema_db,
    )
    models = {p.model for p in hits}
    assert models == {"Nut Milk Bag"}
    assert not any("knife" in (m or "").lower() for m in models)


def test_find_related_for_model_sku_dual_part_and_accessory_knife_and_bag(
    schema_db: Path,
):
    """Multi-intent: нож (part) + мешок (accessory) — оба типа в schema."""
    query = "BDS-04 дай ссылку на нож и дай еще мешок для орехового молока"
    hits = psl.find_related_for_model_sku("BDS-04", query=query, db_path=schema_db)
    models = {p.model for p in hits}
    assert "Nut Milk Bag" in models
    assert models & {"BD knife rubber", " BD knife base 4hp"}


def test_query_requests_schema_lookup_cartridge_trigger():
    response = QueryParseResults(product_names=["rmc-01"], categories=None, other_products=False)
    query = "У кофемашины rmc-01 сломался картридж под капсулы — что заказать?"
    assert psl.query_requests_schema_lookup(query, response, direct_sku_hits=["rmc-01"])


def _patch_find_products_db(monkeypatch, direct_rows, schema_db: Path):
    class FakeDatabase:
        def __init__(self, path=None):
            self.path = path or schema_db

        def __enter__(self):
            conn = sqlite3.connect(self.path)
            conn.create_function("LOWER", 1, lambda s: s.lower() if s else s)
            db = MagicMock()
            db.conn = conn
            db.cursor = conn.cursor()
            return db

        def __exit__(self, *args):
            return False

    monkeypatch.setattr(core, "Database", FakeDatabase)
    monkeypatch.setattr(psl, "Database", FakeDatabase)
    monkeypatch.setattr(
        core,
        "evaluate_manufacturer_bypass",
        lambda *a, **k: (False, ""),
    )
    monkeypatch.setattr(core.dbfuncs, "find_mentioned_products", lambda **k: [])
    monkeypatch.setattr(core.llm_funcs, "find_related_products", lambda *a, **k: [])


def test_find_products_schema_knife_for_bds04(monkeypatch, schema_db: Path, caplog):
    direct_rows = [
        _product_row(
            product_id=420,
            model="BDS-04",
            name="Блендер  BDS-04",
            status=1,
            category="Блендеры",
            price="100 руб.",
            quantity=10,
            manufacturer_id=RAWMID_MANUFACTURER_ID,
        ),
    ]
    _patch_find_products_db(monkeypatch, direct_rows, schema_db)
    monkeypatch.setattr(
        core.llm_funcs,
        "parse_query",
        lambda q, model_name=None: QueryParseResults(
            product_names=["BDS-04"],
            categories=["Блендеры"],
            other_products=False,
        ),
    )

    with caplog.at_level("INFO"):
        result = core.find_products("нож к BDS-04")

    models = {p.model for p in result.products}
    assert "BDS-04" in models
    assert any("knife" in (m or "").lower() for m in models)
    assert "product_schema_lookup model='BDS-04'" in caplog.text


def test_find_products_schema_not_mixed_for_plain_sku(monkeypatch, schema_db: Path):
    _patch_find_products_db(monkeypatch, [], schema_db)
    monkeypatch.setattr(
        core.llm_funcs,
        "parse_query",
        lambda q, model_name=None: QueryParseResults(
            product_names=["BDS-04"],
            categories=["Блендеры"],
            other_products=False,
        ),
    )

    result = core.find_products("BDS-04")
    models = [p.model for p in result.products]
    assert models == ["BDS-04"]


def test_golden1_bearing_query_caps_schema_hits(golden1_schema_db: Path, caplog):
    with caplog.at_level("INFO"):
        hits = psl.find_related_for_model_sku(
            "BDS-04",
            query=GOLDEN1_QUERY,
            db_path=golden1_schema_db,
        )
    models = {p.model for p in hits}
    assert len(hits) <= 5
    assert models & {"RAWMID BD bears", "BD knife bearings rubber"}
    assert "returned=" in caplog.text and "dropped_noise=" in caplog.text


def test_golden3_cover_query_finds_gasket(golden3_schema_db: Path):
    hits = psl.find_related_for_model_sku(
        "BDC-03",
        query=GOLDEN3_QUERY,
        db_path=golden3_schema_db,
    )
    models = {p.model for p in hits}
    assert len(hits) <= 5
    assert "blender jar 600 cover gasket" in models


def test_golden5_plain_sku_skips_schema_lookup():
    response = QueryParseResults(
        product_names=["BDC-03"],
        categories=["Блендеры"],
        other_products=False,
    )
    assert not psl.query_requests_schema_lookup(
        GOLDEN5_QUERY,
        response,
        direct_sku_hits=["BDC-03"],
    )


def test_filter_by_mention_zero_match_top_k_fallback():
    products = [
        Product(
            name="A",
            price="",
            url="",
            description="",
            specs="",
            model="noise-a",
            quantity=1,
        ),
        Product(
            name="подшипник",
            price="",
            url="",
            description="",
            specs="",
            model="target-b",
            quantity=2,
        ),
        Product(
            name="C",
            price="",
            url="",
            description="",
            specs="",
            model="noise-c",
            quantity=3,
        ),
    ]
    hits = [
        psl._SchemaHit(product=products[0], schema_num=10, link_type="part"),
        psl._SchemaHit(product=products[1], schema_num=1, link_type="part"),
        psl._SchemaHit(product=products[2], schema_num=20, link_type="part"),
    ]
    filtered = psl._filter_by_mention(
        hits,
        tokens=["несуществующий"],
        query="",
        inferred_link_type="part",
        max_hits=5,
        fallback_k=2,
    )
    assert len(filtered) == 2
    assert filtered[0].product.model == "target-b"


def test_stems_from_query_fallback_when_tokens_empty():
    stems = psl._stems_from_query("нужен винт к блендеру")
    assert "винт" in stems
    products = [
        Product(
            name="Винт крепежный",
            price="",
            url="",
            description="",
            specs="",
            model="screw",
            quantity=1,
        ),
        Product(
            name="Кабель",
            price="",
            url="",
            description="",
            specs="",
            model="cable",
            quantity=1,
        ),
    ]
    hits = [
        psl._SchemaHit(product=products[0], schema_num=1, link_type="part"),
        psl._SchemaHit(product=products[1], schema_num=2, link_type="part"),
    ]
    filtered = psl._filter_by_mention(
        hits,
        tokens=[],
        query="нужен винт к блендеру",
        inferred_link_type="part",
        max_hits=5,
        fallback_k=3,
    )
    assert len(filtered) == 1
    assert filtered[0].product.model == "screw"


def test_find_products_skips_related_when_schema_lookup_used(
    monkeypatch, golden2_schema_db: Path, caplog
):
    """При schema lookup + other_products не вызываем find_related_products."""
    direct_rows = [
        _product_row(
            product_id=420,
            model="BDS-04",
            name="Блендер BDS-04",
            status=1,
            category="Блендеры",
            price="100 руб.",
            quantity=10,
            manufacturer_id=RAWMID_MANUFACTURER_ID,
        ),
    ]
    _patch_find_products_db(monkeypatch, direct_rows, golden2_schema_db)
    related_calls: list[str] = []

    def _track_related(*args, **kwargs):
        related_calls.append("called")
        return list(_LLM_NOISE_PRODUCTS)

    monkeypatch.setattr(core.llm_funcs, "find_related_products", _track_related)
    monkeypatch.setattr(
        core.llm_funcs,
        "parse_query",
        lambda q, model_name=None: QueryParseResults(
            product_names=["BDS-04"],
            categories=["Блендеры"],
            other_products=True,
        ),
    )

    with caplog.at_level("INFO"):
        result = core.find_products(GOLDEN2_QUERY)

    assert related_calls == []
    assert "skipped_related_products_reason=schema_lookup_used" in caplog.text
    models = {p.model for p in result.products}
    assert " BD funnel" in models
    assert " BD spatula" in models
    assert not any("BDM-" in (m or "") for m in models)
    assert not any("RPB-" in (m or "") for m in models)


def test_find_products_skips_category_when_schema_lookup_used(
    monkeypatch, golden2_schema_db: Path, caplog
):
    """При schema lookup + other_products не вызываем category search."""
    direct_rows = [
        _product_row(
            product_id=420,
            model="BDS-04",
            name="Блендер  BDS-04",
            status=1,
            category="Блендеры",
            price="100 руб.",
            quantity=10,
            manufacturer_id=RAWMID_MANUFACTURER_ID,
        ),
    ]
    _patch_find_products_db(monkeypatch, direct_rows, golden2_schema_db)
    category_calls: list[str] = []

    def _track_category(**kwargs):
        category_calls.append("called")
        return list(_LLM_NOISE_PRODUCTS)

    monkeypatch.setattr(core.dbfuncs, "find_mentioned_products", _track_category)
    monkeypatch.setattr(core.llm_funcs, "find_related_products", lambda *a, **k: [])
    monkeypatch.setattr(
        core.llm_funcs,
        "parse_query",
        lambda q, model_name=None: QueryParseResults(
            product_names=["BDS-04"],
            categories=["Блендеры"],
            other_products=True,
        ),
    )

    with caplog.at_level("INFO"):
        result = core.find_products(GOLDEN2_QUERY)

    assert category_calls == []
    assert "skipped_category_search_reason=schema_lookup_used" in caplog.text
    models = {p.model for p in result.products}
    assert len(result.products) <= 8
    assert " BD funnel" in models
    assert " BD spatula" in models


def test_golden2_funnel_spatula_bounded_without_llm_noise(
    monkeypatch, golden2_schema_db: Path
):
    """Golden #2: funnel + spatula, ≤8 позиций, без jar из LLM."""
    direct_rows = [
        _product_row(
            product_id=420,
            model="BDS-04",
            name="Блендер BDS-04",
            status=1,
            category="Блендеры",
            price="100 руб.",
            quantity=10,
            manufacturer_id=RAWMID_MANUFACTURER_ID,
        ),
    ]
    _patch_find_products_db(monkeypatch, direct_rows, golden2_schema_db)
    monkeypatch.setattr(
        core.llm_funcs,
        "find_related_products",
        lambda *a, **k: list(_LLM_NOISE_PRODUCTS),
    )
    monkeypatch.setattr(
        core.llm_funcs,
        "parse_query",
        lambda q, model_name=None: QueryParseResults(
            product_names=["BDS-04"],
            categories=["Блендеры"],
            other_products=True,
        ),
    )

    result = core.find_products(GOLDEN2_QUERY)
    models = {p.model for p in result.products}

    assert " BD funnel" in models
    assert " BD spatula" in models
    assert len(result.products) <= 8
    assert not any("BDM-" in (m or "") for m in models)
    assert not any("RPB-" in (m or "") for m in models)


def test_golden3_find_products_no_llm_bloat(monkeypatch, golden3_schema_db: Path):
    """Golden #3: без find_related_products контекст не раздувается до 100 SKU."""
    direct_rows = [
        _product_row(
            product_id=310,
            model="BDC-03",
            name="Блендер  BDC-03",
            status=1,
            category="Блендеры",
            price="100 руб.",
            quantity=10,
            manufacturer_id=RAWMID_MANUFACTURER_ID,
        ),
    ]
    _patch_find_products_db(monkeypatch, direct_rows, golden3_schema_db)
    llm_flood = [
        Product(
            name=f"Noise {i}",
            price="",
            url="",
            description="",
            specs="",
            model=f"NOISE-{i}",
            category="Запчасти для блендеров",
            status=1,
            quantity=1,
            manufacturer_id=RAWMID_MANUFACTURER_ID,
            product_id=9000 + i,
        )
        for i in range(70)
    ]
    monkeypatch.setattr(
        core.llm_funcs,
        "find_related_products",
        lambda *a, **k: llm_flood,
    )
    monkeypatch.setattr(
        core.llm_funcs,
        "parse_query",
        lambda q, model_name=None: QueryParseResults(
            product_names=["BDC-03"],
            categories=["Блендеры"],
            other_products=True,
        ),
    )

    result = core.find_products(GOLDEN3_QUERY)
    models = {p.model for p in result.products}

    assert "blender jar 600 cover gasket" in models
    assert len(result.products) <= 15


def test_find_products_calls_related_without_schema_lookup(monkeypatch, caplog):
    """Регрессия: без schema lookup find_related_products вызывается при other_products."""
    class FakeDatabase:
        def __enter__(self):
            cursor = MagicMock()
            cursor.execute.side_effect = lambda sql, params=None: setattr(
                cursor, "fetchall", lambda: []
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
    related_calls: list[str] = []
    monkeypatch.setattr(
        core.llm_funcs,
        "find_related_products",
        lambda *a, **k: related_calls.append("called") or [],
    )
    monkeypatch.setattr(
        core.llm_funcs,
        "parse_query",
        lambda q, model_name=None: QueryParseResults(
            product_names=["блендер"],
            categories=["Блендеры"],
            other_products=True,
        ),
    )

    core.find_products("нужны аксессуары к блендеру")

    assert related_calls == ["called"]
    assert "skipped_related_products_reason=schema_lookup_used" not in caplog.text


def test_find_products_calls_category_without_schema_lookup(monkeypatch, caplog):
    """Регрессия: без schema lookup category search вызывается при other_products."""
    class FakeDatabase:
        def __enter__(self):
            cursor = MagicMock()
            cursor.execute.side_effect = lambda sql, params=None: setattr(
                cursor, "fetchall", lambda: []
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
    category_calls: list[str] = []
    monkeypatch.setattr(
        core.dbfuncs,
        "find_mentioned_products",
        lambda **k: category_calls.append("called") or [],
    )
    monkeypatch.setattr(core.llm_funcs, "find_related_products", lambda *a, **k: [])
    monkeypatch.setattr(
        core.llm_funcs,
        "parse_query",
        lambda q, model_name=None: QueryParseResults(
            product_names=["блендер"],
            categories=["Блендеры"],
            other_products=True,
        ),
    )

    core.find_products("нужны аксессуары к блендеру")

    assert category_calls == ["called"]
    assert "skipped_category_search_reason=schema_lookup_used" not in caplog.text
