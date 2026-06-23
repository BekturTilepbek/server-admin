import logging

from aiogram import BaseMiddleware
from aiogram.types import Message, CallbackQuery

from config import ADMIN_ID
from services.store import load_users_raw

logger = logging.getLogger(__name__)


class AuthMiddleware(BaseMiddleware):
    async def __call__(self, handler, event, data):
        tg_user = data.get("event_from_user")
        if not tg_user:
            return await handler(event, data)

        user_id = tg_user.id

        # Один кэшированный (in-memory) проход по пользователям вместо
        # двух обращений к диску, как было раньше.
        users = load_users_raw()
        user_map = {u["id"]: u["name"] for u in users}

        is_admin = user_id == ADMIN_ID
        if is_admin:
            user_label = "👑 ADMIN"
        else:
            db_name = user_map.get(user_id, "Неизвестный")
            user_label = f"👤 {db_name} ({user_id})"

        if isinstance(event, Message):
            action = f"Написал: '{event.text}'"
        elif isinstance(event, CallbackQuery):
            action = f"Нажал кнопку: '{event.data}'"
        else:
            action = "Unknown"

        if is_admin or user_id in user_map:
            logger.info("%s >> %s", user_label, action)
            return await handler(event, data)

        logger.warning("⛔️ %s попытался: %s (ДОСТУП ЗАПРЕЩЕН)", user_label, action)
        if isinstance(event, Message):
            await event.answer(f"⛔️ Доступ запрещен.\nВаш ID: <code>{user_id}</code>")
        return
