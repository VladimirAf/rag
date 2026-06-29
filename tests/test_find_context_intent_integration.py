from __future__ import annotations

from types import SimpleNamespace

import pytest

from models import SearchResults


def test_find_context_uses_intent_route_and_files_override(monkeypatch):
    """
    Проверяем, что:
    - до retrieval вызывается классификатор
    - стадии идут в порядке маршрута
    - для files пробрасывается data_type_order override из роутера
    """
    import core

    calls: list[tuple[str, object]] = []

    # 1) Мокаем классификатор и роутер (чтобы не дергать LLM/конфиг).
    monkeypatch.setattr(
        core,
        "classify_intent",
        lambda query, model_name=None: SimpleNamespace(
            intent_label="recipe_cooking",
            confidence=0.9,
            reason="тест",
            predicted_intent_text=None,
        ),
    )
    monkeypatch.setattr(
        core,
        "resolve_intent_route",
        lambda classifier_out: SimpleNamespace(
            route_id="recipe_first",
            is_fallback=False,
            stages=[
                SimpleNamespace(stage="files", files_data_type_order=["recipe", "seocrm_article", "files"]),
                SimpleNamespace(stage="products", files_data_type_order=None),
                SimpleNamespace(stage="tickets", files_data_type_order=None),
            ],
        ),
    )

    # 2) Мокаем retrieval-стадии.
    def _files_search_staged(*args, **kwargs):
        calls.append(("files", kwargs.get("data_type_order")))
        return SearchResults(chunks=None, documents=None, file_ids=None, filenames=None)

    def _tickets_search(*args, **kwargs):
        calls.append(("tickets", None))
        return SearchResults(chunks=None, documents=None, ticket_ids=None)

    def _find_products(*args, **kwargs):
        calls.append(("products", None))
        return SimpleNamespace(products=[], parse_results=core.QueryParseResults(product_names=None, categories=None, other_products=None))

    monkeypatch.setattr(core.files_manager.crud, "search_staged", _files_search_staged)
    monkeypatch.setattr(core.tickets_manager.crud, "search", _tickets_search)
    monkeypatch.setattr(core, "find_products", _find_products)

    # 3) Мокаем финализацию, чтобы не писать в БД.
    monkeypatch.setattr(core, "finalize_context", lambda *a, **k: (a[3], 123))

    parse_calls: list[str] = []
    monkeypatch.setattr(
        core.llm_funcs,
        "parse_query",
        lambda q, model_name=None: (parse_calls.append(q) or core.QueryParseResults(product_names=None, categories=None, other_products=None)),
    )

    core.find_context(
        "как приготовить борщ",
        tickets_search_amount=5,
        files_search_amount=5,
        products_search_enabled=True,
    )

    assert calls == [
        ("files", ["recipe", "seocrm_article", "files"]),
        ("products", None),
        ("tickets", None),
    ]
    assert len(parse_calls) == 1


def test_find_context_files_staged_sufficient_skips_products_and_tickets(monkeypatch):
    """Если search_staged вернул files_staged_sufficient, пайплайн не идёт дальше."""
    import core

    calls: list[str] = []

    monkeypatch.setattr(
        core,
        "classify_intent",
        lambda query, model_name=None: SimpleNamespace(
            intent_label="recipe_cooking",
            confidence=0.9,
            reason="тест",
            predicted_intent_text=None,
        ),
    )
    monkeypatch.setattr(
        core,
        "resolve_intent_route",
        lambda classifier_out: SimpleNamespace(
            route_id="recipe_first",
            is_fallback=False,
            stages=[
                SimpleNamespace(stage="files", files_data_type_order=["recipe", "seocrm_article", "files"]),
                SimpleNamespace(stage="products", files_data_type_order=None),
                SimpleNamespace(stage="tickets", files_data_type_order=None),
            ],
        ),
    )

    def _files_search_staged(*args, **kwargs):
        calls.append("files")
        return SearchResults(
            chunks=["x"],
            documents=None,
            file_ids=["1"],
            filenames=["r.txt"],
            files_staged_sufficient=True,
        )

    monkeypatch.setattr(core.files_manager.crud, "search_staged", _files_search_staged)
    monkeypatch.setattr(
        core.tickets_manager.crud,
        "search",
        lambda *a, **k: (calls.append("tickets") or SearchResults(chunks=None, documents=None, ticket_ids=None)),
    )
    monkeypatch.setattr(
        core,
        "find_products",
        lambda *a, **k: (calls.append("products") or SimpleNamespace(products=[], parse_results=core.QueryParseResults(product_names=None, categories=None, other_products=None))),
    )
    monkeypatch.setattr(core, "finalize_context", lambda *a, **k: (a[3], 123))

    core.find_context(
        "рецепт смузи",
        tickets_search_amount=5,
        files_search_amount=5,
        products_search_enabled=True,
    )

    assert calls == ["files"]


def test_find_context_fallback_keeps_old_stage_order(monkeypatch):
    """Fallback должен сохранять прежний базовый порядок: products -> tickets -> files."""
    import core

    calls: list[str] = []

    monkeypatch.setattr(
        core,
        "classify_intent",
        lambda query, model_name=None: SimpleNamespace(
            intent_label="undefined",
            confidence=0.2,
            reason="тест",
            predicted_intent_text=None,
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
                SimpleNamespace(stage="files", files_data_type_order=["files", "seocrm_article", "recipe"]),
            ],
        ),
    )

    monkeypatch.setattr(core, "find_products", lambda *a, **k: (calls.append("products") or SimpleNamespace(products=[], parse_results=core.QueryParseResults(product_names=None, categories=None, other_products=None))))
    monkeypatch.setattr(
        core.tickets_manager.crud,
        "search",
        lambda *a, **k: (
            calls.append("tickets")
            or SearchResults(chunks=None, documents=None, ticket_ids=None, filenames=None)
        ),
    )
    monkeypatch.setattr(
        core.files_manager.crud,
        "search_staged",
        lambda *a, **k: (
            calls.append("files")
            or SearchResults(chunks=None, documents=None, file_ids=None, filenames=None)
        ),
    )
    monkeypatch.setattr(core, "finalize_context", lambda *a, **k: (a[3], 123))

    core.find_context(
        "какой график работы?",
        tickets_search_amount=5,
        files_search_amount=5,
        products_search_enabled=True,
    )

    assert calls == ["products", "tickets", "files"]

