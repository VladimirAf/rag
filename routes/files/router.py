from fastapi import APIRouter, HTTPException, UploadFile, File, Form, Query
from typing import Optional, Dict, List, Literal
import traceback
import logging
from . import crud
from models import SearchResults
import config
import json
from io import BytesIO
import rag

try:
    from chromadb.errors import InternalError as ChromaInternalError
except ImportError:
    ChromaInternalError = None

files_router = APIRouter(prefix="/files")


@files_router.post(
    "/add",
    description=(
        "Загрузка файла в коллекцию files (multipart: file + metadata JSON). "
        "Повторная загрузка с тем же содержимым (file_hash): существующие чанки удаляются и индексируются заново (upsert). "
        "Имя файла сохраняется в metadata.filename каждого чанка (UploadFile.filename). "
        "Для баз знаний tm (source=tm_knowledge_base): data_type=files обязателен; "
        "рекомендуемый префикс имени knowledge_base_kb{kb_id}_file{kb_file_id}_v{version}_{original}. "
        "Metadata KB: kb_id, kb_file_id, kb_name, kb_categories, kb_product_titles, kb_model_skus, "
        "kb_file_original_name, kb_file_version, kb_file_type; опционально ai_file_type, ai_scenario, ai_role."
    ),
)
async def add(
    file: UploadFile = File(...),
    metadata: Optional[str] = Form(None)
):
    try:
        metadata_dict = json.loads(metadata) if metadata else {}
        file_io = BytesIO(await file.read())
        file_io.name = file.filename

        crud.add_file(file=file_io, metadata=metadata_dict)
    except Exception as e:
        logging.exception("files add failed")
        raise HTTPException(200, f"{type(e).__name__}: {e}")
    return dict(success=True)


@files_router.post(
    "/addtem",
    description="Добавление текстового файла; metadata должен быть в формате JSON строка",
)
async def add_text(
    text: str = Form(...),
    metadata: Optional[str] = Form(None),
):
    try:
        metadata_dict = json.loads(metadata) if metadata else {}
        crud.add_file_text(text=text, metadata=metadata_dict)
    except Exception as e:
        logging.exception("addtem failed")
        if ChromaInternalError is not None and isinstance(e, ChromaInternalError):
            raise HTTPException(
                503,
                detail="Ошибка хранилища векторов (ChromaDB). Попробуйте позже или сбросьте коллекцию.",
            )
        raise HTTPException(200, f"{type(e).__name__}: {e}")
    return dict(success=True)


@files_router.get(
    "/search"
)
async def search(
    query: str,
    k: int = config.RAG_FILES_SEARCH_RESULTS_AMOUNT,
    score_threshold: float = config.RAG_SCORE_threshold,
    data_type: Optional[Literal["recipe", "seocrm_article", "files"]] = Query(
        None,
        description="Фильтр по типу данных в хранилище (data_type)",
    )
) -> SearchResults:
    try:
        return crud.search(query=query, k=k, score_threshold=score_threshold, data_type=data_type)
    except Exception as e:
        logging.exception("files search failed")
        raise HTTPException(200, f"{type(e).__name__}: {e}")


@files_router.get(
    "/search_staged",
    description="Пошаговый поиск с ранним выходом (data_type priority: files -> seocrm_article -> recipe)"
)
async def search_staged(
    query: str,
    k: int = config.RAG_FILES_SEARCH_RESULTS_AMOUNT,
    score_threshold: float = config.RAG_SCORE_threshold,
    model_name: Optional[str] = None
) -> SearchResults:
    try:
        return crud.search_staged(query=query, k=k, score_threshold=score_threshold, model_name=model_name)
    except Exception as e:
        logging.exception("files search_staged failed")
        raise HTTPException(200, f"{type(e).__name__}: {e}")


@files_router.get("/list_all")
async def list_all(
    data_type: Optional[Literal["recipe", "seocrm_article", "files"]] = Query(
        None,
        description="Фильтр по типу данных в хранилище (data_type)",
    )
) -> List:
    try:
        return crud.list_all(data_type=data_type)
    except Exception as e:
        logging.exception("files list_all failed")
        if ChromaInternalError is not None and isinstance(e, ChromaInternalError):
            raise HTTPException(
                503,
                detail="Ошибка хранилища векторов (ChromaDB). Вызовите DELETE /files/delete-all или восстановите из бэкапа.",
            )
        raise HTTPException(200, f"{type(e).__name__}: {e}")


@files_router.delete("/delete")
async def delete_file(metadata: Dict):
    try:
        crud.delete_file(**metadata)
    except Exception as e:
        logging.exception("files delete failed")
        raise HTTPException(200, f"{type(e).__name__}: {e}")
    return dict(success=True)


@files_router.delete("/delete-all")
async def delete_all():
    """
    Удаляет коллекцию 'files' в ChromaDB и сбрасывает кэш store.
    Использовать при повреждении коллекции; после этого коллекция создаётся заново при следующем обращении.
    """
    try:
        rag.delete_files_collection()
        crud.clear_files_store_cache()
    except Exception as e:
        logging.exception("files delete-all failed")
        raise HTTPException(200, f"{type(e).__name__}: {e}")
    return dict(success=True)


@files_router.post(
    "/backup",
    description="Создаёт резервную копию коллекции files (JSON с ids, documents, metadatas, embeddings).",
)
async def backup():
    try:
        rel_path = crud.backup_files_collection()
    except Exception as e:
        logging.exception("files backup failed")
        if ChromaInternalError is not None and isinstance(e, ChromaInternalError):
            raise HTTPException(
                503,
                detail="Ошибка хранилища векторов (ChromaDB) при создании бэкапа. Попробуйте сбросить коллекцию или повторить позже.",
            )
        raise HTTPException(200, f"{type(e).__name__}: {e}")
    return dict(success=True, path=rel_path)


@files_router.post(
    "/restore",
    description="Восстанавливает коллекцию files из бэкапа. body: {\"path\": \"backups/files_YYYYMMDD_HHMMSS.json\"}",
)
async def restore(body: Dict):
    path = body.get("path")
    if not path:
        raise HTTPException(400, detail="Укажите path в теле запроса (например, backups/files_20250210_123456.json)")
    try:
        crud.restore_files_collection(path)
    except FileNotFoundError as e:
        raise HTTPException(404, detail=str(e))
    except Exception as e:
        logging.exception("files restore failed")
        raise HTTPException(200, f"{type(e).__name__}: {e}")
    return dict(success=True)
