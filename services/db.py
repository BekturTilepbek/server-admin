import re
import asyncio
import logging

from services.ssh import execute_command
from services.store import load_servers_sync

logger = logging.getLogger(__name__)


async def clear_cache(server_key: str, bot_id: str, phone: str, actor: str = "неизвестно") -> str:
    """
    Очистка кэша диалога в БД. Возвращает готовый текст для пользователя.
    """
    # 1. Чистим номер до цифр и добавляем суффикс WhatsApp
    clean_phone = re.sub(r"\D", "", phone)
    if not clean_phone:
        return "❌ В сообщении нет цифр. Начните заново."
    db_phone = f"{clean_phone}@c.us"

    # 2. Определяем таблицу
    if bot_id == "bot1":
        table_name = "conversations"
    elif bot_id.startswith("bot") and bot_id[3:].isdigit():
        table_name = f"conversations_{bot_id[3:]}"
    else:
        return "❌ Ошибка: не удалось определить таблицу для этого бота."

    # 3. Достаём сервер
    server_data = load_servers_sync().get(server_key)
    if not server_data:
        return "❌ Ошибка: Сервер не найден."

    # 4. Формируем запрос (search_path: сначала public, потом nevodevs)
    sql_query = (
        f"SET search_path TO public, nevodevs; "
        f"DELETE FROM {table_name} WHERE user_id = '{db_phone}';"
    )
    bash_cmd = (
        f"docker exec whatsapp-postgres "
        f"psql -U adminbek -d moidb -c \"{sql_query}\""
    )

    server_name = server_data.get("name", server_key)
    logger.warning(
        "🧹 %s: очищает кэш %s (таблица %s) на сервере «%s»",
        actor, db_phone, table_name, server_name,
    )

    # 5. Выполняем
    status, out, err = await asyncio.to_thread(execute_command, server_data, bash_cmd)

    # 6. Анализ
    if status != 0:
        logger.error("❌ %s: ошибка очистки кэша %s на «%s»: %s", actor, db_phone, server_name, err)
        return f"❌ Ошибка БД:\n<pre>{err}</pre>"
    if out.strip() == "DELETE 0":
        logger.info("⚠️ %s: кэш %s на «%s» уже был пуст", actor, db_phone, server_name)
        return (
            f"⚠️ Кэш пуст.\nНомер <code>{db_phone}</code> "
            f"не найден в базе ({table_name})."
        )
    logger.warning("✅ %s: кэш %s на «%s» очищен", actor, db_phone, server_name)
    return f"✅ Успешно! Кэш для <code>{db_phone}</code> очищен."