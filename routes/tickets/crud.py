from langchain_unstructured import UnstructuredLoader
from rag import get_vector_store, text_splitter, fix_metadata_value_types
from models import SearchResults
from io import StringIO, BytesIO
from uuid import uuid4
from pydantic import BaseModel
from typing import List, Optional, Dict, Any
import config
import llm_funcs
import logging


vector_store = get_vector_store("tickets")


def add_ticket(
        text: str,
        metadata: Optional[Dict] = None
        ):
    if not text:
        raise ValueError("Нельзя загрузить пустой тикет")
    
    # Ставим нижний регистр, чтобы в дальнейшем более точно находить документы
    # которые содержат название товара
    text_lower = text.lower()
    ticket_id = str(uuid4())
    file = BytesIO(text_lower.encode("utf-8"))
    file.name = f"{ticket_id}.txt"
    loaded_documents = UnstructuredLoader(file=file, metadata_filename=file.name).load()
    for doc in loaded_documents:
        doc.metadata["text_title"] = text.split("\n")[0][:100]
        doc.metadata["ticket_id"] = ticket_id
        if metadata:
            doc.metadata.update(metadata)
        doc.metadata = fix_metadata_value_types(doc.metadata)

    splitted_documents = text_splitter.split_documents(loaded_documents)
    vector_store.add_documents(documents=splitted_documents)


def search(
       query: str,
       k: int = config.RAG_TICKETS_SEARCH_RESULTS_AMOUNT,
       score_threshold: float = config.RAG_SCORE_threshold,
       contains_text: Optional[str] = None,
       model_name: Optional[str] = None
) -> SearchResults:
    query = llm_funcs.summarize_user_query(query, model_name=model_name)

    where_documents_filter = None
    if contains_text:
        where_documents_filter = {"$contains": contains_text.lower()}

    docs = vector_store.similarity_search_with_score(
        query=query,
        k=k,
        where_document=where_documents_filter
        )

    def _to_similarity(distance: float) -> float:
        try:
            d = float(distance)
        except Exception:
            return 0.0
        return 1.0 / (1.0 + d)

    sorted_by_distance = sorted(docs, key=lambda x: x[1])
    # Строгая фильтрация по порогу похожести
    filtered = [(doc, dist) for doc, dist in sorted_by_distance if _to_similarity(dist) >= score_threshold]

    chunks = [doc for doc, _dist in filtered]
    chunks_text = [chunk.page_content for chunk in chunks]
    ticket_ids = list(set([str(chunk.metadata["ticket_id"]) for chunk in chunks]))
    if ticket_ids:
        documents: List[str] = list()
        valid_ticket_ids: List[str] = list()
        for ticket_id in ticket_ids:
            try:
                result = vector_store.get(where={"ticket_id": {"$in": [ticket_id]}})
                ticket_paragraphs = result.get("documents", [])
                if not ticket_paragraphs:
                    # Проверяем, может быть ticket_id имеет другой тип или формат
                    # Пробуем найти по разным вариантам
                    result_str = vector_store.get(where={"ticket_id": {"$eq": str(ticket_id)}})
                    result_int = None
                    try:
                        result_int = vector_store.get(where={"ticket_id": {"$eq": int(ticket_id)}})
                    except (ValueError, TypeError):
                        pass
                    
                    if result_str.get("documents"):
                        ticket_paragraphs = result_str.get("documents", [])
                    elif result_int and result_int.get("documents"):
                        ticket_paragraphs = result_int.get("documents", [])
                    
                    if not ticket_paragraphs:
                        logging.warning(f"Тикет {ticket_id} (тип: {type(ticket_id).__name__}) найден в результатах поиска, но его содержимое пустое. Найдено chunks: {len(result.get('ids', []))}. Возможно, тикет был удален или поврежден.")
                        # Пропускаем пустые тикеты, чтобы не добавлять пустые строки в documents
                        continue
                documents.append(" ".join(ticket_paragraphs))
                valid_ticket_ids.append(ticket_id)
            except Exception as e:
                logging.error(f"Ошибка при получении содержимого тикета {ticket_id} (тип: {type(ticket_id).__name__}): {e}")
                # Пропускаем тикет с ошибкой
                continue
        # Обновляем ticket_ids, чтобы они соответствовали documents
        ticket_ids = valid_ticket_ids if valid_ticket_ids else None
    else:
        documents = None
    return SearchResults(
        chunks=chunks_text,
        documents=documents,
        ticket_ids=ticket_ids if ticket_ids else None
    )


def list_all() -> List:
    chunks = vector_store.get(include=["metadatas"])
    chunks_metadata = chunks["metadatas"]
    added_ids: List[str] = list()
    docs: List[Dict[str, Any]] = list()
    for chunk in chunks_metadata:
        doc = dict()
        ticket_id = chunk["ticket_id"]
        if ticket_id in added_ids:
            continue

        doc["ticket_id"] = ticket_id
        doc["text_title"] = chunk["text_title"]
        docs.append(doc)
        added_ids.append(ticket_id)
    return docs


def delete_ticket(
        **metadata
):
    vector_store.delete(where=metadata)


def reset_collection():
    vector_store.reset_collection()