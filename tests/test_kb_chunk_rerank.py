"""Композитный реранк KB-чанков после vector search."""

from types import SimpleNamespace

from models import QueryParseResults
from routes.files import kb_chunk_rerank as kbr


def _doc(
    filename: str,
    content: str,
    *,
    source: str | None = None,
    kb_product_titles: str | None = None,
    kb_model_skus: str | None = None,
    kb_categories: str | None = None,
):
    meta = {"filename": filename, "file_id": filename}
    if source:
        meta["source"] = source
    if kb_product_titles is not None:
        meta["kb_product_titles"] = kb_product_titles
    if kb_model_skus is not None:
        meta["kb_model_skus"] = kb_model_skus
    if kb_categories is not None:
        meta["kb_categories"] = kb_categories
    return SimpleNamespace(metadata=meta, page_content=content)


def test_kb_samurai_bds04_beats_unrelated_dehydrator():
    query = "как включить блендер самурай BDS-04"
    sku_hints = ["BDS-04"]
    samurai = _doc(
        "knowledge_base_kb1_file1_v1.pdf",
        "инструкция включения",
        source="tm_knowledge_base",
        kb_product_titles="Блендер Samurai BDS-04 [BDS-04]",
        kb_model_skus="BDS-04",
        kb_categories="Блендеры",
    )
    dehydrator = _doc(
        "knowledge_base_kb2_file2_v1.pdf",
        "режимы сушки дегидратор RMD-10",
        source="tm_knowledge_base",
        kb_product_titles="Дегидратор RMD-10",
        kb_model_skus="RMD-10",
        kb_categories="Дегидраторы",
    )
    docs = [(dehydrator, 0.1), (samurai, 0.2)]
    out = kbr.rerank_stage_docs(
        docs,
        raw_query=query,
        sku_hints=sku_hints,
        parse_results=QueryParseResults(
            categories=["Блендеры"],
            product_names=["самурай"],
            other_products=None,
        ),
    )
    assert out[0][0] is samurai


def test_tie_break_parts_below_main_product_category():
    query = "как пользоваться кофемашиной"
    main = _doc(
        "knowledge_base_kb1_v1.pdf",
        "руководство",
        source="tm_knowledge_base",
        kb_categories="Кофемашины",
        kb_product_titles="Кофемашина X",
    )
    parts = _doc(
        "knowledge_base_kb2_v1.pdf",
        "руководство",
        source="tm_knowledge_base",
        kb_categories="Запчасти > Кофемашины",
        kb_product_titles="Фильтр",
    )
    bd_main = kbr.kb_chunk_score_breakdown(query, [], None, main.metadata, main.page_content)
    bd_parts = kbr.kb_chunk_score_breakdown(query, [], None, parts.metadata, parts.page_content)
    assert bd_main["composite"] == bd_parts["composite"]
    assert kbr.get_category_priority(main.metadata["kb_categories"]) < kbr.get_category_priority(
        parts.metadata["kb_categories"]
    )
    out = kbr.rerank_stage_docs(
        [(parts, 0.1), (main, 0.1)],
        raw_query=query,
        sku_hints=[],
    )
    assert out[0][0] is main


def test_non_kb_still_reranks_by_sku_hints():
    query = "дегидратор RMA-12"
    rma = _doc("RAWMID_RMA_12.pdf", "инструкция RMA-12")
    rmd = _doc("RMD_10.pdf", "дегидратор RMD-10")
    out = kbr.rerank_stage_docs(
        [(rmd, 0.05), (rma, 0.2)],
        raw_query=query,
        sku_hints=["RMA-12"],
    )
    assert out[0][0] is rma
