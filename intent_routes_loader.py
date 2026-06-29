"""
Загрузка и валидация app/config/intent_routes.json.

Используется роутингом интентов; при ошибке схемы приложение не стартует (явный fail-fast).
"""
from __future__ import annotations

import json
import logging
from functools import lru_cache
from pathlib import Path
from typing import Literal, Optional

from pydantic import BaseModel, Field, model_validator

logger = logging.getLogger(__name__)

_STAGE = Literal["products", "tickets", "files"]
_FILE_DT = Literal["files", "seocrm_article", "recipe"]


class KnownIntent(BaseModel):
    label: str
    description: str


class StageSpec(BaseModel):
    stage: _STAGE
    files_data_type_order: Optional[list[_FILE_DT]] = None

    @model_validator(mode="after")
    def files_order_only_for_files_stage(self):
        if self.stage != "files" and self.files_data_type_order is not None:
            raise ValueError(
                "files_data_type_order допустим только при stage == \"files\""
            )
        return self


class RouteSpec(BaseModel):
    description: str = ""
    stages: list[StageSpec]


class IntentRoutesConfig(BaseModel):
    version: int = 1
    known_intents: list[KnownIntent]
    intent_to_route: dict[str, str]
    routes: dict[str, RouteSpec]
    aliases: dict[str, str] = Field(default_factory=dict)

    @model_validator(mode="after")
    def unique_intent_labels(self) -> IntentRoutesConfig:
        seen: set[str] = set()
        for ki in self.known_intents:
            if ki.label in seen:
                raise ValueError(f"Дублируется label known_intents: {ki.label!r}")
            seen.add(ki.label)
        return self

    @model_validator(mode="after")
    def validate_references(self) -> IntentRoutesConfig:
        labels = {ki.label for ki in self.known_intents}
        for intent_label, route_name in self.intent_to_route.items():
            if intent_label not in labels:
                raise ValueError(
                    f"intent_to_route: неизвестный интент {intent_label!r} "
                    f"(нет в known_intents)"
                )
            if route_name not in self.routes:
                raise ValueError(
                    f"intent_to_route: маршрут {route_name!r} для {intent_label!r} "
                    "отсутствует в routes"
                )
        if "fallback_default" not in self.routes:
            raise ValueError('routes должен содержать ключ "fallback_default"')
        for alias_src, alias_tgt in self.aliases.items():
            if alias_tgt not in labels:
                raise ValueError(
                    f"aliases: цель {alias_tgt!r} для ключа {alias_src!r} не из known_intents"
                )
        return self


def intent_routes_json_path() -> Path:
    """Путь к JSON (единый источник — config.INTENT_ROUTES_JSON_PATH)."""
    import config as app_config

    return app_config.INTENT_ROUTES_JSON_PATH


def load_intent_routes_from_path(path: Path | None = None) -> IntentRoutesConfig:
    p = path or intent_routes_json_path()
    if not p.is_file():
        raise FileNotFoundError(f"Файл конфигурации интентов не найден: {p}")
    raw = json.loads(p.read_text(encoding="utf-8"))
    return IntentRoutesConfig.model_validate(raw)


@lru_cache(maxsize=1)
def get_intent_routes() -> IntentRoutesConfig:
    """Кэшированная загрузка (после первого успешного вызова путь не перечитывается)."""
    cfg = load_intent_routes_from_path()
    logger.info(
        "intent_routes: загружено %d интентов, %d маршрутов (v%s)",
        len(cfg.known_intents),
        len(cfg.routes),
        cfg.version,
    )
    return cfg


def ensure_intent_routes_loaded() -> IntentRoutesConfig:
    """
    Вызвать при старте приложения: валидирует конфиг и прогревает кэш.
    """
    return get_intent_routes()


def reload_intent_routes() -> IntentRoutesConfig:
    """Сброс кэша (тесты / редкая перезагрузка)."""
    get_intent_routes.cache_clear()
    return get_intent_routes()
