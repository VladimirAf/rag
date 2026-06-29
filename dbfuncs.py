from models import Database, Product
from typing import List, Optional
import logging

from manufacturer_filter import build_manufacturer_sql_clause


def find_unique_in_col(column: str) -> List[str]:
    query = f"SELECT DISTINCT {column} FROM products"

    with Database() as db:
        db.cursor.execute(query)
        rows = db.cursor.fetchall()
        values = [row[0] for row in rows]
    return values


def find_mentioned_products(
        product_names: Optional[List[str]],
        categories: List[str],
        *,
        bypass: bool = False,
    ) -> List[Product]:
    logging.info(f"[DEBUG dbfuncs] find_mentioned_products called with categories: {categories}")
    categories = [val.lower() for val in categories]
    
    products: List[Product] = list()
    mfr_clause = build_manufacturer_sql_clause(bypass)
    products_mfr_clause = build_manufacturer_sql_clause(bypass, table_alias="products")
    with Database() as db:
        # Поиск по категориям (сначала через FTS5 для скорости и морфологии, затем LIKE как фоллбек)
        for cat in categories:
            try:
                # FTS5 поиск по колонке category
                fts_query = f"""
                    SELECT p.* FROM products p
                    INNER JOIN products_fts5 fts ON p.rowid = fts.rowid
                    WHERE fts.category MATCH ? AND p.status != 0
                      {mfr_clause}
                """
                # Используем префиксный поиск для гибкости (напр. "вакууматор" -> "вакууматор*")
                search_term = cat.strip()
                if len(search_term) >= 3:
                    search_term = f"{search_term}*"
                
                db.cursor.execute(fts_query, (search_term,))
                rows = db.cursor.fetchall()
                if rows:
                    products.extend([Product.from_record(row) for row in rows])
                    logging.info(f"[DEBUG dbfuncs] Found {len(rows)} products for category MATCH '{search_term}'")
                    continue # Если нашли через FTS5, LIKE не нужен
            except Exception as e:
                logging.warning(f"[DEBUG dbfuncs] FTS5 category search error: {e}")

            # Фоллбек на LIKE
            cat_query = (
                f"SELECT * FROM products WHERE LOWER(category) LIKE ? AND status != 0 "
                f"{products_mfr_clause}"
            )
            db.cursor.execute(cat_query, (f"%{cat}%",))
            rows = db.cursor.fetchall()
            products.extend([Product.from_record(row) for row in rows])
            logging.info(f"[DEBUG dbfuncs] Found {len(rows)} products for category LIKE '%{cat}%'")

        # Поиск по названиям продуктов (если переданы)
        if product_names:
            for product_name in product_names:
                name_query = (
                    f"SELECT * FROM products WHERE LOWER(description) LIKE ? AND status != 0 "
                    f"{products_mfr_clause}"
                )
                db.cursor.execute(name_query, (f"%{product_name.lower()}%",))
                rows = db.cursor.fetchall()
                products.extend([Product.from_record(row) for row in rows])
                logging.info(f"[DEBUG dbfuncs] Found {len(rows)} products for name LIKE '%{product_name}%'")
    
    # Удаление дубликатов по названию (так как id в модели Product нет)
    unique_products = {p.name: p for p in products}.values()
    final_products = list(unique_products)
    
    logging.info(f"[DEBUG dbfuncs] Total unique products found: {len(final_products)}")
    return final_products