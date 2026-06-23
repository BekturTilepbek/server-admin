import os
import aiofiles
from aiogram import Router, types, F
from aiogram.filters import Command, CommandObject
from config import ADMIN_ID
from keyboards import back_to_main_kb
from services.store import load_servers_sync, save_server

router = Router()

@router.callback_query(F.data == "get_bot_sys_logs")
async def show_bot_logs(call: types.CallbackQuery):
    # Дополнительная проверка безопасности
    if call.from_user.id != ADMIN_ID:
        return await call.answer("Только для админа!", show_alert=True)

    log_file = "bot_log.log"

    if not os.path.exists(log_file):
        return await call.answer("Файл логов пуст или не найден.", show_alert=True)

    await call.answer("Читаю файл...")

    # Читаем последние 4000 символов (примерно 50-70 строк)
    try:
        async with aiofiles.open(log_file, mode='rb') as f:
            # Переходим в конец файла
            await f.seek(0, 2)
            file_size = await f.tell()

            # Если файл маленький, читаем весь
            read_size = min(file_size, 4000)

            # Отступаем назад на read_size байт
            await f.seek(file_size - read_size)

            # Читаем и декодируем
            data = await f.read()
            text_logs = data.decode('utf-8', errors='ignore')

        # Отправляем (обрезаем если что-то лишнее попало)
        await call.message.edit_text(
            f"📑 <b>Системные логи бота (последние записи):</b>\n\n<pre>{text_logs}</pre>",
            reply_markup=back_to_main_kb()  # Кнопка "Назад"
        )

    except Exception as e:
        await call.message.answer(f"Ошибка чтения логов: {e}")


@router.message(Command("setname"))
async def set_bot_name_cmd(message: types.Message, command: CommandObject):
    # Пример: /setname server_1 bot1 Пиццерия Verona
    if message.from_user.id != ADMIN_ID: return

    if not command.args:
        return await message.answer(
            "⚠️ Использование: <code>/setname ID_СЕРВЕРА ID_БОТА НОВОЕ ИМЯ</code>\n"
            "Пример: <code>/setname server_1 bot1 Пиццерия</code>"
        )

    try:
        # Разбиваем команду: 45.11.22.33 | bot1 | Пиццерия Центр
        parts = command.args.split(" ", 2)
        if len(parts) < 3:
            return await message.answer("⚠️ Не хватает аргументов! Нужен IP, ID бота и Имя.")

        target_ip = parts[0]  # Теперь тут IP
        bot_id = parts[1]
        new_name = parts[2]

        servers = load_servers_sync()

        # --- ПОИСК СЕРВЕРА ПО IP ---
        found_key = None
        server_data = None

        for key, data in servers.items():
            if data['ip'] == target_ip:
                found_key = key
                server_data = data
                break  # Нашли! Прерываем цикл

        if not found_key:
            return await message.answer(f"❌ Сервер с IP <code>{target_ip}</code> не найден в базе.")

        # --- ОБНОВЛЕНИЕ ДАННЫХ ---

        # Проверяем, есть ли бот на этом сервере
        if bot_id not in server_data.get('bots', []):
            return await message.answer(f"❌ Бота <b>{bot_id}</b> нет на сервере {target_ip}.")

        # Создаем словарь имен, если нет
        if 'bot_labels' not in server_data:
            server_data['bot_labels'] = {}

        # Записываем новое имя
        server_data['bot_labels'][bot_id] = new_name

        # Сохраняем (используем найденный ключ)
        await save_server(found_key, server_data)

        await message.answer(
            f"✅ Успешно!\n"
            f"Сервер: {server_data['name']} ({target_ip})\n"
            f"Бот: {bot_id} 👉 <b>{new_name}</b>"
        )

    except Exception as e:
        await message.answer(f"Ошибка: {e}")