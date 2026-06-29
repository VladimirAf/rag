"""KB-files: token overlap, embedding_query, SKU в каноне."""

from models import QueryParseResults
from routes.files import retrieval_entities as re


def test_token_overlap_samurai_long_kb_titles():
    query = "как включить блендер самурай BDS-04"
    titles = (
        "Блендер  Samurai BDS-04 [BDS-04] | "
        "Запчасти > Блендеры > Samurai"
    )
    assert re.token_overlap_count(query, titles) >= 2
    assert "самурай" in re.significant_tokens(query)


def test_kb_metadata_overlap_empty_fields_zero_from_titles():
    query = "самурай BDS-04"
    assert re.kb_metadata_overlap_score(query, kb_product_titles="", chunk_text=None) == 0
    assert re.kb_metadata_overlap_score(query, kb_product_titles=None, chunk_text=None) == 0


def test_sku_in_kb_canonical():
    titles = "Блендер  Samurai BDS-04 [BDS-04]"
    assert re.sku_in_kb_canonical("BDS-04", kb_product_titles=titles)
    assert not re.sku_in_kb_canonical("RMA-12", kb_product_titles=titles)


def test_sku_in_kb_model_skus_pipe_list():
    assert re.sku_in_kb_canonical("BDS-04", kb_model_skus="BDS-03|BDS-04|BDG-01")
    assert not re.sku_in_kb_canonical("RMA-12", kb_model_skus="BDS-03|BDS-04")


def test_build_files_embedding_query_without_parse_results():
    out = re.build_files_embedding_query(
        "включить блендер",
        "как включить блендер самурай BDS-04",
        parse_results=None,
    )
    assert "включить блендер" in out
    assert "BDS-04" in out
    assert "Блендеры" not in out


def test_build_files_embedding_query_with_parse_results():
    pr = QueryParseResults(
        product_names=["самурай", "BDS-04"],
        categories=["Блендеры"],
        other_products=None,
    )
    out = re.build_files_embedding_query(
        "инструкция блендер",
        "как включить блендер самурай BDS-04",
        parse_results=pr,
    )
    assert "инструкция блендер" in out
    assert "BDS-04" in out
    assert "Блендеры" in out
    assert "самурай" in out


def test_is_kb_chunk_metadata():
    assert re.is_kb_chunk_metadata({"source": "tm_knowledge_base"})
    assert re.is_kb_chunk_metadata({"filename": "knowledge_base_kb1_file2_v1.pdf"})
    assert not re.is_kb_chunk_metadata({"filename": "recipe_foo.pdf", "source": "other"})


def test_normalize_unicode_dash_in_overlap():
    q = "самурай BDS\u201104"
    titles = "Samurai BDS-04 blender"
    assert re.token_overlap_count(q, titles) >= 1


def test_extract_kb_product_titles_needles_modern_series():
    pr = QueryParseResults(
        product_names=["Modern"],
        categories=["Дегидраторы"],
        other_products=False,
    )
    needles = re.extract_kb_product_titles_needles(
        "режимы в дегидраторах серии Modern",
        parse_results=pr,
        sku_hints=[],
    )
    assert "modern" in needles
    assert "дегидратор" in needles


def test_kb_product_titles_match_score_substring():
    titles = "Дегидратор RAWMID Modern RMD-1015 [RMD-1015]"
    score = re.kb_product_titles_match_score(
        "режимы в дегидраторах серии Modern",
        titles,
        ["modern"],
    )
    assert score >= 3


def test_kb_chunk_query_overlap_prefers_mode_text():
    query = "режимы в дегидраторах серии Modern"
    intro = "При включении дегидратора в сеть, прозвучит сигнал."
    modes = (
        "Режимы Интеллектуальный, Живой, Пастила — сушка при 50–70 градусов."
    )
    assert re.kb_chunk_query_overlap_score(query, intro) < re.kb_chunk_query_overlap_score(
        query, modes
    )


def test_select_best_chunks_per_file_id_by_overlap_not_order():
    from types import SimpleNamespace

    query = "режимы сушки"
    fid = "file-hash-12"
    intro = SimpleNamespace(
        metadata={"file_id": fid},
        page_content="При включении дегидратора в сеть.",
    )
    modes = SimpleNamespace(
        metadata={"file_id": fid},
        page_content="Режимы Интеллектуальный, Живой, Пастила для сушки.",
    )
    pool = [
        (intro, 0.1, fid, re.kb_chunk_query_overlap_score(query, intro.page_content)),
        (modes, 0.2, fid, re.kb_chunk_query_overlap_score(query, modes.page_content)),
    ]
    out = re.select_best_chunks_per_file_id(pool, max_per_file=1, max_total=5)
    assert len(out) == 1
    assert out[0][0] is modes
