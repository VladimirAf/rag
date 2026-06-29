#!/usr/bin/env python3
"""Диагностика отдельных запросов find_products."""
import sys
from pathlib import Path

_APP = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_APP))

import sqlite3
import core

QUERIES = [
    ("6870", "BDS-04 дай ссылку на нож"),
    ("6871", "BDS-04 дай ссылку на нож и дай еще мешок для oрехового молока"),
    ("6889", "Мешок для oрехового молока RAWMID аксессуар"),
    ("6890", "Лопатка силиконовая аксессуар"),
    ("6882", "Штырь в мотор блендера"),
]

for rid, q in QUERIES:
    print("=" * 70)
    print(f"#{rid}: {q}")
    pr = core.llm_funcs.parse_query(q.strip())
    print(f"  NER: names={pr.product_names!r} cat={pr.categories!r} other={pr.other_products}")
    r = core.find_products(q)
    models = [p.model for p in r.products]
    print(f"  count={len(models)} models={models}")
    for needle in ["knife", "nut milk", "spatula", "motor pin", "нож", "мешок"]:
        hits = [m for m in models if needle.lower() in (m or "").lower()]
        if hits:
            print(f"  >> {needle}: {hits}")

conn = sqlite3.connect("/app/data/database.db")
print("\n=== DB ===")
for m in ["Nut Milk Bag", "RAWMID BD spatula", "Rawmid blender motor pin"]:
    row = conn.execute(
        "SELECT model, name, category, quantity, substr(description,1,80) FROM products WHERE model=?",
        (m,),
    ).fetchone()
    print(m, "->", row)
# schema for BDS-04 knife
rows = conn.execute("""
SELECT p.model, p.name FROM products main
JOIN product_schema ps ON ps.product_id = main.product_id
JOIN products p ON p.product_id = ps.related_product_id
WHERE LOWER(main.model) LIKE 'bds-04%' AND p.status!=0
  AND (LOWER(p.name) LIKE '%нож%' OR LOWER(p.model) LIKE '%knife%')
LIMIT 10
""").fetchall()
print("\nBDS-04 knife schema:", rows)
conn.close()

print("\n=== SCHEMA BDS-04 ===")
import product_schema_lookup as psl
from core import escape_fts5_query, Database
import dbfuncs

for q in [
    "BDS-04 дай ссылку на нож",
    "BDS-04 дай ссылку на нож и дай еще мешок для oрехового молока",
]:
    print("---", q[:55])
    print("  tokens:", psl.extract_mention_tokens(q, sku_hints=["BDS-04"]))
    print("  schema:", [p.model for p in psl.find_related_for_model_sku("BDS-04", query=q)])

print("\n=== FTS ===")
conn = sqlite3.connect("/app/data/database.db")
for q in [
    "Мешок для oрехового молока RAWMID аксессуар",
    "Лопатка силиконовая аксессуар",
    "Штырь в мотор блендера",
]:
    term = escape_fts5_query(q)
    rows = conn.execute(
        """SELECT p.model, p.name FROM products p
        INNER JOIN products_fts5 fts ON p.rowid = fts.rowid
        WHERE products_fts5 MATCH ? AND p.status != 0 LIMIT 8""",
        (term,),
    ).fetchall()
    print(q[:45], "->", rows)

parts = dbfuncs.find_mentioned_products(None, ["Запчасти > Блендеры"])
pin = [p for p in parts if "motor pin" in (p.model or "").lower()]
print(f"\ncategory parts={len(parts)} motor_pin_in_pool={len(pin)}")
conn.close()
