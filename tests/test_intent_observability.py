from __future__ import annotations

import json
import logging
from types import SimpleNamespace

import pytest

import intent_observability
from intent_observability import append_undefined_intent_jsonl
from models import Product, UNDEFINED_INTENT_LABEL


def test_normalize_route_label_maps_fallback_default():
    assert intent_observability.normalize_route_label("fallback_default") == "fallback"
    assert intent_observability.normalize_route_label("recipe_first") == "recipe_first"


def test_append_undefined_intent_jsonl_writes_utf8_line(monkeypatch, tmp_path):
    monkeypatch.setattr(intent_observability, "UNDEFINED_INTENTS_LOG_PATH", tmp_path / "u.log")
    append_undefined_intent_jsonl({"ts": "2026-01-01T00:00:00Z", "query": "привет", "x": 1})
    text = (tmp_path / "u.log").read_text(encoding="utf-8").strip()
    row = json.loads(text)
    assert row["query"] == "привет"


def test_append_undefined_intent_jsonl_swallows_errors(monkeypatch):
    monkeypatch.setattr(intent_observability.config, "LOGS_PATH", __import__("pathlib").Path("/nonexistent/read/only/path/xyz"))

    def bad_open(*_a, **_k):
        raise OSError("fail")

    monkeypatch.setattr("builtins.open", bad_open)
    append_undefined_intent_jsonl({"ts": "t"})
    # не пробрасываем


def test_find_context_writes_jsonl_for_undefined(monkeypatch, tmp_path):
    import core

    log_path = tmp_path / "undefined_intents.log"
    monkeypatch.setattr(intent_observability, "UNDEFINED_INTENTS_LOG_PATH", log_path)

    monkeypatch.setattr(
        core,
        "classify_intent",
        lambda query, model_name=None: SimpleNamespace(
            intent_label=UNDEFINED_INTENT_LABEL,
            confidence=0.15,
            reason="низкая уверенность",
            predicted_intent_text="общий вопрос",
        ),
    )
    monkeypatch.setattr(
        core,
        "resolve_intent_route",
        lambda classifier_out: SimpleNamespace(
            route_id="fallback_default",
            is_fallback=True,
            stages=[
                SimpleNamespace(stage="products", files_data_type_order=None),
                SimpleNamespace(stage="tickets", files_data_type_order=None),
                SimpleNamespace(stage="files", files_data_type_order=["files"]),
            ],
        ),
    )

    monkeypatch.setattr(core, "find_products", lambda *a, **k: SimpleNamespace(
        products=[], parse_results=core.QueryParseResults(product_names=None, categories=None, other_products=None),
    ))
    monkeypatch.setattr(
        core.tickets_manager.crud,
        "search",
        lambda *a, **k: __import__("models").SearchResults(chunks=None, documents=None, ticket_ids=None),
    )
    monkeypatch.setattr(
        core.files_manager.crud,
        "search_staged",
        lambda *a, **k: __import__("models").SearchResults(chunks=None, documents=None, file_ids=None, filenames=None),
    )
    monkeypatch.setattr(core, "finalize_context", lambda *a, **k: (a[3], 999))

    core.find_context(
        "какой график работы?",
        tickets_search_amount=5,
        files_search_amount=5,
        products_search_enabled=True,
        source="openwebui_pipeline__testuser",
    )

    row = json.loads(log_path.read_text(encoding="utf-8").strip())
    assert row["route_used"] == "fallback_find_context"
    assert row["query"] == "какой график работы?"
    assert row["confidence"] == pytest.approx(0.15)
    assert row["source"] == "openwebui_pipeline__testuser"


def test_find_context_routing_logs_fallback_and_non_fallback(caplog, monkeypatch):
    import core

    caplog.set_level(logging.INFO)

    monkeypatch.setattr(core, "finalize_context", lambda *a, **k: (a[3], 1))

    # non-fallback
    monkeypatch.setattr(
        core,
        "classify_intent",
        lambda query, model_name=None: SimpleNamespace(
            intent_label="recipe_cooking",
            confidence=0.95,
            reason="r",
            predicted_intent_text=None,
        ),
    )
    monkeypatch.setattr(
        core,
        "resolve_intent_route",
        lambda c: SimpleNamespace(
            route_id="recipe_first",
            is_fallback=False,
            stages=[
                SimpleNamespace(stage="files", files_data_type_order=None),
                SimpleNamespace(stage="products", files_data_type_order=None),
            ],
        ),
    )
    monkeypatch.setattr(
        core.files_manager.crud,
        "search_staged",
        lambda *a, **k: __import__("models").SearchResults(chunks=None, documents=None, file_ids=None, filenames=None),
    )
    monkeypatch.setattr(core, "find_products", lambda *a, **k: SimpleNamespace(
        products=[], parse_results=core.QueryParseResults(product_names=None, categories=None, other_products=None),
    ))

    core.find_context("x", tickets_search_amount=0, files_search_amount=5, products_search_enabled=True)

    routing_msgs = [r.message for r in caplog.records if "find_context routing" in r.message]
    assert routing_msgs
    assert "recipe_first" in routing_msgs[-1]
    assert "fallback_route=False" in routing_msgs[-1]

    caplog.clear()

    # fallback
    monkeypatch.setattr(
        core,
        "classify_intent",
        lambda query, model_name=None: SimpleNamespace(
            intent_label=UNDEFINED_INTENT_LABEL,
            confidence=0.1,
            reason="r",
            predicted_intent_text=None,
        ),
    )
    monkeypatch.setattr(
        core,
        "resolve_intent_route",
        lambda c: SimpleNamespace(
            route_id="fallback_default",
            is_fallback=True,
            stages=[
                SimpleNamespace(stage="products", files_data_type_order=None),
                SimpleNamespace(stage="tickets", files_data_type_order=None),
                SimpleNamespace(stage="files", files_data_type_order=None),
            ],
        ),
    )
    monkeypatch.setattr(
        core.tickets_manager.crud,
        "search",
        lambda *a, **k: __import__("models").SearchResults(chunks=None, documents=None, ticket_ids=None),
    )

    core.find_context("y", tickets_search_amount=5, files_search_amount=5, products_search_enabled=True)

    routing_msgs = [r.message for r in caplog.records if "find_context routing" in r.message]
    assert "fallback_route=True" in routing_msgs[-1]


def test_find_context_early_exit_logged(caplog, monkeypatch):
    import core
    import llm_funcs

    caplog.set_level(logging.INFO)

    monkeypatch.setattr(core, "finalize_context", lambda *a, **k: (a[3], 1))
    monkeypatch.setattr(
        core,
        "classify_intent",
        lambda query, model_name=None: SimpleNamespace(
            intent_label="product_specs_price",
            confidence=0.9,
            reason="r",
            predicted_intent_text=None,
        ),
    )
    monkeypatch.setattr(
        core,
        "resolve_intent_route",
        lambda c: SimpleNamespace(
            route_id="products_first",
            is_fallback=False,
            stages=[SimpleNamespace(stage="products", files_data_type_order=None)],
        ),
    )

    fake_product = Product(
        name="n",
        price="p",
        url="u",
        description="d",
        specs="s",
        category="c",
        model="ctx",
        status=1,
        quantity=1,
    )
    monkeypatch.setattr(
        core,
        "find_products",
        lambda *a, **k: SimpleNamespace(
            products=[fake_product],
            parse_results=core.QueryParseResults(product_names=None, categories=None, other_products=None),
        ),
    )
    monkeypatch.setattr(
        llm_funcs,
        "check_context_sufficiency",
        lambda *a, **k: {"enough_information": True, "short_reason": "ok"},
    )

    core.find_context("цена RFV", tickets_search_amount=0, files_search_amount=0, products_search_enabled=True)

    early = [r.message for r in caplog.records if "find_context early_exit" in r.message]
    assert early
    assert "stage=products" in early[-1]
