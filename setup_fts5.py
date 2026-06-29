"""Скрипт для создания FTS5 таблицы для полнотекстового поиска продуктов"""
import sqlite3
from pathlib import Path

DB_PATH = Path("data/database.db")

def setup_fts5():
    """Создает FTS5 таблицу и заполняет её данными из products"""
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    
    try:
        # Проверяем, существует ли уже FTS5 таблица
        cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='products_fts5'")
        if cursor.fetchone():
            print("FTS5 таблица уже существует. Удаляем старую...")
            cursor.execute("DROP TABLE products_fts5")
        
        # Создаем FTS5 таблицу
        print("Создаем FTS5 таблицу...")
        cursor.execute("""
            CREATE VIRTUAL TABLE products_fts5 USING fts5(
                name,
                model,
                description,
                category,
                content='products',
                content_rowid='rowid'
            )
        """)
        
        # Заполняем FTS5 таблицу данными из products
        print("Заполняем FTS5 таблицу данными...")
        cursor.execute("""
            INSERT INTO products_fts5(rowid, name, model, description, category)
            SELECT rowid, name, model, description, category FROM products
        """)
        
        # Создаем триггеры для автоматического обновления FTS5 при изменении products
        print("Создаем триггеры для автоматического обновления FTS5...")
        
        # Триггер для INSERT
        cursor.execute("""
            CREATE TRIGGER IF NOT EXISTS products_fts5_insert AFTER INSERT ON products BEGIN
                INSERT INTO products_fts5(rowid, name, model, description, category)
                VALUES (new.rowid, new.name, new.model, new.description, new.category);
            END;
        """)
        
        # Триггер для UPDATE
        cursor.execute("""
            CREATE TRIGGER IF NOT EXISTS products_fts5_update AFTER UPDATE ON products BEGIN
                UPDATE products_fts5 SET
                    name = new.name,
                    model = new.model,
                    description = new.description,
                    category = new.category
                WHERE rowid = new.rowid;
            END;
        """)
        
        # Триггер для DELETE
        cursor.execute("""
            CREATE TRIGGER IF NOT EXISTS products_fts5_delete AFTER DELETE ON products BEGIN
                DELETE FROM products_fts5 WHERE rowid = old.rowid;
            END;
        """)
        
        conn.commit()
        print(f"FTS5 таблица создана и заполнена. Записей: {cursor.rowcount}")
        print("Триггеры для автоматического обновления созданы.")
        
        # Проверяем работу поиска
        cursor.execute("SELECT COUNT(*) FROM products_fts5")
        count = cursor.fetchone()[0]
        print(f"Всего записей в FTS5: {count}")
        
        # Тестовый поиск
        cursor.execute("SELECT name FROM products_fts5 WHERE products_fts5 MATCH 'леггинсы COMFORT'")
        results = cursor.fetchall()
        print(f"Тестовый поиск 'леггинсы COMFORT': {len(results)} результатов")
        for row in results:
            print(f"  - {row[0]}")
            
    except Exception as e:
        print(f"Ошибка: {e}")
        conn.rollback()
    finally:
        conn.close()

if __name__ == "__main__":
    setup_fts5()

