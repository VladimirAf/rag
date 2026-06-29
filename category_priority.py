"""
Единая логика приоритета категорий для tie-break.

Нельзя держать реализацию только в `core.py`, потому что её используют и модули
routes (например KB-реранк), а импорт `core -> routes -> ... -> core` создаёт циклы.
"""

from __future__ import annotations

from typing import Optional


def get_category_priority(category: Optional[str]) -> int:
    """
    Возвращает приоритет категории для сортировки.
    Порядок: Товары > Уценка > Аксессуары > Запчасти

    Меньше — выше приоритет (лучше в выдаче).

    В find_products финальный best_priority может быть ослаблен при other_products=true,
    schema_lookup_used или query_has_accessory_intent / query_has_part_intent (см. core._should_keep_accessory_products).
    """
    if not category:
        return 999

    category_lower = category.lower()

    # 1. Основные товары (блендеры, соковыжималки, дегидраторы и т.д.)
    if not any(x in category_lower for x in ("уценка", "уценен", "аксессуар", "запчаст")):
        return 1

    # 2. Уценка
    if "уценка" in category_lower or "уценен" in category_lower:
        return 2

    # 3. Аксессуары
    if "аксессуар" in category_lower:
        return 3

    # 4. Запчасти
    if "запчаст" in category_lower:
        return 4

    return 999

