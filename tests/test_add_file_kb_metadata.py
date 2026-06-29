"""Ингест /files/add: предупреждения KB-контракта (kb_ingest)."""

import importlib.util
import logging
from pathlib import Path


def _load_kb_ingest():
    path = Path(__file__).resolve().parents[1] / "routes/files/kb_ingest.py"
    spec = importlib.util.spec_from_file_location("kb_ingest_under_test", path)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


kb_ingest = _load_kb_ingest()


def test_warn_kb_ingest_missing_data_type(caplog):
    caplog.set_level(logging.WARNING)
    kb_ingest.warn_kb_ingest_if_needed(
        "knowledge_base_kb1_file2_v1_manual.pdf",
        {"source": kb_ingest.KB_INGEST_SOURCE, "data_type": "recipe"},
    )
    assert any("data_type" in r.message for r in caplog.records)


def test_warn_kb_ingest_bad_filename_prefix(caplog):
    caplog.set_level(logging.WARNING)
    kb_ingest.warn_kb_ingest_if_needed(
        "manual.pdf",
        {"source": kb_ingest.KB_INGEST_SOURCE, "data_type": "files"},
    )
    assert any("does not start with" in r.message for r in caplog.records)


def test_warn_kb_ingest_ok_silent(caplog):
    caplog.set_level(logging.WARNING)
    kb_ingest.warn_kb_ingest_if_needed(
        "knowledge_base_kb1_file2_v1_manual.pdf",
        {"source": kb_ingest.KB_INGEST_SOURCE, "data_type": "files"},
    )
    assert not caplog.records


def test_warn_kb_ingest_skipped_for_non_kb(caplog):
    caplog.set_level(logging.WARNING)
    kb_ingest.warn_kb_ingest_if_needed("other.pdf", {"data_type": "files"})
    assert not caplog.records
