from fastapi import FastAPI, APIRouter, HTTPException, Depends, Security, Body
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from typing import Optional, Dict, Any
from pydantic import BaseModel, model_validator
import core
from models import Message, AskResponse, RelatedContext


class FindContextRequestBody(BaseModel):
    """Тело POST /find_context: стадии LLM и опционально лог переписки для rarequests."""

    llm_model_stages: Optional[Dict[str, str]] = None
    conversation_log: Optional[str] = None
    display_query: Optional[str] = None

    @model_validator(mode="before")
    @classmethod
    def legacy_body_was_plain_stages_dict(cls, data: Any):
        if data is None:
            return {}
        if isinstance(data, dict):
            meta_keys = {"llm_model_stages", "conversation_log", "display_query"}
            if data.keys() and set(data.keys()).isdisjoint(meta_keys):
                return {
                    "llm_model_stages": dict(data),
                    "conversation_log": None,
                    "display_query": None,
                }
        return data
from routes.products.router import products_router
from routes.tickets.router import tickets_router
from routes.files.router import files_router
from routes.rareques.router import rarequests_router
from logger import setup_logging
import logging
import traceback
import config
from intent_routes_loader import ensure_intent_routes_loaded


ADVISORS_RAG_DOCS = """
Advisors 2.0 direct RAG contract.

Current V2 path: advisor_chat -> SearchRagTool -> POST /find_context.
OpenWebUI rag_tool path is kept as compatibility/candidate path while V2 gateway behavior is investigated.

Admin mode.rag_config mapping:
- CONFIG:PRODUCTS_SEARCH_ENABLED=true|false -> products_search_enabled
- CONFIG:TICKETS_SEARCH_ENABLED=true|false -> tickets_search_amount=0 when false
- CONFIG:TICKETS_SEARCH_AMOUNT=<int> -> tickets_search_amount
- CONFIG:TICKETS_SEARCH_THRESHOLD=<float> -> tickets_search_threshold
- CONFIG:FILES_SEARCH_ENABLED=true|false -> files_search_amount=0 when false
- CONFIG:FILES_SEARCH_AMOUNT=<int> -> files_search_amount
- CONFIG:FILES_SEARCH_THRESHOLD=<float> -> files_search_threshold
- CONFIG:PRODUCTS_SEARCH_AMOUNT, CONFIG:PRODUCTS_SEARCH_THRESHOLD are bounded/normalized by SearchRagTool until /find_context exposes native product limits.

The source query parameter must identify caller, advisor mode, and source family
""".strip()

app = FastAPI(
    title=" RAG API",
    description=ADVISORS_RAG_DOCS,
)


setup_logging(
    log_dir=config.LOGS_PATH,
)

ensure_intent_routes_loaded()

# Настройка Bearer авторизации
security = HTTPBearer()


async def verify_token(credentials: HTTPAuthorizationCredentials = Security(security)):
    """
    Проверяет Bearer токен из заголовка Authorization
    """
    if not config.BEARER_TOKEN:
        raise HTTPException(
            status_code=500,
            detail="Bearer токен не настроен на сервере"
        )
    
    if credentials.credentials != config.BEARER_TOKEN:
        raise HTTPException(
            status_code=401,
            detail="Неверный Bearer токен",
            headers={"WWW-Authenticate": "Bearer"},
        )
    return credentials.credentials


@app.post(
        "/ask",
        description="""
        Вызов LLM модели с добавлением контекста по товарам
        query - переписка с пользователем
        prompt - системный промпт
        source - источник запроса (например, ticket_id_28 или product_id_77)
        llm_model_stages - словарь с моделями для разных стадий обработки:
            - ner: модель для извлечения названий продуктов (по умолчанию: google/gemini-2.0-flash-001)
            - related_products: модель для поиска связанных продуктов (по умолчанию: google/gemini-2.0-flash-001)
            - summarization: модель для суммаризации запросов (по умолчанию: google/gemini-2.0-flash-001)
            - final_answer: модель для генерации финального ответа (по умолчанию: x-ai/grok-4.3)
        """
    )
async def ask(
    query: str,
    prompt: str = config.DEFAULT_SYSTEM_PROMPT,
    tickets_search_amount: int = config.RAG_TICKETS_SEARCH_RESULTS_AMOUNT,
    tickets_search_threshold: float = config.RAG_SCORE_threshold,
    files_search_amount: int = config.RAG_FILES_SEARCH_RESULTS_AMOUNT,
    files_search_threshold: float = config.RAG_SCORE_threshold,
    products_search_enabled: bool = True,
    source: Optional[str] = 'undefined source',
    debug: bool = False,
    llm_model_stages: Optional[Dict[str, str]] = Body(None),
    token: str = Depends(verify_token)
) -> AskResponse:
    try:
        response = core.ask(
            query,
            prompt=prompt,
            tickets_search_amount=tickets_search_amount,
            tickets_search_threshold=tickets_search_threshold,
            files_search_amount=files_search_amount,
            files_search_threshold=files_search_threshold,
            products_search_enabled=products_search_enabled,
            source=source,
            debug=debug,
            llm_model_stages=llm_model_stages
            )
        logging.info(response)
        if response is None:
            raise Exception("Пустой ответ от core.ask, llm модель не смогла сформировать ответ в нужном формате.")

        return AskResponse(**response)
    except Exception as e:
        logging.error(" ".join([type(e).__name__, str(e)]))
        logging.error(traceback.print_exc())
        raise HTTPException(200, f"{type(e).__name__}: {e}")


@app.post("/find_context", description="""
    Найти релевантный контекст по запросу.

    Advisors 2.0 direct RAG contract (когда RAG вызывается не через OpenWebUI):
    - query: текст запроса пользователя, обязателен;
    - prompt: режим поиска, обычно `запрос только на контекст`;
    - source: строка источника вызова, например `advisor:rawmid-consultant:service_support:tickets`;
    - products_search_enabled: включает поиск по товарам;
    - tickets_search_amount / tickets_search_threshold: лимит и порог поиска по обращениям;
    - files_search_amount / files_search_threshold: лимит и порог поиска по файлам/статьям/рецептам.

    Маппинг admin `mode.rag_config` для Advisors 2.0:
    - CONFIG:PRODUCTS_SEARCH_ENABLED=true|false -> products_search_enabled;
    - CONFIG:PRODUCTS_SEARCH_AMOUNT=<int> -> лимит товаров после нормализации в SearchRagTool;
    - CONFIG:TICKETS_SEARCH_ENABLED=true|false -> tickets_search_amount=0 при false;
    - CONFIG:TICKETS_SEARCH_AMOUNT=<int> -> tickets_search_amount;
    - CONFIG:TICKETS_SEARCH_THRESHOLD=<float> -> tickets_search_threshold;
    - CONFIG:FILES_SEARCH_ENABLED=true|false -> files_search_amount=0 при false;
    - CONFIG:FILES_SEARCH_AMOUNT=<int> -> files_search_amount;
    - CONFIG:FILES_SEARCH_THRESHOLD=<float> -> files_search_threshold.

    Сейчас SearchRagTool вызывает этот endpoint query-параметрами, без JSON body. JSON body используется только для llm_model_stages/conversation_log/display_query.

    llm_model_stages - словарь с моделями для разных стадий обработки:
        - ner: модель для извлечения названий продуктов (по умолчанию: google/gemini-2.0-flash-001)
        - related_products: модель для поиска связанных продуктов (по умолчанию: google/gemini-2.0-flash-001)
        - summarization: модель для суммаризации запросов (по умолчанию: google/gemini-2.0-flash-001)
""")
async def find_context(
    query: str,
    prompt: str = 'запрос только на контекст',
    tickets_search_amount: int = config.RAG_TICKETS_SEARCH_RESULTS_AMOUNT,
    tickets_search_threshold: float = config.RAG_SCORE_threshold,
    files_search_amount: int = config.RAG_FILES_SEARCH_RESULTS_AMOUNT,
    files_search_threshold: float = config.RAG_SCORE_threshold,
    products_search_enabled: bool = True,
    source: Optional[str] = 'undefined source',
    body: Optional[FindContextRequestBody] = Body(None),
    token: str = Depends(verify_token)
) -> RelatedContext:
    try:
        payload = body or FindContextRequestBody()
        llm_model_stages = payload.llm_model_stages
        # core.find_context теперь возвращает кортеж (RelatedContext, rarequest_id)
        response, rarequest_id = core.find_context(
            query,
            prompt=prompt,
            tickets_search_amount=tickets_search_amount,
            tickets_search_threshold=tickets_search_threshold,
            files_search_amount=files_search_amount,
            files_search_threshold=files_search_threshold,
            products_search_enabled=products_search_enabled,
            source=source,
            llm_model_stages=llm_model_stages,
            rarequest_display_query=payload.display_query,
            rarequest_prompt_override=payload.conversation_log,
        )
        logging.info(response)
        # Добавляем rarequest_id в ответ
        response.rarequest_id = rarequest_id
        return response
    except Exception as e:
        logging.error(" ".join([type(e).__name__, str(e)]))
        logging.error(traceback.print_exc())
        raise HTTPException(200, f"{type(e).__name__}: {e}")
    

@app.get("/health", description="Проверка работоспособности сервиса")
async def health_check(token: str = Depends(verify_token)):
    return {"status": "ok"}


app.include_router(products_router, tags=["products"], dependencies=[Depends(verify_token)])
app.include_router(tickets_router, tags=["tickets"], dependencies=[Depends(verify_token)])
app.include_router(files_router, tags=["files"], dependencies=[Depends(verify_token)])
app.include_router(rarequests_router, tags=["rareques"], dependencies=[Depends(verify_token)])
