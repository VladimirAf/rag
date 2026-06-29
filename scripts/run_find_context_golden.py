#!/usr/bin/env python3
"""Интеграционный прогон golden set schema context noise через find_context."""

from __future__ import annotations

import sys
from dataclasses import dataclass
from pathlib import Path

_APP_DIR = Path(__file__).resolve().parents[1]
if not (_APP_DIR / "core.py").is_file():
    _docker_app = Path("/app")
    if (_docker_app / "core.py").is_file():
        _APP_DIR = _docker_app
if str(_APP_DIR) not in sys.path:
    sys.path.insert(0, str(_APP_DIR))

import core  # noqa: E402


@dataclass(frozen=True)
class GoldenQuery:
    num: int
    query: str
    max_models: int
    required_substrings: tuple[str, ...]
    forbidden_substrings: tuple[str, ...] = ()
    only_model: str | None = None


GOLDEN_QUERIES: tuple[GoldenQuery, ...] = (
    GoldenQuery(
        1,
        "Есть ли в наличии подшипник для ножа блендера BDS-04?",
        max_models=7,
        required_substrings=("bear",),
    ),
    GoldenQuery(
        2,
        "Нужны воронка и лопатка к блендеру BDS-04 — дай ссылки",
        max_models=8,
        required_substrings=("funnel", "spatula"),
        forbidden_substrings=("BDM-", "RPB-"),
    ),
    GoldenQuery(
        3,
        "Какая крышка подходит к блендеру BDC-03 и есть ли она в наличии?",
        max_models=15,
        required_substrings=("cover", "gasket"),
    ),
    GoldenQuery(
        4,
        "У кофемашины rmc-01 сломался картридж под капсулы — что заказать?",
        max_models=15,
        required_substrings=("cartridge",),
    ),
    GoldenQuery(
        5,
        "BDC-03 — цена, наличие и основные характеристики",
        max_models=1,
        required_substrings=(),
        only_model="BDC-03",
    ),
)


def _verdict(case: GoldenQuery, models: list[str]) -> str:
    joined = " | ".join(models)
    lower_joined = joined.lower()

    if case.only_model:
        if models == [case.only_model]:
            return "OK"
        return f"FAIL expected only {case.only_model}"

    if len(models) > case.max_models:
        return f"FAIL count={len(models)} > max={case.max_models}"

    for sub in case.required_substrings:
        if sub.lower() not in lower_joined:
            return f"FAIL missing substring {sub!r}"

    for sub in case.forbidden_substrings:
        if any(sub in (m or "") for m in models):
            return f"FAIL forbidden {sub!r} in models"

    return "OK"


def main() -> int:
    print("Golden set find_context integration (schema context noise)")
    print("params: prompt='запрос только на контекст', tickets=0, files=0, products=true")
    print()
    print(f"{'#':<3} {'count':<6} {'verdict':<8} models")
    print("-" * 80)

    failed = 0
    rarequest_ids: list[int | None] = []

    for case in GOLDEN_QUERIES:
        ctx, rarequest_id = core.find_context(
            case.query,
            prompt="запрос только на контекст",
            tickets_search_amount=0,
            files_search_amount=0,
            products_search_enabled=True,
            source="golden_schema_noise",
        )
        rarequest_ids.append(rarequest_id)
        models = [p.model for p in (ctx.products or []) if p.model]
        verdict = _verdict(case, models)
        if verdict != "OK":
            failed += 1
        models_preview = ", ".join(models[:12])
        if len(models) > 12:
            models_preview += f", … (+{len(models) - 12})"
        print(f"{case.num:<3} {len(models):<6} {verdict:<8} {models_preview}")

    print("-" * 80)
    print(f"rarequest_ids: {rarequest_ids}")
    print(f"failed: {failed}/{len(GOLDEN_QUERIES)}")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
