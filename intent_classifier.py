"""
Классификация пользовательского запроса по интентам (short LLM + JSON).

Конфиг интентов — `intent_routes.json` (см. intent_routes_loader).
Итоговый intent_label для downstream — с учётом INTENT_CONFIDENCE_THRESHOLD (одно место правды здесь).
"""
from __future__ import annotations

import logging
from typing import Optional

import config
from intent_routes_loader import IntentRoutesConfig, get_intent_routes
from models import IntentClassifierOutput, Message, UNDEFINED_INTENT_LABEL
import llm_funcs

logger = logging.getLogger(__name__)


def _clamp01(value: object) -> float:
    try:
        x = float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return 0.0
    return max(0.0, min(1.0, x))


def resolve_intent_label(raw: str, routes_cfg: IntentRoutesConfig) -> str:
    """
    Приводит ответ модели к каноническому label из конфига или к `undefined`.
    Учитывает регистр и aliases.
    """
    s = (raw or "").strip()
    if not s:
        return UNDEFINED_INTENT_LABEL

    labels = {ki.label for ki in routes_cfg.known_intents}
    by_lower = {ki.label.lower(): ki.label for ki in routes_cfg.known_intents}

    if s.lower() == UNDEFINED_INTENT_LABEL:
        return UNDEFINED_INTENT_LABEL

    if s in labels:
        return s

    canon = by_lower.get(s.lower())
    if canon is not None:
        return canon

    matched_alias = routes_cfg.aliases.get(s)
    if matched_alias is None:
        for src, tgt in routes_cfg.aliases.items():
            if src.lower() == s.lower():
                matched_alias = tgt
                break
    if matched_alias is not None and matched_alias in labels:
        return matched_alias

    logger.warning("Классификатор вернул неизвестный intent_label=%r → undefined", s)
    return UNDEFINED_INTENT_LABEL


def _build_classifier_system_prompt(routes_cfg: IntentRoutesConfig) -> str:
    catalog = "\n".join(
        f'- "{ki.label}": {ki.description}' for ki in routes_cfg.known_intents
    )
    allowed = ", ".join(f'"{ki.label}"' for ki in routes_cfg.known_intents)
    return f"""Ты классификатор интента коротких запросов пользователя (RAG: товары, тикеты, база файлов).

Определи ОДИН класс запроса. Допустимые значения intent_label: {allowed}, либо точно \"{UNDEFINED_INTENT_LABEL}\".

Интенты:
{catalog}

Правила:
- Ответ ТОЛЬКО JSON без markdown и без текста до/после.
- Ключи: intent_label (строка), confidence (число 0..1), reason (кратко по-русски), predicted_intent_text (строка или null — как ты сам назвал бы тип запроса).

Будь консервативным: при сомнении ставь intent_label=\"{UNDEFINED_INTENT_LABEL}\" и низкую confidence.
"""


def classify_intent(
    query: str,
    *,
    model_name: Optional[str] = None,
    routes_cfg: Optional[IntentRoutesConfig] = None,
    confidence_threshold: Optional[float] = None,
) -> IntentClassifierOutput:
    """
    Вызывает LLM и возвращает нормализованный результат.

    Если парсинг/вызов не удался или confidence ниже порога — intent_label == \"undefined\".
    """
    cfg = routes_cfg or get_intent_routes()
    threshold = (
        confidence_threshold
        if confidence_threshold is not None
        else config.INTENT_CONFIDENCE_THRESHOLD
    )

    q = (query or "").strip()
    if not q:
        return IntentClassifierOutput(
            intent_label=UNDEFINED_INTENT_LABEL,
            confidence=0.0,
            reason="Пустой запрос",
            predicted_intent_text=None,
        )

    messages = [
        Message(role="system", content=_build_classifier_system_prompt(cfg)),
        Message(role="user", content=q),
    ]

    raw = llm_funcs.invoke_json(
        [m.json() for m in messages],
        response_model_keys=None,
        temperature=0,
        max_tokens=512,
        retries=1,
        model_name=model_name,
    )

    if not isinstance(raw, dict):
        logger.error("intent classifier: invoke_json вернул не-object, fallback undefined")
        return IntentClassifierOutput(
            intent_label=UNDEFINED_INTENT_LABEL,
            confidence=0.0,
            reason="Не удалось разобрать ответ классификатора",
            predicted_intent_text=None,
        )

    conf = _clamp01(raw.get("confidence"))
    reason = str(raw.get("reason") or "").strip() or "без формулировки причины"

    pred_raw = raw.get("predicted_intent_text")
    predicted: Optional[str]
    if pred_raw is None:
        predicted = None
    else:
        predicted = str(pred_raw).strip() or None

    canon = resolve_intent_label(str(raw.get("intent_label", "")), cfg)

    effective_label = canon
    if conf < threshold:
        effective_label = UNDEFINED_INTENT_LABEL
        reason = (
            f"{reason} (confidence {conf:.2f} < порога {threshold:.2f})"
            if canon != UNDEFINED_INTENT_LABEL
            else reason
        )

    try:
        return IntentClassifierOutput(
            intent_label=effective_label,
            confidence=conf,
            reason=reason,
            predicted_intent_text=predicted,
        )
    except Exception as e:
        logger.exception("intent classifier: ошибка сборки IntentClassifierOutput: %s", e)
        return IntentClassifierOutput(
            intent_label=UNDEFINED_INTENT_LABEL,
            confidence=conf,
            reason="Ошибка валидации результата классификатора",
            predicted_intent_text=predicted,
        )
