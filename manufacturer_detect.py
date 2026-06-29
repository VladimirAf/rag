"""
Детерминированная детекция производителей в тексте запроса.

Источники: таблица manufacturer (SQLite) + узкий список алиасов из JSON.
"""
from __future__ import annotations

import html
import json
import logging
from functools import lru_cache
from pathlib import Path
from typing import Optional

import config
from models import Database

logger = logging.getLogger(__name__)

_YO_TO_E = str.maketrans({"ё": "е", "Ё": "Е"})


def manufacturer_aliases_json_path() -> Path:
    return Path(__file__).resolve().parent / "config" / "manufacturer_aliases.json"


def normalize_query_text(text: str) -> str:
    """lower + ё→е для сопоставления с брендами."""
    return text.translate(_YO_TO_E).lower()


def normalize_brand_pattern(name: str) -> str:
    """Нормализация имени бренда/алиаса для поиска подстроки."""
    decoded = html.unescape(name or "")
    return decoded.translate(_YO_TO_E).lower().strip()


def _load_aliases(path: Path | None = None) -> list[tuple[str, int]]:
    p = path or manufacturer_aliases_json_path()
    if not p.is_file():
        logger.warning("manufacturer_aliases.json не найден: %s", p)
        return []
    raw = json.loads(p.read_text(encoding="utf-8"))
    aliases = raw.get("aliases") or {}
    patterns: list[tuple[str, int]] = []
    for alias, manufacturer_id in aliases.items():
        pattern = normalize_brand_pattern(str(alias))
        if not pattern:
            continue
        patterns.append((pattern, int(manufacturer_id)))
    return patterns


def _load_manufacturer_names(db_path: Path | None = None) -> list[tuple[str, int]]:
    path = db_path or config.DATABASE_PATH
    patterns: list[tuple[str, int]] = []
    with Database(str(path)) as db:
        db.cursor.execute(
            "SELECT manufacturer_id, name FROM manufacturer WHERE name IS NOT NULL"
        )
        for manufacturer_id, name in db.cursor.fetchall():
            pattern = normalize_brand_pattern(name)
            if not pattern:
                continue
            patterns.append((pattern, int(manufacturer_id)))
    return patterns


def _build_patterns(
    db_path: Path | None = None,
    aliases_path: Path | None = None,
) -> tuple[tuple[str, int], ...]:
    patterns = _load_manufacturer_names(db_path) + _load_aliases(aliases_path)
    # Длинные имена первыми — меньше ложных срабатываний на коротких подстроках.
    patterns.sort(key=lambda item: len(item[0]), reverse=True)
    return tuple(patterns)


@lru_cache(maxsize=4)
def _get_patterns_cached(
    db_path_str: str,
    aliases_path_str: str,
) -> tuple[tuple[str, int], ...]:
    return _build_patterns(Path(db_path_str), Path(aliases_path_str))


def reload_manufacturer_patterns() -> None:
    """Сброс кэша (тесты / перезагрузка справочника)."""
    _get_patterns_cached.cache_clear()


def detect_manufacturers_in_query(
    query: str,
    *,
    db_path: Optional[Path] = None,
    aliases_path: Optional[Path] = None,
) -> list[int]:
    """
    Возвращает список manufacturer_id, найденных в тексте запроса.
    Порядок не гарантируется; дубликаты отсутствуют.
    """
    if not query or not str(query).strip():
        return []

    normalized = normalize_query_text(query)
    db_p = db_path or config.DATABASE_PATH
    aliases_p = aliases_path or manufacturer_aliases_json_path()
    patterns = _get_patterns_cached(str(db_p), str(aliases_p))

    found: set[int] = set()
    for pattern, manufacturer_id in patterns:
        if pattern in normalized:
            found.add(manufacturer_id)

    return sorted(found)
