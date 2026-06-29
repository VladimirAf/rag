#!/usr/bin/env python3
"""Импорт product_schema из CSV в SQLite RAG (идемпотентно)."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

# app/ на PYTHONPATH при запуске из корня репозитория
_APP_DIR = Path(__file__).resolve().parents[1]
if str(_APP_DIR) not in sys.path:
    sys.path.insert(0, str(_APP_DIR))

from product_schema_lookup import import_product_schema_from_csv  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description="Import product_schema CSV into SQLite")
    parser.add_argument(
        "--csv",
        default=str(Path(__file__).resolve().parents[2] / "data" / "product_schema.csv"),
        help="Path to product_schema CSV (product_id,zap_id,num)",
    )
    parser.add_argument(
        "--db",
        default=None,
        help="SQLite path (default: config.DATABASE_PATH)",
    )
    args = parser.parse_args()
    count = import_product_schema_from_csv(args.csv, db_path=args.db)
    print(f"Imported {count} product_schema rows from {args.csv}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
