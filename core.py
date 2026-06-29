from typing import List, Dict, Optional, Literal
from models import (
    Product,
    Database,
    Message,
    RelatedContext,
    ProductSearchResult,
    QueryParseResults,
    UNDEFINED_INTENT_LABEL,
)
import time
import llm_funcs 
import logging
from routes import tickets as tickets_manager
from routes import files as files_manager
from routes.files.sku_hints import extract_model_sku_hints
import config
import dbfuncs
import product_schema_lookup
from intent_classifier import classify_intent
from intent_router import resolve_intent_route
from intent_observability import (
    log_find_context_early_exit,
    log_find_context_routing,
    log_undefined_intent_event,
)
from pathlib import Path
from logging.handlers import RotatingFileHandler
import re
from category_priority import get_category_priority as _get_category_priority
from manufacturer_filter import (
    build_manufacturer_sql_clause,
    evaluate_manufacturer_bypass,
    filter_products_by_manufacturer,
    sku_hints_for_probe,
)

# Заглушка на случай, когда LLM не использовала ни одного источника из RAG-контекста
# (products/tickets/files) и ссылается только на [source][prompt] или не даёт цитат вовсе.
NO_RAG_DATA_MESSAGE: str = (
    "Упс! Похоже, моя база знаний споткнулась на этом вопросе. "
    "Намекните чуть подробнее или другими словами — о какой детали/модели речь? "
    "Я попробую зайти на второй круг поиска!"
)


def escape_fts5_query(query: str) -> str:
    """
    Экранирует специальные символы FTS5 для безопасного поиска.
    FTS5 специальные символы: ", ', *, AND, OR, NOT, -, ., и другие.
    """
    if not query:
        return query
    
    # Убираем лишние пробелы
    query = query.strip()
    
    # Если запрос содержит только одно слово без спецсимволов, возвращаем как есть
    if re.match(r'^[a-zA-Zа-яА-ЯёЁ0-9]+$', query):
        return query
    
    # Экранируем кавычки
    query = query.replace('"', '""')
    
    # Если запрос содержит спецсимволы или пробелы, оборачиваем в кавычки
    # Это позволяет искать фразы с точками, дефисами и другими символами
    if re.search(r'[^\w\s]', query) or ' ' in query:
        return f'"{query}"'
    
    return query


# Слова, по которым бессмысленно гонять FTS по каталогу товаров (FAQ, предлоги, «заказ/оплата/сайт» и т.п.).
# Иначе fallback «по каждому слову» раздувает выдачу на запросах вида «как оплатить заказ на сайте».
_FTS5_PRODUCT_FALLBACK_STOP_WORDS = frozenset({
    "как", "что", "где", "когда", "почему", "зачем", "кто", "чей", "чем", "кого", "чего",
    "это", "этот", "эта", "эти", "того", "тем", "том", "ту", "те", "та", "то",
    "все", "всё", "весь", "вся", "всю", "наш", "ваш", "мой", "моя", "мои", "их", "его", "её",
    "есть", "были", "было", "будет", "можно", "нужно", "надо", "хочу", "дайте", "скажите",
    "меня", "тебя", "вас", "нас", "мне", "тебе", "вам", "нам", "они", "мы", "вы", "он", "она", "оно",
    # предлоги и союзы (частые в вопросах)
    "в", "во", "на", "по", "из", "у", "о", "об", "от", "до", "за", "при", "без", "над", "под", "про",
    "для", "со", "ни", "или", "ли", "же", "бы", "не", "но", "а", "да", "нет", "если", "то",
    # типичные слова инфо-запросов про магазин (не названия товаров)
    "сайт", "сайте", "сайта", "интернет", "магазин", "магазине", "магазина",
    "заказ", "заказа", "заказе", "заказу", "заказы",
    "оплатить", "оплата", "оплаты", "оплату", "оплачу", "оплачен",
    "доставка", "доставки", "доставку", "доставке",
    "оформить", "оформления", "оформление", "получить", "получение",
    "вернуть", "возврат", "возврата",
    "руб", "рублей", "рубля",
})


def _fts5_product_fallback_tokens(text: str) -> List[str]:
    """Токены для FTS5 word/prefix fallback: без шумных коротких слов и стоп-слов."""
    words = re.findall(r"[a-zA-Zа-яА-ЯёЁ0-9]+", text)
    out: List[str] = []
    for w in words:
        wl = w.lower()
        if len(wl) < 4:
            continue
        if wl in _FTS5_PRODUCT_FALLBACK_STOP_WORDS:
            continue
        out.append(w)
    return out


def get_category_priority(category):
    """
    Возвращает приоритет категории для сортировки.
    Порядок: Товары > Уценка > Аксессуары > Запчасти
    """
    # Оставляем функцию в core.py как публичную точку, но реализацию держим в util,
    # чтобы её могли безопасно использовать другие модули без циклических импортов.
    return _get_category_priority(category)


def _should_keep_accessory_products(
    response: QueryParseResults,
    *,
    query: str = "",
    schema_lookup_used: bool = False,
) -> bool:
    """Не сужать выдачу best_priority при other_products, schema lookup или intent аксессуар/запчасть."""
    return (
        bool(response.other_products)
        or schema_lookup_used
        or product_schema_lookup.query_has_accessory_intent(query)
        or product_schema_lookup.query_has_part_intent(query)
    )


def _alnum_compact(s: str) -> str:
    return re.sub(r"[^a-z0-9]", "", (s or "").lower())


def _product_names_contain_sku(product_names: Optional[List[str]], sku: str) -> bool:
    """Проверяет, есть ли артикул уже среди product_names от parse_query."""
    needle = _alnum_compact(sku)
    if len(needle) < 4:
        return False
    if not product_names:
        return False
    for name in product_names:
        if needle in _alnum_compact(name):
            return True
    return False


def _product_matches_direct_sku_hit(product: Product, direct_sku_hits: set[str]) -> bool:
    """Проверяет, относится ли товар к линейке direct SKU (по model/name)."""
    candidates = [product.model, product.name]
    return any(
        _product_names_contain_sku([candidate], hit_sku)
        for candidate in candidates
        if candidate
        for hit_sku in direct_sku_hits
    )


def _related_products_contain_direct_sku(
    products: List[Product],
    direct_sku_hits: set[str],
) -> bool:
    return any(_product_matches_direct_sku_hit(p, direct_sku_hits) for p in products)


def _requested_skus_from_query(query: str) -> set[str]:
    """Артикулы из текста запроса (probe), без ошибочных product_names от NER."""
    return {
        hint.strip().upper().replace(" ", "")
        for hint in sku_hints_for_probe(query)
        if hint and hint.strip()
    }


def _product_context_tier(
    product: Product,
    *,
    anchor_skus: set[str],
    schema_hit_product_ids: set[int],
    llm_selected_product_ids: set[int],
) -> int:
    """0=direct anchor, 1=schema, 2=LLM-selected, 3=остальное."""
    if anchor_skus and _product_matches_direct_sku_hit(product, anchor_skus):
        return 0
    pid = int(product.product_id) if product.product_id is not None else None
    if pid is not None and pid in schema_hit_product_ids:
        return 1
    if pid is not None and pid in llm_selected_product_ids:
        return 2
    return 3


def _cap_products_tiered(
    products: List[Product],
    *,
    max_items: int,
    anchor_skus: set[str],
    schema_hit_product_ids: set[int],
    llm_selected_product_ids: set[int],
) -> List[Product]:
    """
    Tiered-cap: direct SKU → schema hits → LLM-selected → остальное (quantity DESC).
    Dedupe по name с сохранением первого вхождения по tier.
    """
    if max_items < 1 or len(products) <= max_items:
        return products

    buckets: dict[int, List[Product]] = {0: [], 1: [], 2: [], 3: []}
    for product in products:
        tier = _product_context_tier(
            product,
            anchor_skus=anchor_skus,
            schema_hit_product_ids=schema_hit_product_ids,
            llm_selected_product_ids=llm_selected_product_ids,
        )
        buckets[tier].append(product)

    buckets[3].sort(key=lambda p: -(getattr(p, "quantity", None) or 0))

    ordered = buckets[0] + buckets[1] + buckets[2] + buckets[3]
    seen_names: set[str] = set()
    deduped: List[Product] = []
    for product in ordered:
        if product.name in seen_names:
            continue
        seen_names.add(product.name)
        deduped.append(product)

    return deduped[:max_items]


def _merge_regex_skus_into_parse_results(
    response: QueryParseResults,
    query: str,
) -> QueryParseResults:
    """
    Дополняет product_names артикулами из запроса (regex), если LLM их не вернула.
    """
    hints = extract_model_sku_hints(query)
    if not hints:
        return response
    names = list(response.product_names or [])
    changed = False
    for sku in hints:
        if not _product_names_contain_sku(names, sku):
            names.append(sku)
            changed = True
    if not changed:
        return response
    return response.model_copy(update={"product_names": names})


def find_products(
    query: str,
    product_name: Optional[str] = None,
    ner_model: Optional[str] = None,
    related_products_model: Optional[str] = None,
    parse_results: Optional[QueryParseResults] = None,
    ) -> ProductSearchResult:
    query = query.strip()
    response = parse_results or llm_funcs.parse_query(query, model_name=ner_model)
    response = _merge_regex_skus_into_parse_results(response, query)

    bypass, bypass_reason = evaluate_manufacturer_bypass(query, response)
    rawmid_filter_enabled = not bypass
    mfr_clause = build_manufacturer_sql_clause(bypass)
    logging.info(
        "rawmid_filter_enabled=%s bypass_reason=%r",
        rawmid_filter_enabled,
        bypass_reason or None,
    )

    related_products = list()
    schema_lookup_used = False
    schema_hit_product_ids: set[int] = set()

    # Быстрый и надёжный путь для SKU: FTS5 плохо переносит дефисы ("RMC-01") и иногда уходит в шум.
    # В нашей БД model обычно начинается с артикула или равен ему, поэтому ищем напрямую по products.model.
    # Direct SKU: regex из запроса (sku_hints) + SQL по products.model (линейка артикула).
    # Не путать с product_names от NER — их дополняем через _merge_regex_skus_into_parse_results.
    direct_sku_hits: set[str] = set()
    sku_hints = sku_hints_for_probe(query)
    if sku_hints:
        with Database() as db:
            for sku in sku_hints:
                sku_low = (sku or "").strip().lower()
                if not sku_low:
                    continue
                try:
                    db.cursor.execute(
                        f"""
                        SELECT p.* FROM products p
                        WHERE p.status != 0
                          {mfr_clause}
                          AND (
                            LOWER(p.model) = ?
                            OR LOWER(p.model) LIKE (? || ' %')
                            OR LOWER(p.model) LIKE (? || '-%')
                            OR LOWER(p.model) LIKE (? || '_%')
                          )
                        """,
                        (sku_low, sku_low, sku_low, sku_low),
                    )
                    rows = db.cursor.fetchall()
                    if rows:
                        related_products.extend([Product.from_record(row) for row in rows])
                        direct_sku_hits.add(sku.strip().upper().replace(" ", ""))
                        logging.info(
                            f"[DEBUG] Direct model lookup by SKU={sku!r}: n_rows={len(rows)}"
                        )
                except Exception as e:
                    logging.warning(f"[DEBUG] Direct model lookup failed for SKU={sku!r}: {e}")

    if direct_sku_hits and product_schema_lookup.query_requests_schema_lookup(
        query,
        response,
        direct_sku_hits=sorted(direct_sku_hits),
    ):
        for sku in sorted(direct_sku_hits):
            schema_hits = product_schema_lookup.find_related_for_model_sku(
                sku,
                query=query,
                bypass=bypass,
            )
            if schema_hits:
                for hit in schema_hits:
                    if hit.product_id is not None:
                        schema_hit_product_ids.add(int(hit.product_id))
                related_products.extend(schema_hits)
                schema_lookup_used = True

    # Вариант A: FTS5 по артикулу даёт шум (RMC-01 → RMP-04); не дублируем успешный direct SKU.
    product_names_for_fts: list[str] = []
    if response.product_names:
        product_names_for_fts = [
            pn
            for pn in response.product_names
            if not any(
                _product_names_contain_sku([pn], hit_sku) for hit_sku in direct_sku_hits
            )
        ]
        if direct_sku_hits and len(product_names_for_fts) < len(response.product_names):
            logging.info(
                "[DEBUG] Skipping FTS5 for product_names covered by direct SKU lookup: "
                f"skipped={[pn for pn in response.product_names if pn not in product_names_for_fts]!r}, "
                f"direct_hits={sorted(direct_sku_hits)}"
            )

    # Поиск по извлеченным названиям продуктов через FTS5
    if product_names_for_fts:
        with Database() as db:
            for product_name in product_names_for_fts:
                try:
                    # Экранируем специальные символы FTS5
                    search_term = escape_fts5_query(product_name)
                    db.cursor.execute(f"""
                        SELECT p.* FROM products p
                        INNER JOIN products_fts5 fts ON p.rowid = fts.rowid
                        WHERE products_fts5 MATCH ? AND p.status != 0
                          {mfr_clause}
                    """, (search_term,))
                    rows = db.cursor.fetchall()
                    product_records = [Product.from_record(row) for row in rows]
                    related_products.extend(product_records)
                except Exception as e:
                    logging.warning(f"FTS5 search error for '{product_name}': {e}")
                    # Fallback: простой поиск по словам (без стоп-слов и коротких токенов — меньше шума)
                    for word in _fts5_product_fallback_tokens(product_name):
                        try:
                            word_term = escape_fts5_query(word)
                            db.cursor.execute(f"""
                                SELECT p.* FROM products p
                                INNER JOIN products_fts5 fts ON p.rowid = fts.rowid
                                WHERE products_fts5 MATCH ? AND p.status != 0
                                  {mfr_clause}
                            """, (word_term,))
                            rows = db.cursor.fetchall()
                            product_records = [Product.from_record(row) for row in rows]
                            related_products.extend(product_records)
                        except Exception:
                            continue
    
    # Если по артикулам уже есть попадания, не подмешиваем сотни SKU из родительской категории
    # («Блендеры» даёт FTS по общему названию категории). Исключение — явный запрос альтернатив
    # (other_products=true), но не когда schema lookup уже дал релевантные связи.
    skip_category_search = (
        bool(response.product_names)
        and bool(related_products)
        and (
            not response.other_products
            or (schema_lookup_used and bool(schema_hit_product_ids))
        )
    )

    # Поиск по категориям из NER (напр. "вакууматор" -> категория "Вакууматоры")
    if response.categories and not skip_category_search:
        logging.info(f"[DEBUG] NER extracted categories: {response.categories}")
        try:
            by_category = dbfuncs.find_mentioned_products(
                product_names=None,
                categories=response.categories,
                bypass=bypass,
            )
            logging.info(f"[DEBUG] Found {len(by_category)} products by categories")
            related_products.extend(by_category)
        except Exception as e:
            logging.warning(f"Search by categories error: {e}")
    elif response.categories and skip_category_search:
        if schema_lookup_used and schema_hit_product_ids:
            logging.info(
                "[DEBUG] skipped_category_search_reason=schema_lookup_used "
                f"schema_hit_count={len(schema_hit_product_ids)} "
                f"product_names={response.product_names!r} other_products={response.other_products}"
            )
        else:
            logging.info(
                "[DEBUG] Skipping category search: matches already found for product_names "
                f"{response.product_names}, other_products={response.other_products}"
            )

    # Fallback: если LLM не извлек product_names и по категориям ничего нет, ищем по запросу через FTS5
    if not related_products and query:
        logging.info(f"[DEBUG] No products found by NER/Categories, falling back to FTS5 with query: {query}")
        with Database() as db:
            try:
                # Экранируем специальные символы FTS5
                search_term = escape_fts5_query(query)
                logging.info(f"[DEBUG] FTS5 full query search term: {search_term}")
                db.cursor.execute(f"""
                    SELECT p.* FROM products p
                    INNER JOIN products_fts5 fts ON p.rowid = fts.rowid
                    WHERE products_fts5 MATCH ? AND p.status != 0
                      {mfr_clause}
                    LIMIT 20
                """, (search_term,))
                rows = db.cursor.fetchall()
                if rows:
                    product_records = [Product.from_record(row) for row in rows]
                    related_products.extend(product_records)
                    logging.info(f"[DEBUG] FTS5 full query found {len(rows)} products")
                else:
                    logging.info(f"[DEBUG] FTS5 full query found 0 products")
            except Exception as e:
                logging.warning(f"FTS5 search error for query '{query}': {e}")
                
            # Fallback: поиск по отдельным словам и по префиксу (для словоформ: "вакууматора" -> "вакуум*")
            if not related_products:
                words = _fts5_product_fallback_tokens(query)
                logging.info(f"[DEBUG] FTS5 word fallback tokens (filtered): {words}")
                if not words:
                    logging.info("[DEBUG] FTS5 word fallback skipped: no tokens after stopword filter")
                for word in words:
                    # Точное совпадение слова
                    try:
                        word_term = escape_fts5_query(word)
                        db.cursor.execute(f"""
                            SELECT p.* FROM products p
                            INNER JOIN products_fts5 fts ON p.rowid = fts.rowid
                            WHERE products_fts5 MATCH ? AND p.status != 0
                              {mfr_clause}
                            LIMIT 5
                        """, (word_term,))
                        rows = db.cursor.fetchall()
                        if rows:
                            product_records = [Product.from_record(row) for row in rows]
                            related_products.extend(product_records)
                            logging.info(f"[DEBUG] FTS5 word match '{word}' found {len(rows)} products")
                    except Exception:
                        pass
                    # Префиксный поиск для длинных слов (вакууматора -> вакуум*)
                    if len(word) >= 5:
                        try:
                            prefix_term = word[:5].lower() + "*"
                            db.cursor.execute(f"""
                                SELECT p.* FROM products p
                                INNER JOIN products_fts5 fts ON p.rowid = fts.rowid
                                WHERE products_fts5 MATCH ? AND p.status != 0
                                  {mfr_clause}
                                LIMIT 10
                            """, (prefix_term,))
                            rows = db.cursor.fetchall()
                            if rows:
                                product_records = [Product.from_record(row) for row in rows]
                                related_products.extend(product_records)
                                logging.info(f"[DEBUG] FTS5 prefix match '{prefix_term}' found {len(rows)} products")
                        except Exception:
                            pass
    
    # Убираем дубликаты по name
    seen_names = set()
    unique_products = []
    for product in related_products:
        if product.name not in seen_names:
            seen_names.add(product.name)
            unique_products.append(product)
    related_products = unique_products

    # Если NER нашёл категории, стараемся отфильтровать товары по ним
    if response.categories:
        ner_categories = [c.lower() for c in response.categories]
        filtered_products = []
        for product in related_products:
            product_category = (product.category or "").lower()
            # Оставляем только основные товары (priority == 1),
            # у которых категория содержит хотя бы одну из NER‑категорий
            if (
                get_category_priority(product_category) == 1
                and any(cat in product_category for cat in ner_categories)
            ):
                filtered_products.append(product)
        # Если в результате фильтрации что‑то осталось — работаем только с этим списком.
        # Guard: при exact SKU полная замена опасна — NER-категория может быть неверной
        # (инцидент 5793: RMC-01 + NER «Мультиварки» → RMP-04 вместо кофемашины).
        if filtered_products:
            requested_skus = _requested_skus_from_query(query)
            if direct_sku_hits and _related_products_contain_direct_sku(
                related_products, direct_sku_hits
            ):
                logging.info(
                    "[DEBUG] skipped_ner_category_filter_reason=direct_sku "
                    f"direct_hits={sorted(direct_sku_hits)} "
                    f"requested_skus={sorted(requested_skus)} "
                    f"ner_categories={response.categories!r} "
                    f"filtered_count={len(filtered_products)}"
                )
                if requested_skus:
                    related_products = [
                        p
                        for p in related_products
                        if _product_matches_direct_sku_hit(p, requested_skus)
                        or (
                            schema_lookup_used
                            and p.product_id is not None
                            and int(p.product_id) in schema_hit_product_ids
                        )
                    ]
            else:
                related_products = filtered_products

    if response.other_products:
        if schema_lookup_used and schema_hit_product_ids:
            logging.info(
                "[DEBUG] skipped_related_products_reason=schema_lookup_used "
                f"schema_hit_count={len(schema_hit_product_ids)}"
            )
            llm_selected_product_ids: set[int] = set()
        else:
            llm_related = llm_funcs.find_related_products(
                query,
                product_names=response.product_names,
                categories=response.categories,
                products=related_products,
                db_products=related_products,  # Передаем уже найденные товары
                model_name=related_products_model,
            )
            llm_selected_product_ids = {
                int(p.product_id)
                for p in llm_related
                if p.product_id is not None
            }
            related_products.extend(llm_related)
    else:
        llm_selected_product_ids = set()

    # После добора связанных товаров снова убираем дубликаты (find_related_products может вернуть уже найденные).
    seen_names = set()
    unique_products = []
    for product in related_products:
        if product.name not in seen_names:
            seen_names.add(product.name)
            unique_products.append(product)
    related_products = unique_products

    # 1) Фильтрация по приоритету категорий:
    #    выбираем товары только с лучшим (минимальным) приоритетом,
    #    кроме запросов с other_products=true (NER: сопутствующие товары без явного артикула).
    if related_products:
        if _should_keep_accessory_products(
            response,
            query=query,
            schema_lookup_used=schema_lookup_used,
        ):
            logging.info("[DEBUG] priority_filter_mode=skip_best_priority")
        else:
            best_priority = min(get_category_priority(p.category) for p in related_products)
            related_products = [
                p for p in related_products
                if get_category_priority(p.category) == best_priority
            ]

    # 2) Исключаем товары со статусом 0 (уже отфильтровано в SQL, но оставим для надежности)
    related_products = [
        p for p in related_products
        if getattr(p, "status", None) != 0
    ]


    related_products = filter_products_by_manufacturer(related_products, bypass)

    # 3) Сортировка по убыванию остатков (quantity)
    related_products.sort(
        key=lambda p: -(getattr(p, "quantity", None) or 0)
    )

    # 4) Tiered-cap итогового контекста (direct → schema → LLM → остальное)
    before_cap = len(related_products)
    anchor_skus = direct_sku_hits | _requested_skus_from_query(query)
    related_products = _cap_products_tiered(
        related_products,
        max_items=config.PRODUCTS_CONTEXT_MAX,
        anchor_skus=anchor_skus,
        schema_hit_product_ids=schema_hit_product_ids,
        llm_selected_product_ids=llm_selected_product_ids,
    )
    if before_cap > len(related_products):
        logging.info(
            "[DEBUG] context_cap applied max=%d before=%d after=%d dropped=%d",
            config.PRODUCTS_CONTEXT_MAX,
            before_cap,
            len(related_products),
            before_cap - len(related_products),
        )

    return ProductSearchResult(
        products=related_products,
        parse_results=response
    )


def find_context(
    query: str,
    prompt: str = 'запрос только на контекст',
    tickets_search_amount: int = config.RAG_TICKETS_SEARCH_RESULTS_AMOUNT,
    tickets_search_threshold: float = config.RAG_SCORE_threshold,
    files_search_amount: int = config.RAG_FILES_SEARCH_RESULTS_AMOUNT,
    files_search_threshold: float = config.RAG_SCORE_threshold,
    products_search_enabled: bool = True,
    source: Optional[str] = None,
    llm_model_stages: Optional[Dict[str, str]] = None,
    *,
    rarequest_display_query: Optional[str] = None,
    rarequest_prompt_override: Optional[str] = None,
) -> tuple:
    # Значения по умолчанию для моделей на разных стадиях
    default_models = {
        "ner": "google/gemini-2.5-flash-lite",
        "related_products": "google/gemini-2.5-flash-lite",
        "summarization": "google/gemini-2.5-flash-lite",
        "final_answer": "google/gemini-3-flash-preview"
    }
    if llm_model_stages:
        default_models.update(llm_model_stages)

    def _finalize(results: RelatedContext) -> tuple:
        return finalize_context(
            query,
            prompt,
            source,
            results,
            stored_query=rarequest_display_query,
            stored_prompt=rarequest_prompt_override,
        )

    # Результирующий контекст
    products = []
    tickets = None
    files = None

    # До retrieval: классифицируем интент и разрешаем маршрут стадий.
    classifier_out = classify_intent(
        query,
        model_name=default_models.get("summarization"),
    )
    route = resolve_intent_route(classifier_out)

    # Один parse_query на запрос: используем и для products, и для files.
    stages = [getattr(s, "stage", None) for s in getattr(route, "stages", [])]
    need_parse_results = (products_search_enabled and "products" in stages) or (
        files_search_amount > 0 and "files" in stages
    )
    if need_parse_results:
        parse_results = llm_funcs.parse_query(query.strip(), model_name=default_models.get("ner"))
        parse_results = _merge_regex_skus_into_parse_results(parse_results, query)
    else:
        parse_results = QueryParseResults(product_names=None, categories=None, other_products=None)

    stage_order = ",".join(getattr(s, "stage", "") for s in route.stages)
    log_find_context_routing(
        classifier_out=classifier_out,
        route=route,
        stage_order=stage_order,
    )
    if classifier_out.intent_label == UNDEFINED_INTENT_LABEL:
        log_undefined_intent_event(
            query=query,
            classifier_out=classifier_out,
            source=source,
        )

    for idx, stage_spec in enumerate(route.stages, start=1):
        stage = stage_spec.stage

        # Стадия: Товары
        if stage == "products":
            if not products_search_enabled:
                continue
            logging.info(f"[DEBUG] find_context Stage {idx}: Products (route={route.route_id})")
            product_search = find_products(
                query,
                ner_model=default_models.get("ner"),
                related_products_model=default_models.get("related_products"),
                parse_results=parse_results,
            )
            products = product_search.products
            parse_results = product_search.parse_results

            if products:
                temp_results = RelatedContext(
                    products=products,
                    tickets=tickets,
                    files=files,
                    parse_results=parse_results,
                )
                judge_res = llm_funcs.check_context_sufficiency(
                    query,
                    temp_results.prettify(),
                    model_name=default_models.get("summarization"),
                )
                logging.info(
                    f"[DEBUG] Stage {idx} judge: enough={judge_res['enough_information']}, reason={judge_res.get('short_reason')}"
                )
                if judge_res.get("enough_information"):
                    log_find_context_early_exit(
                        classifier_out=classifier_out,
                        route=route,
                        stage=stage,
                        stage_index=idx,
                    )
                    return _finalize(temp_results)

        # Стадия: Тикеты
        elif stage == "tickets":
            if tickets_search_amount <= 0:
                continue
            logging.info(f"[DEBUG] find_context Stage {idx}: Tickets (route={route.route_id})")
            tickets = tickets_manager.crud.search(
                query,
                k=tickets_search_amount,
                score_threshold=tickets_search_threshold,
                model_name=default_models.get("summarization"),
            )

            if tickets and tickets.documents:
                temp_results = RelatedContext(
                    products=products,
                    tickets=tickets,
                    files=files,
                    parse_results=parse_results,
                )
                judge_res = llm_funcs.check_context_sufficiency(
                    query,
                    temp_results.prettify(),
                    model_name=default_models.get("summarization"),
                )
                logging.info(
                    f"[DEBUG] Stage {idx} judge: enough={judge_res['enough_information']}, reason={judge_res.get('short_reason')}"
                )
                if judge_res.get("enough_information"):
                    log_find_context_early_exit(
                        classifier_out=classifier_out,
                        route=route,
                        stage=stage,
                        stage_index=idx,
                    )
                    return _finalize(temp_results)

        # Стадия: Файлы (внутренний staged-поиск + judge_files_sufficiency + early exit)
        elif stage == "files":
            if files_search_amount <= 0:
                continue
            logging.info(f"[DEBUG] find_context Stage {idx}: Files (route={route.route_id})")
            files = files_manager.crud.search_staged(
                query,
                k=files_search_amount,
                score_threshold=files_search_threshold,
                model_name=default_models.get("summarization"),
                data_type_order=stage_spec.files_data_type_order,
                parse_results=parse_results,
                intent_label=classifier_out.intent_label,
            )
            if files.files_staged_sufficient:
                temp_results = RelatedContext(
                    products=products,
                    tickets=tickets,
                    files=files,
                    parse_results=parse_results,
                )
                log_find_context_early_exit(
                    classifier_out=classifier_out,
                    route=route,
                    stage="files",
                    stage_index=idx,
                )
                return _finalize(temp_results)
        else:
            logging.warning(f"[DEBUG] find_context: unknown stage={stage!r} in route={route.route_id}")
        
    results = RelatedContext(
        products=products,
        tickets=tickets,
        files=files,
        parse_results=parse_results
    )
    
    return _finalize(results)


def finalize_context(
    query: str,
    prompt: str,
    source: Optional[str],
    results: RelatedContext,
    *,
    stored_query: Optional[str] = None,
    stored_prompt: Optional[str] = None,
) -> tuple:
    """Вспомогательная функция для логирования и сохранения контекста перед возвратом"""
    num_products = len(results.products) if results.products else 0
    num_tickets = len(results.tickets.documents) if results.tickets and results.tickets.documents else 0
    num_files = len(results.files.documents) if results.files and results.files.documents else 0
    
    logging.info(f"[DEBUG] Final Context: {num_products} products, {num_tickets} tickets, {num_files} files")
    
    # Подготавливаем контекст для сохранения в БД
    context_products = None
    context_tickets = None
    context_files = None
    
    if results.products:
        models = [product.model for product in results.products if product.model]
        context_products = "\n\n".join(models) if models else None
    
    if results.tickets and results.tickets.ticket_ids:
        context_tickets = "\n\n".join(results.tickets.ticket_ids)
    
    if results.files and results.files.filenames:
        context_files = "\n\n".join(results.files.filenames)
    
    db_query = stored_query if stored_query is not None else query
    db_prompt = stored_prompt if stored_prompt is not None else prompt

    rarequest_id = None
    try:
        with Database() as db:
            db.cursor.execute(
                '''INSERT INTO rarequests (query, prompt, source, context_products, context_tickets, context_files) 
                   VALUES (?, ?, ?, ?, ?, ?)''',
                (db_query, db_prompt, source, context_products, context_tickets, context_files)
            )
            rarequest_id = db.cursor.lastrowid
            db.conn.commit()
            logging.info(f"Rarequest запись сохранена в БД: query={db_query[:50]}..., id={rarequest_id}")
    except Exception as e:
        logging.error(f"Ошибка при сохранении записи в таблицу rarequests: {e}")

    if rarequest_id:
        rarequest_logger = get_rarequest_logger(rarequest_id)
        rarequest_logger.info(f"/find_context results:\n{results.prettify()}")

    return results, rarequest_id

def get_ticket_ids_from_search(query: str, k: int, score_threshold: float, model_name: Optional[str] = None) -> List[str]:
    """Получает список ID тикетов из результатов поиска"""
    from rag import get_vector_store
    import llm_funcs
    
    vector_store = get_vector_store("tickets")
    query = llm_funcs.summarize_user_query(query, model_name=model_name)
    
    docs = vector_store.similarity_search_with_score(
        query=query,
        k=k
    )
    def _to_similarity(distance: float) -> float:
        try:
            d = float(distance)
        except Exception:
            return 0.0
        return 1.0 / (1.0 + d)

    sorted_by_distance = sorted(docs, key=lambda x: x[1])
    # Строгая фильтрация по порогу похожести
    filtered = [(doc, dist) for doc, dist in sorted_by_distance if _to_similarity(dist) >= score_threshold]
    chunks = [doc for doc, _dist in filtered]
    ticket_ids = list(set([chunk.metadata.get("ticket_id") for chunk in chunks if chunk.metadata.get("ticket_id")]))
    return ticket_ids

def get_rarequest_logger(rarequest_id: int):
    """Создает и возвращает логгер для конкретного запроса"""
    # Используем DATA_PATH из config, который указывает на ./data
    log_dir = Path(config.DATA_PATH) / "rarequests" / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    
    log_file = log_dir / f"{rarequest_id}.log"
    logger_name = f"rarequest_{rarequest_id}"
    
    # Проверяем, не создан ли уже такой логгер
    if logger_name in logging.Logger.manager.loggerDict:
        return logging.getLogger(logger_name)
    
    logger = logging.getLogger(logger_name)
    logger.setLevel(logging.INFO)
    
    # Удаляем существующие обработчики, если есть
    logger.handlers.clear()
    
    # Создаем обработчик для файла
    file_handler = RotatingFileHandler(
        log_file,
        maxBytes=5 * 1024 * 1024,  # 5MB
        backupCount=5,
        encoding='utf-8'
    )
    file_handler.setLevel(logging.INFO)
    
    formatter = logging.Formatter('%(asctime)s - %(levelname)s - %(message)s')
    file_handler.setFormatter(formatter)
    
    logger.addHandler(file_handler)
    logger.propagate = False  # Не передаем логи в родительский логгер
    
    return logger

def get_file_names_from_search(query: str, k: int, score_threshold: float, model_name: Optional[str] = None) -> List[str]:
    """Получает список имен файлов с расширением из результатов поиска"""
    from rag import get_vector_store
    import llm_funcs
    
    vector_store = get_vector_store("files")
    query = llm_funcs.summarize_user_query(query, model_name=model_name)
    
    docs = vector_store.similarity_search_with_score(
        query=query,
        k=k
    )
    def _to_similarity(distance: float) -> float:
        try:
            d = float(distance)
        except Exception:
            return 0.0
        return 1.0 / (1.0 + d)

    sorted_by_distance = sorted(docs, key=lambda x: x[1])
    # Строгая фильтрация по порогу похожести
    filtered = [(doc, dist) for doc, dist in sorted_by_distance if _to_similarity(dist) >= score_threshold]
    chunks = [doc for doc, _dist in filtered]
    # Извлекаем имена файлов из метаданных
    file_names = []
    for chunk in chunks:
        filename = chunk.metadata.get("filename") or chunk.metadata.get("source") or chunk.metadata.get("file_name")
        if filename:
            # Если filename содержит путь, извлекаем только имя файла
            import os
            filename = os.path.basename(filename)
            file_names.append(filename)
    
    # Убираем дубликаты, сохраняя порядок
    seen = set()
    unique_file_names = []
    for name in file_names:
        if name not in seen:
            seen.add(name)
            unique_file_names.append(name)
    
    return unique_file_names


def ask(
    query: str,
    prompt: str,
    tickets_search_amount: int = config.RAG_TICKETS_SEARCH_RESULTS_AMOUNT,
    tickets_search_threshold: float = config.RAG_SCORE_threshold,
    files_search_amount: int = config.RAG_FILES_SEARCH_RESULTS_AMOUNT,
    files_search_threshold: float = config.RAG_SCORE_threshold,
    products_search_enabled: bool = True,
    source: Optional[str] = None,
    debug: bool = False,
    llm_model_stages: Optional[Dict[str, str]] = None,
):
    # Значения по умолчанию для моделей на разных стадиях
    default_models = {
        "ner": "google/gemini-2.5-flash-lite",
        "related_products": "google/gemini-2.5-flash-lite",
        "summarization": "google/gemini-2.5-flash-lite",
        "final_answer": "google/gemini-3-flash-preview"
    }
    if llm_model_stages:
        default_models.update(llm_model_stages)
    
    # Получаем контекст перед сохранением
    related_context, rarequest_id = find_context(
        query,
        prompt,
        tickets_search_amount=tickets_search_amount,
        tickets_search_threshold=tickets_search_threshold,
        files_search_amount=files_search_amount,
        files_search_threshold=files_search_threshold,
        products_search_enabled=products_search_enabled,
        source=source,
        llm_model_stages=llm_model_stages
    )
    messages = [Message(role="user", content=query)]

    prompt = f"""
            {prompt}
                                
            The answers MUST follow this JSON format:
            - response_text: str - твой текстовый ответ
            - enough_information: bool - достаточно ли информации для формирования полезного ответа?
            - need_assistance: bool - нужна ли помощь человека, для решения запроса. В случае если ты, как ИИ не сможешь помочь пользователю.

            ВАЖНО: ЦИТИРОВАНИЕ ИСТОЧНИКОВ
            В своем ответе (response_text) ты ОБЯЗАТЕЛЬНО должен указывать источники информации после каждого факта, знания, инструкции, руководства к действию, порядка решения проблемы, расписания, графика работы, адресов или любого утверждения, которое требует подтверждения.

            Формат цитирования источников:
            - Для товаров (products): [source][product][model] - где model это модель товара из контекста
            - Для тикетов (tickets): [source][ticket][ticket_id] - где ticket_id это ID тикета из контекста
            - Для файлов (files): [source][file][filename] - где filename это имя файла из контекста
            - Если источник информации неизвестен или информация взята из общего промпта: [source][prompt]
            
            Примеры правильного цитирования:
            - "Для работы с вакууматором используйте специальные пакеты [source][product][RFV-04 thread]"
            - "Согласно инструкции, индикатор P означает паузу [source][file][VDP-02 Manual new.pdf.637bce5012d8eb91af23c78e06bd237e]"
            - "В похожем обращении пользователь столкнулся с такой же проблемой [source][ticket][13967]"
            - "Рекомендую обратиться в сервисный центр [source][prompt]"
            
            Цитата должна быть размещена сразу после предложения или абзаца, к которому она относится. Если информация из нескольких источников, укажи все соответствующие источники через пробел: [source][product][RFV-04 pump] [source][ticket][15025]

            Соблюдай блок «ДОСТУПНЫЕ ИСТОЧНИКИ ДЛЯ ЦИТИРОВАНИЯ» в начале контекста ниже.
            """

    system_msg_text = f"""
                    {prompt}

                    {related_context.prettify()}
                    """

    messages.insert(0, Message(role="system", content=system_msg_text).json())

    # Логируем в отдельный файл для запроса
    if rarequest_id:
        rarequest_logger = get_rarequest_logger(rarequest_id)
        rarequest_logger.info(f"/ask request messages\n{prompt}")
    else:
        # Если rarequest_id нет, используем обычное логирование
        logging.info(f"/ask request messages\n{messages}")

    import time
    ts_start = time.time()
    t0 = time.perf_counter()
    response = llm_funcs.invoke_json(
        messages,
        response_model_keys=["response_text", "enough_information", "need_assistance"],
        temperature=1,
        max_tokens=5000,
        retries=4,
        model_name=default_models.get("final_answer")
    )
    t1 = time.perf_counter()
    ts_end = time.time()
    
    # Логируем ответ перед проверками
    if response:
        logging.info(f"LLM response received with keys: {list(response.keys()) if isinstance(response, dict) else 'not a dict'}")
    else:
        logging.error(f"LLM returned None after invoke_json. This means LLM failed to generate valid JSON response.")
        return None
    
    # Проверяем наличие цитат из контекста в ответе (до удаления)
    if response and isinstance(response, dict) and "response_text" in response:
        import re
        import json
        response_text = response["response_text"]
        
        # Проверяем наличие цитат из тикетов, продуктов или файлов
        # Ищем паттерны: [source][ticket][...], [source][product][...], [source][file][...]
        # НЕ учитываем [source][prompt] - это не цитата из контекста
        has_ticket_citation = bool(re.search(r'\[source\]\[ticket\]\[[^\]]+\]', response_text))
        has_product_citation = bool(re.search(r'\[source\]\[product\]\[[^\]]+\]', response_text))
        has_file_citation = bool(re.search(r'\[source\]\[file\]\[[^\]]+\]', response_text))
        
        has_any_citation = has_ticket_citation or has_product_citation or has_file_citation
        
        # Если нет ни одной цитаты из контекста, устанавливаем need_assistance = True
        if not has_any_citation:
            response["need_assistance"] = True
            logging.warning(f"В ответе LLM отсутствуют цитаты из контекста (тикеты, продукты, файлы). Установлен need_assistance = True")
            # И дополнительно подменяем ответ на заглушку, чтобы не выдавать "ответ из головы"
            response["response_text"] = NO_RAG_DATA_MESSAGE
            # Когда нет цитат из контекста — фактически информации недостаточно
            response["enough_information"] = False
    
    # Сохранение response в таблицу rarequests
    if rarequest_id and response:
        try:
            import json
            response_json = json.dumps(response, ensure_ascii=False)
            with Database() as db:
                db.cursor.execute(
                    'UPDATE rarequests SET response = ? WHERE id = ?',
                    (response_json, rarequest_id)
                )
                if debug:
                    metrics = None
                    try:
                        model_name = default_models.get("final_answer")
                        duration_ms = int((t1 - t0) * 1000)

                        def _count_tokens(text: str, model_name_hint: Optional[str] = None) -> int:
                            if not text:
                                return 0
                            try:
                                import tiktoken
                                # Для не-OpenAI моделей берём базовую кодировку
                                enc = None
                                if model_name_hint:
                                    try:
                                        enc = tiktoken.encoding_for_model(model_name_hint)
                                    except Exception:
                                        enc = None
                                if enc is None:
                                    enc = tiktoken.get_encoding("cl100k_base")
                                return len(enc.encode(text))
                            except Exception:
                                # крайне грубая оценка ~4 символа на токен
                                return max(1, int(len(text) / 4))

                        # Оценка input токенов по текстам сообщений
                        input_text = "\n".join(
                            [(m.get("role", "") + ":" + (m.get("content") or "")) for m in messages if isinstance(m, dict)]
                        )
                        output_text = ""
                        if isinstance(response, dict):
                            output_text = str(response.get("response_text") or "")

                        tokens_input = _count_tokens(input_text, model_name_hint=model_name)
                        tokens_output = _count_tokens(output_text, model_name_hint=model_name)
                        tokens_total = tokens_input + tokens_output

                        metrics = {
                            "model": model_name,
                            "duration_ms": duration_ms,
                            "tokens_input": tokens_input,
                            "tokens_output": tokens_output,
                            "tokens_total": tokens_total,
                            "ts_start": ts_start,
                            "ts_end": ts_end,
                        }
                    except Exception as e:
                        metrics = {"error": f"{type(e).__name__}: {e}"}

                    metrics_json = json.dumps(metrics, ensure_ascii=False)
                    db.cursor.execute(
                        "UPDATE rarequests SET metrics = ? WHERE id = ?",
                        (metrics_json, rarequest_id),
                    )
                db.conn.commit()
                logging.info(f"Response сохранен в БД для записи id={rarequest_id}")
        except Exception as e:
            logging.error(f"Ошибка при сохранении response в таблицу rarequests: {e}")
    
    # Удаляем все ссылки на источники [source][...] из response_text перед возвратом
    if response and isinstance(response, dict) and "response_text" in response:
        # Удаляем все вхождения [source][...] включая вложенные скобки
        # Паттерн удаляет [source] и все последующие [текст] (одну или несколько пар скобок)
        # Например: [source][product][model], [source][ticket][123], [source][file][filename], [source][prompt]
        response["response_text"] = re.sub(r'\[source\](?:\[[^\]]+\])*', '', response["response_text"])
        # Удаляем возможные двойные пробелы после удаления ссылок
        response["response_text"] = re.sub(r'\s+', ' ', response["response_text"]).strip()

    return response