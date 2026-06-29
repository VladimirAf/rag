"""HTTP-клиент OpenWebUI pipeline для CSV-автотеста."""

from __future__ import annotations

import json
import re
import sqlite3
import time
from typing import Any, Optional

import requests

from test_csv_common import DbRow, fetch_rarequest, fetch_rarequest_owui_fallback, response_text_from_db, stats_text

DEFAULT_OWUI_MODEL_CANDIDATE = ""

# Короткие alias / устаревшие id → актуальный id в /api/v1/chat/completions (manifold: {id}.{sub_id}).
_OWUI_MODEL_ALIASES = {
    "": DEFAULT_OWUI_MODEL_CANDIDATE,
    "": DEFAULT_OWUI_MODEL_CANDIDATE,
    "": DEFAULT_OWUI_MODEL_CANDIDATE,
}


def normalize_owui_model(model: str) -> str:
    """Приводит id модели к формату, ожидаемому OWU chat/completions."""
    model = (model or "").strip()
    if not model:
        return ""
    return _OWUI_MODEL_ALIASES.get(model, model)

_STATS_MARKER = "\n\n---\n\n"


def auth_headers(bearer_token: str) -> dict[str, str]:
    return {
        "Authorization": f"Bearer {bearer_token}",
        "Content-Type": "application/json",
    }


def extract_response_text(content: str) -> str:
    """
    Достаёт response_text из ответа LLM (JSON, markdown-json или YAML-подобный текст).
    Логика совместима с Pipeline._extract_llm_response_payload.
    """
    text = (content or "").strip()
    if not text:
        return ""

    final_text = text
    fenced = re.search(r"```(?:json)?\s*(\{[\s\S]*\})\s*```", text, re.IGNORECASE)
    json_blob = fenced.group(1) if fenced else None
    if not json_blob:
        brace = re.search(r"\{[\s\S]*\"response_text\"[\s\S]*\}", text)
        if brace:
            json_blob = brace.group(0)

    if json_blob:
        try:
            parsed = json.loads(json_blob)
            if isinstance(parsed, dict) and "response_text" in parsed:
                return str(parsed.get("response_text") or "").strip()
        except json.JSONDecodeError:
            pass

    rt_match = re.search(r"response_text\s*:\s*", text, re.IGNORECASE)
    if rt_match:
        start = rt_match.end()
        end = len(text)
        for key in ["enough_information", "need_assistance"]:
            m = re.search(rf"\b{key}\s*:", text[start:], re.IGNORECASE)
            if m:
                end = min(end, start + m.start())
        return text[start:end].strip().rstrip(" \t\r\n-–—").strip()

    return text


def split_answer_and_stats_tail(content: str) -> tuple[str, str]:
    """Отделяет текст ответа от хвоста _format_stats (блок после ---)."""
    text = (content or "").strip()
    if not text:
        return "", ""

    idx = text.rfind(_STATS_MARKER)
    if idx < 0:
        return text, ""

    body = text[:idx].strip()
    tail = text[idx + len(_STATS_MARKER) :].strip()
    if "**Модель:**" in tail or "**Время:**" in tail or "**Токены:**" in tail:
        return body, tail
    return text, ""


def format_stats_tail_human(stats_tail: str) -> str:
    """Преобразует markdown-хвост pipeline в блок Модель/Время/Токены."""
    if not stats_tail.strip():
        return ""

    model_m = re.search(r"\*\*Модель:\*\*\s*`?([^`\n]+)`?", stats_tail)
    time_m = re.search(r"\*\*Время:\*\*\s*([^\n]+)", stats_tail)
    tok_m = re.search(r"\*\*Токены:\*\*\s*([^\n]+)", stats_tail)

    lines = [
        f"Модель: {model_m.group(1).strip() if model_m else '(неизвестно)'}",
        f"Время: {time_m.group(1).strip() if time_m else '(неизвестно)'}",
        f"Токены: {tok_m.group(1).strip() if tok_m else '(неизвестно)'}",
    ]
    return "\n".join(lines)


def pipeline_stats_text(
    *,
    query_index: int,
    source: str,
    ok_http: bool,
    http_error: str,
    db_row: Optional[DbRow],
    stats_tail: str,
    owui_model: str,
) -> str:
    if not ok_http:
        return "\n".join(
            [
                "Модель: (неизвестно)",
                "Время: (неизвестно)",
                "Токены: (неизвестно)",
                "",
                f"error: OWUI chat/completions failed: {http_error}",
                f"owui_model: {owui_model}",
                f"source: {source}",
                f"query_index: {query_index}",
            ]
        ).strip()

    if db_row and db_row.metrics:
        return stats_text(
            query_index=query_index,
            source=source,
            ok_http=True,
            http_error="",
            db_row=db_row,
        )

    if stats_tail.strip():
        human = format_stats_tail_human(stats_tail)
        extra: list[str] = []
        if not db_row:
            extra.append("db: запись rarequests не найдена по (source, query); stats из HTTP-ответа")
        else:
            extra.append(f"rarequest_id: {db_row.id}")
            if db_row.created_at:
                extra.append(f"created_at: {db_row.created_at}")
        extra.append(f"owui_model: {owui_model}")
        extra.append(f"source: {source}")
        extra.append(f"query_index: {query_index}")
        return "\n".join([human, "", *extra]).strip()

    base = stats_text(
        query_index=query_index,
        source=source,
        ok_http=True,
        http_error="",
        db_row=db_row,
    )
    if "db: запись rarequests не найдена" in base:
        base = base + "\nowui_model: " + owui_model
    return base


def post_owui_chat(
    *,
    base_url: str,
    bearer_token: str,
    model: str,
    query: str,
    run_id: str,
    temperature: float,
    max_tokens: int,
    timeout_s: float,
    retries: int,
    retry_backoff_s: float,
) -> tuple[bool, str, str, str]:
    """
    POST /api/v1/chat/completions.

    Returns:
        (ok, error_text, answer_body_without_stats, stats_tail)
    """
    url = base_url.rstrip("/") + "/api/v1/chat/completions"
    payload: dict[str, Any] = {
        "model": model,
        "chat_id": run_id,
        "messages": [{"role": "user", "content": query}],
        "temperature": temperature,
        "max_tokens": max_tokens,
        "stream": False,
        "metadata": {"chat_id": run_id},
    }
    last_err = ""
    for attempt in range(1, retries + 1):
        try:
            r = requests.post(
                url,
                headers=auth_headers(bearer_token),
                json=payload,
                timeout=timeout_s,
            )
            if r.status_code == 200:
                try:
                    data = r.json()
                except Exception as e:
                    return False, f"invalid JSON: {e}", "", ""
                choices = data.get("choices") or []
                if not choices:
                    return False, "empty choices in response", "", ""
                content = (choices[0].get("message") or {}).get("content") or ""
                body, tail = split_answer_and_stats_tail(str(content))
                return True, "", body, tail
            last_err = f"HTTP {r.status_code}: {r.text[:500]}"
        except Exception as e:
            last_err = f"{type(e).__name__}: {e}"

        if attempt < retries:
            time.sleep(retry_backoff_s * attempt)
    return False, last_err, "", ""


def resolve_answer(
    *,
    db_row: Optional[DbRow],
    http_body: str,
) -> str:
    if db_row and db_row.response:
        text = response_text_from_db(db_row.response)
        if text.strip():
            return text
    if http_body.strip():
        return extract_response_text(http_body)
    return ""


def fetch_rarequest_retry(
    conn: sqlite3.Connection,
    *,
    source: str,
    query: str,
    after_id: int = 0,
    attempts: int = 6,
    delay_s: float = 1.0,
) -> Optional[DbRow]:
    for attempt in range(1, attempts + 1):
        row = fetch_rarequest(conn, source=source, query=query)
        if row:
            return row
        row = fetch_rarequest_owui_fallback(conn, query=query, after_id=after_id)
        if row:
            return row
        if attempt < attempts:
            time.sleep(delay_s)
    return None
