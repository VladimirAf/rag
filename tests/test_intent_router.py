"""Юнит-тесты intent_router: матрица интент → маршрут и fallback."""
from __future__ import annotations

from pathlib import Path

import pytest

from intent_router import ResolvedIntentRoute, resolve_intent_route
from intent_routes_loader import load_intent_routes_from_path
from models import IntentClassifierOutput, UNDEFINED_INTENT_LABEL


@pytest.fixture(scope="module")
def routes_cfg():
    json_path = Path(__file__).resolve().parent.parent / "config" / "intent_routes.json"
    return load_intent_routes_from_path(json_path)


def _cls(label: str, conf: float = 0.9) -> IntentClassifierOutput:
    return IntentClassifierOutput(
        intent_label=label,
        confidence=conf,
        reason="test",
        predicted_intent_text=None,
    )


def test_undefined_is_fallback_default(routes_cfg):
    out = resolve_intent_route(_cls(UNDEFINED_INTENT_LABEL), routes_cfg=routes_cfg)
    fb = routes_cfg.routes["fallback_default"].stages
    assert isinstance(out, ResolvedIntentRoute)
    assert out.route_id == "fallback_default"
    assert out.is_fallback is True
    assert [s.stage for s in out.stages] == [s.stage for s in fb]
    assert out.stages == fb


def test_fallback_files_staged_order_matches_tz(routes_cfg):
    """Базовый пайплайн: products → tickets → files(files, seocrm_article, recipe)."""
    out = resolve_intent_route(_cls(UNDEFINED_INTENT_LABEL), routes_cfg=routes_cfg)
    assert [s.stage for s in out.stages] == ["products", "tickets", "files"]
    files_stage = out.stages[-1]
    assert files_stage.stage == "files"
    assert files_stage.files_data_type_order == ["files", "seocrm_article", "recipe"]


def test_recipe_first(routes_cfg):
    out = resolve_intent_route(_cls("recipe_cooking"), routes_cfg=routes_cfg)
    assert out.route_id == "recipe_first"
    assert out.is_fallback is False
    assert out.stages[0].stage == "files"
    assert out.stages[0].files_data_type_order == [
        "recipe",
        "seocrm_article",
        "files",
    ]


def test_products_first(routes_cfg):
    out = resolve_intent_route(_cls("product_specs_price"), routes_cfg=routes_cfg)
    assert out.route_id == "products_first"
    assert out.is_fallback is False
    assert out.stages[0].stage == "products"


def test_tickets_first(routes_cfg):
    out = resolve_intent_route(_cls("malfunction_howto"), routes_cfg=routes_cfg)
    assert out.route_id == "tickets_first"
    assert out.is_fallback is False
    assert out.stages[0].stage == "tickets"


def test_manuals_first_excludes_recipe(routes_cfg):
    out = resolve_intent_route(_cls("manual_usage"), routes_cfg=routes_cfg)
    assert out.route_id == "manuals_first"
    assert out.stages[0].files_data_type_order == ["files", "seocrm_article"]
    assert "recipe" not in (out.stages[0].files_data_type_order or [])


def test_unknown_label_fallback(routes_cfg):
    out = resolve_intent_route(_cls("not_in_config_intent_xyz"), routes_cfg=routes_cfg)
    assert out.route_id == "fallback_default"
    assert out.is_fallback is True
    assert [s.stage for s in out.stages] == ["products", "tickets", "files"]
