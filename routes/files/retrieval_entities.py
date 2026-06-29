"""
Сущности и скоринг для KB-files retrieval (без LangChain/Chroma).

Матчинг запрос ↔ metadata: token overlap, SKU в каноне, обогащение embedding_query.
"""

from __future__ import annotations

import re
from typing import Any, Dict, List, Optional, Sequence, Tuple

from models import QueryParseResults

from .kb_ingest import KB_FILENAME_PREFIX, KB_INGEST_SOURCE
from .sku_hints import (
    chroma_contains_needles,
    extract_model_sku_hints,
    normalize_hint_source_text,
)

# Русские служебные слова запроса — не ищем по ним в kb_product_titles.
_KB_TITLES_QUERY_STOPWORDS = frozenset({
    "как", "что", "где", "когда", "можно", "нужно", "надо", "есть", "или", "для",
    "при", "это", "эта", "эти", "все", "ещё", "еще", "так", "также", "тоже",
    "работа", "работы", "инструкция",
    "пользоваться", "включить", "выключить", "серия", "серии", "линейка", "линейки",
    "модель", "модели", "товар", "товара", "товары",
})

_ALNUM_COMPACT_RE = re.compile(r"[^a-z0-9]+")
_TOKEN_RE = re.compile(
    r"[a-zа-яё]{2,}|\d{2,}|[a-z]{2,5}-?\d{1,3}",
    re.IGNORECASE,
)
_PIPE_SPLIT_RE = re.compile(r"\s*\|\s*")


def is_kb_chunk_metadata(metadata: Optional[dict]) -> bool:
    """Чанк из базы знаний tm: source или префикс filename."""
    if not metadata:
        return False
    if metadata.get("source") == KB_INGEST_SOURCE:
        return True
    filename = (metadata.get("filename") or "").strip()
    return bool(filename.startswith(KB_FILENAME_PREFIX))


def alnum_compact(s: str) -> str:
    return _ALNUM_COMPACT_RE.sub("", normalize_hint_source_text(s or "").lower())


def significant_tokens(text: str) -> Set[str]:
    """Значимые токены для overlap (слова ≥2 символов, артикулы, цифры ≥2)."""
    q = normalize_hint_source_text(text or "").lower()
    out: Set[str] = set()
    for m in _TOKEN_RE.finditer(q):
        tok = m.group(0).replace(" ", "")
        if len(tok) >= 2:
            out.add(tok)
    return out


def token_overlap_count(query: str, target: str) -> int:
    """
    Число общих значимых токенов между запросом и каноном (metadata/текст).
    Пустой target → 0.
    """
    if not (target or "").strip():
        return 0
    q_tokens = significant_tokens(query)
    if not q_tokens:
        return 0
    t_tokens = significant_tokens(target)
    return len(q_tokens & t_tokens)


def sku_in_kb_canonical(
    sku: str,
    kb_product_titles: Optional[str] = None,
    kb_model_skus: Optional[str] = None,
) -> bool:
    """SKU входит в канонические названия или список model из metadata БЗ."""
    if not sku:
        return False
    needle = alnum_compact(sku)
    if len(needle) < 4:
        return False
    for field in (kb_product_titles, kb_model_skus):
        if not field:
            continue
        for part in _PIPE_SPLIT_RE.split(field):
            part = part.strip()
            if not part:
                continue
            if needle in alnum_compact(part):
                return True
    return False


# Сколько чанков одного KB-файла отдаём из фильтра kb_product_titles в retrieval.
KB_TITLE_CHUNKS_PER_FILE = 3


def kb_chunk_query_overlap_score(
    raw_query: str,
    chunk_text: str,
    kb_product_titles: Optional[str] = None,
    needles: Optional[List[str]] = None,
) -> int:
    """
    Релевантность чанка запросу для отбора внутри file_id (не путать с реранком после vector).

    kb_product_titles намеренно не учитывается: у всех чанков одного файла оно одинаково.
    """
    _ = kb_product_titles
    score = token_overlap_count(raw_query, chunk_text or "")
    if needles and chunk_text:
        low = (chunk_text or "").lower()
        for needle in needles:
            if needle in low:
                score += min(len(needle), 6)
    q_tokens = significant_tokens(raw_query)
    low = (chunk_text or "").lower()
    for tok in q_tokens:
        if tok in _KB_TITLES_QUERY_STOPWORDS or len(tok) < 4:
            continue
        if tok in low:
            score += 2
        elif len(tok) > 5 and tok[:-1] in low:
            score += 1
    return score


def select_best_chunks_per_file_id(
    items: Sequence[Tuple[Any, float, str, int]],
    *,
    max_per_file: int = KB_TITLE_CHUNKS_PER_FILE,
    max_total: int,
) -> List[Tuple[Any, float]]:
    """
    items: (Document, distance, file_id, overlap_score).
    На каждый file_id — до max_per_file чанков с наибольшим overlap, затем общий лимит max_total.
    """
    by_file: Dict[str, List[Tuple[Any, float, str, int]]] = {}
    no_file: List[Tuple[Any, float, str, int]] = []
    for row in items:
        fid = (row[2] or "").strip()
        if fid:
            by_file.setdefault(fid, []).append(row)
        else:
            no_file.append(row)

    picked: List[Tuple[Any, float, str, int]] = []
    for fid, rows in by_file.items():
        rows.sort(key=lambda r: (-r[3], r[1]))
        picked.extend(rows[:max_per_file])
    for row in no_file:
        picked.append(row)

    picked.sort(key=lambda r: (-r[3], r[1]))
    return [(doc, dist) for doc, dist, _fid, _ov in picked[:max_total]]


def kb_metadata_overlap_score(
    query: str,
    kb_product_titles: Optional[str] = None,
    chunk_text: Optional[str] = None,
) -> int:
    """
    Сводный overlap запроса с KB-полями (для реранка и тестов).
    Пустые KB-поля не дают вклада от titles; учитывается текст чанка при наличии.
    """
    score = 0
    if kb_product_titles:
        score = max(score, token_overlap_count(query, kb_product_titles))
    if chunk_text:
        score = max(score, token_overlap_count(query, chunk_text))
    return score


def extract_parse_result_embedding_tokens(
    parse_results: Optional[QueryParseResults],
) -> List[str]:
    """Уникальные токены из categories / product_names для embedding_query (порядок сохранён)."""
    if parse_results is None:
        return []
    seen: Set[str] = set()
    out: List[str] = []
    for field in (parse_results.categories, parse_results.product_names):
        if not field:
            continue
        for item in field:
            phrase = (item or "").strip()
            if not phrase:
                continue
            if phrase.lower() in seen:
                continue
            seen.add(phrase.lower())
            out.append(phrase)
    return out


def extract_kb_product_titles_needles(
    raw_query: str,
    parse_results: Optional[QueryParseResults] = None,
    sku_hints: Optional[List[str]] = None,
) -> List[str]:
    """
    Подстроки (lowercase) для сопоставления с metadata kb_product_titles при retrieval.
    Chroma не поддерживает $contains по metadata — используется в Python-фильтре.
    """
    seen: Set[str] = set()
    out: List[str] = []

    def add(value: str) -> None:
        v = (value or "").strip().lower()
        if len(v) < 3 or v in seen or v in _KB_TITLES_QUERY_STOPWORDS:
            return
        seen.add(v)
        out.append(v)

    for hint in sku_hints or []:
        add(hint)
        for needle in chroma_contains_needles(hint):
            add(needle)

    if parse_results:
        for field in (parse_results.product_names, parse_results.categories):
            if not field:
                continue
            for item in field:
                phrase = (item or "").strip()
                if not phrase:
                    continue
                add(phrase)
                low_phrase = phrase.lower()
                if len(low_phrase) > 4 and low_phrase.endswith(("ы", "и")):
                    add(low_phrase[:-1])
                for tok in significant_tokens(phrase):
                    add(tok)

    for tok in significant_tokens(raw_query):
        if tok in _KB_TITLES_QUERY_STOPWORDS:
            continue
        if re.fullmatch(r"[a-z0-9][a-z0-9\-]{2,}", tok, re.IGNORECASE):
            add(tok)

    return out


def kb_product_titles_match_score(
    raw_query: str,
    kb_product_titles: Optional[str],
    needles: List[str],
) -> int:
    """Сила совпадения запроса с каноническим kb_product_titles (0 = нет)."""
    titles = (kb_product_titles or "").strip()
    if not titles:
        return 0
    low = titles.lower()
    score = 0
    for needle in needles:
        if needle in low:
            score = max(score, min(len(needle), 12))
    overlap = token_overlap_count(raw_query, titles)
    if overlap > 0:
        score = max(score, overlap)
    return score


def build_files_embedding_query(
    summarized_query: str,
    raw_query: str,
    parse_results: Optional[QueryParseResults] = None,
) -> str:
    """
    Текст для векторного поиска на стадии data_type=files.

    База: summarize + SKU из сырого запроса. При переданном parse_results — дополнительно
    категории и product_names из NER (без дублирования parse_query).
    """
    parts: List[str] = [(summarized_query or "").strip()]
    sku_hints = extract_model_sku_hints(raw_query)
    if sku_hints:
        parts.append(" ".join(sku_hints))
    ner_tokens = extract_parse_result_embedding_tokens(parse_results)
    if ner_tokens:
        parts.append(" ".join(ner_tokens))
    return " ".join(p for p in parts if p).strip()
