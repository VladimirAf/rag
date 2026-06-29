"""
JSONL-лог неопределённых интентов и единый формат диагностики маршрута find_context.

Файл `data/logs/undefined_intents.log` — только append (ротация как у app.log — вне scope).
"""
from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from typing import Any, Optional

import config

logger = logging.getLogger(__name__)

# Явный путь относительно LOGS_PATH (volume: ./data/logs)
UNDEFINED_INTENTS_LOG_PATH = config.LOGS_PATH / "undefined_intents.log"


def normalize_route_label(route_id: str) -> str:
    """Человекочитаемое имя маршрута для логов (fallback_default → fallback)."""
    if route_id == "fallback_default":
        return "fallback"
    return route_id


def append_undefined_intent_jsonl(record: dict[str, Any]) -> None:
    """Одна строка JSON UTF-8. Сбои записи не пробрасываются и не ломают запрос."""
    try:
        config.LOGS_PATH.mkdir(parents=True, exist_ok=True)
        line = json.dumps(record, ensure_ascii=False, default=str) + "\n"
        with open(UNDEFINED_INTENTS_LOG_PATH, "a", encoding="utf-8") as f:
            f.write(line)
    except Exception as e:
        logger.warning("undefined_intents JSONL: запись не удалась: %s", e)


def log_undefined_intent_event(
    *,
    query: str,
    classifier_out: Any,
    source: Optional[str] = None,
) -> None:
    """Строка JSONL при undefined / низкой уверенности (после нормализации классификатора)."""
    conf = getattr(classifier_out, "confidence", None)
    try:
        conf_val = float(conf) if conf is not None else None
    except (TypeError, ValueError):
        conf_val = None

    ts = (
        datetime.now(timezone.utc)
        .isoformat(timespec="milliseconds")
        .replace("+00:00", "Z")
    )
    record: dict[str, Any] = {
        "ts": ts,
        "query": query,
        "reason": getattr(classifier_out, "reason", None),
        "predicted_intent_text": getattr(classifier_out, "predicted_intent_text", None),
        "route_used": "fallback_find_context",
    }
    if conf_val is not None:
        record["confidence"] = conf_val
    if source:
        record["source"] = source

    append_undefined_intent_jsonl(record)


def log_find_context_routing(
    *,
    classifier_out: Any,
    route: Any,
    stage_order: str,
) -> None:
    logger.info(
        "find_context routing intent=%s confidence=%.4f route_applied=%s "
        "fallback_route=%s stages=%s",
        getattr(classifier_out, "intent_label", None),
        float(getattr(classifier_out, "confidence", 0.0) or 0.0),
        normalize_route_label(getattr(route, "route_id", "")),
        getattr(route, "is_fallback", False),
        stage_order,
    )


def log_find_context_early_exit(
    *,
    classifier_out: Any,
    route: Any,
    stage: str,
    stage_index: int,
) -> None:
    logger.info(
        "find_context early_exit intent=%s route_applied=%s stage=%s stage_index=%s",
        getattr(classifier_out, "intent_label", None),
        normalize_route_label(getattr(route, "route_id", "")),
        stage,
        stage_index,
    )
