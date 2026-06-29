"""Прогон автотеста LLM (aiRAG / OWUI pipeline) и генерация CSV."""

from __future__ import annotations

import argparse
import os
import time
from pathlib import Path
from typing import Iterable, Optional

import requests

from autotest_owui_client import (
    DEFAULT_OWUI_MODEL_CANDIDATE,
    fetch_rarequest_retry,
    normalize_owui_model,
    pipeline_stats_text,
    post_owui_chat,
    resolve_answer,
)
from test_csv_common import (
    DbRow,
    context_block,
    db_connect,
    empty_result_row,
    fetch_rarequest,
    get_max_rarequest_id,
    now_run_id,
    read_queries,
    resolve_data_path,
    response_text_from_db,
    stats_text,
    write_csv,
)

LLM_SOURCES = ("airag", "owui")


def _auth_headers(bearer_token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {bearer_token}"}


def _post_ask(
    *,
    base_url: str,
    bearer_token: str,
    query: str,
    source: str,
    debug: bool,
    timeout_s: float,
    retries: int,
    retry_backoff_s: float,
) -> tuple[bool, str]:
    """
    Возвращает (ok, error_text). Даже при ok=True тело /ask нам не нужно (источник истины — SQLite).
    """
    url = base_url.rstrip("/") + "/ask"
    params = {"query": query, "source": source, "debug": bool(debug)}
    last_err = ""
    for attempt in range(1, retries + 1):
        try:
            r = requests.post(
                url,
                params=params,
                headers=_auth_headers(bearer_token),
                timeout=timeout_s,
            )
            if r.status_code == 200:
                return True, ""
            last_err = f"HTTP {r.status_code}: {r.text[:500]}"
        except Exception as e:
            last_err = f"{type(e).__name__}: {e}"

        if attempt < retries:
            time.sleep(retry_backoff_s * attempt)
    return False, last_err


def _iter_rows_airag(
    *,
    queries: list[str],
    base_url: str,
    bearer_token: str,
    source: str,
    db_path: Path,
    debug: bool,
    timeout_s: float,
    retries: int,
    retry_backoff_s: float,
) -> Iterable[dict[str, str]]:
    conn = db_connect(db_path)
    try:
        for idx, q in enumerate(queries):
            ok_http, err = _post_ask(
                base_url=base_url,
                bearer_token=bearer_token,
                query=q,
                source=source,
                debug=debug,
                timeout_s=timeout_s,
                retries=retries,
                retry_backoff_s=retry_backoff_s,
            )

            db_row: Optional[DbRow] = fetch_rarequest(conn, source=source, query=q) if ok_http else None
            answer = response_text_from_db(db_row.response if db_row else None)
            ctx = context_block(db_row)
            stats = stats_text(
                query_index=idx,
                source=source,
                ok_http=ok_http,
                http_error=err,
                db_row=db_row,
            )

            row = empty_result_row()
            row["юзерский запрос"] = q
            row["ответ aiRAG"] = answer
            row["разбор-коммент aiRAG"] = ""
            row["состав контекста aiRAG"] = ctx
            row["оценка aiRAG"] = "нет оценки"
            row["статистика aiRAG"] = stats
            yield row
    finally:
        conn.close()


def _iter_rows_owui(
    *,
    queries: list[str],
    run_id: str,
    owui_base_url: str,
    owui_model: str,
    bearer_token: str,
    db_path: Path,
    temperature: float,
    max_tokens: int,
    timeout_s: float,
    retries: int,
    retry_backoff_s: float,
) -> Iterable[dict[str, str]]:
    source = f"openwebui_pipeline_{run_id}"
    conn = db_connect(db_path)
    try:
        for idx, q in enumerate(queries):
            after_id = get_max_rarequest_id(conn)
            ok_http, err, http_body, stats_tail = post_owui_chat(
                base_url=owui_base_url,
                bearer_token=bearer_token,
                model=owui_model,
                query=q,
                run_id=run_id,
                temperature=temperature,
                max_tokens=max_tokens,
                timeout_s=timeout_s,
                retries=retries,
                retry_backoff_s=retry_backoff_s,
            )

            db_row: Optional[DbRow] = (
                fetch_rarequest_retry(conn, source=source, query=q, after_id=after_id)
                if ok_http
                else None
            )
            answer = resolve_answer(db_row=db_row, http_body=http_body) if ok_http else ""
            ctx = context_block(db_row)
            stats = pipeline_stats_text(
                query_index=idx,
                source=source,
                ok_http=ok_http,
                http_error=err,
                db_row=db_row,
                stats_tail=stats_tail,
                owui_model=owui_model,
            )

            row = empty_result_row()
            row["юзерский запрос"] = q
            row["ответ pipeline"] = answer
            row["разбор-коммент pipeline"] = ""
            row["состав контекста pipeline"] = ctx
            row["оценка pipeline"] = "нет оценки"
            row["статистика pipeline"] = stats
            yield row
    finally:
        conn.close()


def _build_arg_parser(*, llm_source_required: bool) -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="Прогон LLM-автотеста и генерация CSV.")
    p.add_argument(
        "--llm-source",
        required=llm_source_required,
        choices=LLM_SOURCES,
        help="Источник ответов: airag (FastAPI /ask) или owui (OpenWebUI pipeline).",
    )
    p.add_argument(
        "--base-url",
        default=os.getenv("AUTOTEST_BASE_URL", "http://localhost:8000"),
        help="Base URL сервиса (например http://localhost:8000). ENV: AUTOTEST_BASE_URL",
    )
    p.add_argument(
        "--bearer-token",
        default=os.getenv("AUTOTEST_BEARER_TOKEN") or os.getenv("BEARER_TOKEN") or "",
        help="Bearer токен. ENV: AUTOTEST_BEARER_TOKEN или BEARER_TOKEN",
    )
    p.add_argument(
        "--db-path",
        default=os.getenv("AUTOTEST_DB_PATH", "app/data/database.db"),
        help="Путь к SQLite database.db. ENV: AUTOTEST_DB_PATH",
    )
    p.add_argument(
        "--input-csv",
        default=os.getenv("AUTOTEST_INPUT_CSV", "app/data/all_tests_queries.csv"),
        help="Входной пул запросов. ENV: AUTOTEST_INPUT_CSV",
    )
    p.add_argument(
        "--output-dir",
        default=os.getenv("AUTOTEST_OUTPUT_DIR", "app/data/rarequests/tests"),
        help="Директория для результирующего CSV. ENV: AUTOTEST_OUTPUT_DIR",
    )
    p.add_argument("--timeout-s", type=float, default=float(os.getenv("AUTOTEST_TIMEOUT_S", "120")))
    p.add_argument("--retries", type=int, default=int(os.getenv("AUTOTEST_RETRIES", "3")))
    p.add_argument("--retry-backoff-s", type=float, default=float(os.getenv("AUTOTEST_RETRY_BACKOFF_S", "1.5")))
    p.add_argument(
        "--debug",
        action="store_true",
        default=os.getenv("AUTOTEST_DEBUG", "1").strip() not in ("0", "false", "False"),
        help="Передавать debug=1 в /ask, чтобы сервер писал metrics. ENV: AUTOTEST_DEBUG (0/1)",
    )
    p.add_argument(
        "--limit",
        type=int,
        default=0,
        help="Ограничить число запросов из пула (0 = все). Для smoke-тестов.",
    )
    p.add_argument(
        "--owui-base-url",
        default=os.getenv("AUTOTEST_OWUI_BASE_URL", ""),
        help="Base URL OpenWebUI. ENV: AUTOTEST_OWUI_BASE_URL",
    )
    p.add_argument(
        "--owui-model",
        default=os.getenv("AUTOTEST_OWUI_MODEL", ""),
        help=(
            "Id модели/pipeline на OWU (обязателен для --llm-source owui). "
            f"ENV: AUTOTEST_OWUI_MODEL. Кандидат: {DEFAULT_OWUI_MODEL_CANDIDATE}"
        ),
    )
    p.add_argument(
        "--owui-temperature",
        type=float,
        default=float(os.getenv("AUTOTEST_OWUI_TEMPERATURE", "0.3")),
    )
    p.add_argument(
        "--owui-max-tokens",
        type=int,
        default=int(os.getenv("AUTOTEST_OWUI_MAX_TOKENS", "5000")),
    )
    return p


def main(*, default_llm_source: Optional[str] = None) -> int:
    if default_llm_source is not None and default_llm_source not in LLM_SOURCES:
        raise ValueError(f"unknown default_llm_source: {default_llm_source}")

    p = _build_arg_parser(llm_source_required=default_llm_source is None)
    args = p.parse_args()

    llm_source = default_llm_source or args.llm_source

    if not args.bearer_token:
        raise SystemExit(
            "Не задан Bearer токен. Укажите --bearer-token или переменную окружения "
            "AUTOTEST_BEARER_TOKEN (или BEARER_TOKEN)."
        )

    run_id = now_run_id()

    input_path = resolve_data_path(Path(args.input_csv))
    queries = read_queries(input_path)
    if not queries:
        raise SystemExit(f"В {input_path} не найдено ни одного запроса.")
    if args.limit and args.limit > 0:
        queries = queries[: int(args.limit)]

    db_path = resolve_data_path(Path(args.db_path))
    output_dir = resolve_data_path(Path(args.output_dir))
    out_path = output_dir / f"{run_id}.csv"

    if llm_source == "airag":
        source = f"autotest_{run_id}"
        rows_iter = _iter_rows_airag(
            queries=queries,
            base_url=args.base_url,
            bearer_token=args.bearer_token,
            source=source,
            db_path=db_path,
            debug=bool(args.debug),
            timeout_s=float(args.timeout_s),
            retries=int(args.retries),
            retry_backoff_s=float(args.retry_backoff_s),
        )
    else:
        owui_model = normalize_owui_model(args.owui_model or "")
        if not owui_model:
            raise SystemExit(
                "Для --llm-source owui задайте --owui-model или AUTOTEST_OWUI_MODEL "
                f"(кандидат: {DEFAULT_OWUI_MODEL_CANDIDATE}; список: GET /api/v1/models)."
            )
        source = f"openwebui_pipeline_{run_id}"
        rows_iter = _iter_rows_owui(
            queries=queries,
            run_id=run_id,
            owui_base_url=args.owui_base_url,
            owui_model=owui_model,
            bearer_token=args.bearer_token,
            db_path=db_path,
            temperature=float(args.owui_temperature),
            max_tokens=int(args.owui_max_tokens),
            timeout_s=float(args.timeout_s),
            retries=int(args.retries),
            retry_backoff_s=float(args.retry_backoff_s),
        )

    written = write_csv(out_path, rows_iter)
    print(f"OK: written {written} rows to {out_path}")
    print(f"source: {source}")
    print(f"llm_source: {llm_source}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
