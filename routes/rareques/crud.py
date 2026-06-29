from models import Database


def save_response(id: int, ans: str):
    """
    Сохраняет ответ (ans) в поле response для записи с указанным id в таблице rarequests
    """
    with Database() as db:
        # Проверяем, существует ли запись с таким id
        db.cursor.execute("SELECT id FROM rarequests WHERE id = ?", (id,))
        if not db.cursor.fetchone():
            raise ValueError(f"Запись с id={id} не найдена в таблице rarequests")
        
        # Обновляем поле response
        db.cursor.execute(
            "UPDATE rarequests SET response = ? WHERE id = ?",
            (ans, id)
        )
        db.conn.commit()

