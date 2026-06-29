"""Общие константы и утилиты CSV-автотеста (заголовки, IO, SQLite, форматирование)."""

from __future__ import annotations

import csv
import datetime as dt
import json
import sqlite3
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, Optional


CSV_HEADERS_BASE: list[str] = [
    "юзерский запрос",
    "ответ rag-tool",
    "разбор-коммент rag-tool",
    "запросов от rag-tool",
    "состав контекста rag-tool",
    "оценка rag-tool",
    "статистика rag-tool",
    "ответ pipeline",
    "разбор-коммент pipeline",
    "состав контекста pipeline",
    "оценка pipeline",
    "статистика pipeline",
    "сравнение rag-tool и pipeline",
]

CSV_HEADERS_AIRAG: list[str] = [
    "ответ aiRAG",
    "разбор-коммент aiRAG",
    "состав контекста aiRAG",
    "оценка aiRAG",
    "статистика aiRAG",
]

CSV_HEADERS_ALL: list[str] = CSV_HEADERS_BASE + CSV_HEADERS_AIRAG


@dataclass(frozen=True)
class DbRow:
    id: int
    created_at: Optional[str]
    response: Optional[str]
    metrics: Optional[str]
    context_products: Optional[str]
    context_tickets: Optional[str]
    context_files: Optional[str]


def now_run_id() -> str:
    return dt.datetime.now().strftime("%d%m%Y-%H%M")


def read_queries(path: Path) -> list[str]:
    raw = path.read_text(encoding="utf-8-sig", errors="replace")
    lines = [ln.strip() for ln in raw.splitlines()]
    lines = [ln for ln in lines if ln]
    if not lines:
        return []

    # Поддержка формата "одна колонка без delimiter" как в app/data/all_tests_queries.csv
    if all(";" not in ln for ln in lines):
        if lines and lines[0].strip().lower() == "юзерский запрос":
            lines = lines[1:]
        return lines

    # Fallback: ;‑CSV с заголовком
    with path.open("r", encoding="utf-8-sig", newline="") as f:
        reader = csv.reader(f, delimiter=";", quotechar='"')
        try:
            header = next(reader)
        except StopIteration:
            return []
        header_norm = [(h or "").strip().lower() for h in header]
        col_idx = 0
        if "юзерский запрос" in header_norm:
            col_idx = header_norm.index("юзерский запрос")
        out: list[str] = []
        for row in reader:
            if not row:
                continue
            if col_idx >= len(row):
                continue
            q = (row[col_idx] or "").strip()
            if q:
                out.append(q)
        return out


def write_csv(path: Path, rows: Iterable[dict[str, str]]) -> int:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8-sig", newline="") as f:
        writer = csv.DictWriter(
            f,
            fieldnames=CSV_HEADERS_ALL,
            delimiter=";",
            quotechar='"',
            quoting=csv.QUOTE_MINIMAL,
            lineterminator="\n",
            extrasaction="ignore",
        )
        writer.writeheader()
        count = 0
        for r in rows:
            writer.writerow(r)
            count += 1
        return count


def db_connect(db_path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    return conn


def fetch_rarequest(conn: sqlite3.Connection, *, source: str, query: str) -> Optional[DbRow]:
    cur = conn.cursor()
    cur.execute(
        """
        SELECT id, created_at, response, metrics, context_products, context_tickets, context_files
        FROM rarequests
        WHERE source = ? AND query = ?
        ORDER BY id DESC
        LIMIT 1
        """,
        (source, query),
    )
    row = cur.fetchone()
    if not row:
        return None
    return DbRow(
        id=int(row["id"]),
        created_at=row["created_at"],
        response=row["response"],
        metrics=row["metrics"],
        context_products=row["context_products"],
        context_tickets=row["context_tickets"],
        context_files=row["context_files"],
    )


def get_max_rarequest_id(conn: sqlite3.Connection) -> int:
    cur = conn.cursor()
    cur.execute("SELECT COALESCE(MAX(id), 0) FROM rarequests")
    row = cur.fetchone()
    return int(row[0]) if row else 0


def fetch_rarequest_owui_fallback(
    conn: sqlite3.Connection,
    *,
    query: str,
    after_id: int = 0,
) -> Optional[DbRow]:
    """
    OWUI API часто не передаёт chat_id в pipeline: source в БД
    openwebui_pipeline__userid_<uuid>, а не openwebui_pipeline_<run_id>.
    """
    cur = conn.cursor()
    cur.execute(
        """
        SELECT id, created_at, response, metrics, context_products, context_tickets, context_files
        FROM rarequests
        WHERE source LIKE 'openwebui_pipeline_%' AND query = ? AND id > ?
        ORDER BY id DESC
        LIMIT 1
        """,
        (query, after_id),
    )
    row = cur.fetchone()
    if not row:
        return None
    return DbRow(
        id=int(row["id"]),
        created_at=row["created_at"],
        response=row["response"],
        metrics=row["metrics"],
        context_products=row["context_products"],
        context_tickets=row["context_tickets"],
        context_files=row["context_files"],
    )


def response_text_from_db(response_raw: Optional[str]) -> str:
    if not response_raw:
        return ""
    try:
        parsed = json.loads(response_raw)
        if isinstance(parsed, dict) and "response_text" in parsed:
            return str(parsed.get("response_text") or "")
        return json.dumps(parsed, ensure_ascii=False)
    except Exception:
        return str(response_raw)


def context_block(row: Optional[DbRow]) -> str:
    if not row:
        return ""
    sections: list[tuple[str, Optional[str]]] = [
        ("📦 Products", row.context_products),
        ("🎫 Tickets", row.context_tickets),
        ("📄 Files", row.context_files),
    ]
    parts: list[str] = []
    for title, val in sections:
        val = (val or "").strip()
        parts.append(f"**{title}**")
        parts.append(val if val else "(пусто)")
        parts.append("")  # blank line
    return "\n".join(parts).rstrip()


def stats_text(
    *,
    query_index: int,
    source: str,
    ok_http: bool,
    http_error: str,
    db_row: Optional[DbRow],
) -> str:
    if not ok_http:
        return "\n".join(
            [
                "Модель: (неизвестно)",
                "Время: (неизвестно)",
                "Токены: (неизвестно)",
                "",
                f"error: /ask failed: {http_error}",
                f"source: {source}",
                f"query_index: {query_index}",
            ]
        ).strip()

    metrics_obj: Any = None
    if db_row and db_row.metrics:
        try:
            metrics_obj = json.loads(db_row.metrics)
        except Exception:
            metrics_obj = None

    model = None
    duration_ms = None
    tokens_in = None
    tokens_out = None
    tokens_total = None
    metrics_error = None

    if isinstance(metrics_obj, dict):
        metrics_error = metrics_obj.get("error")
        model = metrics_obj.get("model")
        duration_ms = metrics_obj.get("duration_ms")
        tokens_in = metrics_obj.get("tokens_input")
        tokens_out = metrics_obj.get("tokens_output")
        tokens_total = metrics_obj.get("tokens_total")

    model_s = str(model) if model else "(неизвестно)"
    if isinstance(duration_ms, (int, float)):
        time_s = f"{int(duration_ms)} ms"
    else:
        time_s = "(неизвестно)"
    tok_s = (
        f"in={tokens_in}, out={tokens_out}, total={tokens_total}"
        if isinstance(tokens_total, (int, float))
        or isinstance(tokens_in, (int, float))
        or isinstance(tokens_out, (int, float))
        else "(неизвестно)"
    )

    extra: list[str] = []
    if metrics_error:
        extra.append(f"metrics_error: {metrics_error}")
    if not db_row:
        extra.append("db: запись rarequests не найдена по (source, query)")
    else:
        extra.append(f"rarequest_id: {db_row.id}")
        if db_row.created_at:
            extra.append(f"created_at: {db_row.created_at}")

    extra.append(f"source: {source}")
    extra.append(f"query_index: {query_index}")
    return "\n".join(
        [
            f"Модель: {model_s}",
            f"Время: {time_s}",
            f"Токены: {tok_s}",
            "",
            *extra,
        ]
    ).strip()


def empty_result_row() -> dict[str, str]:
    return {h: "" for h in CSV_HEADERS_ALL}


_REPO_ROOT = Path(__file__).resolve().parent.parent.parent


def _subpath_under_app_data(path: Path) -> Optional[Path]:
    """Часть пути под app/data (all_tests_queries.csv, rarequests/tests, …)."""
    p = path.as_posix()
    if p.startswith("/app/data/"):
        return Path(p[len("/app/data/") :])
    rel = p.lstrip("./")
    if rel.startswith("app/data/"):
        return Path(rel[len("app/data/") :])
    return None


def resolve_data_path(path: Path) -> Path:
    """
    Пути app/data/... и /app/data/... → каталог данных окружения:
    - в Docker: /app/data/... (volume);
    - на хосте: <корень репо>/app/data/... (дефолты вроде /app/data/... тоже мапятся сюда).
    """
    sub = _subpath_under_app_data(path)
    if sub is not None:
        container_base = Path("/app/data")
        if container_base.is_dir():
            return container_base / sub
        return (_REPO_ROOT / "app" / "data" / sub).resolve()
    if path.is_absolute():
        return path
    return (Path.cwd() / path).resolve()
