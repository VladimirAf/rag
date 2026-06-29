"""
Поиск аксессуаров и запчастей по каноническим связям product_schema (аналог oc_product_schema).

Маппинг ID: product_schema.product_id / related_product_id = products.product_id (внешний ID витрины OpenCart).
"""

from __future__ import annotations

import csv
import logging
import re
import sqlite3
from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional, Sequence, Union

LinkTypeFilter = Union[str, Sequence[str], None]

import config
from manufacturer_filter import build_manufacturer_sql_clause
from models import PRODUCT_COLS, Database, Product, QueryParseResults

PRODUCT_SCHEMA_DDL = """
CREATE TABLE IF NOT EXISTS product_schema (
    product_id INTEGER NOT NULL,
    related_product_id INTEGER NOT NULL,
    schema_num INTEGER NOT NULL DEFAULT 0,
    link_type TEXT NOT NULL,
    PRIMARY KEY (product_id, related_product_id)
);
CREATE INDEX IF NOT EXISTS idx_product_schema_related ON product_schema(related_product_id);
CREATE INDEX IF NOT EXISTS idx_product_schema_product ON product_schema(product_id);
"""

_SCHEMA_LOOKUP_TRIGGER_STEMS = (
    "нож",
    "лезв",
    "крышк",
    "мешок",
    "капсул",
    "картридж",
    "картри",
    "чалд",
    "запчаст",
    "аксессуар",
    "лопатк",
    "воронк",
    "фильтр",
    "кольц",
    "проклад",
    "подшипник",
    "винт",
    "кабель",
    "тумблер",
    "регулятор",
    "комплект",
    "гайк",
    "шнек",
    "жмых",
    "орехов",
    "молок",
    "ссылк",
    "купить",
    "есть ли",
)

_PART_MENTION_STEMS = (
    "нож",
    "лезв",
    "винт",
    "подшипник",
    "проклад",
    "гайк",
    "кабель",
    "тумблер",
    "регулятор",
    "крепеж",
    "шнур",
    "импульс",
    "база",
    "блок",
    "резин",
    "силикон",
    "штыр",
    "картридж",
    "картри",
    "чалд",
)

_CARTRIDGE_MENTION_STEMS = (
    "картридж",
    "картри",
    "чалд",
    "капсул",
)

_ACCESSORY_MENTION_STEMS = (
    "мешок",
    "капсул",
    "лопатк",
    "воронк",
    "комплект",
    "бутылк",
    "фиксир",
    "кольц",
)

_STEMS_FROM_QUERY = tuple(
    dict.fromkeys(
        _SCHEMA_LOOKUP_TRIGGER_STEMS
        + _PART_MENTION_STEMS
        + _ACCESSORY_MENTION_STEMS
        + _CARTRIDGE_MENTION_STEMS
    )
)

_QUERY_STOP_WORDS = frozenset({
    "к",
    "для",
    "на",
    "по",
    "из",
    "у",
    "о",
    "от",
    "до",
    "за",
    "при",
    "без",
    "или",
    "ли",
    "же",
    "не",
    "но",
    "а",
    "да",
    "нет",
    "если",
    "то",
    "есть",
    "дай",
    "дайте",
    "скажите",
    "нужен",
    "нужна",
    "нужно",
    "хочу",
    "ссылку",
    "ссылка",
    "купить",
    "этот",
    "этого",
    "этому",
    "этим",
    "этой",
    "этом",
    "блендер",
    "блендера",
    "блендеру",
    "блендером",
    "блендере",
    "блендеры",
    "rawmid",
    "dream",
    "samurai",
    "professional",
    "профессиональный",
    "профессионального",
})


def ensure_product_schema_table(conn: sqlite3.Connection) -> None:
    conn.executescript(PRODUCT_SCHEMA_DDL)


def import_product_schema_from_csv(
    csv_path: str | Path,
    *,
    db_path: Optional[str] = None,
) -> int:
    """
    Идемпотентный импорт product_schema из CSV (колонки product_id, zap_id, num).
    Возвращает число загруженных строк.
    """
    path = Path(csv_path)
    rows: list[tuple[int, int, int, str]] = []
    with path.open(newline="", encoding="utf-8") as fh:
        reader = csv.DictReader(fh)
        for row in reader:
            product_id = int(row["product_id"])
            related_product_id = int(row["zap_id"])
            schema_num = int(row["num"])
            link_type = "accessory" if schema_num == 0 else "part"
            rows.append((product_id, related_product_id, schema_num, link_type))

    if db_path:
        conn = sqlite3.connect(db_path)
        conn.create_function("LOWER", 1, lambda s: s.lower() if s else s)
    else:
        conn = sqlite3.connect(Database().path)
        conn.create_function("LOWER", 1, lambda s: s.lower() if s else s)

    try:
        ensure_product_schema_table(conn)
        conn.execute("DELETE FROM product_schema")
        conn.executemany(
            """
            INSERT INTO product_schema (product_id, related_product_id, schema_num, link_type)
            VALUES (?, ?, ?, ?)
            """,
            rows,
        )
        conn.commit()
    finally:
        conn.close()
    return len(rows)


def _model_lookup_params(model: str) -> tuple[str, str, str, str, str]:
    sku_low = (model or "").strip().lower()
    return sku_low, sku_low, sku_low, sku_low, sku_low


def resolve_storefront_product_id(model: str, *, db_path: Optional[str] = None) -> Optional[int]:
    """Внешний product_id основного товара по артикулу model."""
    sku_low, p1, p2, p3, p4 = _model_lookup_params(model)
    if not sku_low:
        return None

    ctx = Database(db_path) if db_path else Database()
    with ctx as db:
        db.cursor.execute(
            """
            SELECT product_id FROM products
            WHERE status != 0
              AND product_id IS NOT NULL
              AND (
                LOWER(model) = ?
                OR LOWER(model) LIKE (? || ' %')
                OR LOWER(model) LIKE (? || '-%')
                OR LOWER(model) LIKE (? || '_%')
              )
            ORDER BY
              CASE WHEN LOWER(model) = ? THEN 0 ELSE 1 END,
              quantity DESC
            LIMIT 1
            """,
            (sku_low, p1, p2, p3, p4),
        )
        row = db.cursor.fetchone()
    return int(row[0]) if row else None


def _normalize_token_compact(word: str) -> str:
    """Буквы/цифры без пунктуации: латиница, кириллица (а-яё), 0-9."""
    return re.sub(r"[^a-z0-9а-яё]", "", (word or "").lower())


def _infer_link_type_from_query(query: str) -> LinkTypeFilter:
    q = (query or "").lower()
    if "аксессуар" in q:
        return "accessory"
    if "запчаст" in q:
        return "part"
    if any(stem in q for stem in _CARTRIDGE_MENTION_STEMS):
        return ("part", "accessory")
    has_part = any(stem in q for stem in _PART_MENTION_STEMS)
    has_accessory = any(stem in q for stem in _ACCESSORY_MENTION_STEMS)
    if has_part and has_accessory:
        return ("part", "accessory")
    if has_accessory:
        return "accessory"
    if has_part:
        return "part"
    return None


def _build_link_type_clause(link_type: LinkTypeFilter) -> tuple[str, list[object]]:
    if not link_type:
        return "", []
    if isinstance(link_type, str):
        return "AND ps.link_type = ?", [link_type]
    types = list(link_type)
    placeholders = ", ".join("?" * len(types))
    return (
        f"AND (ps.link_type IN ({placeholders}) OR ps.link_type IS NULL)",
        types,
    )


def _format_link_type_for_log(link_type: LinkTypeFilter) -> str:
    if link_type is None:
        return "any"
    if isinstance(link_type, str):
        return link_type
    return "+".join(link_type)


def extract_mention_tokens(query: str, *, sku_hints: Optional[Sequence[str]] = None) -> List[str]:
    """Токены из запроса для сужения schema-списка (без SKU и стоп-слов)."""
    sku_compact = {
        _normalize_token_compact(s)
        for s in (sku_hints or [])
        if s
    }
    words = re.findall(r"[a-zA-Zа-яА-ЯёЁ0-9]+", (query or "").lower())
    tokens: list[str] = []
    for word in words:
        if len(word) < 2:
            continue
        if word in _QUERY_STOP_WORDS:
            continue
        compact = _normalize_token_compact(word)
        if compact and compact in sku_compact:
            continue
        if compact and any(
            compact in sku or sku in compact for sku in sku_compact if len(sku) >= 4
        ):
            continue
        tokens.append(word)
    return tokens


@dataclass(frozen=True)
class _SchemaHit:
    product: Product
    schema_num: int
    link_type: str


def _product_haystack(product: Product) -> str:
    return f"{product.name or ''} {product.model or ''}".lower()


def _stems_from_query(query: str) -> List[str]:
    q = (query or "").lower()
    return [stem for stem in _STEMS_FROM_QUERY if stem in q]


def _product_matches_mention(product: Product, tokens: Sequence[str]) -> bool:
    haystack = _product_haystack(product)
    return any(token in haystack for token in tokens)


def _product_matches_query_stems(product: Product, stems: Sequence[str]) -> bool:
    if not stems:
        return False
    haystack = _product_haystack(product)
    return any(stem in haystack for stem in stems)


def _link_type_score(link_type: str, inferred: LinkTypeFilter) -> int:
    if not inferred:
        return 0
    if isinstance(inferred, str):
        return 3 if link_type == inferred else 0
    if link_type in inferred:
        return 3
    return 0


def _score_schema_hit(
    hit: _SchemaHit,
    *,
    tokens: Sequence[str],
    query_stems: Sequence[str],
    inferred_link_type: LinkTypeFilter,
) -> int:
    haystack = _product_haystack(hit.product)
    score = max(0, 1000 - hit.schema_num)
    score += _link_type_score(hit.link_type, inferred_link_type)
    if tokens:
        score += sum(10 for token in tokens if token in haystack)
    elif query_stems:
        score += sum(5 for stem in query_stems if stem in haystack)
    return score


def _sort_hits_by_score(
    hits: Sequence[_SchemaHit],
    *,
    tokens: Sequence[str],
    query_stems: Sequence[str],
    inferred_link_type: LinkTypeFilter,
) -> List[_SchemaHit]:
    return sorted(
        hits,
        key=lambda h: (
            -_score_schema_hit(
                h,
                tokens=tokens,
                query_stems=query_stems,
                inferred_link_type=inferred_link_type,
            ),
            h.schema_num,
            -(h.product.quantity or 0),
        ),
    )


def _filter_by_mention(
    hits: List[_SchemaHit],
    *,
    tokens: Sequence[str],
    query: str,
    inferred_link_type: LinkTypeFilter,
    max_hits: int,
    fallback_k: int,
) -> List[_SchemaHit]:
    """Strict mention filter; при 0 matches — top-K по score; cap max_hits."""
    total = len(hits)
    query_stems = _stems_from_query(query) if not tokens else ()

    if tokens:
        strict = [h for h in hits if _product_matches_mention(h.product, tokens)]
        if strict:
            selected = _sort_hits_by_score(
                strict,
                tokens=tokens,
                query_stems=query_stems,
                inferred_link_type=inferred_link_type,
            )
        else:
            selected = _sort_hits_by_score(
                hits,
                tokens=tokens,
                query_stems=query_stems,
                inferred_link_type=inferred_link_type,
            )[:fallback_k]
    elif query_stems:
        stem_filtered = [
            h for h in hits if _product_matches_query_stems(h.product, query_stems)
        ]
        pool = stem_filtered if stem_filtered else hits
        selected = _sort_hits_by_score(
            pool,
            tokens=(),
            query_stems=query_stems,
            inferred_link_type=inferred_link_type,
        )
        if not stem_filtered:
            selected = selected[:fallback_k]
    else:
        selected = _sort_hits_by_score(
            hits,
            tokens=(),
            query_stems=(),
            inferred_link_type=inferred_link_type,
        )[:fallback_k]

    returned = selected[:max_hits]
    dropped_noise = total - len(returned)
    logging.info(
        "product_schema_lookup filter returned=%d dropped_noise=%d "
        "tokens=%r query_stems=%r total=%d",
        len(returned),
        dropped_noise,
        list(tokens),
        list(query_stems),
        total,
    )
    return returned


def find_related_for_storefront_product_id(
    storefront_product_id: int,
    *,
    link_type: LinkTypeFilter = None,
    mention_tokens: Optional[Sequence[str]] = None,
    query: str = "",
    bypass: bool = False,
    db_path: Optional[str] = None,
) -> List[Product]:
    mfr_clause = build_manufacturer_sql_clause(bypass, table_alias="p")
    ctx = Database(db_path) if db_path else Database()
    with ctx as db:
        params: list[object] = [storefront_product_id]
        link_clause, link_params = _build_link_type_clause(link_type)
        params.extend(link_params)
        db.cursor.execute(
            f"""
            SELECT p.*, ps.schema_num, ps.link_type
            FROM product_schema ps
            JOIN products p ON p.product_id = ps.related_product_id
            WHERE ps.product_id = ?
              AND p.status != 0
              {link_clause}
              {mfr_clause}
            ORDER BY ps.schema_num, p.quantity DESC
            """,
            params,
        )
        rows = db.cursor.fetchall()
    hits: list[_SchemaHit] = []
    seen_models: set[str] = set()
    n_product_cols = len(PRODUCT_COLS)
    for row in rows:
        product = Product.from_record(row[:n_product_cols])
        model_key = (product.model or product.name or "").strip().lower()
        if not model_key or model_key in seen_models:
            continue
        seen_models.add(model_key)
        hits.append(
            _SchemaHit(
                product=product,
                schema_num=int(row[n_product_cols]),
                link_type=str(row[n_product_cols + 1] or ""),
            )
        )

    tokens = list(mention_tokens or ())
    filtered_hits = _filter_by_mention(
        hits,
        tokens=tokens,
        query=query,
        inferred_link_type=link_type,
        max_hits=config.PRODUCT_SCHEMA_MAX_HITS,
        fallback_k=config.PRODUCT_SCHEMA_STRICT_FALLBACK_K,
    )
    return [h.product for h in filtered_hits]


def find_accessories_for_model(
    model: str,
    *,
    mention: Optional[str] = None,
    bypass: bool = False,
    db_path: Optional[str] = None,
) -> List[Product]:
    storefront_id = resolve_storefront_product_id(model, db_path=db_path)
    if storefront_id is None:
        logging.info("product_schema_lookup model=%r hits=0 link_type=accessory reason=no_main_product", model)
        return []
    tokens = extract_mention_tokens(mention or "")
    products = find_related_for_storefront_product_id(
        storefront_id,
        link_type="accessory",
        mention_tokens=tokens or None,
        query=mention or "",
        bypass=bypass,
        db_path=db_path,
    )
    logging.info(
        "product_schema_lookup model=%r hits=%d link_type=accessory mention=%r",
        model,
        len(products),
        mention,
    )
    return products


def find_parts_for_model(
    model: str,
    *,
    mention: Optional[str] = None,
    bypass: bool = False,
    db_path: Optional[str] = None,
) -> List[Product]:
    storefront_id = resolve_storefront_product_id(model, db_path=db_path)
    if storefront_id is None:
        logging.info("product_schema_lookup model=%r hits=0 link_type=part reason=no_main_product", model)
        return []
    tokens = extract_mention_tokens(mention or "")
    products = find_related_for_storefront_product_id(
        storefront_id,
        link_type="part",
        mention_tokens=tokens or None,
        query=mention or "",
        bypass=bypass,
        db_path=db_path,
    )
    logging.info(
        "product_schema_lookup model=%r hits=%d link_type=part mention=%r",
        model,
        len(products),
        mention,
    )
    return products


def find_related_for_model_sku(
    model: str,
    *,
    query: str = "",
    bypass: bool = False,
    db_path: Optional[str] = None,
) -> List[Product]:
    """Универсальный lookup для find_products: link_type из запроса + mention-токены."""
    storefront_id = resolve_storefront_product_id(model, db_path=db_path)
    if storefront_id is None:
        logging.info(
            "product_schema_lookup model=%r hits=0 link_type=unknown reason=no_main_product",
            model,
        )
        return []

    link_type = _infer_link_type_from_query(query)
    mention_tokens = extract_mention_tokens(query, sku_hints=[model])
    products = find_related_for_storefront_product_id(
        storefront_id,
        link_type=link_type,
        mention_tokens=mention_tokens or None,
        query=query,
        bypass=bypass,
        db_path=db_path,
    )
    logging.info(
        "product_schema_lookup model=%r hits=%d link_type=%s mention_tokens=%r",
        model,
        len(products),
        _format_link_type_for_log(link_type),
        mention_tokens,
    )
    return products


def query_has_accessory_intent(query: str) -> bool:
    """Запрос явно про аксессуар — не сужать best_priority до основных товаров."""
    q = (query or "").lower()
    if "аксессуар" in q:
        return True
    return any(stem in q for stem in _ACCESSORY_MENTION_STEMS)


def query_has_part_intent(query: str) -> bool:
    """Запрос явно про запчасть — не сужать best_priority до основных товаров."""
    q = (query or "").lower()
    if "запчаст" in q:
        return True
    return any(stem in q for stem in _PART_MENTION_STEMS)


def query_requests_schema_lookup(
    query: str,
    response: QueryParseResults,
    *,
    direct_sku_hits: Optional[Sequence[str]] = None,
) -> bool:
    """
    Нужен ли schema lookup: есть якорный SKU и маркеры аксессуара/запчасти или other_products.
    Чистый запрос «BDS-04» без детали — False.
    """
    if not direct_sku_hits:
        return False
    if response.other_products:
        return True
    q = (query or "").lower()
    return any(stem in q for stem in _SCHEMA_LOOKUP_TRIGGER_STEMS)
