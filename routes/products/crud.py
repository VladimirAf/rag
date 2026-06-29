from models import Product, Message, Database, RAWMID_MANUFACTURER_ID
from typing import Optional, Dict, List
import re
from llm_funcs import invoke


def parse_text_to_json(
    text,
    keys_map: Dict[str, str]
    ):
    # Шаблон для извлечения секций вида [Любое название] ... текст ...
    pattern = r'\[([^\]]+)\]\n(.+?)(?=\n\n\[|\Z)'
    
    # Результат в виде словаря
    result = {}
    
    # Поиск всех секций
    matches = re.findall(pattern, text, re.DOTALL)
    for key, value in matches:
        # Приведение ключа к нижнему регистру и замена пробелов на подчеркивания
        key = keys_map[key]
        # Удаление лишних пробелов и переносов строк из значения
        value = value.strip()
        result[key] = value
    return result


def get_category(name):
    category = invoke([
            Message(
                role="system", 
                content="Extract category of this product by its name in one word in Russian (the category strictly should be taken from the product name, if it's possible, it SHOULD NOT BE a general category like 'electronics' or 'accessory')"
            ),
            Message(
                role="user",
                content=name
            )
        ])
    return category

    
def parse_product(
    text: str,
    category: Optional[str] = None,
    keys_map: Dict[str, str] = {
        "Название товара": "name",
        "Цена": "price",
        "Ссылка": "url",
        "Описание": "description",
        "Характеристики": "specs",
        "Модель": "model"
    }
) -> Product:
    product_dict = parse_text_to_json(
        text,
        keys_map=keys_map
        )
    if not category:
        category = get_category(product_dict["name"])
    product_dict["category"] = category
    return Product(**product_dict)


def create_product(
    product: Optional[Product] = None,
    product_text: Optional[str] = None
):
    if (product is None and product_text is None) or (product is not None and product_text is not None):
        raise TypeError("Одна из product и product_text должна быть заполнена")
    
    if product_text is not None:
        product = parse_product(product_text)
    
    if product.category is None:
        product.category = get_category(product.name)

    if product.manufacturer_id is None:
        product.manufacturer_id = RAWMID_MANUFACTURER_ID

    with Database() as db:
        product_dict = product.__dict__
        # Фильтруем None значения для опциональных полей
        filtered_dict = {k: v for k, v in product_dict.items() if v is not None}
        db.cursor.execute(
            f'INSERT INTO products ({", ".join(list(filtered_dict.keys()))}) VALUES ({", ".join("?" * len(filtered_dict))})',
            list(filtered_dict.values())
            )
        db.conn.commit()


def edit_product(
        model: str,
        data: Dict[str, str]
):
    with Database() as db:
        set_clause = ', '.join(f"{key} = ?" for key in data.keys())
        query = f'UPDATE products SET {set_clause} WHERE model = ?'

        # Параметры для запроса (значения из словаря + модель для поиска)
        params = list(data.values()) + [model]
        db.cursor.execute(query, params)
        db.conn.commit()


def delete_product(
        model: str
):
    with Database() as db:
        db.cursor.execute('DELETE FROM products WHERE model = ?', (model,))
        db.conn.commit()
    

def get_product(
        name: str
) -> Optional[List[Product]]:
    with Database() as db:
        db.cursor.execute('SELECT * FROM products WHERE name = ?', (name,))
        rows = db.cursor.fetchall()
        rows = [Product.from_record(row) for row in rows]
    return rows

def list_all() -> List[Product]:
    with Database() as db:
        db.cursor.execute('SELECT * FROM products')
        rows = db.cursor.fetchall()
        rows = [Product.from_record(row) for row in rows]
    return rows