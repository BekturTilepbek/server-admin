import re
import asyncio
from services.ssh import execute_command
from services.store import load_servers_sync


async def clear_cache(server_key: str, bot_id: str, phone: str) -> str:
    """
    Выполняет логику очистки кэша в БД.
    Возвращает готовый текст для отправки пользователю.
    """
    # 1. Форматируем номер
    clean_phone = re.sub(r'\D', '', phone)
    if not clean_phone:
        return "❌ В сообщении нет цифр. Начните заново."

    db_phone = clean_phone if clean_phone.endswith('@c.us') else f"{clean_phone}@c.us"

    # 2. Определяем таблицу
    if bot_id == "bot1":
        table_name = "conversations"
    elif bot_id.startswith("bot") and bot_id[3:].isdigit():
        table_name = f"conversations_{bot_id[3:]}"
    else:
        return "❌ Ошибка: не удалось определить таблицу для этого бота."

    # 3. Достаем сервер
    servers = load_servers_sync()
    server_data = servers.get(server_key)
    if not server_data:
        return "❌ Ошибка: Сервер не найден."

    # 4. Формируем запрос
    sql_query = f"SET search_path TO public, nevodevs; DELETE FROM {table_name} WHERE user_id = '{db_phone}';"
    bash_cmd = f"docker exec whatsapp-postgres psql -U adminbek -d moidb -c \"{sql_query}\""

    # 5. Выполняем через SSH
    status, out, err = await asyncio.to_thread(execute_command, server_data, bash_cmd)

    # 6. Анализируем результат
    if status == 0:
        if out.strip() == "DELETE 0":
            return f"⚠️ Кэш пуст.\nНомер <code>{db_phone}</code> не найден в базе ({table_name})."
        else:
            return f"✅ Успешно! Кэш для <code>{db_phone}</code> очищен."
    else:
        return f"❌ Ошибка БД:\n<pre>{err}</pre>"