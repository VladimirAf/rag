from fastapi import APIRouter, HTTPException
from models import Product
from typing import Optional, Dict, List, Any
import routes.products.crud as crud
import traceback
import logging
import core
from models import Message

products_router = APIRouter(prefix="/products")


@products_router.get("/search")
async def search(
    query: str
):
    try:
        return core.find_products(query)
    except Exception as e:
        logging.error(traceback.print_exc())
        raise HTTPException(200, f"{type(e).__name__}: {e}")
    

@products_router.post(
        "/create",
        description="""
        Добавление записи товара в базу данных
        Либо используя json схему Product
        Описание названия ключей:
        name - название продукта
        price - цена товара в рублях
        url - ссылка на товар
        description - описание товара
        specs - технические характеристики
        model - модель товара (необязательно)
        manufacturer_id - ID производителя из справочника manufacturer (по умолчанию 46)
        product_id - ID товара на витрине магазина (необязательно)
        """
        )
async def create(
    product: Product
):
    try:
        crud.create_product(
            product=product,
        )
    except Exception as e:
        logging.error(traceback.print_exc())
        raise HTTPException(200, f"{type(e).__name__}: {e}")
    return dict(success=True)


@products_router.post(
        "/edit",
        description="""
        model - Модель товара
        data - Для редактирования здесь так же используется json схема Product, параметры Product опциональны.
        manufacturer_id - можно сменить производителя (например 24 = JTC)
        """
        )
async def edit_product(
    model: str,
    data: Dict[str, Any]
):
    try:
        crud.edit_product(
            model=model,
            data=data
        )
    except Exception as e:
        logging.error(traceback.print_exc())
        raise HTTPException(200, f"{type(e).__name__}: {e}")
    return dict(success=True)


@products_router.delete(
        "/delete",
        description="""
        model - Модель товара
        """
        )
async def delete_product(
    model: str
):
    try:
        crud.delete_product(model=model)
    except Exception as e:
        logging.error(traceback.print_exc())
        raise HTTPException(200, f"{type(e).__name__}: {e}")
    return dict(success=True)
    

@products_router.get(
        "/get",
        description="""
        Получить запись товара
        name - Название товара
        """
        )
async def get_product(
    name: str
) -> Optional[List[Dict[str, str]]]:
    try:
        return [product.__dict__ for product in crud.get_product(name=name)]
    except Exception as e:
        logging.error(traceback.print_exc())
        raise HTTPException(200, f"{type(e).__name__}: {e}")
    

@products_router.get("/list_all")
async def list_all() -> List:
    try:
        return [product.__dict__ for product in crud.list_all()]
    except Exception as e:
        logging.error(traceback.print_exc())
        raise HTTPException(200, f"{type(e).__name__}: {e}")