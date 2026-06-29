"""Upsert в add_file: удаление существующих чанков по file_hash перед индексацией."""

import importlib
import sys
from contextlib import contextmanager
from io import BytesIO
from unittest.mock import MagicMock, patch

from langchain_core.documents import Document


def _load_files_crud():
    """Реальный crud.py: conftest подменяет routes.files.crud лёгкой заглушкой."""
    fq = "routes.files.crud"
    sys.modules.pop(fq, None)
    return importlib.import_module(fq)


crud = _load_files_crud()


@contextmanager
def _noop_lock(_name):
    yield


def _make_store(*, existing_ids):
    store = MagicMock()
    store.get.return_value = {"ids": list(existing_ids)}
    return store


@patch.object(crud, "text_splitter")
@patch.object(crud, "UnstructuredLoader")
@patch("rag.chroma_lock", side_effect=_noop_lock)
def test_add_file_deletes_existing_chunks_by_file_hash(
    _lock, loader_cls, splitter
):
    store = _make_store(existing_ids=["chunk-1", "chunk-2"])
    doc = Document(page_content="hello\nworld", metadata={})
    loader_cls.return_value.load.return_value = [doc]
    splitter.split_documents.return_value = [doc]

    payload = BytesIO(b"same-bytes")
    payload.name = "manual.pdf"
    expected_hash = crud.get_file_hash(BytesIO(b"same-bytes"))

    with patch.object(crud, "_get_files_store", return_value=store):
        crud.add_file(file=payload, metadata={"data_type": "files"})

    store.get.assert_called_once_with(
        where={"file_hash": {"$eq": expected_hash}}
    )
    store.delete.assert_called_once_with(
        where={"file_hash": {"$eq": expected_hash}}
    )
    store.add_documents.assert_called_once()


@patch.object(crud, "text_splitter")
@patch.object(crud, "UnstructuredLoader")
@patch("rag.chroma_lock", side_effect=_noop_lock)
def test_add_file_skips_delete_when_file_hash_is_new(_lock, loader_cls, splitter):
    store = _make_store(existing_ids=[])
    doc = Document(page_content="hello", metadata={})
    loader_cls.return_value.load.return_value = [doc]
    splitter.split_documents.return_value = [doc]

    payload = BytesIO(b"new-bytes")
    payload.name = "new.pdf"

    with patch.object(crud, "_get_files_store", return_value=store):
        crud.add_file(file=payload)

    store.get.assert_called_once()
    store.delete.assert_not_called()
    store.add_documents.assert_called_once()
