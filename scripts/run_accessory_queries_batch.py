#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Прогон accessory-запросов через find_context с проверкой целевой детали."""

from __future__ import annotations

import re
import sys
from pathlib import Path

_APP_DIR = Path(__file__).resolve().parents[1]
if str(_APP_DIR) not in sys.path:
    sys.path.insert(0, str(_APP_DIR))

import core  # noqa: E402

CASES: list[tuple[str, str, list[str] | None]] = [
    (
        "найди кофемашину, у которой капсулы будут дешевле, чем у RMC-01",
        "кофемашина \u2260 RMC-01 (сравнение)",
        None,
    ),
    (
        "\u043d\u0443\u0436\u0435\u043d \u043c\u0435\u0448\u043e\u043a \u0434\u043b\u044f \u043e\u0440\u0435\u0445\u043e\u0432\u043e\u0433\u043e \u043c\u043e\u043b\u043e\u043a\u0430",
        "Nut Milk Bag",
        ["nut milk bag"],
    ),
    (
        "BDS-04 \u0434\u0430\u0439 \u0441\u0441\u044b\u043b\u043a\u0443 \u043d\u0430 \u043d\u043e\u0436 \u0438 \u0434\u0430\u0439 \u0435\u0449\u0435 \u043c\u0435\u0448\u043e\u043a \u0434\u043b\u044f \u043e\u0440\u0435\u0445\u043e\u0432\u043e\u0433\u043e \u043c\u043e\u043b\u043e\u043a\u0430",
        "\u043d\u043e\u0436 + Nut Milk Bag",
        ["nut milk bag", "knife"],
    ),
    (
        "\u041c\u0435\u0448\u043e\u043a \u0434\u043b\u044f \u043e\u0440\u0435\u0445\u043e\u0432\u043e\u0433\u043e \u043c\u043e\u043b\u043e\u043a\u0430 \u0430\u043a\u0441\u0435\u0441\u0441\u0443\u0430\u0440",
        "Nut Milk Bag",
        ["nut milk bag"],
    ),
    (
        "\u041c\u0435\u0448\u043e\u043a \u0434\u043b\u044f \u043e\u0440\u0435\u0445\u043e\u0432\u043e\u0433\u043e \u043c\u043e\u043b\u043e\u043a\u0430 \u0430\u043a\u0441\u0435\u0441\u0441\u0443\u0430\u0440",
        "Nut Milk Bag",
        ["nut milk bag"],
    ),
    (
        "\u043d\u0430\u0439\u0434\u0438 \u043a \u0431\u043b\u0435\u043d\u0434\u0435\u0440\u0443 \u043c\u0435\u0448\u043e\u0447\u0435\u043a \u0434\u043b\u044f \u043e\u0440\u0435\u0445\u043e\u0432\u043e\u0433\u043e \u043c\u043e\u043b\u043e\u043a\u0430",
        "Nut Milk Bag",
        ["nut milk bag"],
    ),
    (
        "\u043a\u0440\u044b\u0448\u043a\u0430 \u0434\u043b\u044f \u0441\u043f\u043e\u0440\u0442\u0438\u0432\u043d\u043e\u0439 \u0431\u0443\u0442\u044b\u043b\u043a\u0438",
        " sport bottle jar",
        ["sport bottle jar"],
    ),
    (
        "\u041a\u0430\u0431\u0435\u043b\u044c \u043f\u0438\u0442\u0430\u043d\u0438\u044f 3-pin",
        "Cable 3-pin",
        ["cable 3-pin", "3-pin"],
    ),
    (
        "\u041e\u043f\u043e\u043b\u0430\u0441\u043a\u0438\u0432\u0430\u0442\u0435\u043b\u044c \u0434\u043b\u044f \u043a\u0443\u0432\u0448\u0438\u043d\u043e\u0432  RAR-01",
        "\u043e\u043f\u043e\u043b\u0430\u0441\u043a\u0438\u0432\u0430\u0442\u0435\u043b\u044c RAR-01",
        ["\u043e\u043f\u043e\u043b\u0430\u0441\u043a", "rinse"],
    ),
    (
        "\u041e\u043f\u043e\u043b\u0430\u0441\u043a\u0438\u0432\u0430\u0442\u0435\u043b\u044c \u0434\u043b\u044f \u043a\u0443\u0432\u0448\u0438\u043d\u043e\u0432",
        "\u043e\u043f\u043e\u043b\u0430\u0441\u043a\u0438\u0432\u0430\u0442\u0435\u043b\u044c",
        ["\u043e\u043f\u043e\u043b\u0430\u0441\u043a", "rinse"],
    ),
    (
        "\u0420\u0435\u0437\u0438\u043d\u043e\u0432\u043e\u0435 \u043a\u043e\u043b\u044c\u0446\u043e \u043d\u0430 \u0431\u043b\u043e\u043a \u043f\u043e\u0434\u0448\u0438\u043f\u043d\u0438\u043a\u043e\u0432",
        "bearings rubber",
        ["bearings rubber", "knife bearings rubber", "knife rubber"],
    ),
    (
        "\u041b\u043e\u043f\u0430\u0442\u043a\u0430 \u0441\u0438\u043b\u0438\u043a\u043e\u043d\u043e\u0432\u0430\u044f",
        "spatula",
        ["spatula", "\u043b\u043e\u043f\u0430\u0442"],
    ),
    (
        "\u041c\u0435\u0448\u043e\u043a \u0434\u043b\u044f \u043e\u0440\u0435\u0445\u043e\u0432\u043e\u0433\u043e \u043c\u043e\u043b\u043e\u043a\u0430 \u043a BDM-06",
        "Nut Milk Bag",
        ["nut milk bag"],
    ),
    (
        "\u0428\u0442\u044b\u0440\u044c \u0432 \u043c\u043e\u0442\u043e\u0440 \u0431\u043b\u0435\u043d\u0434\u0435\u0440\u0430",
        "motor pin",
        ["motor pin", "blender motor pin"],
    ),
]


def _hay(p) -> str:
    return f"{p.model or ''} {p.name or ''}".lower()


def _matches(p, patterns: list[str]) -> bool:
    h = _hay(p)
    hc = re.sub(r"[^a-z0-9\u0430-\u044f\u0451]", "", h)
    for pat in patterns:
        pl = pat.lower()
        if pl in h:
            return True
        if pl.replace(" ", "") in hc:
            return True
    return False


def _eval_coffee_compare(products) -> tuple[bool, list[str]]:
    hits = []
    for p in products:
        m = (p.model or "").upper()
        cat = (p.category or "").lower()
        if m.startswith("RMC-01"):
            continue
        if "rmp" in m.lower() or "\u043a\u043e\u0444\u0435\u043c\u0430\u0448" in cat:
            hits.append(p.model or "")
    return bool(hits), hits[:5]


def main() -> int:
    print(f"{'#':<3} {'cnt':<4} {'OK':<4} expected | hit models | top context")
    print("=" * 110)
    ok_total = 0
    for i, (query, expected, patterns) in enumerate(CASES, start=1):
        ctx, rid = core.find_context(
            query,
            prompt="\u0437\u0430\u043f\u0440\u043e\u0441 \u0442\u043e\u043b\u044c\u043a\u043e \u043d\u0430 \u043a\u043e\u043d\u0442\u0435\u043a\u0441\u0442",
            tickets_search_amount=0,
            files_search_amount=0,
            products_search_enabled=True,
            source="accessory_batch_test",
        )
        products = ctx.products or []
        models = [p.model for p in products if p.model]

        if patterns is None:
            ok, hit_models = _eval_coffee_compare(products)
        else:
            hit_models = [p.model for p in products if _matches(p, patterns)]
            ok = bool(hit_models)

        ok_total += int(ok)
        mark = "YES" if ok else "NO"
        hits = ", ".join(hit_models[:3]) if hit_models else "\u2014"
        tops = ", ".join(models[:4])
        if len(models) > 4:
            tops += f" \u2026(+{len(models)-4})"
        qshort = query if len(query) <= 70 else query[:67] + "\u2026"
        print(f"{i:<3} {len(products):<4} {mark:<4} {expected}")
        print(f"    hit: {hits}")
        print(f"    ctx: {tops}")
        print(f"    Q: {qshort}  [id={rid}]")
        print()

    print(f"\u0418\u0442\u043e\u0433\u043e: {ok_total}/{len(CASES)} \u2014 \u0446\u0435\u043b\u0435\u0432\u0430\u044f \u0434\u0435\u0442\u0430\u043b\u044c/\u0442\u043e\u0432\u0430\u0440 \u0432 \u043a\u043e\u043d\u0442\u0435\u043a\u0441\u0442\u0435")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
