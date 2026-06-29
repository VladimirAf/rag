"""Tiered-cap итогового related_products (PRODUCTS_CONTEXT_MAX)."""

from models import Product

import core


def _product(
    *,
    name: str,
    model: str | None = None,
    product_id: int | None = None,
    quantity: int = 0,
    category: str = "Запчасти",
) -> Product:
    return Product(
        name=name,
        model=model or name,
        product_id=product_id,
        quantity=quantity,
        category=category,
        status=1,
        price="100 руб.",
        url="",
        description="",
        specs="",
        manufacturer_id=46,
    )


def test_cap_products_tiered_preserves_direct_and_schema_over_noise():
    anchor = _product(name="RMC-01 main", model="RMC-01", product_id=1, quantity=1)
    schema_hit = _product(
        name="RMC-01 nespresso cartridge",
        model="RMC-01 nespresso cartridge",
        product_id=2,
        quantity=0,
    )
    noise = [
        _product(name=f"noise-{i}", model=f"NOISE-{i}", product_id=100 + i, quantity=50 - i)
        for i in range(20)
    ]
    products = noise + [anchor, schema_hit]

    capped = core._cap_products_tiered(
        products,
        max_items=5,
        anchor_skus={"RMC-01"},
        schema_hit_product_ids={2},
        llm_selected_product_ids=set(),
    )

    models = [p.model for p in capped]
    assert "RMC-01" in models
    assert "RMC-01 nespresso cartridge" in models
    assert len(capped) == 5


def test_cap_products_tiered_keeps_llm_tier_before_overflow_tail():
    anchor = _product(name="BDS-04", model="BDS-04", product_id=10, quantity=2)
    llm_pick = _product(name="Nut Milk Bag", model="Nut Milk Bag", product_id=11, quantity=1)
    noise = [
        _product(name=f"jar-{i}", model=f"JAR-{i}", product_id=200 + i, quantity=100 - i)
        for i in range(10)
    ]
    products = noise + [anchor, llm_pick]

    capped = core._cap_products_tiered(
        products,
        max_items=3,
        anchor_skus={"BDS-04"},
        schema_hit_product_ids=set(),
        llm_selected_product_ids={11},
    )

    assert [p.model for p in capped] == ["BDS-04", "Nut Milk Bag", "JAR-0"]


def test_cap_products_tiered_noop_when_under_limit():
    products = [_product(name="only", model="ONLY-1", product_id=1, quantity=1)]
    assert core._cap_products_tiered(
        products,
        max_items=15,
        anchor_skus=set(),
        schema_hit_product_ids=set(),
        llm_selected_product_ids=set(),
    ) is products


def test_cap_products_tiered_dedupes_by_name_when_over_limit():
    noise = [
        _product(name=f"n{i}", model=f"N{i}", product_id=100 + i, quantity=50 - i)
        for i in range(18)
    ]
    dup_a = _product(name="Same", model="M1", product_id=1, quantity=1)
    dup_b = _product(name="Same", model="M2", product_id=2, quantity=0)
    capped = core._cap_products_tiered(
        noise + [dup_a, dup_b],
        max_items=5,
        anchor_skus=set(),
        schema_hit_product_ids={1},
        llm_selected_product_ids=set(),
    )
    assert len(capped) == 5
    assert sum(1 for p in capped if p.name == "Same") == 1
    assert capped[0].model == "M1"
