from langchain_unstructured import UnstructuredLoader
from rag import get_vector_store, text_splitter, fix_metadata_value_types
import rag
from models import QueryParseResults, SearchResults
from io import BytesIO
from uuid import uuid4
from typing import List, Optional, Dict, Any, Sequence, Tuple
from langchain_core.documents import Document
from pathlib import Path
from datetime import datetime
import json
import logging
import config
import llm_funcs
from .kb_ingest import (
    KB_INGEST_SOURCE,
    is_transcript_file,
    merge_short_documents,
    warn_kb_ingest_if_needed,
)
from .kb_chunk_rerank import rerank_stage_docs
from .retrieval_entities import (
    build_files_embedding_query,
    extract_kb_product_titles_needles,
    kb_chunk_query_overlap_score,
    kb_product_titles_match_score,
    select_best_chunks_per_file_id,
)
from .sku_hints import (
    SKU_HARD_SELECT_MIN_STRENGTH,
    chroma_contains_needles,
    extract_model_sku_hints,
    hard_select_candidate_indexes,
)
from .topical_fallback import (
    allows_seocrm_topical_fallback,
    rank_topical_strengths,
    topical_select_candidate_indexes,
)
from .utils import get_file_hash

BACKUPS_DIR = config.DATA_PATH / "backups"

# Стадии staged-поиска по data_type (дефолтный порядок совместим с историческим поведением).
FILES_STAGED_ALLOWED_DATA_TYPES: Tuple[str, ...] = (
    "files",
    "seocrm_article",
    "recipe",
)
FILES_STAGED_DEFAULT_DATA_TYPE_ORDER: Tuple[str, ...] = FILES_STAGED_ALLOWED_DATA_TYPES


def _normalize_staged_data_type_order(
    data_type_order: Optional[Sequence[str]],
) -> List[str]:
    """
    None → дефолтный порядок. Иначе — переданная последовательность (пустая допустима явно).
    Значения должны быть из FILES_STAGED_ALLOWED_DATA_TYPES.
    """
    if data_type_order is None:
        return list(FILES_STAGED_DEFAULT_DATA_TYPE_ORDER)
    out: List[str] = []
    for dt in data_type_order:
        if dt not in FILES_STAGED_ALLOWED_DATA_TYPES:
            raise ValueError(
                f"Неизвестный data_type в data_type_order: {dt!r}; "
                f"допустимо: {list(FILES_STAGED_ALLOWED_DATA_TYPES)}"
            )
        out.append(dt)
    return out


def _merge_doc_score_lists(
    *lists: Sequence[Tuple[Any, float]],
    max_items: int,
) -> List[Tuple[Any, float]]:
    """Объединяет (Document, distance) без дублей по id чанка; сохраняет лучший distance."""
    merged: Dict[str, Tuple[Any, float]] = {}
    for items in lists:
        for doc, dist in items:
            key = getattr(doc, "id", None) or id(doc)
            prev = merged.get(key)
            if prev is None or float(dist) < float(prev[1]):
                merged[key] = (doc, float(dist))
    ordered = sorted(merged.values(), key=lambda x: x[1])
    return ordered[:max_items]


def _fetch_docs_by_kb_product_titles(
    store,
    where_filter: Dict[str, Any],
    raw_query: str,
    parse_results: Optional[QueryParseResults],
    sku_hints: List[str],
    max_chunks: int = 40,
) -> List[Tuple[Document, float]]:
    """
    Чанки KB, у которых metadata kb_product_titles пересекается с запросом.
    Chroma metadata не поддерживает $contains — фильтрация в Python после get().
    """
    needles = extract_kb_product_titles_needles(
        raw_query, parse_results=parse_results, sku_hints=sku_hints
    )
    if not needles and not (raw_query or "").strip():
        return []

    kb_where: Dict[str, Any] = {
        "$and": [
            where_filter,
            {"source": {"$eq": KB_INGEST_SOURCE}},
        ]
    }
    try:
        with rag.chroma_lock("files"):
            batch = store.get(where=kb_where, include=["metadatas", "documents"])
    except Exception as e:
        logging.warning(
            "[DEBUG] Staged search: kb_product_titles get failed: %s", e
        )
        return []

    ids = batch.get("ids") or []
    metadatas = batch.get("metadatas") or []
    documents = batch.get("documents") or []
    pool: List[Tuple[Document, float, str, int]] = []

    for i, doc_id in enumerate(ids):
        meta = metadatas[i] if i < len(metadatas) else {}
        text = documents[i] if i < len(documents) else ""
        if not isinstance(meta, dict):
            meta = {}
        titles = meta.get("kb_product_titles")
        title_match = kb_product_titles_match_score(raw_query, titles, needles)
        if title_match <= 0:
            continue
        overlap = kb_chunk_query_overlap_score(
            raw_query, text or "", kb_product_titles=titles, needles=needles
        )
        # distance для merge/rerank: выше overlap → меньше distance
        dist = max(0.05, 0.5 - 0.05 * min(overlap + title_match, 12))
        pool.append(
            (
                Document(page_content=text or "", metadata=meta, id=doc_id),
                dist,
                meta.get("file_id") or "",
                overlap,
            )
        )

    if not pool:
        return []

    out = select_best_chunks_per_file_id(pool, max_total=max_chunks)

    logging.info(
        "[DEBUG] Staged search: kb_product_titles filter needles=%s n_chunks=%s",
        needles[:8],
        len(out),
    )
    return out


def _retrieve_stage_docs_vector(
    store,
    embedding_query: str,
    where_filter: Dict[str, Any],
    k: int,
    sku_hints: List[str],
    raw_query: str = "",
    parse_results: Optional[QueryParseResults] = None,
) -> List[Any]:
    """
    Векторный поиск по стадии data_type. Если в запросе есть SKU — сначала ограничиваем
    чанки подстрокой артикула в тексте документа (Chroma where_document $contains),
    иначе часто выигрывает «похожий» мануал другой модели (тот же класс устройства).
    Для KB также подмешиваются чанки с совпадением по metadata kb_product_titles.
    После выборки чанки пересортированы по явному совпадению SKU с filename/текстом.
    """
    k_fetch = min(max(k * 3, k), 80) if sku_hints else k
    title_hits = _fetch_docs_by_kb_product_titles(
        store,
        where_filter,
        raw_query,
        parse_results,
        sku_hints,
    )

    with rag.chroma_lock("files"):
        if sku_hints:
            for hint in sku_hints:
                for needle in chroma_contains_needles(hint):
                    try:
                        narrowed = store.similarity_search_with_score(
                            query=embedding_query,
                            k=k_fetch,
                            filter=where_filter,
                            where_document={"$contains": needle},
                        )
                    except Exception as e:
                        logging.warning(
                            f"[DEBUG] Staged search: Chroma where_document contains={needle!r} failed: {e}"
                        )
                        narrowed = []
                    if narrowed:
                        logging.info(
                            f"[DEBUG] Staged search: using document contains filter {needle!r}, "
                            f"n_chunks={len(narrowed)}"
                        )
                        merged = _merge_doc_score_lists(
                            title_hits,
                            narrowed,
                            max_items=k_fetch,
                        )
                        return rerank_stage_docs(
                            merged,
                            raw_query=raw_query,
                            sku_hints=sku_hints,
                            parse_results=parse_results,
                        )[:k]
            logging.info(
                "[DEBUG] Staged search: SKU hints %s produced no hits with $contains; "
                "falling back to vector-only (k_fetch=%s)",
                sku_hints,
                k_fetch,
            )
        raw = store.similarity_search_with_score(
            query=embedding_query,
            k=k_fetch,
            filter=where_filter,
        )
        combined = _merge_doc_score_lists(
            title_hits,
            raw,
            max_items=k_fetch,
        )
        trimmed = rerank_stage_docs(
            combined,
            raw_query=raw_query,
            sku_hints=sku_hints,
            parse_results=parse_results,
        )
        return trimmed[:k]


# Ленивая загрузка store: не держим ссылку при старте, чтобы после
# delete_files_collection() следующий запрос получил новую пустую коллекцию.
_files_vector_store = None


def _get_files_store():
    global _files_vector_store
    if _files_vector_store is None:
        _files_vector_store = get_vector_store("files")
    return _files_vector_store


def clear_files_store_cache():
    """Сбросить кэш store после удаления коллекции (например, через delete_files_collection)."""
    global _files_vector_store
    _files_vector_store = None


def add_file(
        file: BytesIO,
        metadata: Optional[Dict] = None
        ):
    if not file:
        raise ValueError("Нельзя загрузить пустой файл")

    upload_filename = file.name
    warn_kb_ingest_if_needed(upload_filename, metadata)

    file_hash = get_file_hash(file)
    store = _get_files_store()
    # Если по этому file_hash уже есть документы, удаляем их перед добавлением новых.
    with rag.chroma_lock("files"):
        existing = store.get(where={"file_hash": {"$eq": file_hash}})
        if existing and existing.get("ids"):
            store.delete(where={"file_hash": {"$eq": file_hash}})

    loaded_documents = UnstructuredLoader(
        file=file,
        metadata_filename=upload_filename,
        ).load()
    if is_transcript_file(metadata):
        loaded_documents = merge_short_documents(loaded_documents, min_chunk_len=300)
    for doc in loaded_documents:
        doc.metadata["text_title"] = doc.page_content.split("\n")[0][:100]
        doc.metadata["file_hash"] = file_hash
        doc.metadata["file_id"] = file_hash
        if metadata:
            doc.metadata.update(metadata)
        # Стабильное имя для реранка/SKU и delete по версии (tm: knowledge_base_kb*_file*_v*_*)
        if upload_filename:
            doc.metadata["filename"] = upload_filename
        doc.metadata = fix_metadata_value_types(doc.metadata)

    splitted_documents = text_splitter.split_documents(loaded_documents)
    with rag.chroma_lock("files"):
        store.add_documents(documents=splitted_documents)


def add_file_text(
        text: str,
        metadata: Optional[Dict] = None
        ):
    if not text:
        raise ValueError("Нельзя загрузить пустой текст")

    text_lower = text.lower()
    file_text_id = str(uuid4())
    data_type = (metadata or {}).get("data_type")
    item_id = (metadata or {}).get("item_id")

    if data_type is not None and item_id is not None:
        filename = f"{data_type}_{str(item_id)}"
    else:
        filename = f"{file_text_id}.txt"

    # Если по этому data_type+item_id уже есть документы, удаляем их перед добавлением новых (по filename).
    store = _get_files_store()
    with rag.chroma_lock("files"):
        existing_by_filename = store.get(where={"filename": {"$eq": filename}})
        if existing_by_filename and existing_by_filename.get("ids"):
            store.delete(where={"filename": {"$eq": filename}})

    file = BytesIO(text_lower.encode("utf-8"))
    file.name = filename
    file_hash = get_file_hash(file)

    loaded_documents = UnstructuredLoader(file=file, metadata_filename=file.name).load()
    for doc in loaded_documents:
        doc.metadata["text_title"] = text.split("\n")[0][:100]
        doc.metadata["file_text_id"] = file_text_id
        doc.metadata["file_id"] = file_hash
        doc.metadata["file_hash"] = file_hash
        doc.metadata["filename"] = file.name
        doc.metadata["filetype"] = "txt"
        if metadata:
            doc.metadata.update(metadata)
        doc.metadata = fix_metadata_value_types(doc.metadata)

    splitted_documents = text_splitter.split_documents(loaded_documents)
    with rag.chroma_lock("files"):
        store.add_documents(documents=splitted_documents)


def search(
       query: str,
       k: int = config.RAG_FILES_SEARCH_RESULTS_AMOUNT,
       score_threshold: float = config.RAG_SCORE_threshold,
       contains_text: Optional[str] = None,
       model_name: Optional[str] = None,
       data_type: Optional[str] = None
) -> SearchResults:
    query = llm_funcs.summarize_user_query(query, model_name=model_name)
    store = _get_files_store()

    where_filter = None
    if data_type:
        where_filter = {"data_type": {"$eq": data_type}}

    where_documents_filter = None
    if contains_text:
        where_documents_filter = {"$contains": contains_text.lower()}

    with rag.chroma_lock("files"):
        docs = store.similarity_search_with_score(
            query=query,
            k=k,
            filter=where_filter,
            where_document=where_documents_filter
            )

    def _to_similarity(distance: float) -> float:
        try:
            d = float(distance)
        except Exception:
            return 0.0
        # Монотонное преобразование: меньше distance -> больше similarity.
        return 1.0 / (1.0 + d)

    # Сортируем по возрастанию distance (лучшие первыми)
    sorted_by_distance = sorted(docs, key=lambda x: x[1])

    # Строгая фильтрация по порогу похожести
    filtered = [(doc, dist) for doc, dist in sorted_by_distance if _to_similarity(dist) >= score_threshold]

    chunks = [doc for doc, _dist in filtered]
    chunks_text = [chunk.page_content for chunk in chunks]
    file_ids = list(set([chunk.metadata["file_id"] for chunk in chunks]))
    if file_ids:
        documents: List[str] = list()
        filenames: List[str] = list()
        for file_id in file_ids:
            with rag.chroma_lock("files"):
                file_paragraphs = store.get(where={"file_id": {"$in": [file_id]}})["documents"]
            documents.append(" ".join(file_paragraphs))
            with rag.chroma_lock("files"):
                file_meta = store.get(
                    where={"file_id": {"$in": [file_id]}},
                    include=["metadatas"]
                )
            metadatas = file_meta.get("metadatas", [])
            filename = None
            for meta in metadatas:
                filename = meta.get("filename")
                if filename:
                    break
            filenames.append(filename or "unknown")
    else:
        documents = None
        filenames = None
    return SearchResults(
        chunks=chunks_text,
        documents=documents,
        file_ids=file_ids if file_ids else None,
        filenames=filenames
    )

def search_staged(
    query: str,
    k: int = config.RAG_FILES_SEARCH_RESULTS_AMOUNT,
    score_threshold: float = config.RAG_SCORE_threshold,
    model_name: Optional[str] = None,
    data_type_order: Optional[Sequence[str]] = None,
    parse_results: Optional[QueryParseResults] = None,
    intent_label: Optional[str] = None,
) -> SearchResults:
    """
    Пошаговый поиск в коллекции 'files' с ранним выходом.
    По умолчанию порядок стадий: files -> seocrm_article -> recipe.
    Если передан data_type_order — используется он (те же литералы data_type).
    """
    stages = _normalize_staged_data_type_order(data_type_order)
    if not stages:
        return SearchResults(
            chunks=None,
            documents=None,
            file_ids=None,
            filenames=None,
        )
    final_chunks_text = []
    final_file_ids = []
    final_filenames = []
    final_documents = []
    exited_after_staged_judge_sufficient = False
    files_sku_hard_selected = False

    # Модель для судьи (по умолчанию Gemini Flash 2.0)
    judge_model = model_name or "google/gemini-2.5-flash-lite"
    
    # 0. Суммаризация запроса один раз для всех стадий
    summarized_query = llm_funcs.summarize_user_query(query, model_name=judge_model)
    sku_hints = extract_model_sku_hints(query)
    # Подмешиваем артикулы в embedding-текст: summarize_user_query иногда «съедает» точный SKU.
    embedding_query = summarized_query
    if sku_hints:
        embedding_query = f"{summarized_query} {' '.join(sku_hints)}"
        logging.info(f"[DEBUG] Staged search: SKU hints from raw query: {sku_hints}")

    files_embedding_query = build_files_embedding_query(
        summarized_query,
        query,
        parse_results=parse_results,
    )
    if parse_results and files_embedding_query != embedding_query:
        logging.info(
            "[DEBUG] Staged search: files embedding_query enriched from parse_results"
        )

    store = _get_files_store()

    for stage in stages:
        logging.info(f"[DEBUG] Staged search: checking stage '{stage}'")

        stage_embedding_query = (
            files_embedding_query if stage == "files" else embedding_query
        )

        # 1. Поиск в текущем data_type с метаданными
        where_filter = {"data_type": {"$eq": stage}}
        docs = _retrieve_stage_docs_vector(
            store,
            embedding_query=stage_embedding_query,
            where_filter=where_filter,
            k=k,
            sku_hints=sku_hints,
            raw_query=query,
            parse_results=parse_results,
        )

        if not docs:
            logging.info(f"[DEBUG] Stage '{stage}' returned 0 docs.")
            continue

        # 2. Подготовка кандидатов для арбитра
        candidates = []
        for doc, score in docs:
            sim = 1.0 / (1.0 + float(score))
            if sim < score_threshold:
                continue
            candidates.append({
                "excerpt": doc.page_content,
                "filename": doc.metadata.get("filename", "unknown"),
                "text_title": doc.metadata.get("text_title", "unknown"),
                "file_id": doc.metadata.get("file_id")
            })
            
        if not candidates:
            logging.info(f"[DEBUG] Stage '{stage}' no candidates above threshold.")
            continue

        seocrm_topical_allowed = (
            stage == "seocrm_article"
            and allows_seocrm_topical_fallback(intent_label)
        )
        if seocrm_topical_allowed:
            logging.info(
                "[DEBUG] Staged search: topical strengths top=%s stage=%s",
                rank_topical_strengths(candidates, query),
                stage,
            )

        hard_indexes: List[int] = []
        hard_strength = 0
        if sku_hints and stage == "files":
            hard_indexes, hard_strength = hard_select_candidate_indexes(
                candidates,
                sku_hints,
                min_strength=SKU_HARD_SELECT_MIN_STRENGTH,
            )
            if hard_indexes:
                logging.info(
                    "[DEBUG] Staged search: hard-select SKU strength=%s "
                    "candidate_idx=%s stage=%s",
                    hard_strength,
                    hard_indexes,
                    stage,
                )

        # 3. Вызов арбитра
        judge_res = llm_funcs.judge_files_sufficiency(
            query=query,
            candidates=candidates,
            data_type=stage,
            model_name=judge_model
        )
        
        logging.info(f"[DEBUG] Stage '{stage}' judge: enough={judge_res['enough_information']}, reason={judge_res.get('short_reason')}")
        
        # 4. Обработка результатов арбитра
        selected_indexes_raw = judge_res.get("selected_candidate_indexes", [])
        selected_indexes: List[int] = []
        # Арбитр может вернуть индексы как int, строки, или даже списки (ошибочный JSON).
        # Нормализуем в плоский список int и отбрасываем мусор.
        if isinstance(selected_indexes_raw, list):
            for item in selected_indexes_raw:
                if isinstance(item, int):
                    selected_indexes.append(item)
                elif isinstance(item, str):
                    try:
                        selected_indexes.append(int(item))
                    except Exception:
                        continue
                elif isinstance(item, list):
                    for sub in item:
                        if isinstance(sub, int):
                            selected_indexes.append(sub)
                        elif isinstance(sub, str):
                            try:
                                selected_indexes.append(int(sub))
                            except Exception:
                                continue
        elif isinstance(selected_indexes_raw, int):
            selected_indexes = [selected_indexes_raw]
        elif isinstance(selected_indexes_raw, str):
            try:
                selected_indexes = [int(selected_indexes_raw)]
            except Exception:
                selected_indexes = []

        if (
            stage == "files"
            and hard_indexes
            and hard_strength >= SKU_HARD_SELECT_MIN_STRENGTH
        ):
            selected_indexes = hard_indexes
        elif not selected_indexes and hard_indexes:
            selected_indexes = hard_indexes
            logging.info(
                "[DEBUG] Staged search: judge selected none; "
                "using hard-select SKU strength=%s idx=%s",
                hard_strength,
                hard_indexes,
            )

        topical_indexes: List[int] = []
        topical_strength = 0
        if seocrm_topical_allowed and not selected_indexes:
            topical_indexes, topical_strength = topical_select_candidate_indexes(
                candidates,
                query,
            )
            if topical_indexes:
                selected_indexes = topical_indexes
                fname = candidates[topical_indexes[0]].get("filename", "?")
                logging.info(
                    "[DEBUG] Staged search: topical fallback seocrm "
                    "strength=%s idx=%s filename=%s",
                    topical_strength,
                    topical_indexes,
                    fname,
                )

        if selected_indexes:
            stage_file_ids = set()
            for idx in selected_indexes:
                if 0 <= idx < len(candidates):
                    final_chunks_text.append(candidates[idx]["excerpt"])
                    f_id = candidates[idx].get("file_id")
                    if f_id:
                        stage_file_ids.add(f_id)

            if (
                stage == "files"
                and hard_indexes
                and hard_strength >= SKU_HARD_SELECT_MIN_STRENGTH
                and stage_file_ids
            ):
                files_sku_hard_selected = True

            # Достаем полные документы для выбранных file_id
            for f_id in stage_file_ids:
                if f_id not in final_file_ids:
                    with rag.chroma_lock("files"):
                        res = store.get(where={"file_id": {"$eq": f_id}}, include=["documents", "metadatas"])
                        docs_content = res.get("documents", [])
                        metas = res.get("metadatas", [])
                        if docs_content:
                            final_documents.append(" ".join(docs_content))
                            final_file_ids.append(f_id)
                            # Имя файла из первой найденной метадаты
                            fname = "unknown"
                            for m in metas:
                                if m.get("filename"):
                                    fname = m["filename"]
                                    break
                            final_filenames.append(fname)

        # Если арбитр сказал "достаточно", выходим из цикла стадий (только по data_type)
        if judge_res.get("enough_information"):
            logging.info(f"[DEBUG] Early exit triggered at stage '{stage}'")
            exited_after_staged_judge_sufficient = True
            break

        # Мануал по SKU уже в контексте — не идём в seocrm_article/recipe (судья на files часто enough=false)
        if files_sku_hard_selected:
            logging.info(
                "[DEBUG] Early exit after files SKU hard-select (strength=%s)",
                hard_strength,
            )
            exited_after_staged_judge_sufficient = True
            break
            
    # Если за все стадии ничего не выбрали, но что-то нашли в мануалах (на всякий случай)
    # или если мы хотим вернуть хоть какой-то результат - но по ТЗ арбитр решает.
    
    return SearchResults(
        chunks=final_chunks_text if final_chunks_text else None,
        documents=final_documents if final_documents else None,
        file_ids=final_file_ids if final_file_ids else None,
        filenames=final_filenames if final_filenames else None,
        files_staged_sufficient=True if exited_after_staged_judge_sufficient else None,
    )

def list_all(data_type: Optional[str] = None) -> List:
    try:
        return _list_all_internal(data_type)
    except Exception as e:
        # Если коллекция была удалена другим воркером, сбрасываем кэш и пробуем снова
        if "does not exist" in str(e).lower():
            logging.info("Collection not found, clearing cache and retrying...")
            clear_files_store_cache()
            return _list_all_internal(data_type)
        raise e

def _list_all_internal(data_type: Optional[str] = None) -> List:
    store = _get_files_store()
    with rag.chroma_lock("files"):
        where = {"data_type": {"$eq": data_type}} if data_type else None
        chunks = store.get(where=where, include=["metadatas"])
    chunks_metadata = chunks.get("metadatas") or []
    added_ids: List[str] = list()
    docs: List[Dict[str, Any]] = list()
    for chunk in chunks_metadata:
        if not isinstance(chunk, dict):
            continue
        file_id = chunk.get("file_id")
        if file_id is None:
            continue
        if file_id in added_ids:
            continue

        doc = {
            "file_id": file_id,
            "text_title": chunk.get("text_title", "unknown"),
            "filename": chunk.get("filename", "unknown"),
            "filetype": chunk.get("filetype", "unknown"),
            "filehash": chunk.get("file_hash", "unknown"),
            "url": chunk.get("url"),
            "data_type": chunk.get("data_type")
        }
        docs.append(doc)
        added_ids.append(file_id)
    return docs


def delete_file(
        **metadata
):
    with rag.chroma_lock("files"):
        _get_files_store().delete(where=metadata)


def set_default_data_type_for_legacy_files(default: str = "files") -> int:
    """
    Проставляет data_type=default для всех документов коллекции 'files',
    у которых data_type отсутствует или равен null/пустой строке.

    Возвращает количество обновлённых документов.
    """
    store = _get_files_store()
    coll = getattr(store, "_collection", None)
    if coll is None:
        # Нечего мигрировать, если нет прямого доступа к коллекции
        return 0

    with rag.chroma_lock("files"):
        raw = coll.get(include=["metadatas"])

    ids = raw.get("ids", []) or []
    metadatas = raw.get("metadatas", []) or []

    ids_to_update: List[str] = []
    new_metadatas: List[Dict[str, Any]] = []

    for _id, meta in zip(ids, metadatas):
        if not isinstance(meta, dict):
            continue
        dt = meta.get("data_type")
        if dt is None or dt == "" or str(dt).lower() == "null":
            new_meta = dict(meta)
            new_meta["data_type"] = default
            ids_to_update.append(_id)
            new_metadatas.append(new_meta)

    if not ids_to_update:
        return 0

    # Chroma ограничивает размер батча, поэтому обновляем порциями
    batch_size = 5000
    updated = 0
    with rag.chroma_lock("files"):
        for i in range(0, len(ids_to_update), batch_size):
            batch_ids = ids_to_update[i:i + batch_size]
            batch_metas = new_metadatas[i:i + batch_size]
            coll.update(ids=batch_ids, metadatas=batch_metas)
            updated += len(batch_ids)

    return updated


def backup_files_collection() -> str:
    """
    Сохраняет коллекцию 'files' в JSON-файл (ids, documents, metadatas, embeddings).
    Чтение из Chroma выполняется батчами, чтобы не превышать лимит размера ответа и не падать по таймауту.
    """
    BACKUPS_DIR.mkdir(parents=True, exist_ok=True)
    store = _get_files_store()
    coll = getattr(store, "_collection", None)
    batch_size = 5000

    if coll is None:
        # Fallback: только документы и метаданные (без embeddings)
        with rag.chroma_lock("files"):
            raw = store.get()
        payload = {
            "ids": raw.get("ids", []),
            "documents": raw.get("documents", []),
            "metadatas": raw.get("metadatas", []),
            "embeddings": None,
        }
    else:
        ids_acc: List[str] = []
        documents_acc: List[str] = []
        metadatas_acc: List[Dict[str, Any]] = []
        embeddings_acc: List[Optional[List[float]]] = []

        offset = 0
        while True:
            with rag.chroma_lock("files"):
                raw = coll.get(
                    include=["documents", "metadatas", "embeddings"],
                    limit=batch_size,
                    offset=offset,
                )
            batch_ids = raw.get("ids", []) or []
            if not batch_ids:
                break
            ids_acc.extend(batch_ids)
            documents_acc.extend(raw.get("documents", []) or [])
            metadatas_acc.extend(raw.get("metadatas", []) or [])
            emb = raw.get("embeddings")
            if emb is not None and len(emb) > 0:
                # Chroma может вернуть list или numpy array; для JSON всегда list of lists
                if hasattr(emb, "tolist"):
                    embeddings_acc.extend(emb.tolist())
                else:
                    for _vec in emb:
                        embeddings_acc.append(
                            _vec.tolist() if hasattr(_vec, "tolist") else list(_vec) if _vec is not None else None
                        )
            else:
                embeddings_acc.extend([None] * len(batch_ids))
            offset += len(batch_ids)
            if len(batch_ids) < batch_size:
                break

        has_embeddings = False
        for _e in embeddings_acc:
            if _e is not None:
                has_embeddings = True
                break
        payload = {
            "ids": ids_acc,
            "documents": documents_acc,
            "metadatas": metadatas_acc,
            "embeddings": embeddings_acc if has_embeddings else None,
        }

    timestamp = datetime.utcnow().strftime("%Y%m%d_%H%M%S")
    filename = f"files_{timestamp}.json"
    path = BACKUPS_DIR / filename
    with open(path, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=0)
    return f"backups/{filename}"


def restore_files_collection(backup_rel_path: str) -> None:
    """
    Восстанавливает коллекцию 'files' из JSON-файла бэкапа.
    backup_rel_path — путь относительно DATA_PATH (например, backups/files_20250210_123456.json).
    """
    from langchain_core.documents import Document
    import rag

    full_path = config.DATA_PATH / backup_rel_path
    if not full_path.is_file():
        raise FileNotFoundError(f"Файл бэкапа не найден: {full_path}")
    with open(full_path, "r", encoding="utf-8") as f:
        payload = json.load(f)
    ids = payload.get("ids") or []
    documents = payload.get("documents") or []
    metadatas = payload.get("metadatas") or []
    embeddings = payload.get("embeddings")

    rag.delete_files_collection()
    clear_files_store_cache()

    if not documents and not ids:
        return

    logging.info(f"Starting restoration of {len(documents)} documents from {backup_rel_path}")

    if embeddings and len(embeddings) == len(ids):
        import chromadb
        with rag.chroma_lock("files"):
            client = chromadb.PersistentClient(path=str(config.CHROMA_PATH))
            coll = client.get_or_create_collection("files")
            # Для больших объемов с эмбеддингами Chroma может переварить всё сразу, 
            # но для надежности тоже можно батчами (Chroma рекомендует ~5000)
            batch_size = 5000
            for i in range(0, len(ids), batch_size):
                end = min(i + batch_size, len(ids))
                coll.add(
                    ids=ids[i:end],
                    documents=documents[i:end],
                    metadatas=metadatas[i:end],
                    embeddings=embeddings[i:end]
                )
                logging.info(f"Restored {end}/{len(ids)} documents (with embeddings)")
    else:
        # Если эмбеддингов нет, они будут пересчитаны через OpenAI
        # Используем маленькие батчи, чтобы не падать по таймауту и видеть прогресс
        batch_size = 500 
        store = _get_files_store()
        for i in range(0, len(documents), batch_size):
            end = min(i + batch_size, len(documents))
            batch_docs = []
            batch_ids = ids[i:end] if (ids and len(ids) == len(documents)) else None
            
            for j in range(i, end):
                meta = metadatas[j] if j < len(metadatas) else {}
                batch_docs.append(Document(page_content=documents[j], metadata=meta or {}))
            
            with rag.chroma_lock("files"):
                if batch_ids:
                    store.add_documents(documents=batch_docs, ids=batch_ids)
                else:
                    store.add_documents(documents=batch_docs)
            
            logging.info(f"Restored {end}/{len(documents)} documents (re-calculating embeddings...)")
    
    logging.info("Restoration completed successfully.")
