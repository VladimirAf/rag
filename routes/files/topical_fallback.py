"""
Topical fallback для seocrm_article при malfunction_howto.

Детерминированный выбор чанка по overlap запроса с title/excerpt, если судья
не выбрал кандидатов. Без LangChain/Chroma/pydantic — удобно для юнит-тестов.
"""

from __future__ import annotations

import re
from typing import Any, List, Mapping, Optional, Sequence, Set, Tuple


def normalize_hint_source_text(q: str) -> str:
    q = q or ""
    for ch in ("\u2011", "\u2013", "\u2014", "\u2212"):
        q = q.replace(ch, "-")
    return q

MALFUNCTION_INTENT_LABEL = "malfunction_howto"
TOPICAL_FALLBACK_MIN_STRENGTH = 3
_TOPICAL_TOKEN_WEIGHT = 2
_SEOCRM_FILENAME_BONUS = 1

_ALNUM_COMPACT_RE = re.compile(r"[^a-z0-9а-яё]+", re.IGNORECASE)
_TOKEN_RE = re.compile(
    r"[a-zа-яё]{2,}|\d{2,}|[a-z]{2,5}-?\d{1,3}",
    re.IGNORECASE,
)

# Короткие, но важные для неисправностей/ухода (подстрока в запросе).
_TOPICAL_QUERY_WHITELIST = frozenset(
    {
        "крышка",
        "крышк",
        "стакан",
        "стакана",
        "течь",
        "теч",
        "подтекать",
        "подтек",
        "мыть",
        "мытья",
        "блендер",
        "проверить",
        "детал",
        "детали",
        "аксессуар",
        "аксессуары",
        "запчаст",
        "неисправ",
        "протеч",
    }
)


def allows_seocrm_topical_fallback(intent_label: Optional[str]) -> bool:
    return (intent_label or "").strip() == MALFUNCTION_INTENT_LABEL


def _alnum_compact(s: str) -> str:
    return _ALNUM_COMPACT_RE.sub("", normalize_hint_source_text(s or "").lower())


def _significant_tokens(text: str) -> Set[str]:
    q = normalize_hint_source_text(text or "").lower()
    out: Set[str] = set()
    for m in _TOKEN_RE.finditer(q):
        tok = m.group(0).replace(" ", "")
        if len(tok) >= 2:
            out.add(tok)
    return out


def _query_topical_tokens(query: str) -> Set[str]:
    """Значимые токены запроса + whitelist-подстроки из сырого текста."""
    tokens = _significant_tokens(query)
    q_low = normalize_hint_source_text(query or "").lower()
    for marker in _TOPICAL_QUERY_WHITELIST:
        if marker in q_low:
            tokens.add(marker)
    return tokens


def _token_matches_haystack(token: str, hay_lower: str, hay_compact: str) -> bool:
    tok = (token or "").lower().strip()
    if not tok:
        return False
    if tok in hay_lower:
        return True
    compact = _alnum_compact(tok)
    return len(compact) >= 2 and compact in hay_compact


def topical_match_strength(
    query: str,
    filename: Optional[str],
    title: Optional[str],
    excerpt: str,
) -> int:
    """
    Скор topical overlap запроса с кандидатом seocrm_article.
    +2 за каждый релевантный токен запроса в title/excerpt; +1 бонус для seocrm + блендер/стакан.
    """
    hay_lower = f"{title or ''} {excerpt or ''}".lower()
    hay_compact = _alnum_compact(hay_lower)
    if not hay_compact:
        return 0

    score = 0
    for tok in _query_topical_tokens(query):
        if len(tok) < 4 and tok not in _TOPICAL_QUERY_WHITELIST:
            continue
        if _token_matches_haystack(tok, hay_lower, hay_compact):
            score += _TOPICAL_TOKEN_WEIGHT

    fn_low = (filename or "").lower()
    if "seocrm_article" in fn_low and "блендер" in hay_lower and (
        "стакан" in hay_lower or "крыш" in hay_lower
    ):
        score += _SEOCRM_FILENAME_BONUS

    return score


def topical_select_candidate_indexes(
    candidates: Sequence[Mapping[str, Any]],
    query: str,
    min_strength: int = TOPICAL_FALLBACK_MIN_STRENGTH,
) -> Tuple[List[int], int]:
    """
    Индекс лучшего кандидата по topical_match_strength.
    Возвращает ([], 0) если лучший strength < min_strength.
    """
    if not query or not candidates:
        return [], 0

    best_strength = 0
    best_idx: Optional[int] = None
    for i, c in enumerate(candidates):
        strength = topical_match_strength(
            query,
            c.get("filename"),
            c.get("text_title") or c.get("title"),
            c.get("excerpt") or "",
        )
        if strength > best_strength or (strength == best_strength and best_idx is None):
            best_strength = strength
            best_idx = i

    if best_idx is None or best_strength < min_strength:
        return [], best_strength

    return [best_idx], best_strength


def rank_topical_strengths(
    candidates: Sequence[Mapping[str, Any]],
    query: str,
    limit: int = 3,
) -> List[Tuple[int, int]]:
    """(index, strength) по убыванию strength — для debug-логов."""
    scored: List[Tuple[int, int]] = []
    for i, c in enumerate(candidates):
        scored.append(
            (
                i,
                topical_match_strength(
                    query,
                    c.get("filename"),
                    c.get("text_title") or c.get("title"),
                    c.get("excerpt") or "",
                ),
            )
        )
    scored.sort(key=lambda x: (-x[1], x[0]))
    return scored[:limit]
