"""
Проверки контракта ингеста файлов базы знаний (tm/knowledge_base).

Отдельный модуль без LangChain/Chroma — удобен для юнит-тестов.
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional, Sequence

KB_INGEST_SOURCE = "tm_knowledge_base"
KB_FILENAME_PREFIX = "knowledge_base"


def warn_kb_ingest_if_needed(
    upload_filename: Optional[str],
    metadata: Optional[Dict[str, Any]],
) -> None:
    """Мягкие проверки контракта tm/knowledge_base (не блокируют индексацию)."""
    if not metadata or metadata.get("source") != KB_INGEST_SOURCE:
        return
    if metadata.get("data_type") != "files":
        logging.warning(
            "[KB ingest] source=%s but data_type=%r (expected 'files'); filename=%r",
            KB_INGEST_SOURCE,
            metadata.get("data_type"),
            upload_filename,
        )
    name = (upload_filename or "").strip()
    if name and not name.startswith(KB_FILENAME_PREFIX):
        logging.warning(
            "[KB ingest] filename %r does not start with %r (tm export contract)",
            name,
            KB_FILENAME_PREFIX,
        )


def is_transcript_file(metadata: Optional[Dict]) -> bool:
    """True только при явном metadata.ai_file_type == 'transcript'."""
    return (metadata or {}).get("ai_file_type") == "transcript"


def merge_short_documents(
    docs: Sequence[Any],
    min_chunk_len: int = 300,
    separator: str = "\n\n",
) -> List[Any]:
    """
    Склеивает соседние короткие Document-элементы до min_chunk_len символов.

    metadata берётся от первого элемента серии; хвост короче порога не отбрасывается.
    """
    if not docs:
        return []

    merged: List[Any] = []
    buffer_parts: List[str] = []
    buffer_meta: Optional[Dict[str, Any]] = None
    doc_cls = type(docs[0])

    def _buffer_len() -> int:
        return len(separator.join(buffer_parts))

    def _flush() -> None:
        nonlocal buffer_parts, buffer_meta
        if not buffer_parts:
            return
        merged.append(
            doc_cls(
                page_content=separator.join(buffer_parts),
                metadata=dict(buffer_meta or {}),
            )
        )
        buffer_parts = []
        buffer_meta = None

    for doc in docs:
        content = doc.page_content or ""
        if not buffer_parts:
            meta = getattr(doc, "metadata", None)
            buffer_meta = dict(meta) if meta else {}
            buffer_parts = [content]
        else:
            buffer_parts.append(content)

        if _buffer_len() >= min_chunk_len:
            _flush()

    _flush()
    return merged
