"""Извлечение SKU из запроса для узкого поиска по мануалам."""

import importlib.util
from pathlib import Path


def _load_sku_hints():
    """Обход заглушки routes.files.crud из conftest — модуль без тяжёлых зависимостей."""
    path = Path(__file__).resolve().parents[1] / "routes/files/sku_hints.py"
    spec = importlib.util.spec_from_file_location("sku_hints_under_test", path)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


sku_hints = _load_sku_hints()


def test_extract_model_sku_hints_rma():
    assert sku_hints.extract_model_sku_hints("Как пользоваться дегидратором RMA-12") == ["RMA-12"]


def test_extract_model_sku_hints_multiple_distinct():
    out = sku_hints.extract_model_sku_hints("Сравни RMD-10 и RMA-12 для сушки")
    assert out == ["RMD-10", "RMA-12"]


def test_extract_model_sku_hints_unicode_dash():
    q = "Инструкция RMA\u201112"
    assert sku_hints.extract_model_sku_hints(q) == ["RMA-12"]


def test_extract_model_sku_hints_empty():
    assert sku_hints.extract_model_sku_hints("Как включить дегидратор") == []


def test_chroma_contains_needles_variants():
    assert sku_hints.chroma_contains_needles("RMA-12")[:4] == ["rma-12", "rma 12", "rma12", "rma_12"]
    needles = sku_hints.chroma_contains_needles("RMA-12")
    assert "rma\u201112" in needles


def test_sku_match_strength_filename():
    fn = "RAWMID_Modern_RMA_12.pdf.abc"
    assert sku_hints.sku_match_strength("RMA-12", fn, "обрывок") == 4


def test_sku_match_strength_body_only():
    assert sku_hints.sku_match_strength("RMA-12", "other.pdf", "инструкция RMA-12 для печи") == 3


def test_sku_match_strength_no_match():
    assert sku_hints.sku_match_strength("RMA-12", "RMD_10.pdf", "дегидратор RMD-10") == 0


def test_hard_select_picks_filename_match():
    candidates = [
        {"filename": "other.pdf", "excerpt": "текст RMA-12", "file_id": "a"},
        {"filename": "RAWMID_Modern_RMA_12.pdf", "excerpt": "обрывок", "file_id": "b"},
    ]
    indexes, strength = sku_hints.hard_select_candidate_indexes(
        candidates, ["RMA-12"]
    )
    assert strength == 4
    assert indexes == [1]


def test_hard_select_below_threshold_returns_empty():
    candidates = [
        {"filename": "x.pdf", "excerpt": "без артикула", "file_id": "a"},
    ]
    indexes, strength = sku_hints.hard_select_candidate_indexes(
        candidates, ["RMA-12"]
    )
    assert indexes == []
    assert strength == 0
