
from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

import config
from manufacturer_constants import RAWMID_MANUFACTURER_ID, UNMARKED_MANUFACTURER_ID
from models import Database, Product, QueryParseResults

logger = logging.getLogger(__name__)

# Причины bypass для логов и тестов.
BYPASS_REASON_SKU_CROSS_BRAND = "sku_cross_brand"
BYPASS_REASON_TWO_BRANDS = "two_brands_in_query"
BYPASS_REASON_COMPETITOR_EXPLICIT = "competitor_explicit"

# Расширенный паттерн для probe: суффикс буквы после цифр (TM-800A), без изменения глобального sku_hints.
_SKU_PROBE_SUFFIX_RE = re.compile(
    r"\b([A-Z]{2,5}-?\d{1,3}[A-Z]{0,2})\b",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class ProbeHit:
    model: str
    manufacturer_id: int


def _normalize_probe_query(query: str) -> str:
    q = query or ""
    for ch in ("\u2011", "\u2013", "\u2014", "\u2212"):
        q = q.replace(ch, "-")
    return q


def sku_hints_for_probe(query: str, sku_hints: Optional[list[str]] = None) -> list[str]:
    """
    SKU для probe lookup: базовые hints + литералы с буквенным суффиксом (TM-800A).
    Не заменяет extract_model_sku_hints в остальном пайплайне.
    """
    from routes.files.sku_hints import extract_model_sku_hints

    hints: list[str] = list(sku_hints or extract_model_sku_hints(query))
    seen = {h.upper().replace(" ", "") for h in hints}
    q = _normalize_probe_query(query)
    for m in _SKU_PROBE_SUFFIX_RE.finditer(q):
        token = m.group(1).upper().replace(" ", "")
        if token not in seen:
            seen.add(token)
            hints.append(token)
    return hints


def probe_lookup_by_sku(
    sku_hints: list[str],
    *,
    db_path: Optional[Path] = None,
) -> list[ProbeHit]:
    """
    Возвращает найденные пары (model, manufacturer_id).
    """
    if not sku_hints:
        return []

    path = db_path or config.DATABASE_PATH
    hits: list[ProbeHit] = []
    seen_keys: set[tuple[str, int]] = set()

    with Database(str(path)) as db:
        for sku in sku_hints:
            sku_low = (sku or "").strip().lower()
            if not sku_low:
                continue
            try:
                db.cursor.execute(
                    """
                    SELECT p.model, p.manufacturer_id FROM products p
                    WHERE p.status != 0
                      AND (
                        LOWER(p.model) = ?
                        OR LOWER(p.model) LIKE (? || ' %')
                        OR LOWER(p.model) LIKE (? || '-%')
                        OR LOWER(p.model) LIKE (? || '_%')
                      )
                    """,
                    (sku_low, sku_low, sku_low, sku_low),
                )
                for model, manufacturer_id in db.cursor.fetchall():
                    if manufacturer_id is None:
                        continue
                    key = (model, int(manufacturer_id))
                    if key in seen_keys:
                        continue
                    seen_keys.add(key)
                    hits.append(ProbeHit(model=model, manufacturer_id=int(manufacturer_id)))
            except Exception as e:
                logger.warning("probe_lookup_by_sku failed for SKU=%r: %s", sku, e)

    return hits


def _distinct_manufacturer_ids_for_cross_brand(probe_result: list[ProbeHit]) -> set[int]:
    """ID для условия A; 93 не считается отдельным брендом при кросс-бренде."""
    ids = {h.manufacturer_id for h in probe_result}
    ids.discard(UNMARKED_MANUFACTURER_ID)
    return ids


def should_bypass_rawmid_filter(
    query: str,
    parse_results: Optional[QueryParseResults],
    sku_hints: list[str],
    probe_result: list[ProbeHit],
    brands_in_text: list[int],
) -> tuple[bool, str]:
    """
    Возвращает (bypass, reason). reason пустая строка, если bypass=false.
    parse_results зарезервирован для условия D (не реализовано).
    """
    _ = parse_results  # условие D — отдельная задача

    # A — кросс-бренд по SKU
    cross_brand_ids = _distinct_manufacturer_ids_for_cross_brand(probe_result)
    if len(cross_brand_ids) >= 2:
        return True, BYPASS_REASON_SKU_CROSS_BRAND


    brand_set = set(brands_in_text)
    non_rawmid_brands = {b for b in brand_set if b != RAWMID_MANUFACTURER_ID}
    if len(brand_set) >= 2 and non_rawmid_brands:
        return True, BYPASS_REASON_TWO_BRANDS


    probe_non_rawmid = [
        h for h in probe_result if h.manufacturer_id != RAWMID_MANUFACTURER_ID
    ]
    if non_rawmid_brands and probe_non_rawmid:
        return True, BYPASS_REASON_COMPETITOR_EXPLICIT

    return False, ""


def build_manufacturer_sql_clause(bypass: bool, *, table_alias: str = "p") -> str:
    """
    SQL-фрагмент для SELECT по products.
    """
    col = f"{table_alias}.manufacturer_id"
    if not bypass:
        return f"AND {col} = {RAWMID_MANUFACTURER_ID}"
    return f"AND {col} IS NOT NULL AND {col} != {UNMARKED_MANUFACTURER_ID}"


def filter_products_by_manufacturer(
    products: list[Product],
    bypass: bool,
) -> list[Product]:
    """Пост-фильтр: исключить 93 и NULL; при bypass=false — только 46."""
    filtered: list[Product] = []
    for product in products:
        mid = getattr(product, "manufacturer_id", None)
        if mid is None or mid == UNMARKED_MANUFACTURER_ID:
            continue
        if not bypass and mid != RAWMID_MANUFACTURER_ID:
            continue
        filtered.append(product)
    return filtered


def evaluate_manufacturer_bypass(
    query: str,
    parse_results: Optional[QueryParseResults] = None,
    *,
    db_path: Optional[Path] = None,
) -> tuple[bool, str]:
    """
    Полный детерминированный пайплайн bypass (шаги 1–4 из ТЗ) для интеграции и тестов.
    """
    from manufacturer_detect import detect_manufacturers_in_query

    hints = sku_hints_for_probe(query)
    brands = detect_manufacturers_in_query(query, db_path=db_path)
    probe = probe_lookup_by_sku(hints, db_path=db_path)
    bypass, reason = should_bypass_rawmid_filter(
        query, parse_results, hints, probe, brands
    )
    logger.info(
        "manufacturer_bypass: enabled=%s reason=%r sku_hints=%r brands=%r probe_hits=%d",
        bypass,
        reason or None,
        hints,
        brands,
        len(probe),
    )
    return bypass, reason
