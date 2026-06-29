from fastapi import APIRouter, HTTPException, Form
from typing import Optional, Dict, List
import traceback
import logging
from . import crud
from models import SearchResults
import config
import json

tickets_router = APIRouter(prefix="/tickets")


@tickets_router.post(
    "/add",
    description="metadata должен быть в формате JSON строка"
)
async def add(
    text: str,
    metadata: Optional[str] = Form(None)
):
    try:
        metadata_dict = json.loads(metadata) if metadata else {}
        crud.add_ticket(text=text, metadata=metadata_dict)
    except Exception as e:
        logging.error(traceback.print_exc())
        raise HTTPException(200, f"{type(e).__name__}: {e}")
    return dict(success=True)


@tickets_router.get(
    "/search"
)
async def search(
    query: str,
    k: int = config.RAG_TICKETS_SEARCH_RESULTS_AMOUNT,
    score_threshold: float = config.RAG_SCORE_threshold
) -> SearchResults:
    try:
        return crud.search(query=query, k=k, score_threshold=score_threshold)
    except Exception as e:
        logging.error(traceback.print_exc())
        raise HTTPException(200, f"{type(e).__name__}: {e}")
    

@tickets_router.get("/list_all")
async def list_all() -> List:
    try:
        return crud.list_all()
    except Exception as e:
        logging.error(traceback.print_exc())
        raise HTTPException(200, f"{type(e).__name__}: {e}")
    

@tickets_router.delete("/delete")
async def delete_ticket(metadata: Dict):
    try:
        crud.delete_ticket(**metadata)
    except Exception as e:
        logging.error(traceback.print_exc())
        raise HTTPException(200, f"{type(e).__name__}: {e}")
    return dict(success=True)


@tickets_router.delete("/delete-all")
async def delete_all():
    try:
        crud.reset_collection()
    except Exception as e:
        logging.error(traceback.print_exc())
        raise HTTPException(200, f"{type(e).__name__}: {e}")
    return dict(success=True)