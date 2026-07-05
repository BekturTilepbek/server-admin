import os
import logging

import aiofiles
from aiogram import Router, types, F
from aiogram.filters import Command, CommandObject

from config import ADMIN_ID
from keyboards import back_to_main_kb
from services.store import load_servers_sync, save_server
from services.audit import actor_label
from services.billing import get_all_accounts_billing, format_digest

router = Router()
logger = logging.getLogger(__name__)


@router.callback_query(F.data == "billing_do")
async def show_billing(call: types.CallbackQuery):
    # Дублируем проверку: кнопка скрыта для не-админов, но callback может
    # прийти напрямую, поэтому валидируем доступ и здесь.
    if call.from_user.id != ADMIN_ID:
        return await call.answer("Только для админа!", show_alert=True)

    actor = actor_label(call.from_user.id, call.from_user.full_name)
    logger.info("💳 %s: запросил биллинг DigitalOcean вручную", actor)

    await call.answer()
    await call.message.edit_text(
        "⏳ Запрашиваю биллинг DigitalOcean...", reply_markup=None
    )
    accounts = await get_all_accounts_billing()
    await call.message.edit_text(
        format_digest(accounts), reply_markup=back_to_main_kb()
    )


@router.callback_query(F.data == "get_bot_sys_logs")
async def show_bot_logs(call: types.CallbackQuery):
    if call.from_user.id != ADMIN_ID:
        return await call.answer("Только для админа!", show_alert=True)

    actor = actor_label(call.from_user.id, call.from_user.full_name)
    logger.info("📑 %s: запросил системные логи бота", actor)

    log_file = "bot_log.log"
    if not os.path.exists(log_file):
        return await call.answer("Файл логов пуст или не найден.", show_alert=True)

    await call.answer("Читаю файл...")
    try:
        async with aiofiles.open(log_file, mode="rb") as f:
            await f.seek(0, 2)
            file_size = await f.tell()
            read_size = min(file_size, 4000)
            await f.seek(file_size - read_size)
            data = await f.read()
            text_logs = data.decode("utf-8", errors="ignore")

        await call.message.edit_text(
            f"📑 <b>Системные логи бота (последние записи):</b>\n\n<pre>{text_logs}</pre>",
            reply_markup=back_to_main_kb(),
        )
    except Exception as e:
        logger.error("❌ %s: ошибка чтения системных логов: %s", actor, e)
        await call.message.answer(f"Ошибка чтения логов: {e}")


@router.message(Command("setname"))
async def set_bot_name_cmd(message: types.Message, command: CommandObject):
    # /setname <IP> <bot_id> <Новое имя>
    if message.from_user.id != ADMIN_ID:
        return

    actor = actor_label(message.from_user.id, message.from_user.full_name)

    if not command.args:
        return await message.answer(
            "⚠️ Использование: <code>/setname IP ID_БОТА НОВОЕ ИМЯ</code>\n"
            "Пример: <code>/setname 45.11.22.33 bot1 Пиццерия</code>"
        )

    try:
        parts = command.args.split(" ", 2)
        if len(parts) < 3:
            return await message.answer("⚠️ Не хватает аргументов! Нужен IP, ID бота и Имя.")

        target_ip, bot_id, new_name = parts[0], parts[1], parts[2]
        servers = load_servers_sync()

        found_key, server_data = None, None
        for key, data in servers.items():
            if data["ip"] == target_ip:
                found_key, server_data = key, data
                break

        if not found_key:
            logger.warning("🏷 %s: /setname — сервер %s не найден", actor, target_ip)
            return await message.answer(f"❌ Сервер с IP <code>{target_ip}</code> не найден.")
        if bot_id not in server_data.get("bots", []):
            logger.warning("🏷 %s: /setname — бота %s нет на %s", actor, bot_id, target_ip)
            return await message.answer(f"❌ Бота <b>{bot_id}</b> нет на сервере {target_ip}.")

        old_name = server_data.get("bot_labels", {}).get(bot_id, bot_id)
        server_data.setdefault("bot_labels", {})[bot_id] = new_name
        await save_server(found_key, server_data)

        logger.info(
            "🏷 %s: имя бота %s на «%s» изменено: «%s» 👉 «%s»",
            actor, bot_id, server_data["name"], old_name, new_name,
        )

        await message.answer(
            f"✅ Успешно!\n"
            f"Сервер: {server_data['name']} ({target_ip})\n"
            f"Бот: {bot_id} 👉 <b>{new_name}</b>"
        )
    except Exception as e:
        logger.error("❌ %s: ошибка /setname: %s", actor, e)
        await message.answer(f"Ошибка: {e}")