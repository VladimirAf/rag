"""Дополнение product_names артикулами из regex после parse_query."""

from models import QueryParseResults

from core import _merge_regex_skus_into_parse_results, _product_names_contain_sku


def test_merge_adds_sku_when_llm_missed():
    response = QueryParseResults(
        product_names=["самурай"],
        categories=["Блендеры"],
        other_products=False,
    )
    merged = _merge_regex_skus_into_parse_results(
        response, "как включить блендер самурай BDS-04"
    )
    assert merged.product_names is not None
    assert "BDS-04" in merged.product_names
    assert "самурай" in merged.product_names


def test_merge_skips_duplicate_sku():
    response = QueryParseResults(
        product_names=["BDS-04"],
        categories=None,
        other_products=None,
    )
    merged = _merge_regex_skus_into_parse_results(response, "блендер BDS-04")
    assert merged.product_names == ["BDS-04"]


def test_product_names_contain_sku_compact():
    assert _product_names_contain_sku(["Dream Samurai BDS-04 BPA"], "BDS-04")
    assert not _product_names_contain_sku(["самурай"], "BDS-04")
