"""Юнит-тесты is_transcript_file и merge_short_documents (kb_ingest)."""

import importlib
import importlib.util
import sys
from contextlib import contextmanager
from io import BytesIO
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from langchain_core.documents import Document


def _load_kb_ingest():
    path = Path(__file__).resolve().parents[1] / "routes/files/kb_ingest.py"
    spec = importlib.util.spec_from_file_location("kb_ingest_under_test", path)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _load_files_crud():
    fq = "routes.files.crud"
    sys.modules.pop(fq, None)
    return importlib.import_module(fq)


kb_ingest = _load_kb_ingest()


def _make_doc(text: str, metadata: dict | None = None) -> Document:
    return Document(page_content=text, metadata=metadata or {})


@pytest.mark.parametrize(
    "metadata,expected",
    [
        ({"ai_file_type": "transcript"}, True),
        ({}, False),
        (None, False),
        ({"ai_file_type": "manual"}, False),
    ],
)
def test_is_transcript_file(metadata, expected):
    assert kb_ingest.is_transcript_file(metadata) is expected


def test_merge_short_documents_empty():
    assert kb_ingest.merge_short_documents([]) == []


def test_merge_short_documents_single_short_tail():
    docs = [_make_doc("y" * 50, {"a": 1})]
    merged = kb_ingest.merge_short_documents(docs, min_chunk_len=300)
    assert len(merged) == 1
    assert len(merged[0].page_content) == 50
    assert merged[0].metadata == {"a": 1}


def test_merge_short_documents_ten_microchunks():
    docs = [_make_doc("x" * 40, {"i": i}) for i in range(10)]
    merged = kb_ingest.merge_short_documents(docs, min_chunk_len=300)

    assert len(merged) <= 2
    for doc in merged[:-1]:
        assert len(doc.page_content) >= 300
    if len(merged) == 1:
        assert len(merged[0].page_content) >= 300

    original_text = "".join(d.page_content for d in docs)
    merged_text = "".join(d.page_content.replace("\n\n", "") for d in merged)
    assert merged_text == original_text
    assert merged[0].metadata == {"i": 0}


@contextmanager
def _noop_lock(_name):
    yield


@patch("rag.chroma_lock", side_effect=_noop_lock)
def test_add_file_applies_transcript_merge(_lock):
    crud = _load_files_crud()
    store = MagicMock()
    store.get.return_value = {"ids": []}

    short_docs = [_make_doc("x" * 40, {"i": i}) for i in range(10)]

    with (
        patch.object(crud, "text_splitter") as splitter,
        patch.object(crud, "UnstructuredLoader") as loader_cls,
        patch.object(crud, "_get_files_store", return_value=store),
    ):
        loader_cls.return_value.load.return_value = short_docs
        splitter.split_documents.side_effect = lambda docs: docs

        payload = BytesIO(b"transcript-bytes")
        payload.name = "knowledge_base_kb6_file1_v1_talk.ru.txt"
        crud.add_file(
            file=payload,
            metadata={"ai_file_type": "transcript", "data_type": "files"},
        )

        split_input = splitter.split_documents.call_args[0][0]
        assert len(split_input) <= 2
        assert len(split_input[0].page_content) >= 300


@patch("rag.chroma_lock", side_effect=_noop_lock)
def test_add_file_skips_merge_for_non_transcript(_lock):
    crud = _load_files_crud()
    store = MagicMock()
    store.get.return_value = {"ids": []}

    short_docs = [_make_doc("x" * 40, {"i": i}) for i in range(10)]

    with (
        patch.object(crud, "text_splitter") as splitter,
        patch.object(crud, "UnstructuredLoader") as loader_cls,
        patch.object(crud, "_get_files_store", return_value=store),
    ):
        loader_cls.return_value.load.return_value = list(short_docs)
        splitter.split_documents.side_effect = lambda docs: docs

        payload = BytesIO(b"manual-bytes")
        payload.name = "knowledge_base_kb6_file1_v1_manual.pdf"
        crud.add_file(
            file=payload,
            metadata={"ai_file_type": "manual", "data_type": "files"},
        )

        split_input = splitter.split_documents.call_args[0][0]
        assert len(split_input) == 10
        assert all(len(d.page_content) == 40 for d in split_input)
