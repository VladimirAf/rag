"""
Разрешение интента в маршрут пайплайна по `intent_routes.json`.

Отдельный модуль (KISS/SRP): классификатор даёт label, роутер — порядок стадий и override для files.
При `undefined`, неизвестном label или отсутствии маппинга — консервативный fallback (`fallback_default`).
"""
from __future__ import annotations

import logging
from typing import Optional

from pydantic import BaseModel, Field

from intent_routes_loader import (
    IntentRoutesConfig,
    RouteSpec,
    StageSpec,
    get_intent_routes,
)
from models import IntentClassifierOutput, UNDEFINED_INTENT_LABEL

logger = logging.getLogger(__name__)


class ResolvedIntentRoute(BaseModel):
    """Идентификатор маршрута (для логов) и упорядоченные стадии."""

    route_id: str
    stages: list[StageSpec]
    is_fallback: bool = Field(
        default=False,
        description="Базовый пайплайн (undefined или принудительный fallback)",
    )


def _fallback_route(cfg: IntentRoutesConfig) -> ResolvedIntentRoute:
    spec = cfg.routes["fallback_default"]
    return ResolvedIntentRoute(
        route_id="fallback_default",
        stages=list(spec.stages),
        is_fallback=True,
    )


def resolve_intent_route(
    classifier: IntentClassifierOutput,
    *,
    routes_cfg: Optional[IntentRoutesConfig] = None,
) -> ResolvedIntentRoute:
    """
    По результату классификации возвращает маршрут из конфига.

    - ``intent_label == undefined`` → ``fallback_default`` (как в ТЗ).
    - Неизвестный интент или отсутствующий маршрут → тот же fallback.
    """
    cfg = routes_cfg or get_intent_routes()

    raw_label = (classifier.intent_label or "").strip()
    if raw_label == UNDEFINED_INTENT_LABEL or not raw_label:
        return _fallback_route(cfg)

    known = {ki.label for ki in cfg.known_intents}
    if raw_label not in known:
        logger.warning(
            "intent_router: неизвестный intent_label=%r после классификации → fallback",
            raw_label,
        )
        return _fallback_route(cfg)

    route_name = cfg.intent_to_route.get(raw_label)
    if not route_name:
        logger.warning(
            "intent_router: нет intent_to_route для %r → fallback",
            raw_label,
        )
        return _fallback_route(cfg)

    route_spec: RouteSpec | None = cfg.routes.get(route_name)
    if route_spec is None:
        logger.warning(
            "intent_router: маршрут %r отсутствует в routes → fallback",
            route_name,
        )
        return _fallback_route(cfg)

    return ResolvedIntentRoute(
        route_id=route_name,
        stages=list(route_spec.stages),
        is_fallback=False,
    )
