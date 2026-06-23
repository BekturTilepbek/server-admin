import logging
from aiogram import BaseMiddleware
from aiogram.types import Message, CallbackQuery
from config import ADMIN_ID
from services.store import get_allowed_ids, get_user_name


class AuthMiddleware(BaseMiddleware):
    async def __call__(self, handler, event, data):
        # Получаем объект юзера Telegram
        tg_user = data.get("event_from_user")
        if not tg_user:
            return await handler(event, data)

        user_id = tg_user.id

        # 1. Определяем, КТО это
        if user_id == ADMIN_ID:
            user_label = "👑 ADMIN"
        else:
            # Ищем имя в нашем JSON файле
            db_name = get_user_name(user_id)
            user_label = f"👤 {db_name} ({user_id})"

        # 2. Определяем, ЧТО он сделал
        action = "Unknown"
        if isinstance(event, Message):
            action = f"Написал: '{event.text}'"
        elif isinstance(event, CallbackQuery):
            action = f"Нажал кнопку: '{event.data}'"

        # 3. ПРОВЕРКА ДОСТУПА
        allowed_ids = get_allowed_ids()

        # Если это Админ или юзер из списка
        if user_id == ADMIN_ID or user_id in allowed_ids:
            # ✅ ЛОГИРУЕМ УСПЕШНОЕ ДЕЙСТВИЕ
            logging.info(f"{user_label} >> {action}")
            return await handler(event, data)

        # 4. Если доступ запрещен
        logging.warning(f"⛔️ {user_label} попытался: {action} (ДОСТУП ЗАПРЕЩЕН)")

        if isinstance(event, Message):
            await event.answer(f"⛔️ Доступ запрещен.\nВаш ID: <code>{user_id}</code>")
        return