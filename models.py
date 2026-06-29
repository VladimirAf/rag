from dataclasses import dataclass
import sqlite3
from typing import Literal, List, Dict, Any, Final, Optional
import json
from pydantic import BaseModel, Field, field_validator
import config


from manufacturer_constants import RAWMID_MANUFACTURER_ID

PRODUCT_COLS = [
    "name", "price", "url", "description", "specs", "category", "model",
    "status", "quantity", "manufacturer_id", "product_id",
]

# Результат LLM-классификатора интента (intent routing)
UNDEFINED_INTENT_LABEL: Final[str] = "undefined"


class IntentClassifierOutput(BaseModel):
    """Итог классификации: label из конфига или `undefined`; confidence — число модели в [0, 1]."""

    intent_label: str
    confidence: float = Field(ge=0.0, le=1.0)
    reason: str
    predicted_intent_text: Optional[str] = None

    @field_validator("confidence", mode="before")
    @classmethod
    def _coerce_confidence(cls, v: Any) -> float:
        try:
            x = float(v)
        except (TypeError, ValueError):
            return 0.0
        return max(0.0, min(1.0, x))


DB_PATH = config.DATABASE_PATH


@dataclass
class Product:
    name: str
    price: str
    url: str
    description: str
    specs: str
    category: Optional[str] = None
    model: Optional[str] = None
    status: Optional[int] = None
    quantity: Optional[int] = None
    manufacturer_id: Optional[int] = RAWMID_MANUFACTURER_ID
    product_id: Optional[int] = None

    @classmethod
    def from_record(cls, row):
        # Создаем словарь, сопоставляя поля из PRODUCT_COLS со значениями из row
        # Если в row меньше полей, чем в PRODUCT_COLS, недостающие поля будут None
        row_dict = dict(zip(PRODUCT_COLS, row)) if len(row) >= len(PRODUCT_COLS) else dict(zip(PRODUCT_COLS[:len(row)], row))
        # Убеждаемся, что все поля из PRODUCT_COLS присутствуют
        for col in PRODUCT_COLS:
            if col not in row_dict:
                row_dict[col] = None
        return cls(**row_dict)

    @classmethod
    def from_dict(cls, data):
        return cls(**data)
    
    def __repr__(self):
        return f'<Product name="{self.name} price="{self.price}" category="{self.category}">'

    def json(self):
        return json.dumps({key: value for key, value in self.__dict__.items()}, ensure_ascii=False)


class Database:
    def __init__(self, path: str=DB_PATH):
        self.path = path

    def __enter__(self):
        self.conn = sqlite3.connect(self.path)
        # Регистрируем функцию LOWER для поддержки кириллицы
        self.conn.create_function("LOWER", 1, lambda s: s.lower() if s else s)
        self.cursor = self.conn.cursor()
        return self
        
    def __exit__(self, *something):
        self.conn.close()


@dataclass
class Message:
    role: Literal["system", "user", "ai", "context"]
    content: str

    def json(self):
        return dict(role=self.role, content=self.content)
    

class SearchResults(BaseModel):
    chunks: Optional[List[str]]
    documents: Optional[List[str]]
    # Для тикетов: список ticket_id; для файлов: список file_id
    ticket_ids: Optional[List[str]] = None
    file_ids: Optional[List[str]] = None
    # Для файлов: список имён файлов
    filenames: Optional[List[str]] = None
    # search_staged: арбитр признал контент достаточным — find_context может не идти в products/tickets
    files_staged_sufficient: Optional[bool] = None


class AskResponse(BaseModel):
    response_text: str
    enough_information: bool
    need_assistance: bool


class QueryParseResults(BaseModel):
    product_names: Optional[List[str]]
    categories: Optional[List[str]]
    other_products: Optional[bool] = None


class ProductSearchResult(BaseModel):
    products: List[Product]
    parse_results: QueryParseResults


# Правила faithfulness по SKU (блок в prettify; синхронизировать с OpenWebUI pipeline при правках).
CITATION_FAITHFULNESS_RULES: Final[str] = """
Запрещено придумывать другие источники. Используй только перечисленные в «ДОСТУПНЫЕ ИСТОЧНИКИ» ниже.

ЗАПРЕЩЕНО в response_text:
- указывать в цитатах [source][product][...] артикулы, которых нет в списке products;
- упоминать в тексте (с цитатой или без) артикулы, SKU и конкретные названия моделей, для которых нет карточки в блоке «Товары» и нет тега [source][product][model] в «ДОСТУПНЫЕ ИСТОЧНИКИ»;
- сравнивать, хвалить или критиковать модель, данных о которой нет в контексте.

Если не хватает карточки запрошенной модели — скажи, что в переданном контексте нет данных; enough_information=false; не называй отсутствующий артикул.
"""


class RelatedContext(BaseModel):
    # В Pydantic v2 аннотация Optional[...] без дефолта всё равно означает "поле обязательное".
    # Здесь нам важно, чтобы на ранних стадиях пайплайна можно было возвращать частичный контекст.
    products: Optional[List[Product]] = None
    tickets: Optional[SearchResults] = None
    files: Optional[SearchResults] = None
    parse_results: Optional[QueryParseResults] = None
    rarequest_id: Optional[int] = None

    def prettify(
            self,
            docs_sep: str = "\n\n--------------------\n\n",
            no_info_text: str = "-- Нет релевантной информации --"
        ) -> str:
        # Блок 1. Товары
        if self.products:
            # Добавляем источник перед каждым товаром
            products_with_sources = []
            for product in self.products:
                product_json = product.json()
                if getattr(product, "model", None):
                    source_tag = f"[source][product][{product.model}]:"
                    products_with_sources.append(f"{source_tag}{product_json}")
                else:
                    products_with_sources.append(product_json)
            products_block = docs_sep.join(products_with_sources)
            product_sources_list = [
                f"[source][product][{product.model}]"
                for product in self.products
                if getattr(product, "model", None)
            ]
            product_sources = "\n".join(product_sources_list) if product_sources_list else ""
        else:
            products_block = no_info_text
            product_sources = ""

        # Блок 2. Тикеты
        if self.tickets and self.tickets.documents:
            ticket_ids = getattr(self.tickets, "ticket_ids", None) or []
            # Добавляем источник перед каждым документом тикета
            tickets_with_sources = []
            for i, document in enumerate(self.tickets.documents):
                if i < len(ticket_ids) and ticket_ids[i]:
                    source_tag = f"[source][ticket][{ticket_ids[i]}]:"
                    tickets_with_sources.append(f"{source_tag}{document}")
                else:
                    tickets_with_sources.append(document)
            tickets_block = docs_sep.join(tickets_with_sources)
            ticket_sources_list = [
                f"[source][ticket][{ticket_id}]"
                for ticket_id in ticket_ids
            ]
            ticket_sources = "\n".join(ticket_sources_list) if ticket_sources_list else ""
        else:
            tickets_block = no_info_text
            ticket_sources = ""

        # Блок 3. Файлы
        if self.files and self.files.documents:
            filenames = getattr(self.files, "filenames", None) or []
            # Добавляем источник перед каждым документом файла
            files_with_sources = []
            for i, document in enumerate(self.files.documents):
                if i < len(filenames) and filenames[i]:
                    source_tag = f"[source][file][{filenames[i]}]:"
                    files_with_sources.append(f"{source_tag}{document}")
                else:
                    files_with_sources.append(document)
            files_block = docs_sep.join(files_with_sources)
            file_sources_list = [
                f"[source][file][{filename}]"
                for filename in filenames
            ]
            file_sources = "\n".join(file_sources_list) if file_sources_list else ""
        else:
            files_block = no_info_text
            file_sources = ""

        products_line = product_sources if product_sources else "нет"
        tickets_line = ticket_sources if ticket_sources else "нет"
        files_line = file_sources if file_sources else "нет"

        text = f"""
        ДОСТУПНЫЕ ИСТОЧНИКИ ДЛЯ ЦИТИРОВАНИЯ (только они разрешены в этом запросе; не весь каталог и не «все в наличии»):
        - products:
        {products_line}
        - tickets:
        {tickets_line}
        - files:
        {files_line}
        - prompt: [source][prompt]
        {CITATION_FAITHFULNESS_RULES}

        Товары, которые могут иметь отношение к запросу пользователя:
        {products_block}

        Похожие обращения ДРУГИХ пользователей
        {tickets_block}

        Похожие файлы из базы знаний
        {files_block}
        """
        return text