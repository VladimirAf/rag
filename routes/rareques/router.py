from fastapi import APIRouter, HTTPException, Form
import traceback
import logging
from . import crud

rarequests_router = APIRouter(prefix="/rareques")


@rarequests_router.post(
    "/save_response",
    description="Сохраняет ответ (ans) в поле response для записи с указанным id"
)
async def save_response(
    id: int = Form(...),
    ans: str = Form(...)
):
    try:
        crud.save_response(id=id, ans=ans)
    except Exception as e:
        logging.error(traceback.print_exc())
        raise HTTPException(200, f"{type(e).__name__}: {e}")
    return dict(success=True)

