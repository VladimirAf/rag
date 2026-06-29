"""
Композитный реранк чанков базы знаний после векторного поиска (стадия files).

Веса — константы модуля; режим broad/narrow по наличию SKU в запросе (без ML).
Tie-break: меньший kb_category_priority (см. core.get_category_priority).
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional, Tuple

from models import QueryParseResults

from category_priority import get_category_priority
from .retrieval_entities import (
    is_kb_chunk_metadata,
    kb_metadata_overlap_score,
    sku_in_kb_canonical,
    token_overlap_count,
)
from .sku_hints import sku_match_strength

# --- Веса композитного скора (broad: нет SKU в запросе → упор на категории) ---
W_SKU_BROAD = 1.0
W_OVERLAP_BROAD = 2.0
W_CATEGORY_BROAD = 4.0
W_KB_CANONICAL_SKU_BROAD = 2.0

# --- narrow: есть SKU → упор на sku_match_strength и overlap с каноном ---
W_SKU_NARROW = 5.0
W_OVERLAP_NARROW = 3.0
W_CATEGORY_NARROW = 1.0
W_KB_CANONICAL_SKU_NARROW = 4.0

# Бонус за явное вхождение категории из parse в путь kb_categories (подстрока)
_CATEGORY_SUBSTRING_BONUS = 3


def _category_match_score(
    parse_results: Optional[QueryParseResults],
    kb_categories: Optional[str],
) -> int:
    if not parse_results or not parse_results.categories or not (kb_categories or "").strip():
        return 0
    kb = kb_categories or ""
    score = 0
    for cat in parse_results.categories:
        phrase = (cat or "").strip()
        if not phrase:
            continue
        score = max(score, token_overlap_count(phrase, kb))
        if phrase.lower() in kb.lower():
            score = max(score, _CATEGORY_SUBSTRING_BONUS)
    return score


def _kb_canonical_sku_bonus(
    sku_hints: List[str],
    kb_product_titles: Optional[str],
    kb_model_skus: Optional[str],
) -> int:
    if not sku_hints:
        return 0
    for hint in sku_hints:
        if sku_in_kb_canonical(hint, kb_product_titles, kb_model_skus):
            return 3
    return 0


def kb_chunk_score_breakdown(
    raw_query: str,
    sku_hints: List[str],
    parse_results: Optional[QueryParseResults],
    metadata: dict,
    excerpt: str,
) -> Dict[str, float]:
    """Компоненты скора для одного KB-чанка (для сортировки и debug-лога)."""
    narrow = bool(sku_hints)
    w_sku = W_SKU_NARROW if narrow else W_SKU_BROAD
    w_overlap = W_OVERLAP_NARROW if narrow else W_OVERLAP_BROAD
    w_category = W_CATEGORY_NARROW if narrow else W_CATEGORY_BROAD
    w_canon = W_KB_CANONICAL_SKU_NARROW if narrow else W_KB_CANONICAL_SKU_BROAD

    fname = metadata.get("filename") if metadata else None
    kb_titles = metadata.get("kb_product_titles")
    kb_skus = metadata.get("kb_model_skus")
    kb_cats = metadata.get("kb_categories")

    sku_strength = 0
    if sku_hints:
        sku_strength = max(
            sku_match_strength(h, fname, excerpt) for h in sku_hints
        )

    overlap = float(
        kb_metadata_overlap_score(raw_query, kb_titles, chunk_text=excerpt)
    )
    category = float(_category_match_score(parse_results, kb_cats))
    canonical = float(_kb_canonical_sku_bonus(sku_hints, kb_titles, kb_skus))

    composite = (
        w_sku * sku_strength
        + w_overlap * overlap
        + w_category * category
        + w_canon * canonical
    )
    return {
        "composite": composite,
        "sku_strength": float(sku_strength),
        "overlap": overlap,
        "category": category,
        "canonical": canonical,
        "mode": "narrow" if narrow else "broad",
    }


def _stage_doc_sort_key(
    item: Any,
    raw_query: str,
    sku_hints: List[str],
    parse_results: Optional[QueryParseResults],
) -> Tuple[float, int, float]:
    doc, dist = item
    meta = doc.metadata or {}
    excerpt = doc.page_content or ""
    fname = meta.get("filename")
    try:
        d = float(dist)
    except Exception:
        d = 1e9

    if is_kb_chunk_metadata(meta):
        bd = kb_chunk_score_breakdown(
            raw_query, sku_hints, parse_results, meta, excerpt
        )
        cat_pri = get_category_priority(meta.get("kb_categories"))
        return (-bd["composite"], cat_pri, d)

    if sku_hints:
        strength = max(
            sku_match_strength(h, fname, excerpt) for h in sku_hints
        )
        return (-float(strength), 999, d)

    return (0.0, 999, d)


def rerank_stage_docs(
    docs_with_scores: List[Any],
    *,
    raw_query: str,
    sku_hints: List[str],
    parse_results: Optional[QueryParseResults] = None,
) -> List[Any]:
    """
    Реранк после vector search: KB-чанки — композитный скор; остальные — SKU-hints как раньше.
    """
    if not docs_with_scores:
        return docs_with_scores

    has_kb = any(
        is_kb_chunk_metadata((doc.metadata or {}))
        for doc, _ in docs_with_scores
    )
    if not has_kb and not sku_hints:
        return docs_with_scores

    reranked = sorted(
        docs_with_scores,
        key=lambda item: _stage_doc_sort_key(
            item, raw_query, sku_hints, parse_results
        ),
    )

    if has_kb and reranked != docs_with_scores:
        _log_kb_rerank_top3(reranked, raw_query, sku_hints, parse_results)

    elif sku_hints and reranked != docs_with_scores and not has_kb:
        top_doc, top_dist = reranked[0]
        st = max(
            sku_match_strength(
                h,
                top_doc.metadata.get("filename") if top_doc.metadata else None,
                top_doc.page_content or "",
            )
            for h in sku_hints
        )
        logging.info(
            "[DEBUG] Staged search: reranked chunks by SKU match (top strength=%s, distance=%.4f)",
            st,
            float(top_dist) if top_dist is not None else -1,
        )

    return reranked


def _log_kb_rerank_top3(
    reranked: List[Any],
    raw_query: str,
    sku_hints: List[str],
    parse_results: Optional[QueryParseResults],
) -> None:
    logged = 0
    for doc, dist in reranked:
        meta = doc.metadata or {}
        if not is_kb_chunk_metadata(meta):
            continue
        bd = kb_chunk_score_breakdown(
            raw_query,
            sku_hints,
            parse_results,
            meta,
            doc.page_content or "",
        )
        cat_pri = get_category_priority(meta.get("kb_categories"))
        file_id = meta.get("file_id") or meta.get("file_hash") or "?"
        fname = (meta.get("filename") or "")[:48]
        logging.info(
            "[DEBUG] KB rerank top #%s: composite=%.2f mode=%s "
            "sku=%.0f overlap=%.0f category=%.0f canonical=%.0f "
            "cat_pri=%s dist=%.4f file_id=%s filename_prefix=%r",
            logged + 1,
            bd["composite"],
            bd["mode"],
            bd["sku_strength"],
            bd["overlap"],
            bd["category"],
            bd["canonical"],
            cat_pri,
            float(dist) if dist is not None else -1.0,
            file_id,
            fname,
        )
        logged += 1
        if logged >= 3:
            break
