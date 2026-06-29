"""
Извлечение литералов артикулов (RMA-12, BDG-03) из запроса.

Отдельный модуль без LangChain/Chroma — доступен юнит-тестам без прод-зависимостей.
"""

from __future__ import annotations

import re
from typing import Any, List, Mapping, Optional, Sequence, Tuple

# Пороги для sku_match_strength (см. docs/terms.md).
SKU_STRENGTH_FILENAME = 4
SKU_STRENGTH_BODY = 3
SKU_HARD_SELECT_MIN_STRENGTH = 3

_SKU_HINT_RE = re.compile(r"\b([A-Z]{2,5}-?\d{1,3})\b", re.IGNORECASE)


def normalize_hint_source_text(q: str) -> str:
    q = q or ""
    for ch in ("\u2011", "\u2013", "\u2014", "\u2212"):
        q = q.replace(ch, "-")
    return q


def chroma_contains_needles(normalized_sku: str) -> List[str]:
    """
    Варианты подстрок для Chroma where_document $contains.
    В PDF часто «RMA-12», в filename — RMA_12 или RMA-12.
    """
    raw = (normalized_sku or "").strip()
    if not raw:
        return []
    low = raw.lower()
    out: List[str] = []
    # В PDF/OCR часто встречаются разные "дефисы" (non-breaking hyphen и т.п.).
    # Для where_document $contains нужен буквальный матч, поэтому добавляем варианты.
    dash_variants = ("-", "\u2011", "\u2013", "\u2014", "\u2212")

    def _emit(v: str) -> None:
        if len(v) >= 3 and v not in out:
            out.append(v)

    for variant in (
        low,
        low.replace("-", " "),
        low.replace("-", ""),
        low.replace("-", "_"),
    ):
        _emit(variant)

    if "-" in low:
        for dv in dash_variants:
            if dv == "-":
                continue
            _emit(low.replace("-", dv))
    parts = low.split("-", 1)
    if len(parts) == 2 and parts[0] and parts[1]:
        merged = f"{parts[0]}_{parts[1]}"
        _emit(merged)

    # Chroma where_document $contains может вести себя регистрозависимо (зависит от backend),
    # поэтому добавляем варианты в верхнем регистре, чтобы не потерять мануалы с "RMA-12".
    upper_variants = [v.upper() for v in list(out)]
    for v in upper_variants:
        _emit(v)
    return out


def _alnum_compact(s: str) -> str:
    return re.sub(r"[^a-z0-9]", "", (s or "").lower())


def sku_match_strength(hint: str, filename: Optional[str], excerpt: str) -> int:
    """
    Оценка совпадения чанка с артикулом (для ранжирования после vector search).
    4 — явное вхождение в filename; 3 — в тексте чанка; 0 — нет совпадения.
    """
    if not hint:
        return 0
    h = _alnum_compact(hint)
    if len(h) < 4:
        return 0
    fn = _alnum_compact(filename or "")
    body = _alnum_compact(excerpt or "")
    if h in fn:
        return SKU_STRENGTH_FILENAME
    if h in body:
        return SKU_STRENGTH_BODY
    return 0


def max_sku_match_strength(
    filename: Optional[str],
    excerpt: str,
    sku_hints: Sequence[str],
) -> int:
    """Максимальный sku_match_strength по всем hints из запроса."""
    if not sku_hints:
        return 0
    return max(
        sku_match_strength(h, filename, excerpt) for h in sku_hints
    )


def hard_select_candidate_indexes(
    candidates: Sequence[Mapping[str, Any]],
    sku_hints: Sequence[str],
    min_strength: int = SKU_HARD_SELECT_MIN_STRENGTH,
) -> Tuple[List[int], int]:
    """
    Детерминированный выбор кандидатов по SKU без LLM.

    Возвращает (индексы в candidates, лучший strength).
    Если лучший strength < min_strength — ([], 0).

    Выбирается чанк с максимальным strength; при равенстве — с меньшим индексом
    (стабильность).
    """
    if not sku_hints or not candidates:
        return [], 0

    best_strength = 0
    best_idx: Optional[int] = None
    for i, c in enumerate(candidates):
        strength = max_sku_match_strength(
            c.get("filename"),
            c.get("excerpt") or "",
            sku_hints,
        )
        if strength > best_strength or (
            strength == best_strength and best_idx is None
        ):
            best_strength = strength
            best_idx = i

    if best_idx is None or best_strength < min_strength:
        return [], best_strength

    return [best_idx], best_strength


def extract_model_sku_hints(query: str) -> List[str]:
    """
    Литералы моделей из текста запроса (латиница + цифры).
    Используется для lexical-фильтра Chroma и усиления embedding-запроса.
    """
    q = normalize_hint_source_text(query)
    seen: List[str] = []
    for m in _SKU_HINT_RE.finditer(q):
        token = m.group(1).upper().replace(" ", "")
        if token not in seen:
            seen.append(token)
    return seen
